"""Assistente consultando os módulos e fazendo a triagem do Service Desk (modelo de linguagem simulado)."""
import json
from unittest import mock

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.assistente import servico, telegram
from apps.assistente.models import ConfiguracaoIA
from apps.core.models import Fornecedor, Notificacao
from apps.servicedesk import ia_triagem
from apps.servicedesk.models import Area, ArtigoConhecimento, Chamado, Servico


def modelo(*respostas):
    return mock.patch("apps.assistente.ia.chamar", side_effect=[
        r if isinstance(r, Exception) else json.dumps(r) if isinstance(r, dict) else r for r in respostas])


class ConsultaTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_erp", verbosity=0)
        cls.comp = User.objects.create_user("comp", password="x")
        cls.comp.groups.add(Group.objects.get(name="Compras"))
        cls.comum = User.objects.create_user("comum", password="x")
        cls.comum.groups.add(Group.objects.get(name="Usuários"))
        c = ConfiguracaoIA.atual()
        c.provedor, c.modelo = "ollama", "teste"
        c.save()
        Fornecedor.objects.create(nome="Papelaria Sol Ltda", documento="11.444.777/0001-61")
        Fornecedor.objects.create(nome="Aço Forte S.A.", documento="11.222.333/0001-81")

    def test_consulta_mostra_resultado_sem_enviar_dados_ao_modelo(self):
        with modelo({"consulta": "core.fornecedor"}, {"busca": "papelaria", "filtros": {"inventado": "x"}}) as m:
            sc = servico.interpretar(self.comp, "quais fornecedores têm papelaria no nome?")
        self.assertEqual((sc.tipo, sc.alvo, sc.dados), ("consulta", "core.fornecedor", {"q": "papelaria"}))
        enviado = " ".join(str(a) for chamada in m.call_args_list for a in chamada.args[1:])
        self.assertNotIn("Papelaria Sol", enviado)                 # o modelo só vê o pedido e os nomes dos filtros
        self.client.force_login(self.comp)
        r = self.client.get(reverse("assistente:conversa"))
        res = r.context["pedidos"][0].resultado
        self.assertEqual(res["total"], 1)
        self.assertContains(r, "Papelaria Sol Ltda")
        self.assertNotContains(r, "Aço Forte")

    def test_consulta_respeita_permissao(self):
        with modelo({"consulta": "core.fornecedor"}):
            sc = servico.interpretar(self.comum, "liste os fornecedores")
        self.assertEqual((sc.status, sc.tipo, sc.dados), ("nao_entendida", "cadastro", {}))

    def test_telegram_nao_consulta(self):
        with modelo({"consulta": "core.fornecedor"}) as m:
            sc = servico.interpretar(self.comp, "liste os fornecedores", "telegram", consultar=False)
        self.assertEqual((sc.status, sc.tipo), ("nao_entendida", "cadastro"))
        self.assertIn("(nenhuma)", m.call_args.args[2])
        self.assertIn("consultar=False", open(telegram.__file__).read())


@override_settings(SERVICEDESK_HORARIO="24x7")
class TriagemTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_erp", verbosity=0)
        cls.ana = User.objects.create_user("ana", password="x")
        cls.ana.groups.add(Group.objects.get(name="Usuários"))
        cls.outro = User.objects.create_user("outro", password="x")
        cls.outro.groups.add(Group.objects.get(name="Usuários"))
        gti = Group.objects.create(name="Equipe TI teste")
        cls.tec = User.objects.create_user("tec", password="x")
        cls.tec.groups.add(gti)
        cls.ti = Area.objects.create(nome="TI teste", grupo=gti)
        cls.rh = Area.objects.create(nome="RH teste", grupo=Group.objects.create(name="Equipe RH teste"))
        cls.senha = Servico.objects.create(area=cls.ti, nome="Senha e acesso", palavras_chave="senha login bloqueio")
        cls.ferias = Servico.objects.create(area=cls.rh, nome="Férias", palavras_chave="férias")
        cls.artigo = ArtigoConhecimento.objects.create(
            titulo="Senha bloqueada", servico=cls.senha, area=cls.ti, sintoma="senha bloqueada não consigo entrar login",
            solucao="1. Acesse o portal. 2. Clique em Esqueci a senha.", palavras_chave="senha bloqueada login")
        cls.interno = ArtigoConhecimento.objects.create(
            titulo="Senha: procedimento interno", area=cls.ti, sintoma="senha bloqueada login",
            solucao="SEGREDO-DA-EQUIPE", palavras_chave="senha bloqueada", publico=False)
        c = ConfiguracaoIA.atual()
        c.provedor, c.modelo, c.servicedesk_triagem = "ollama", "teste", True
        c.save()

    def abrir(self, *respostas):
        self.client.force_login(self.ana)
        with modelo(*respostas) as m:
            r = self.client.post(reverse("servicedesk:abrir_com_ia"), {"texto": "minha senha está bloqueada no login"})
        self.chamadas = m.call_args_list
        return Chamado.objects.order_by("-id").first(), r

    def test_classifica_orienta_e_usuario_encerra(self):
        c, _ = self.abrir({"servico": self.senha.pk, "prioridade": "critica", "titulo": "Senha bloqueada"},
                          {"resolve": True, "artigo": self.artigo.pk, "orientacao": "1. Acesse o portal."})
        self.assertEqual((c.servico, c.area, c.solicitante, c.status), (self.senha, self.ti, self.ana, "aberto"))
        self.assertNotEqual(c.prioridade, "critica")               # a IA não escolhe prioridade crítica
        self.assertTrue(c.classificado_por_ia)
        self.assertEqual((c.triagem_ia, c.artigo), ("orientado", self.artigo))
        self.assertIsNone(c.primeira_resposta_em)                  # resposta da IA não conta no SLA
        self.assertNotIn("SEGREDO-DA-EQUIPE", str(self.chamadas))  # artigo interno não vai para o usuário
        self.assertTrue(c.mensagens.filter(ia=True, autor=None).exists())
        tela = self.client.get(c.get_absolute_url())
        self.assertContains(tela, "Resolveu, pode encerrar")
        self.assertContains(self.client.get(reverse("servicedesk:catalogo")), "Abrir chamado com a IA")
        self.assertEqual(self.client.get(reverse("servicedesk:mensagens", args=[c.pk])).json()["mensagens"][-1]["autor"],
                         "Assistente (IA)")
        self.client.force_login(self.tec)
        self.assertContains(self.client.get(reverse("servicedesk:fila")), "com a IA")
        # outra pessoa não encerra
        self.client.force_login(self.outro)
        self.client.post(reverse("servicedesk:ia_resposta", args=[c.pk]), {"resolveu": "sim"})
        c.refresh_from_db()
        self.assertEqual(c.status, "aberto")
        self.client.force_login(self.ana)
        self.client.post(reverse("servicedesk:ia_resposta", args=[c.pk]), {"resolveu": "sim"})
        c.refresh_from_db()
        self.artigo.refresh_from_db()
        self.assertEqual((c.status, c.triagem_ia, self.artigo.util_sim), ("fechado", "resolvido", 1))
        self.assertIn("Acesse o portal", c.solucao)

    def test_nao_resolveu_encaminha_para_a_equipe(self):
        c, _ = self.abrir({"servico": self.senha.pk, "prioridade": "media", "titulo": "Senha"},
                          {"resolve": True, "artigo": self.artigo.pk, "orientacao": "1. Acesse o portal."})
        self.client.post(reverse("servicedesk:ia_resposta", args=[c.pk]), {"resolveu": "nao"})
        c.refresh_from_db()
        self.artigo.refresh_from_db()
        self.assertEqual((c.status, c.triagem_ia, self.artigo.util_nao), ("aberto", "encaminhado", 1))
        self.assertTrue(Notificacao.objects.filter(usuario=self.tec, texto__contains=c.numero).exists())

    def test_sem_solucao_ou_com_falha_encaminha(self):
        c, _ = self.abrir({"servico": self.senha.pk, "prioridade": "media", "titulo": "Senha"}, {"resolve": False})
        self.assertEqual((c.status, c.triagem_ia), ("aberto", "encaminhado"))
        c, _ = self.abrir({"servico": self.senha.pk, "prioridade": "media", "titulo": "Senha"},
                          ValidationError("fora do ar"))
        self.assertEqual((c.status, c.triagem_ia), ("aberto", "encaminhado"))
        # artigo que não estava na lista oferecida é recusado
        c, _ = self.abrir({"servico": self.senha.pk, "prioridade": "media", "titulo": "Senha"},
                          {"resolve": True, "artigo": self.interno.pk, "orientacao": "faça isso"})
        self.assertEqual(c.triagem_ia, "encaminhado")

    def test_servico_desconhecido_volta_ao_catalogo(self):
        antes = Chamado.objects.count()
        _, r = self.abrir({"servico": 99999})
        self.assertEqual(Chamado.objects.count(), antes)
        self.assertRedirects(r, reverse("servicedesk:catalogo"))

    def test_conversa_tem_limite_e_analista_assume(self):
        c, _ = self.abrir({"servico": self.senha.pk, "prioridade": "media", "titulo": "Senha"},
                          {"resolve": True, "artigo": self.artigo.pk, "orientacao": "passo 1"})
        url = reverse("servicedesk:mensagens", args=[c.pk])
        ok = {"resolve": True, "artigo": self.artigo.pk, "orientacao": "tente de novo"}
        with modelo(ok, ok, ok):
            self.client.post(url, {"texto": "não deu"})
            self.client.post(url, {"texto": "ainda não"})
            c.refresh_from_db()
            self.assertEqual((c.triagem_ia, c.ia_turnos), ("orientado", 3))
            self.client.post(url, {"texto": "nada"})
        c.refresh_from_db()
        self.assertEqual(c.triagem_ia, "encaminhado")
        c2, _ = self.abrir({"servico": self.senha.pk, "prioridade": "media", "titulo": "Senha"},
                           {"resolve": True, "artigo": self.artigo.pk, "orientacao": "passo 1"})
        c2.assumir(self.tec)
        c2.refresh_from_db()
        self.assertEqual((c2.triagem_ia, c2.status), ("encaminhado", "atendimento"))
        with self.assertRaises(ValidationError):
            c2.ia_resolveu(self.ana)

    def test_desligada_nao_interfere(self):
        ConfiguracaoIA.objects.update(servicedesk_triagem=False)
        c = Chamado.objects.create(servico=self.senha, titulo="Senha bloqueada", descricao="login", solicitante=self.ana)
        ia_triagem.primeiro_atendimento(c)
        self.assertEqual(c.triagem_ia, "")
        self.assertIsNone(ia_triagem.classificar("senha"))
