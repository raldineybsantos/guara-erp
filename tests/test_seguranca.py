"""Testes de segurança: cada teste corresponde a um item da auditoria (ver SEGURANCA.md)."""
import datetime
import io
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.cobranca.models import Cobranca, ReguaCobranca, renderizar_mensagem, validar_modelo_mensagem
from apps.core.crud import celula_csv
from apps.core.models import Cliente, Colaborador, Empresa, Fornecedor, LogAuditoria
from apps.financeiro.models import ContaPagar, ContaReceber
from apps.fiscal.importador import importar_nfe
from apps.ged.models import Documento, Pasta
from apps.workflow.models import Instancia, Processo

HOJE = timezone.localdate()


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_erp", stdout=io.StringIO())
        cls.admin = User.objects.create_superuser("admin", "a@a.com", "SenhaForte#2026")
        Empresa.objects.create(razao_social="Teste", cnpj="11222333000181", chave_pix="pix@teste.com")
        cls.cli = Cliente.objects.create(nome="Cliente", email="cli@a.com")
        cls.forn = Fornecedor.objects.create(nome="Fornecedor")

    def usuario(self, nome, grupo=None):
        u = User.objects.create_user(nome, password="SenhaForte#2026")
        if grupo:
            u.groups.add(Group.objects.get(name=grupo))
        return u


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class InjecaoDeTemplateTest(Base):
    """A régua de cobrança não pode executar código de template nem acessar objetos do banco."""

    def test_nao_vaza_dados_de_usuarios(self):
        cr = ContaReceber.objects.create(cliente=self.cli, descricao="Mensal", valor=Decimal("10"), vencimento=HOJE)
        cob = Cobranca.objects.create(conta_receber=cr, valor=cr.valor, vencimento=HOJE, criado_por=self.admin)
        ctx = cob.contexto_mensagem()
        self.assertTrue(all(isinstance(v, str) for v in ctx.values()))
        saida = renderizar_mensagem("{{ cobranca.criado_por.password }}{% debug %}{{ cliente }}", ctx)
        self.assertNotIn("pbkdf2", saida)
        self.assertNotIn("debug", saida)
        self.assertEqual(saida, "Cliente")

    def test_validador_recusa_tags_e_variaveis_desconhecidas(self):
        with self.assertRaises(ValidationError):
            validar_modelo_mensagem("{% load static %}")
        with self.assertRaises(ValidationError):
            validar_modelo_mensagem("{{ titulo.empresa.cnpj }}")
        validar_modelo_mensagem("Olá {{ cliente }}, valor {{ valor }}")

    def test_envio_continua_funcionando(self):
        cr = ContaReceber.objects.create(cliente=self.cli, descricao="Mensal", valor=Decimal("10"),
                                         vencimento=HOJE + datetime.timedelta(days=3))
        Cobranca.objects.create(conta_receber=cr, valor=cr.valor, vencimento=cr.vencimento)
        self.assertEqual(ReguaCobranca.executar(), 1)
        self.assertIn("PIX copia-e-cola: 000201", mail.outbox[0].body)


class PermissoesTest(Base):
    def test_acao_desabilitada_bloqueada_pela_url(self):
        u = self.usuario("u1", "Usuários")  # tem add_instancia
        self.client.force_login(u)
        self.assertEqual(self.client.get(reverse("workflow:instancia_create")).status_code, 403)
        proc = Processo.objects.get(codigo="aprovacao-pagamento")
        r = self.client.post(reverse("workflow:instancia_create"), {
            "processo": proc.pk, "titulo": "forjada", "solicitante": self.admin.pk, "status": "concluido"})
        self.assertEqual(r.status_code, 403)
        self.assertFalse(Instancia.objects.filter(titulo="forjada").exists())
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("core:logauditoria_create")).status_code, 403)

    def test_solicitacoes_visiveis_so_para_envolvidos(self):
        proc = Processo.objects.get(codigo="aprovacao-pagamento")
        fin = self.usuario("fin", "Financeiro")
        rh = self.usuario("rh", "RH")
        inst = proc.iniciar("Pagamento sigiloso", fin)
        self.client.force_login(rh)
        self.assertEqual(self.client.get(reverse("workflow:instancia_detail", args=[inst.pk])).status_code, 404)
        self.assertNotContains(self.client.get(reverse("workflow:instancia_list")), "sigiloso")
        self.client.post(reverse("workflow:agir", args=[inst.pk]), {"acao": "cancelar"})
        inst.refresh_from_db()
        self.assertEqual(inst.status, "andamento")
        self.client.force_login(fin)
        self.assertEqual(self.client.get(reverse("workflow:instancia_detail", args=[inst.pk])).status_code, 200)

    def test_dados_de_colaboradores_e_auditoria_restritos(self):
        Colaborador.objects.create(nome="Fulano", cpf="52998224725", salario=Decimal("9999"))
        for grupo in ("Financeiro", "Fiscal", "Compras", "Usuários"):
            u = self.usuario(f"u_{grupo}", grupo)
            self.assertFalse(u.has_perm("core.view_colaborador"), grupo)
            self.assertFalse(u.has_perm("core.view_logauditoria"), grupo)
        rh = self.usuario("rh2", "RH")
        self.assertTrue(rh.has_perm("core.view_colaborador"))
        self.assertFalse(rh.has_perm("core.view_logauditoria"))
        self.assertFalse(rh.has_perm("ged.ver_confidencial"))

    def test_banco_de_dados_so_superusuario(self):
        diretor = self.usuario("dir", "Administradores")
        self.client.force_login(diretor)
        for url in ("/banco-de-dados/", "/banco-de-dados/backup/"):
            self.assertEqual(self.client.get(url).status_code, 302, url)
        self.client.post("/banco-de-dados/manutencao/")

    def test_auditoria_somente_leitura_no_admin(self):
        LogAuditoria.objects.create(acao="acao", modelo="x", descricao="y")
        log = LogAuditoria.objects.first()
        self.client.force_login(self.admin)
        r = self.client.post(f"/admin/core/logauditoria/{log.pk}/delete/", {"post": "yes"})
        self.assertEqual(r.status_code, 403)
        self.assertTrue(LogAuditoria.objects.filter(pk=log.pk).exists())

    def test_gerar_cobranca_exige_post(self):
        cr = ContaReceber.objects.create(cliente=self.cli, descricao="X", valor=Decimal("5"), vencimento=HOJE)
        self.client.force_login(self.admin)
        self.client.get(reverse("cobranca:gerar_cobranca", args=[cr.pk]))
        self.assertFalse(Cobranca.objects.exists())
        self.client.post(reverse("cobranca:gerar_cobranca", args=[cr.pk]))
        self.assertTrue(Cobranca.objects.exists())


class LoginTest(Base):
    def test_bloqueio_apos_tentativas_e_auditoria(self):
        u = self.usuario("joao")
        for _ in range(5):
            r = self.client.post("/login/", {"username": "joao", "password": "errada"})
            self.assertEqual(r.status_code, 200)
        r = self.client.post("/login/", {"username": "joao", "password": "SenhaForte#2026"})
        self.assertEqual(r.status_code, 429)  # bloqueado mesmo com a senha certa
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(LogAuditoria.objects.filter(modelo="auth.login", descricao__startswith="FALHA").count(), 5)
        # depois do período de bloqueio volta a funcionar
        LogAuditoria.objects.filter(modelo="auth.login").update(data=timezone.now() - datetime.timedelta(hours=1))
        r = self.client.post("/login/", {"username": "joao", "password": "SenhaForte#2026"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(int(self.client.session["_auth_user_id"]), u.pk)

    def test_login_do_admin_passa_pela_tela_protegida(self):
        r = self.client.get("/admin/login/?next=/admin/")
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r["Location"].startswith("/login/"))

    def test_redirecionamento_externo_bloqueado(self):
        self.client.force_login(self.admin)
        r = self.client.post(reverse("core:cargo_create") + "?next=//site-malicioso.com/x", {"nome": "Cargo", "salario_base": "1"})
        self.assertEqual(r.status_code, 302)
        self.assertNotIn("site-malicioso", r["Location"])

    def test_cabecalhos_de_seguranca(self):
        r = self.client.get("/login/")
        self.assertEqual(r["X-Frame-Options"], "DENY")
        self.assertEqual(r["X-Content-Type-Options"], "nosniff")
        self.assertEqual(r["Referrer-Policy"], "same-origin")


class ArquivosTest(Base):
    def test_upload_bloqueia_executaveis_e_html(self):
        pasta = Pasta.objects.get(nome="Fiscal")
        self.client.force_login(self.admin)
        for nome in ("virus.exe", "script.ps1", "pagina.html", "imagem.svg"):
            r = self.client.post(reverse("ged:documento_create"), {
                "pasta": pasta.pk, "titulo": nome, "tipo": "outro",
                "arquivo": SimpleUploadedFile(nome, b"conteudo")})
            self.assertEqual(r.status_code, 200, nome)
        self.assertFalse(Documento.objects.exists())

    @override_settings(ARQUIVO_MAX_MB=1)
    def test_limite_de_tamanho(self):
        pasta = Pasta.objects.get(nome="Fiscal")
        self.client.force_login(self.admin)
        self.client.post(reverse("ged:documento_create"), {
            "pasta": pasta.pk, "titulo": "grande", "tipo": "outro",
            "arquivo": SimpleUploadedFile("grande.pdf", b"0" * (1024 * 1024 + 1))})
        self.assertFalse(Documento.objects.exists())

    def test_visualizacao_inline_so_para_tipos_seguros(self):
        pasta = Pasta.objects.get(nome="Fiscal")
        doc = Documento.objects.create(pasta=pasta, titulo="d")
        v = doc.adicionar_versao(SimpleUploadedFile("planilha.csv", b"a;b"), usuario=self.admin)
        self.client.force_login(self.admin)
        r = self.client.get(reverse("ged:download", args=[v.pk]) + "?ver=1")
        self.assertIn("attachment", r["Content-Disposition"])
        self.assertIn("sandbox", r["Content-Security-Policy"])
        v2 = doc.adicionar_versao(SimpleUploadedFile("foto.png", b"\x89PNG"), usuario=self.admin)
        r = self.client.get(reverse("ged:download", args=[v2.pk]) + "?ver=1")
        self.assertIn("inline", r["Content-Disposition"])
        self.assertEqual(r["Content-Type"], "image/png")

    def test_xml_com_entidade_externa_rejeitado(self):
        xxe = b"""<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]>
<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe"><infNFe>&x;</infNFe></nfeProc>"""
        with self.assertRaises(Exception) as ctx:
            importar_nfe(SimpleUploadedFile("nota.xml", xxe))
        self.assertNotIn("root:", str(ctx.exception))
        bomba = b"""<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">
<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">]><l>&c;</l>"""
        with self.assertRaises(Exception):
            importar_nfe(SimpleUploadedFile("nota.xml", bomba))

    def test_xml_fiscal_nao_e_publico(self):
        self.assertFalse(any("media" in str(p.pattern) for p in __import__("config.urls").urls.urlpatterns))
        u = self.usuario("rh3", "RH")
        self.client.force_login(u)
        self.assertEqual(self.client.get(reverse("fiscal:baixar_xml", args=[1])).status_code, 403)


class CsvTest(TestCase):
    def test_injecao_de_formula(self):
        self.assertEqual(celula_csv("=HYPERLINK(\"http://x\")"), "'=HYPERLINK(\"http://x\")")
        self.assertEqual(celula_csv("@SUM(A1)"), "'@SUM(A1)")
        self.assertEqual(celula_csv("-1.234,56"), "-1.234,56")
        self.assertEqual(celula_csv("R$ 10,00"), "R$ 10,00")


class SqlInjectionTest(Base):
    def test_busca_filtros_e_ordenacao_nao_executam_sql(self):
        ContaPagar.objects.create(fornecedor=self.forn, descricao="Aluguel", valor=Decimal("10"), vencimento=HOJE)
        self.client.force_login(self.admin)
        for params in ({"q": "'; DROP TABLE financeiro_contapagar; --"},
                       {"o": "descricao; DROP TABLE core_cliente"},
                       {"fornecedor": "1 OR 1=1"},
                       {"vencimento__gte": "2020-01-01' OR '1'='1"},
                       {"q": "' UNION SELECT password FROM auth_user --", "export": "csv"}):
            r = self.client.get(reverse("financeiro:contapagar_list"), params)
            self.assertIn(r.status_code, (200,), params)
            self.assertNotIn(b"pbkdf2", r.content)
        self.assertEqual(ContaPagar.objects.count(), 1)
        self.assertTrue(Cliente.objects.exists())


class XssTest(Base):
    def test_conteudo_digitado_e_escapado(self):
        mal = '<script>alert("x")</script>'
        f = Fornecedor.objects.create(nome=mal, categoria=mal)
        cp = ContaPagar.objects.create(fornecedor=f, descricao=mal, valor=Decimal("1"), vencimento=HOJE)
        self.client.force_login(self.admin)
        for url in (reverse("financeiro:contapagar_list"), cp.get_absolute_url(), f.get_absolute_url(), "/"):
            r = self.client.get(url)
            self.assertNotIn(b"<script>alert", r.content, url)
