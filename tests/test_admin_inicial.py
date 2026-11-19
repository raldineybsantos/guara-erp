from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from apps.seguranca.models import TrocaSenhaObrigatoria

SENHA = "Admin$Guara"


class AdminInicialTest(TestCase):
    def criar(self):
        call_command("criar_admin_inicial", verbosity=0)
        return User.objects.get(username="GuaraSuperAdmin")

    def test_cria_superusuario_com_troca_obrigatoria(self):
        u = self.criar()
        self.assertTrue(u.is_superuser and u.is_staff and u.check_password(SENHA))
        self.assertTrue(TrocaSenhaObrigatoria.pendente(u))

    def test_nao_age_se_ja_existe_superusuario(self):
        User.objects.create_superuser("outro", password="x")
        call_command("criar_admin_inicial", verbosity=0)
        self.assertFalse(User.objects.filter(username="GuaraSuperAdmin").exists())

    def test_idempotente_e_nao_reinicia_senha_ja_trocada_depois_que_ha_outro_admin(self):
        u = self.criar()
        self.criar()
        self.assertEqual(User.objects.filter(username="GuaraSuperAdmin").count(), 1)
        u.set_password("NovaSenha#2026x")
        u.save()
        TrocaSenhaObrigatoria.objects.all().delete()
        call_command("criar_admin_inicial", verbosity=0)   # já é superusuário: não repõe a senha padrão
        u.refresh_from_db()
        self.assertTrue(u.check_password("NovaSenha#2026x"))
        self.assertFalse(TrocaSenhaObrigatoria.pendente(u))

    def test_primeiro_login_so_alcanca_a_troca_de_senha(self):
        self.criar()
        r = self.client.post(reverse("login"), {"username": "GuaraSuperAdmin", "password": SENHA})
        self.assertEqual(r.status_code, 302)
        for url in ("/", "/financeiro/", "/banco-de-dados/", "/admin/"):
            r = self.client.get(url)
            self.assertEqual((r.status_code, r["Location"]), (302, reverse("password_change")), url)
        self.assertEqual(self.client.get(reverse("password_change")).status_code, 200)

    def test_troca_libera_o_sistema(self):
        u = self.criar()
        self.client.login(username="GuaraSuperAdmin", password=SENHA)
        r = self.client.post(reverse("password_change"), {
            "old_password": SENHA, "new_password1": "SenhaForte#2026", "new_password2": "SenhaForte#2026"})
        self.assertEqual(r.status_code, 302)
        self.assertFalse(TrocaSenhaObrigatoria.pendente(u))
        u.refresh_from_db()
        self.assertTrue(u.check_password("SenhaForte#2026"))
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_nao_aceita_repetir_a_senha_provisoria_nem_senha_fraca(self):
        u = self.criar()
        self.client.login(username="GuaraSuperAdmin", password=SENHA)
        for nova in (SENHA, "12345678", "curta"):
            r = self.client.post(reverse("password_change"), {
                "old_password": SENHA, "new_password1": nova, "new_password2": nova})
            self.assertEqual(r.status_code, 200, nova)
        self.assertTrue(TrocaSenhaObrigatoria.pendente(u))
        u.refresh_from_db()
        self.assertTrue(u.check_password(SENHA))

    def test_logout_continua_permitido(self):
        self.criar()
        self.client.login(username="GuaraSuperAdmin", password=SENHA)
        self.assertEqual(self.client.post(reverse("logout")).status_code, 302)
        self.assertEqual(self.client.get(reverse("password_change")).status_code, 302)  # voltou ao login
