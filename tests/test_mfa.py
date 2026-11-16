"""Verificação em duas etapas: TOTP, códigos de recuperação, obrigatoriedade, bloqueio e auditoria."""
import io
import time

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.core.models import LogAuditoria
from apps.seguranca import mfa, totp
from apps.seguranca.models import DispositivoMFA


class TotpTests(TestCase):
    def test_vetores_rfc6238(self):
        # RFC 6238, apêndice B (SHA-1, segredo ASCII "12345678901234567890", 8 dígitos -> últimos 6)
        import base64
        seg = base64.b32encode(b"12345678901234567890").decode()
        for t, esperado in [(59, "287082"), (1111111109, "081804"), (1234567890, "005924"), (20000000000, "353130")]:
            self.assertEqual(totp.codigo(seg, t), esperado)

    def test_janela_e_reuso(self):
        seg = totp.novo_segredo()
        agora = 1_700_000_000
        c = totp.codigo(seg, agora)
        passo = totp.verificar(seg, c, 0, agora=agora + 25)       # dentro da tolerância
        self.assertIsNotNone(passo)
        self.assertIsNone(totp.verificar(seg, c, passo, agora=agora))  # mesmo código de novo: recusado
        self.assertIsNone(totp.verificar(seg, c, 0, agora=agora + 120))  # velho demais
        self.assertIsNone(totp.verificar(seg, "12345", 0))
        self.assertIsNone(totp.verificar(seg, "abcdef", 0))


@override_settings(MFA_ATIVO=True)
class MfaFluxoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_erp", stdout=io.StringIO())
        cls.admin = User.objects.create_superuser("chefe", "c@x.com", "Senha-forte-123")
        cls.comum = User.objects.create_user("zé", "z@x.com", "Senha-forte-123")
        cls.fin = User.objects.create_user("fin", "f@x.com", "Senha-forte-123")
        cls.fin.groups.add(Group.objects.get(name="Financeiro"))

    def entrar(self, usuario):
        self.client.logout()
        r = self.client.post(reverse("login"), {"username": usuario, "password": "Senha-forte-123"})
        self.assertEqual(r.status_code, 302)
        return r

    def ativar(self, usuario):
        self.entrar(usuario)
        self.client.get(reverse("seguranca:mfa_ativar"))
        u = User.objects.get(username=usuario)
        seg = DispositivoMFA.objects.get(usuario=u).obter_segredo()
        r = self.client.post(reverse("seguranca:mfa_ativar"), {"codigo": totp.codigo(seg)})
        self.assertContains(r, "Guarde estes códigos")
        return u, seg, r.context["codigos"]

    def test_obrigatorio_para_admin_e_financeiro_mas_nao_para_comum(self):
        self.assertTrue(mfa.exigido(self.admin))
        self.assertTrue(mfa.exigido(self.fin))
        self.assertFalse(mfa.exigido(self.comum))
        self.entrar("zé")
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_sem_2fa_so_alcanca_a_tela_de_cadastro(self):
        self.entrar("chefe")
        for url in ("/", "/cadastros/", "/banco-de-dados/", "/admin/"):
            r = self.client.get(url)
            self.assertEqual(r.status_code, 302, url)
            self.assertIn("/seguranca/2fa/ativar/", r["Location"])
        self.assertEqual(self.client.get(reverse("seguranca:mfa_ativar")).status_code, 200)

    def test_ativacao_libera_a_sessao_e_o_segredo_fica_cifrado(self):
        u, seg, codigos = self.ativar("chefe")
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(len(codigos), 10)
        d = DispositivoMFA.objects.get(usuario=u)
        self.assertNotIn(seg.encode(), bytes(d.segredo))
        self.assertTrue(LogAuditoria.objects.filter(modelo="auth.mfa", descricao__startswith="2FA ativado").exists())

    def test_ativacao_com_codigo_errado_falha(self):
        self.entrar("chefe")
        self.client.get(reverse("seguranca:mfa_ativar"))
        r = self.client.post(reverse("seguranca:mfa_ativar"), {"codigo": "000000"})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(DispositivoMFA.objects.get(usuario=self.admin).confirmado)
        self.assertEqual(self.client.get("/").status_code, 302)

    def test_login_com_2fa_pede_codigo(self):
        _, seg, _ = self.ativar("chefe")
        self.entrar("chefe")
        r = self.client.get("/cadastros/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/seguranca/2fa/verificar/", r["Location"])
        r = self.client.post(reverse("seguranca:mfa_verificar"), {"codigo": "111111"})
        self.assertContains(r, "Código inválido")
        self.assertEqual(self.client.get("/").status_code, 302)
        # novo passo para não colidir com o código usado na ativação
        d = DispositivoMFA.objects.get(usuario=self.admin)
        d.ultimo_passo = 0
        d.save()
        r = self.client.post(reverse("seguranca:mfa_verificar"), {"codigo": totp.codigo(seg), "next": "/cadastros/"})
        self.assertRedirects(r, "/cadastros/", fetch_redirect_response=False)
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_codigo_totp_nao_pode_ser_reutilizado(self):
        _, seg, _ = self.ativar("chefe")
        self.entrar("chefe")
        c = totp.codigo(seg)
        # o código da ativação é do mesmo passo: recusado
        self.client.post(reverse("seguranca:mfa_verificar"), {"codigo": c})
        self.assertEqual(self.client.get("/").status_code, 302)

    def test_codigo_de_recuperacao_vale_uma_vez(self):
        u, seg, codigos = self.ativar("chefe")
        self.entrar("chefe")
        r = self.client.post(reverse("seguranca:mfa_verificar"), {"codigo": codigos[0].upper()})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(mfa.codigos_restantes(u), 9)
        self.entrar("chefe")
        self.client.post(reverse("seguranca:mfa_verificar"), {"codigo": codigos[0]})
        self.assertEqual(self.client.get("/").status_code, 302)  # já gasto
        self.assertTrue(LogAuditoria.objects.filter(modelo="auth.mfa", descricao__contains="código de recuperação").exists())

    def test_bloqueio_apos_varias_falhas_mesmo_com_codigo_certo(self):
        u, seg, _ = self.ativar("chefe")
        self.entrar("chefe")
        for _ in range(5):
            self.client.post(reverse("seguranca:mfa_verificar"), {"codigo": "000000"})
        d = DispositivoMFA.objects.get(usuario=u)
        d.ultimo_passo = 0
        d.save()
        r = self.client.post(reverse("seguranca:mfa_verificar"), {"codigo": totp.codigo(seg)})
        self.assertEqual(r.status_code, 429)
        self.assertEqual(self.client.get("/").status_code, 302)

    def test_usuario_comum_ativa_e_desativa_com_codigo(self):
        u, seg, _ = self.ativar("zé")
        self.assertEqual(self.client.get("/").status_code, 200)
        d = DispositivoMFA.objects.get(usuario=u)
        d.ultimo_passo = 0
        d.save()
        r = self.client.post(reverse("seguranca:mfa_desativar"), {"codigo": "000000"})
        self.assertTrue(DispositivoMFA.objects.filter(usuario=u).exists())
        self.client.post(reverse("seguranca:mfa_desativar"), {"codigo": totp.codigo(seg)})
        self.assertFalse(DispositivoMFA.objects.filter(usuario=u).exists())

    def test_obrigado_nao_pode_desativar(self):
        u, seg, _ = self.ativar("chefe")
        d = DispositivoMFA.objects.get(usuario=u)
        d.ultimo_passo = 0
        d.save()
        self.client.post(reverse("seguranca:mfa_desativar"), {"codigo": totp.codigo(seg)})
        self.assertTrue(DispositivoMFA.objects.filter(usuario=u).exists())

    def test_regenerar_codigos_invalida_os_antigos(self):
        u, seg, antigos = self.ativar("zé")
        d = DispositivoMFA.objects.get(usuario=u)
        d.ultimo_passo = 0
        d.save()
        r = self.client.post(reverse("seguranca:mfa_codigos"), {"codigo": totp.codigo(seg)})
        novos = r.context["codigos"]
        self.assertEqual(len(novos), 10)
        self.assertIsNone(mfa.conferir(u, antigos[0]))

    def test_superusuario_redefine_e_o_usuario_recadastra(self):
        u, seg, _ = self.ativar("fin")
        self.client.logout()
        # chefe ativa o próprio 2FA para poder agir
        _, _, _ = self.ativar("chefe")
        r = self.client.get(reverse("seguranca:mfa_usuarios"))
        self.assertContains(r, "fin")
        self.client.post(reverse("seguranca:mfa_redefinir", args=[u.pk]))
        self.assertFalse(DispositivoMFA.objects.filter(usuario=u).exists())
        self.assertTrue(LogAuditoria.objects.filter(modelo="auth.mfa", descricao__contains="redefinido").exists())
        self.entrar("fin")
        self.assertIn("/seguranca/2fa/ativar/", self.client.get("/")["Location"])

    def test_nao_superusuario_nao_ve_lista_nem_redefine(self):
        self.ativar("fin")
        self.assertEqual(self.client.get(reverse("seguranca:mfa_usuarios")).status_code, 302)
        self.assertEqual(self.client.post(reverse("seguranca:mfa_redefinir", args=[self.admin.pk])).status_code, 302)

    def test_segredo_e_codigos_nunca_vao_para_a_auditoria(self):
        u, seg, codigos = self.ativar("zé")
        todo = " ".join(f"{l.descricao} {l.alteracoes}" for l in LogAuditoria.objects.all())
        self.assertNotIn(seg, todo)
        self.assertNotIn(codigos[0], todo)
        self.assertFalse(LogAuditoria.objects.filter(modelo__startswith="seguranca.").exists())

    def test_next_externo_e_ignorado(self):
        _, seg, _ = self.ativar("chefe")
        self.entrar("chefe")
        d = DispositivoMFA.objects.get(usuario=self.admin)
        d.ultimo_passo = 0
        d.save()
        r = self.client.post(reverse("seguranca:mfa_verificar"), {"codigo": totp.codigo(seg), "next": "https://evil.example/"})
        self.assertEqual(r["Location"], "/")

    @override_settings(MFA_ATIVO=False)
    def test_desligado_nao_exige(self):
        self.entrar("chefe")
        self.assertEqual(self.client.get("/").status_code, 200)
