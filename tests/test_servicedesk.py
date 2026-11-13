import datetime

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.core.middleware import _local
from apps.core.models import Colaborador, Notificacao
from apps.servicedesk import sla
from apps.servicedesk.models import (Area, ArtigoConhecimento, Chamado, Mudanca, Servico, sugerir_servicos,
                                     verificar_slas)
from apps.workflow.models import Instancia

TZ = timezone.get_current_timezone()


def dt(*a):
    return timezone.make_aware(datetime.datetime(*a), TZ)


@override_settings(SERVICEDESK_HORARIO="08:00-18:00")
class SlaTest(TestCase):
    def test_horario_comercial(self):
        # sexta 16h + 4h úteis = segunda 10h
        self.assertEqual(sla.somar(dt(2026, 11, 13, 16), 4 * 3600), dt(2026, 11, 16, 10))
        # sábado conta a partir de segunda 8h
        self.assertEqual(sla.somar(dt(2026, 11, 14, 11), 3600), dt(2026, 11, 16, 9))
        self.assertEqual(sla.entre(dt(2026, 11, 13, 16), dt(2026, 11, 16, 10)), 4 * 3600)

    @override_settings(SERVICEDESK_HORARIO="24x7")
    def test_24x7(self):
        self.assertEqual(sla.somar(dt(2026, 11, 14, 11), 3600), dt(2026, 11, 14, 12))


@override_settings(SERVICEDESK_HORARIO="24x7")
class ServiceDeskTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_erp", verbosity=0)
        usuarios = Group.objects.get(name="Usuários")
        cls.ana = User.objects.create_user("ana", password="x", first_name="Ana", email="ana@x.com")
        cls.ana.groups.add(usuarios)
        cls.bia = User.objects.create_user("bia", password="x", first_name="Bia")  # analista de TI
        cls.bia.groups.add(Group.objects.get(name="Service Desk"), usuarios)
        cls.gil = User.objects.create_user("gil", password="x", first_name="Gil")  # gestor da Ana
        cls.gil.groups.add(usuarios)
        cls.cab = User.objects.create_user("cab", password="x")
        cls.cab.groups.add(Group.objects.get(name="CAB"), usuarios)
        g = Colaborador.objects.create(nome="Gil", cpf="52998224725", usuario=cls.gil)
        Colaborador.objects.create(nome="Ana", cpf="11144477735", usuario=cls.ana, gestor=g)
        cls.ti = Area.objects.create(nome="TI", grupo=Group.objects.get(name="Service Desk"), gestor=cls.gil)
        cls.fac = Area.objects.create(nome="Facilities", grupo=Group.objects.get(name="Manutenção"))
        cls.senha = Servico.objects.create(area=cls.ti, nome="Reset de senha", palavras_chave="senha bloqueada login",
                                           sla_resposta_horas=1, sla_solucao_horas=4)
        cls.notebook = Servico.objects.create(area=cls.ti, nome="Novo notebook", palavras_chave="computador notebook",
                                              aprovacao="gestor_solicitante", sla_solucao_horas=40)

    def abrir(self, servico=None, por=None, prioridade=""):
        _local.usuario = por or self.ana
        try:
            return Chamado.objects.create(servico=servico or self.senha, titulo="Senha bloqueada",
                                          descricao="Errei a senha 3 vezes", prioridade=prioridade)
        finally:
            _local.usuario = None

    def test_abertura_prazos_por_prioridade_e_aviso_a_equipe(self):
        c = self.abrir(prioridade="alta")
        self.assertEqual((c.status, c.area, c.solicitante), ("aberto", self.ti, self.ana))
        self.assertAlmostEqual((c.prazo_solucao - c.sla_iniciado_em).total_seconds(), 2 * 3600, delta=2)  # 4h x 0,5
        self.assertTrue(Notificacao.objects.filter(usuario=self.bia, texto__contains=c.numero).exists())
        self.assertEqual(c.sla_situacao(), "No prazo")

    def test_aprovacao_pelo_gestor_do_solicitante(self):
        c = self.abrir(self.notebook)
        self.assertEqual((c.status, c.aprovador), ("aguardando_aprovacao", self.gil))
        inst = Instancia.objects.get(object_id=c.pk, processo__codigo="aprovacao-servico")
        self.assertEqual(inst.responsavel_atual, self.gil)
        with self.assertRaises(ValidationError):
            inst.avancar(self.bia, "aprovar")
        inst.avancar(self.gil, "aprovar")
        c.refresh_from_db()
        self.assertEqual(c.status, "aberto")
        self.assertIsNotNone(c.prazo_solucao)
        c2 = self.abrir(self.notebook)
        Instancia.objects.get(object_id=c2.pk, processo__codigo="aprovacao-servico").avancar(self.gil, "rejeitar",
                                                                                             "Notebook ainda novo")
        c2.refresh_from_db()
        self.assertEqual(c2.status, "recusado")

    def test_ciclo_chat_pausa_resolucao_e_avaliacao(self):
        c = self.abrir()
        with self.assertRaises(ValidationError):
            c.assumir(self.ana)  # não é da equipe
        c.assumir(self.bia)
        self.assertIsNotNone(c.primeira_resposta_em)
        prazo = c.prazo_solucao
        c.chamar_usuario(self.bia, "Qual o seu ramal?")
        self.assertEqual((c.status, c.sla_situacao()), ("aguardando_usuario", "Pausado"))
        self.assertTrue(Notificacao.objects.filter(usuario=self.ana, texto__contains="precisa de você").exists())
        c.enviar_mensagem(self.bia, "nota para a equipe", interna=True)
        c.enviar_mensagem(self.ana, "Ramal 2040")  # resposta do usuário retoma o SLA
        c.refresh_from_db()
        self.assertEqual(c.status, "atendimento")
        self.assertGreaterEqual(c.prazo_solucao, prazo - datetime.timedelta(seconds=1))
        c.resolver(self.bia, "Senha resetada")
        with self.assertRaises(ValidationError):
            c.fechar(self.bia, 5)  # quem confirma é o solicitante
        c.reabrir(self.ana, "Ainda bloqueada")
        c.resolver(self.bia, "Desbloqueada no AD")
        c.fechar(self.ana, 5, "Rápido!")
        c.refresh_from_db()
        self.assertEqual((c.status, c.avaliacao, c.sla_situacao()), ("fechado", 5, "Cumprido"))

    def test_avisos_de_sla_e_fechamento_automatico(self):
        c = self.abrir()
        agora = timezone.now()
        avisos, _ = verificar_slas(agora + datetime.timedelta(hours=3.5))  # 87% de 4h: em risco
        c.refresh_from_db()
        self.assertTrue(c.alerta_risco)
        self.assertTrue(c.alerta_resposta)  # 1ª resposta (1h) vencida
        verificar_slas(agora + datetime.timedelta(hours=5))
        c.refresh_from_db()
        self.assertTrue(c.alerta_vencido)
        self.assertTrue(Notificacao.objects.filter(usuario=self.gil, texto__startswith="SLA vencido").exists())
        n = Notificacao.objects.count()
        verificar_slas(agora + datetime.timedelta(hours=6))  # não repete
        self.assertEqual(Notificacao.objects.count(), n)
        c.resolver(self.bia, "ok")
        _, fechados = verificar_slas(timezone.now() + datetime.timedelta(days=4))
        self.assertEqual(fechados, 1)

    def test_transferencia_de_area(self):
        c = self.abrir()
        c.transferir(self.bia, self.fac, "É a fechadura da sala")
        self.assertEqual((c.area, c.analista), (self.fac, None))
        self.assertFalse(Chamado.objects.visiveis_para(self.bia).filter(pk=c.pk).exists())
        self.assertTrue(Chamado.objects.visiveis_para(self.ana).filter(pk=c.pk).exists())

    def test_biblioteca_e_assistente(self):
        ArtigoConhecimento.objects.create(titulo="Como desbloquear a senha do Windows", area=self.ti,
                                          sintoma="Conta bloqueada após errar a senha", solucao="Use o autoatendimento",
                                          palavras_chave="senha bloqueio windows")
        ArtigoConhecimento.objects.create(titulo="Procedimento interno de reset", area=self.ti, publico=False,
                                          sintoma="reset de senha no AD", solucao="dsmod...", palavras_chave="senha")
        self.assertEqual([a.titulo for a in ArtigoConhecimento.buscar("minha senha está bloqueada", self.ana)],
                         ["Como desbloquear a senha do Windows"])
        self.assertEqual(len(ArtigoConhecimento.buscar("senha", self.bia)), 2)  # equipe vê o interno
        self.assertEqual(sugerir_servicos("preciso de um notebook novo")[0], self.notebook)
        self.client.force_login(self.ana)
        self.client.post(reverse("servicedesk:assistente"), {"texto": "minha senha bloqueou"})
        r = self.client.get(reverse("servicedesk:assistente"))
        self.assertContains(r, "Como desbloquear a senha do Windows")
        self.assertContains(r, "Abrir chamado: TI › Reset de senha")
        a = ArtigoConhecimento.objects.get(publico=True)
        r = self.client.post(reverse("servicedesk:artigo_ler", args=[a.pk]), {"util": "sim"})
        self.client.post(reverse("servicedesk:artigo_ler", args=[a.pk]), {"util": "sim"})  # voto único
        a.refresh_from_db()
        self.assertEqual((a.util_sim, a.visualizacoes), (1, 2))
        interno = ArtigoConhecimento.objects.get(publico=False)
        self.assertEqual(self.client.get(reverse("servicedesk:artigo_ler", args=[interno.pk])).status_code, 404)

    def test_telas_usuario_e_analista(self):
        self.client.force_login(self.ana)
        self.assertContains(self.client.get(reverse("servicedesk:catalogo")), "Reset de senha")
        r = self.client.get(reverse("servicedesk:chamado_create") + f"?servico={self.senha.pk}")
        self.assertEqual(r.status_code, 200)
        r = self.client.post(reverse("servicedesk:chamado_create"),
                             {"servico": self.senha.pk, "titulo": "Bloqueado", "descricao": "não entra"})
        c = Chamado.objects.get()
        self.assertEqual(r.status_code, 302)
        self.assertEqual(c.solicitante, self.ana)
        r = self.client.post(reverse("servicedesk:mensagens", args=[c.pk]), {"texto": "Alguém?"})
        self.assertEqual(r.json()["ok"], True)
        self.assertEqual(self.client.get(reverse("servicedesk:fila")).status_code, 302)  # não é da equipe
        self.client.force_login(self.bia)
        self.assertContains(self.client.get(reverse("servicedesk:fila")), c.numero)
        self.client.post(reverse("servicedesk:assumir", args=[c.pk]))
        self.client.post(reverse("servicedesk:mensagens", args=[c.pk]), {"texto": "segredo", "interna": "1"})
        self.client.force_login(self.ana)
        msgs = self.client.get(reverse("servicedesk:mensagens", args=[c.pk])).json()["mensagens"]
        self.assertNotIn("segredo", [m["texto"] for m in msgs])
        self.assertContains(self.client.get(c.get_absolute_url()), "Conversa")
        outro = User.objects.create_user("outro", password="x")
        outro.groups.add(Group.objects.get(name="Usuários"))
        self.client.force_login(outro)
        self.assertEqual(self.client.get(c.get_absolute_url()).status_code, 404)
        self.assertEqual(self.client.get(reverse("servicedesk:mensagens", args=[c.pk])).status_code, 404)
        r = self.client.get(reverse("avisos"))
        self.assertEqual(r.status_code, 200)

    def test_mudanca_normal_emergencial_e_padrao(self):
        ini = timezone.now() + datetime.timedelta(days=2)
        base = dict(area=self.ti, descricao="Atualizar firewall", justificativa="Correção de segurança",
                    impacto="Internet fora por 10 min", plano_implantacao="Aplicar patch", janela_inicio=ini,
                    janela_fim=ini + datetime.timedelta(hours=1), responsavel=self.bia)
        m = Mudanca(titulo="Patch firewall", tipo="normal", **base)
        with self.assertRaisesMessage(ValidationError, "plano de retorno"):
            m.full_clean()
        m.plano_retorno = "Voltar a versão anterior"
        m.save()
        c = self.abrir()
        m.chamados.add(c)
        inst = m.enviar_para_aprovacao(self.bia)
        self.assertEqual(inst.responsavel_atual, self.gil)  # gestor da área
        inst.avancar(self.gil, "aprovar")
        inst.refresh_from_db()
        self.assertIsNone(inst.responsavel_atual)
        self.assertIn(inst, Instancia.objects.pendentes_para(self.cab))  # grupo CAB
        inst.avancar(self.cab, "aprovar")
        m.refresh_from_db()
        self.assertEqual(m.status, "aprovada")
        emerg = Mudanca.objects.create(titulo="Servidor caiu", tipo="emergencial", plano_retorno="x", **base)
        self.assertEqual(list(emerg.conflitos()), [m])
        inst2 = emerg.enviar_para_aprovacao(self.bia)
        self.assertEqual(inst2.etapa_atual.nome, "Aprovação do CAB")  # pula o gestor
        padrao = Mudanca.objects.create(titulo="Troca de toner", tipo="padrao", **base)
        self.assertIsNone(padrao.enviar_para_aprovacao(self.bia))
        self.assertEqual(padrao.status, "aprovada")
        m.iniciar_execucao(self.bia)
        m.concluir(self.bia, True, "Patch aplicado, testes ok")
        self.assertTrue(c.mensagens.filter(texto__contains=m.numero).exists())
        self.client.force_login(self.bia)
        self.assertEqual(self.client.get(m.get_absolute_url()).status_code, 200)
        self.assertContains(self.client.get(emerg.get_absolute_url()), "coincide")
