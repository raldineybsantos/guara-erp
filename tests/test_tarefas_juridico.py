"""Testes do Kanban de atividades, da assinatura digital ICP-Brasil e do módulo Jurídico."""
import datetime
import io
import json
import shutil
import tempfile
from decimal import Decimal
from pathlib import Path

from django.contrib.auth.models import Group, Permission, User
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Cliente, Colaborador, Departamento, Empresa, Fornecedor
from apps.core.pdf_simples import gerar_pdf
from apps.ged import assinatura as ass
from apps.ged.cert_teste import gerar_pfx
from apps.ged.models import Assinatura, Documento, Pasta
from apps.juridico.models import Aditivo, Contrato, processar_vencimentos
from apps.tarefas.models import Atividade, equipe_de
from apps.workflow.models import Instancia

HOJE = timezone.localdate()
MEDIA_TESTE = tempfile.mkdtemp(prefix="guara-media-")
CPF_ANA, CPF_BRUNO, CPF_CARLA = "52998224725", "11144477735", "39053344705"
CNPJ_EMPRESA = "11222333000181"


@override_settings(MEDIA_ROOT=MEDIA_TESTE)
class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_erp", stdout=io.StringIO())
        cls.empresa = Empresa.objects.create(razao_social="Empresa Teste", cnpj=CNPJ_EMPRESA)
        cls.admin = User.objects.create_superuser("admin", "a@a.com", "x")
        dep = Departamento.objects.create(nome="Financeiro", codigo="FIN")
        sub = Departamento.objects.create(nome="Tesouraria", codigo="TES", superior=dep)
        cls.gestora = cls.usuario("gestora", "Financeiro")
        cls.ana = cls.usuario("ana", "Financeiro")
        cls.bruno = cls.usuario("bruno", "Financeiro")
        cls.rh = cls.usuario("rh", "RH")
        cls.jur = cls.usuario("jur", "Jurídico")
        c_gestora = Colaborador.objects.create(nome="Gestora", cpf=CPF_CARLA, usuario=cls.gestora, departamento=dep)
        dep.responsavel = c_gestora
        dep.save()
        Colaborador.objects.create(nome="Ana", cpf=CPF_ANA, usuario=cls.ana, departamento=sub)       # via departamento
        Colaborador.objects.create(nome="Bruno", cpf=CPF_BRUNO, usuario=cls.bruno, gestor=c_gestora)  # via gestor direto
        cls.forn = Fornecedor.objects.create(nome="Fornecedor X")
        cls.cli = Cliente.objects.create(nome="Cliente Y")

    @classmethod
    def usuario(cls, nome, grupo):
        u = User.objects.create_user(nome, password="SenhaForte#2026", first_name=nome.title())
        u.groups.add(Group.objects.get(name=grupo))
        return u

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA_TESTE, ignore_errors=True)


# --------------------------------------------------------------------------- Kanban
class KanbanTest(Base):
    def test_equipe_do_gestor(self):
        self.assertEqual(equipe_de(self.gestora), {self.ana.pk, self.bruno.pk})  # subdepartamento + liderado
        self.assertEqual(equipe_de(self.ana), set())

    def test_visibilidade(self):
        propria = Atividade.objects.create(titulo="Da Ana", responsavel=self.ana, criado_por=self.ana)
        grupo = Atividade.objects.create(titulo="Do grupo Financeiro", grupo=Group.objects.get(name="Financeiro"))
        rh = Atividade.objects.create(titulo="Do RH", responsavel=self.rh, criado_por=self.rh)
        vis = lambda u: set(Atividade.objects.visiveis_para(u).values_list("titulo", flat=True))  # noqa: E731
        self.assertEqual(vis(self.ana), {"Da Ana", "Do grupo Financeiro"})
        self.assertEqual(vis(self.gestora), {"Da Ana", "Do grupo Financeiro"})
        self.assertEqual(vis(self.rh), {"Do RH"})
        self.assertEqual(vis(self.admin), {"Da Ana", "Do grupo Financeiro", "Do RH"})
        self.client.force_login(self.rh)
        self.assertEqual(self.client.get(propria.get_absolute_url()).status_code, 404)
        self.assertEqual(self.client.get(rh.get_absolute_url()).status_code, 200)
        self.assertIsNotNone(grupo.pk)

    def _mover(self, atividade, status, ordem=None):
        return self.client.post(reverse("tarefas:mover"), json.dumps(
            {"id": atividade.pk, "status": status, "ordem": ordem or [atividade.pk]}), content_type="application/json")

    def test_mover_e_assumir_atividade_do_grupo(self):
        a = Atividade.objects.create(titulo="Pegar do grupo", grupo=Group.objects.get(name="Financeiro"))
        self.client.force_login(self.bruno)
        r = self._mover(a, "fazendo")
        self.assertEqual(r.status_code, 200)
        a.refresh_from_db()
        self.assertEqual((a.status, a.responsavel), ("fazendo", self.bruno))
        self.assertIsNotNone(a.iniciada_em)
        self._mover(a, "concluida")
        a.refresh_from_db()
        self.assertIsNotNone(a.concluida_em)
        self.assertTrue(a.comentarios.filter(texto__contains="Concluída").exists())

    def test_permissoes_ao_mover(self):
        a = Atividade.objects.create(titulo="Do Bruno", responsavel=self.bruno, criado_por=self.bruno)
        self.client.force_login(self.rh)
        self.assertEqual(self._mover(a, "fazendo").status_code, 404)        # nem enxerga
        self.client.force_login(self.gestora)
        self.assertEqual(self._mover(a, "revisao").status_code, 200)        # gestora gere o Bruno
        self.client.force_login(self.ana)
        self.assertEqual(self._mover(a, "a_fazer").status_code, 404)        # colega fora do grupo da atividade
        r = self.client.post(reverse("tarefas:mover"), json.dumps({"id": a.pk, "status": "xpto"}),
                             content_type="application/json")
        self.assertEqual(r.status_code, 400)

    def test_quadro_visoes(self):
        Atividade.objects.create(titulo="Tarefa da Ana", responsavel=self.ana)
        self.client.force_login(self.gestora)
        r = self.client.get(reverse("tarefas:quadro") + "?visao=equipe")
        self.assertContains(r, "Tarefa da Ana")
        self.assertContains(r, "Minha equipe")
        self.client.force_login(self.ana)
        r = self.client.get(reverse("tarefas:quadro") + "?visao=equipe")  # sem equipe: volta para "minhas"
        self.assertNotContains(r, ">Minha equipe<")
        self.assertContains(r, "Tarefa da Ana")

    def test_atividade_exige_responsavel_ou_grupo(self):
        self.client.force_login(self.ana)
        r = self.client.post(reverse("tarefas:atividade_create"), {"titulo": "Sem dono", "status": "a_fazer",
                                                                    "prioridade": "media", "responsavel": ""})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Atividade.objects.filter(titulo="Sem dono").exists())


# --------------------------------------------------------------------------- Assinatura digital
class AssinaturaTest(Base):
    def setUp(self):
        self.pasta = Pasta.objects.create(nome="Teste")
        self.doc = Documento.objects.create(pasta=self.pasta, titulo="Contrato")
        self.pdf = gerar_pdf("Contrato", ["Cláusula 1"] * 5)
        self.versao = self.doc.adicionar_versao(ContentFile(self.pdf, name="contrato.pdf"), usuario=self.admin)

    def _assinar(self, user, pfx, senha, versao=None):
        self.client.force_login(user)
        return self.client.post(reverse("ged:assinar", args=[(versao or self.versao).pk]), {
            "certificado": SimpleUploadedFile("cert.pfx", pfx), "senha": senha,
            "motivo": "Concordo com o conteúdo deste documento", "local": "SP", "confirmo": "on"})

    def test_pades_dois_signatarios_e_verificacao(self):
        r = self._assinar(self.ana, gerar_pfx("Ana", cpf=CPF_ANA, senha="s1"), "s1")
        self.assertEqual(r.status_code, 302)
        r = self._assinar(self.bruno, gerar_pfx("Bruno", cpf=CPF_BRUNO, senha="s2"), "s2", self.doc.versao_atual)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.doc.num_versao, 3)
        with self.doc.versao_atual.arquivo.open("rb") as f:
            resultados = ass.verificar_pdf(f.read())
        self.assertEqual([(x.documento, x.valido) for x in resultados], [(CPF_ANA, True), (CPF_BRUNO, True)])
        a = Assinatura.objects.first()
        self.assertEqual((a.tipo, a.icp_brasil), ("pades", False))  # certificado de teste não é ICP-Brasil
        r = self.client.get(reverse("ged:verificar", args=[self.doc.versao_atual.pk]))
        self.assertContains(r, "Íntegra e válida", count=2)

    def test_pdf_alterado_e_detectado(self):
        assinado = ass.assinar_pdf(self.pdf, gerar_pfx("Ana", cpf=CPF_ANA, senha="s"), "s", "Assinatura1")
        adulterado = assinado.replace(b"Cl\xe1usula 1", b"Cl\xe1usula 9", 1)
        self.assertNotEqual(assinado, adulterado)
        self.assertFalse(any(r.valido for r in ass.verificar_pdf(adulterado)))

    def test_nao_assina_com_certificado_de_outra_pessoa(self):
        r = self._assinar(self.ana, gerar_pfx("Bruno", cpf=CPF_BRUNO, senha="s"), "s")
        self.assertContains(r, "não é o mesmo do seu cadastro")
        self.assertFalse(Assinatura.objects.exists())

    def test_senha_errada_e_certificado_vencido(self):
        r = self._assinar(self.ana, gerar_pfx("Ana", cpf=CPF_ANA, senha="certa"), "errada")
        self.assertContains(r, "senha incorreta")
        r = self._assinar(self.ana, gerar_pfx("Ana", cpf=CPF_ANA, senha="s", dias=-1), "s")
        self.assertContains(r, "fora da validade")
        self.assertFalse(Assinatura.objects.exists())

    def test_ecnpj_exige_permissao_e_empresa_cadastrada(self):
        pfx = gerar_pfx("Empresa Teste", cnpj=CNPJ_EMPRESA, senha="s")
        self.assertContains(self._assinar(self.ana, pfx, "s"), "e-CNPJ")
        self.ana.user_permissions.add(Permission.objects.get(codename="assinar_ecnpj"))
        self.ana = User.objects.get(pk=self.ana.pk)
        self.assertEqual(self._assinar(self.ana, pfx, "s").status_code, 302)
        outro = gerar_pfx("Outra", cnpj="11444777000161", senha="s")
        self.assertContains(self._assinar(self.ana, outro, "s", self.doc.versao_atual), "nenhuma empresa cadastrada")

    def test_cades_para_arquivo_que_nao_e_pdf(self):
        v = self.doc.adicionar_versao(ContentFile(b"planilha;dados", name="dados.csv"), usuario=self.admin)
        self.assertEqual(self._assinar(self.ana, gerar_pfx("Ana", cpf=CPF_ANA, senha="s"), "s", v).status_code, 302)
        a = Assinatura.objects.get()
        self.assertEqual(a.tipo, "cades")
        self.assertEqual(self.doc.versao_atual, v)  # arquivo original não muda
        with a.arquivo_p7s.open("rb") as f:
            p7s = f.read()
        self.assertTrue(ass.verificar_cades(b"planilha;dados", p7s)[0].valido)
        self.assertFalse(ass.verificar_cades(b"planilha;ALTERADA", p7s)[0].valido)
        self.assertEqual(self.client.get(reverse("ged:baixar_p7s", args=[a.pk])).status_code, 200)

    def test_certificado_nunca_e_gravado(self):
        self._assinar(self.ana, gerar_pfx("Ana", cpf=CPF_ANA, senha="s"), "s")
        gravados = [p.name for p in Path(MEDIA_TESTE).rglob("*") if p.suffix.lower() in (".pfx", ".p12")]
        self.assertEqual(gravados, [])

    def test_precisa_de_permissao_e_acesso_ao_documento(self):
        restrita = Pasta.objects.create(nome="Restrita")
        restrita.grupos_acesso.add(Group.objects.get(name="Jurídico"))
        doc = Documento.objects.create(pasta=restrita, titulo="Sigiloso")
        v = doc.adicionar_versao(ContentFile(self.pdf, name="s.pdf"), usuario=self.admin)
        r = self._assinar(self.ana, gerar_pfx("Ana", cpf=CPF_ANA, senha="s"), "s", v)
        self.assertEqual(r.status_code, 404)
        self.ana.user_permissions.clear()
        self.ana.groups.clear()
        self.assertEqual(self._assinar(self.ana, b"x", "s").status_code, 403)


# --------------------------------------------------------------------------- Jurídico
class ContratoTest(Base):
    def novo(self, **kw):
        dados = dict(titulo="Serviços de limpeza", tipo="fornecedor", fornecedor=self.forn, valor_mensal=Decimal("1000"),
                     valor_total=Decimal("12000"), inicio_vigencia=HOJE - datetime.timedelta(days=300),
                     fim_vigencia=HOJE + datetime.timedelta(days=65), responsavel=self.jur)
        dados.update(kw)
        return Contrato.objects.create(**dados)

    def test_numeracao_sequencial(self):
        a, b = self.novo(), self.novo()
        self.assertTrue(a.numero.startswith(f"CT-{HOJE.year}-"))
        self.assertEqual(int(b.numero[-4:]), int(a.numero[-4:]) + 1)

    def test_aprovacao_e_recusa_pelo_workflow(self):
        c = self.novo()
        self.client.force_login(self.jur)
        self.client.post(reverse("juridico:enviar_aprovacao", args=[c.pk]))
        c.refresh_from_db()
        self.assertEqual(c.status, "em_aprovacao")
        inst = Instancia.objects.get(object_id=c.pk)
        self.client.post(reverse("workflow:agir", args=[inst.pk]), {"acao": "rejeitar", "comentario": "Falta multa"})
        c.refresh_from_db()
        self.assertEqual((c.status, c.motivo_status), ("recusado", "Falta multa"))
        # corrigido e reenviado: jurídico aprova, diretoria aprova
        c.enviar_para_aprovacao(self.jur)
        inst = Instancia.objects.filter(object_id=c.pk, status="andamento").get()
        inst.avancar(self.jur, "aprovar")
        inst.avancar(self.admin, "aprovar")
        c.refresh_from_db()
        self.assertEqual(c.status, "aprovado")
        self.assertTrue(c.eventos.filter(descricao__contains="Recusado").exists())

    def test_fica_vigente_com_as_assinaturas_digitais(self):
        c = self.novo(assinaturas_necessarias=2)
        c.status = "aprovado"
        c.save()
        self.client.force_login(self.jur)
        pdf = gerar_pdf("Contrato", ["texto"])
        r = self.client.post(reverse("juridico:contrato_update", args=[c.pk]), {
            **{k: getattr(c, k) or "" for k in ("titulo", "tipo", "categoria", "prazo_renovacao_meses", "aviso_previo_dias",
                                                "alerta_dias", "assinaturas_necessarias", "observacoes")},
            "responsavel": self.jur.pk, "arquivo": SimpleUploadedFile("contrato.pdf", pdf)})
        self.assertEqual(r.status_code, 302, getattr(r, "context", {}) and r.context["form"].errors)
        c.refresh_from_db()
        self.assertIsNotNone(c.documento)
        v1 = c.documento.versao_atual
        assinado = ass.assinar_pdf(pdf, gerar_pfx("Ana", cpf=CPF_ANA, senha="s"), "s", "Assinatura1")
        self.assertFalse(c.conferir_assinaturas())  # só 1 de 2
        assinado = ass.assinar_pdf(assinado, gerar_pfx("Fornecedor", cpf=CPF_BRUNO, senha="s"), "s", "Assinatura2")
        c.documento.adicionar_versao(ContentFile(assinado, name="assinado.pdf"), "assinado pela outra parte", self.jur)
        self.assertNotEqual(v1, c.documento.versao_atual)
        self.assertTrue(c.conferir_assinaturas(self.jur))
        c.refresh_from_db()
        self.assertEqual(c.status, "vigente")
        self.assertEqual(c.data_assinatura, HOJE)

    def test_termos_travados_depois_de_aprovado(self):
        c = self.novo()
        c.status = "vigente"
        c.save()
        self.client.force_login(self.jur)
        r = self.client.get(reverse("juridico:contrato_update", args=[c.pk]))
        self.assertTrue(r.context["form"].fields["valor_mensal"].disabled)

    def test_renovacao_manual_com_reajuste(self):
        c = self.novo(status="vigente", prazo_renovacao_meses=12)
        fim = c.fim_vigencia
        self.client.force_login(self.jur)
        self.client.post(reverse("juridico:renovar", args=[c.pk]), {"meses": 12, "percentual_reajuste": "4.50"})
        c.refresh_from_db()
        self.assertEqual(c.fim_vigencia.year, fim.year + 1)
        self.assertEqual(c.valor_mensal, Decimal("1045.00"))
        ad = Aditivo.objects.get(contrato=c)
        self.assertEqual((ad.tipo, ad.fim_anterior, ad.valor_anterior), ("renovacao", fim, Decimal("1000.00")))

    def test_alertas_e_renovacao_automatica(self):
        a_vencer = self.novo(status="vigente", fim_vigencia=HOJE + datetime.timedelta(days=20), alerta_dias=60)
        longe = self.novo(status="vigente", fim_vigencia=HOJE + datetime.timedelta(days=300))
        vencido_auto = self.novo(status="vigente", fim_vigencia=HOJE - datetime.timedelta(days=1),
                                 renovacao_automatica=True, prazo_renovacao_meses=6)
        self.assertEqual(processar_vencimentos(), (1, 1))
        self.assertEqual(processar_vencimentos(), (0, 0))  # não duplica alertas
        alerta = Atividade.objects.get()
        self.assertEqual((alerta.vinculo, alerta.responsavel, alerta.prioridade), (a_vencer, self.jur, "alta"))
        vencido_auto.refresh_from_db()
        self.assertGreater(vencido_auto.fim_vigencia, HOJE)
        self.assertFalse(Atividade.objects.filter(object_id=longe.pk).exists())

    def test_encerrar_exige_motivo_e_filtros(self):
        c = self.novo(status="vigente")
        self.client.force_login(self.jur)
        self.client.post(reverse("juridico:encerrar", args=[c.pk]), {"rescisao": "1", "motivo": "Fim da parceria"})
        c.refresh_from_db()
        self.assertEqual(c.status, "rescindido")
        self.novo(status="vigente", fim_vigencia=HOJE + datetime.timedelta(days=10), titulo="Vence logo")
        r = self.client.get(reverse("juridico:contrato_list") + "?vencimento=30")
        self.assertContains(r, "Vence logo")
        self.assertNotContains(r, "Serviços de limpeza")

    def test_permissoes_do_juridico(self):
        c = self.novo()
        self.client.force_login(self.rh)
        self.assertEqual(self.client.get(c.get_absolute_url()).status_code, 403)
        self.assertEqual(self.client.post(reverse("juridico:enviar_aprovacao", args=[c.pk])).status_code, 403)
        self.client.force_login(self.jur)
        self.assertEqual(self.client.get(reverse("juridico:painel")).status_code, 200)
