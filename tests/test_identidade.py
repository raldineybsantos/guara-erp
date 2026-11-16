"""LDAP/AD (diretório simulado com ldap3 MOCK_SYNC) e SSO OpenID Connect (provedor simulado com token assinado de verdade)."""
import io
import time
from urllib.parse import parse_qs, urlparse
from unittest import mock

import jwt
import ldap3
from cryptography.hazmat.primitives.asymmetric import rsa
from django.contrib.auth.models import Group, User
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from jwt.algorithms import RSAAlgorithm

from apps.core.models import LogAuditoria, LogErro
from apps.identidade import ldap_auth, oidc
from apps.identidade.models import ConfiguracaoLDAP, IdentidadeExterna, ProvedorOIDC
from apps.seguranca import mfa


# ----------------------------------------------------------------------------------------------- LDAP
DN_SVC = "cn=svc,dc=emp,dc=local"
ENTRADAS = {
    DN_SVC: {"objectClass": "user", "sAMAccountName": "svc", "userPassword": "svc-pass"},
    "cn=ana,ou=u,dc=emp,dc=local": {"objectClass": "user", "sAMAccountName": "ana", "userPassword": "ana-pass",
                                    "mail": "ana@emp.com.br", "displayName": "Ana Souza", "userAccountControl": "512",
                                    "memberOf": ["cn=fin,ou=g,dc=emp,dc=local", "cn=outro,ou=g,dc=emp,dc=local"]},
    "cn=bia,ou=u,dc=emp,dc=local": {"objectClass": "user", "sAMAccountName": "bia", "userPassword": "bia-pass",
                                    "mail": "bia@emp.com.br", "displayName": "Bia Lima", "userAccountControl": "514"},
    "cn=carlos,ou=u,dc=emp,dc=local": {"objectClass": "user", "sAMAccountName": "carlos", "userPassword": "c-pass",
                                       "mail": "carlos@emp.com.br", "displayName": "Carlos", "userAccountControl": "512"},
}


class LdapBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_erp", stdout=io.StringIO())

    def setUp(self):
        self.entradas = {dn: dict(a) for dn, a in ENTRADAS.items()}
        self.cfg = ConfiguracaoLDAP(nome="AD", servidor="ad.emp.local", base_dn="dc=emp,dc=local", bind_dn=DN_SVC,
                                    mapa_grupos="cn=fin,ou=g,dc=emp,dc=local => Financeiro")
        self.cfg.definir_senha("svc-pass")
        self.cfg.save()
        self.abertas = []

        def abrir(cfg, dn=None, senha=None):
            conn = ldap3.Connection(ldap3.Server("fake"), user=dn, password=senha, client_strategy=ldap3.MOCK_SYNC)
            for d, a in self.entradas.items():
                conn.strategy.add_entry(d, a)
            self.abertas.append((dn, senha))
            return conn if conn.bind() else None
        p = mock.patch("apps.identidade.ldap_auth._abrir", side_effect=abrir)
        p.start()
        self.addCleanup(p.stop)

    def entrar(self, usuario, senha):
        return self.client.post(reverse("login"), {"username": usuario, "password": senha})


class LdapTests(LdapBase):
    def test_login_cria_usuario_sem_senha_local_com_dados_e_grupos(self):
        r = self.entrar("ana", "ana-pass")
        self.assertEqual(r.status_code, 302)
        u = User.objects.get(username="ana")
        self.assertFalse(u.has_usable_password())
        self.assertEqual((u.email, u.first_name, u.last_name), ("ana@emp.com.br", "Ana", "Souza"))
        self.assertEqual(list(u.groups.values_list("name", flat=True)), ["Financeiro"])
        self.assertFalse(u.is_superuser or u.is_staff)
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertTrue(IdentidadeExterna.objects.filter(usuario=u, origem=f"ldap:{self.cfg.pk}").exists())

    def test_saida_do_grupo_no_diretorio_remove_o_grupo_no_erp_mas_preserva_os_manuais(self):
        self.entrar("ana", "ana-pass")
        u = User.objects.get(username="ana")
        u.groups.add(Group.objects.get(name="Compras"))
        self.client.logout()
        self.entradas["cn=ana,ou=u,dc=emp,dc=local"]["memberOf"] = ["cn=outro,ou=g,dc=emp,dc=local"]
        self.entrar("ana", "ana-pass")
        self.assertEqual(set(u.groups.values_list("name", flat=True)), {"Compras"})

    def test_senha_errada_vazia_e_conta_desativada_falham(self):
        for usuario, senha in (("ana", "errada"), ("ana", ""), ("bia", "bia-pass"), ("ninguem", "x")):
            r = self.entrar(usuario, senha)
            self.assertEqual(r.status_code, 200, (usuario, senha))
        self.assertFalse(User.objects.filter(username__in=["ana", "bia", "ninguem"]).exists())
        # senha vazia nem chega a tentar o bind do usuário (bind sem senha seria acesso anônimo)
        self.assertNotIn(("cn=ana,ou=u,dc=emp,dc=local", ""), self.abertas)
        self.assertNotIn(("cn=ana,ou=u,dc=emp,dc=local", None), self.abertas)

    def test_login_digitado_nao_injeta_filtro(self):
        for malicioso in ("*", "ana*", "a*)(sAMAccountName=*", "*)(|(sAMAccountName=*"):
            self.assertIsNone(ldap_auth.LDAPBackend().authenticate(None, malicioso, "ana-pass"), malicioso)

    def test_grupo_obrigatorio(self):
        self.cfg.grupo_obrigatorio = "CN=FIN,ou=g,dc=emp,dc=local"
        self.cfg.save()
        self.assertEqual(self.entrar("ana", "ana-pass").status_code, 302)
        self.client.logout()
        self.assertEqual(self.entrar("carlos", "c-pass").status_code, 200)

    def test_usuario_local_de_mesmo_login_nao_e_sequestrado_pelo_diretorio(self):
        User.objects.create_user("carlos", password="senha-local-123")
        self.assertEqual(self.entrar("carlos", "c-pass").status_code, 200)         # senha do AD não vale para a conta local
        self.assertEqual(self.entrar("carlos", "senha-local-123").status_code, 302)  # a local continua valendo
        self.client.logout()
        self.cfg.vincular_existentes = True
        self.cfg.save()
        self.assertEqual(self.entrar("carlos", "c-pass").status_code, 302)
        self.assertTrue(IdentidadeExterna.objects.filter(usuario__username="carlos").exists())

    def test_sem_criacao_automatica_usuario_novo_nao_entra(self):
        self.cfg.criar_usuarios = False
        self.cfg.save()
        self.assertEqual(self.entrar("ana", "ana-pass").status_code, 200)

    def test_diretorio_fora_do_ar_nao_derruba_login_local_e_vai_para_log_de_erros(self):
        User.objects.create_user("local", password="Senha-local-123")
        with mock.patch("apps.identidade.ldap_auth._abrir", side_effect=ldap3.core.exceptions.LDAPSocketOpenError("sem rede")):
            self.assertEqual(self.entrar("local", "Senha-local-123").status_code, 302)
            self.client.logout()
            self.assertEqual(self.entrar("ana", "ana-pass").status_code, 200)
        self.assertTrue(LogErro.objects.filter(origem="ldap").exists())

    def test_usuario_desativado_no_erp_nao_entra(self):
        self.entrar("ana", "ana-pass")
        self.client.logout()
        User.objects.filter(username="ana").update(is_active=False)
        self.assertEqual(self.entrar("ana", "ana-pass").status_code, 200)

    def test_senha_de_servico_cifrada_e_fora_da_auditoria(self):
        self.assertNotIn(b"svc-pass", bytes(ConfiguracaoLDAP.objects.get(pk=self.cfg.pk).bind_senha))
        self.assertEqual(self.cfg.obter_senha(), "svc-pass")
        self.cfg.nome = "AD 2"
        self.cfg.save()
        todo = " ".join(f"{l.descricao} {l.alteracoes}" for l in LogAuditoria.objects.all())
        self.assertNotIn("svc-pass", todo)

    def test_validacao_da_configuracao(self):
        self.cfg.filtro_usuario = "(sAMAccountName=fixo)"
        with self.assertRaises(ValidationError):
            self.cfg.clean()
        self.cfg.filtro_usuario = "(sAMAccountName={usuario})"
        self.cfg.mapa_grupos = "cn=x => GrupoInexistente"
        with self.assertRaises(ValidationError):
            self.cfg.clean()

    @override_settings(MFA_ATIVO=True)
    def test_login_ldap_continua_sujeito_ao_2fa_do_erp(self):
        self.cfg.mapa_grupos = "cn=fin,ou=g,dc=emp,dc=local => Financeiro"
        self.cfg.save()
        self.entrar("ana", "ana-pass")                       # ana é Financeiro: 2FA obrigatório
        self.assertIn("/seguranca/2fa/ativar/", self.client.get("/")["Location"])

    def test_teste_de_conexao(self):
        res = ldap_auth.testar(self.cfg, "ana", "ana-pass")
        self.assertTrue(all(ok for ok, _ in res), res)
        self.assertFalse(all(ok for ok, _ in ldap_auth.testar(self.cfg, "ana", "errada")))
        self.assertFalse(all(ok for ok, _ in ldap_auth.testar(self.cfg, "fantasma")))


# ----------------------------------------------------------------------------------------------- OIDC
CHAVE = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OUTRA = rsa.generate_private_key(public_exponent=65537, key_size=2048)
JWKS = {"keys": [{**__import__("json").loads(RSAAlgorithm.to_jwk(CHAVE.public_key())), "kid": "k1", "alg": "RS256", "use": "sig"}]}
TENANT = "11111111-2222-3333-4444-555555555555"


def desc(emissor):
    return {"issuer": emissor, "authorization_endpoint": emissor + "/auth", "token_endpoint": emissor + "/token",
            "jwks_uri": emissor + "/jwks"}


class OidcBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_erp", stdout=io.StringIO())

    def setUp(self):
        cache.clear()
        self.google = self.novo("google", "Google", dominios_permitidos="emp.com.br", criar_usuarios=True)
        self.token = {}

        def get(url):
            if url.endswith("/jwks"):
                return JWKS
            for p in ProvedorOIDC.objects.all():
                if url.startswith(oidc.emissor_esperado(p)):
                    return desc(oidc.emissor_esperado(p))
            raise AssertionError(url)
        for alvo, efeito in (("apps.identidade.oidc._get", get),
                             ("apps.identidade.oidc._post", lambda url, dados: dict(self.token))):
            p = mock.patch(alvo, side_effect=efeito)
            p.start()
            self.addCleanup(p.stop)

    def novo(self, tipo, nome, **kw):
        p = ProvedorOIDC(tipo=tipo, nome=nome, client_id="cid-" + tipo, **kw)
        if tipo == "microsoft":
            p.tenant = kw.get("tenant", TENANT)
        p.definir_segredo("segredo-do-app")
        p.save()
        return p

    def comecar(self, prov, proximo=None):
        url = reverse("identidade:entrar", args=[prov.slug]) + (f"?next={proximo}" if proximo else "")
        r = self.client.get(url)
        self.assertEqual(r.status_code, 302)
        q = parse_qs(urlparse(r["Location"]).query)
        return {k: v[0] for k, v in q.items()}, r["Location"]

    def id_token(self, prov, q, **extra):
        agora = int(time.time())
        claims = {"iss": oidc.emissor_esperado(prov), "aud": prov.client_id, "sub": "sub-123", "iat": agora,
                  "exp": agora + 300, "nonce": q["nonce"], "email": "bia@emp.com.br", "email_verified": True,
                  "name": "Bia Lima", "hd": "emp.com.br"}
        claims.update(extra)
        for k in [k for k, v in claims.items() if v is None]:
            del claims[k]
        return jwt.encode(claims, CHAVE, algorithm="RS256", headers={"kid": "k1"})

    def voltar(self, prov, q, token=None, state=None, **extra):
        self.token = {"id_token": token or self.id_token(prov, q, **extra)}
        return self.client.get(reverse("identidade:retorno", args=[prov.slug]), {"code": "abc", "state": state or q["state"]})

    def entrou(self):
        return self.client.get("/").status_code == 200

    def erro_na_tela(self, r):
        self.assertRedirects(r, reverse("login"), fetch_redirect_response=False)
        return [str(m) for m in r.wsgi_request._messages]


class OidcTests(OidcBase):
    def test_fluxo_completo_google_cria_usuario_sem_acesso_e_sem_senha(self):
        q, url = self.comecar(self.google, "/cadastros/")
        self.assertEqual(q["code_challenge_method"], "S256")
        self.assertEqual(q["response_type"], "code")
        self.assertIn("/identidade/retorno/google/", q["redirect_uri"])
        r = self.voltar(self.google, q)
        self.assertRedirects(r, "/cadastros/", fetch_redirect_response=False)
        u = User.objects.get(email="bia@emp.com.br")
        self.assertFalse(u.has_usable_password())
        self.assertEqual(u.groups.count(), 0)
        self.assertTrue(IdentidadeExterna.objects.filter(usuario=u, origem="oidc:google", subject="sub-123").exists())
        self.assertTrue(LogAuditoria.objects.filter(descricao__startswith="Login por SSO").exists())
        self.assertTrue(self.entrou())

    def test_novo_usuario_recebe_o_grupo_padrao_configurado(self):
        self.google.grupo_padrao = Group.objects.get(name="Usuários")
        self.google.save()
        q, _ = self.comecar(self.google)
        self.voltar(self.google, q)
        self.assertEqual(list(User.objects.get(email="bia@emp.com.br").groups.values_list("name", flat=True)), ["Usuários"])

    def test_state_errado_e_reuso_da_resposta_sao_recusados(self):
        q, _ = self.comecar(self.google)
        msgs = self.erro_na_tela(self.voltar(self.google, q, state="outro"))
        self.assertTrue(any("inválido" in m or "expirada" in m for m in msgs))
        self.assertFalse(self.entrou())
        # o state é de uso único: a mesma resposta não vale uma segunda vez
        q, _ = self.comecar(self.google)
        self.assertEqual(self.voltar(self.google, q).status_code, 302)
        self.client.logout()
        self.erro_na_tela(self.voltar(self.google, q))
        self.assertFalse(self.entrou())

    def test_retorno_sem_ter_iniciado_e_recusado(self):
        self.erro_na_tela(self.client.get(reverse("identidade:retorno", args=["google"]), {"code": "x", "state": "y"}))
        self.assertFalse(self.entrou())

    def test_token_invalido_em_varios_aspectos(self):
        casos = {"nonce": dict(nonce="de-outro-login"), "público": dict(aud="outro-app"), "emissor": dict(iss="https://evil.example"),
                 "expirado": dict(exp=int(time.time()) - 3600, iat=int(time.time()) - 7200),
                 "sem sub": dict(sub=None)}
        for nome, extra in casos.items():
            q, _ = self.comecar(self.google)
            self.erro_na_tela(self.voltar(self.google, q, **extra))
            self.assertFalse(self.entrou(), nome)
        self.assertFalse(User.objects.filter(email="bia@emp.com.br").exists())

    def test_assinatura_de_outra_chave_e_algoritmos_fracos_sao_recusados(self):
        q, _ = self.comecar(self.google)
        agora = int(time.time())
        base = {"iss": "https://accounts.google.com", "aud": self.google.client_id, "sub": "x", "iat": agora,
                "exp": agora + 300, "nonce": q["nonce"], "email": "bia@emp.com.br", "email_verified": True, "hd": "emp.com.br"}
        falsos = [jwt.encode(base, OUTRA, algorithm="RS256", headers={"kid": "k1"}),
                  jwt.encode(base, "segredo", algorithm="HS256", headers={"kid": "k1"}),
                  jwt.encode(base, None, algorithm="none")]
        for tok in falsos:
            q, _ = self.comecar(self.google)
            self.erro_na_tela(self.voltar(self.google, q, token=tok))
            self.assertFalse(self.entrou())

    def test_dominio_nao_autorizado_e_hd_diferente(self):
        for extra in (dict(email="bia@gmail.com", hd=None), dict(email="bia@emp.com.br", hd="outra.com"),
                      dict(email="bia@emp.com.br", hd=None)):
            q, _ = self.comecar(self.google)
            self.erro_na_tela(self.voltar(self.google, q, **extra))
            self.assertFalse(self.entrou(), extra)

    def test_email_nao_verificado_e_recusado_no_primeiro_acesso(self):
        q, _ = self.comecar(self.google)
        self.erro_na_tela(self.voltar(self.google, q, email_verified=False))
        self.assertFalse(User.objects.filter(email="bia@emp.com.br").exists())

    def test_usuario_local_com_mesmo_email_nao_e_assumido_sem_autorizacao(self):
        local = User.objects.create_user("bia", "bia@emp.com.br", "Senha-forte-123")
        q, _ = self.comecar(self.google)
        self.erro_na_tela(self.voltar(self.google, q))
        self.assertFalse(IdentidadeExterna.objects.exists())
        self.google.vincular_por_email = True
        self.google.save()
        q, _ = self.comecar(self.google)
        self.assertEqual(self.voltar(self.google, q).status_code, 302)
        self.assertTrue(IdentidadeExterna.objects.filter(usuario=local).exists())
        # depois de vinculado, vale o identificador do provedor, mesmo que o e-mail mude lá
        self.client.logout()
        q, _ = self.comecar(self.google)
        self.assertEqual(self.voltar(self.google, q, email="outro@emp.com.br").status_code, 302)
        self.assertEqual(User.objects.filter(email="outro@emp.com.br").count(), 0)

    def test_sem_criacao_automatica_e_sem_cadastro_nao_entra(self):
        self.google.criar_usuarios = False
        self.google.save()
        q, _ = self.comecar(self.google)
        msgs = self.erro_na_tela(self.voltar(self.google, q))
        self.assertTrue(any("cadastro" in m for m in msgs))

    def test_usuario_desativado_nao_entra(self):
        q, _ = self.comecar(self.google)
        self.voltar(self.google, q)
        self.client.logout()
        User.objects.update(is_active=False)
        q, _ = self.comecar(self.google)
        self.erro_na_tela(self.voltar(self.google, q))
        self.assertFalse(self.entrou())

    def test_microsoft_confere_o_tenant_do_token(self):
        ms = self.novo("microsoft", "Microsoft", criar_usuarios=True)
        q, _ = self.comecar(ms)
        self.assertIn(f"/{TENANT}/v2.0/", oidc.emissor_esperado(ms) + "/")
        self.erro_na_tela(self.voltar(ms, q, tid="99999999-0000-0000-0000-000000000000", email="a@x.com", hd=None))
        self.assertFalse(self.entrou())
        q, _ = self.comecar(ms)
        r = self.voltar(ms, q, tid=TENANT, email=None, preferred_username="bia@emp.com.br", hd=None)
        self.assertEqual(r.status_code, 302)
        self.assertTrue(User.objects.filter(email="bia@emp.com.br").exists())

    def test_tenant_comum_e_criacao_sem_dominio_sao_barrados_na_configuracao(self):
        for tenant in ("common", "organizations", "consumers", ""):
            with self.assertRaises(ValidationError):
                ProvedorOIDC(tipo="microsoft", nome="x", client_id="c", tenant=tenant).clean()
        with self.assertRaises(ValidationError):
            ProvedorOIDC(tipo="google", nome="x", client_id="c", criar_usuarios=True).clean()
        with self.assertRaises(ValidationError):
            ProvedorOIDC(tipo="generico", nome="x", client_id="c", emissor="http://inseguro").clean()

    def test_provedor_generico_e_emissor_divergente_na_descoberta(self):
        kc = self.novo("generico", "Keycloak", emissor="https://sso.emp.com.br/realms/erp", dominios_permitidos="emp.com.br",
                       criar_usuarios=True)
        q, _ = self.comecar(kc)
        self.assertEqual(self.voltar(kc, q, hd=None).status_code, 302)
        cache.clear()
        with mock.patch("apps.identidade.oidc._get", return_value=desc("https://falso.example")):
            r = self.client.get(reverse("identidade:entrar", args=[kc.slug]))
        self.assertRedirects(r, reverse("login"), fetch_redirect_response=False)

    def test_2fa_do_erp_so_e_exigido_se_o_provedor_marcar(self):
        adm = User.objects.create_superuser("chefe", "bia@emp.com.br", "Senha-forte-123")
        self.google.vincular_por_email = True
        self.google.save()
        with override_settings(MFA_ATIVO=True):
            q, _ = self.comecar(self.google)
            self.voltar(self.google, q)
            self.assertTrue(self.entrou())                     # confia no MFA do Google
            self.client.logout()
            self.google.exigir_2fa_erp = True
            self.google.save()
            q, _ = self.comecar(self.google)
            self.voltar(self.google, q)
            self.assertIn("/seguranca/2fa/ativar/", self.client.get("/")["Location"])

    def test_next_externo_e_ignorado(self):
        q, _ = self.comecar(self.google, "https://evil.example/x")
        r = self.voltar(self.google, q)
        self.assertRedirects(r, "/", fetch_redirect_response=False)

    def test_provedor_inativo_some_e_nao_inicia(self):
        self.google.ativo = False
        self.google.save()
        self.assertEqual(self.client.get(reverse("identidade:entrar", args=["google"])).status_code, 404)
        self.assertNotContains(self.client.get(reverse("login")), "Entrar com Google")

    def test_botoes_aparecem_no_login(self):
        self.assertContains(self.client.get(reverse("login")), "Entrar com Google")

    def test_falha_de_sso_entra_na_auditoria_de_login(self):
        q, _ = self.comecar(self.google)
        self.voltar(self.google, q, state="x")
        self.assertTrue(LogAuditoria.objects.filter(modelo="auth.login", descricao__startswith="FALHA de login SSO").exists())


class IdentidadeTelasTests(OidcBase):
    def test_so_superusuario_e_segredo_nao_vaza(self):
        comum = User.objects.create_user("comum", password="x")
        adm = User.objects.create_superuser("adm", "a@x.com", "x")
        self.client.force_login(comum)
        for url in (reverse("identidade:painel"), reverse("identidade:oidc_novo"), reverse("identidade:ldap_novo")):
            self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(adm)
        r = self.client.get(reverse("identidade:painel"))
        self.assertContains(r, "/identidade/retorno/google/")
        self.assertNotContains(r, "segredo-do-app")
        r = self.client.get(reverse("identidade:oidc_editar", args=[self.google.pk]))
        self.assertNotContains(r, "segredo-do-app")

    def test_cadastrar_provedor_e_ldap_pela_tela_cifra_os_segredos(self):
        adm = User.objects.create_superuser("adm", "a@x.com", "x")
        self.client.force_login(adm)
        r = self.client.post(reverse("identidade:oidc_novo"), {
            "tipo": "microsoft", "nome": "Microsoft 365", "ativo": "on", "client_id": "abc", "segredo": "s3gredo-novo",
            "tenant": TENANT, "emissor": "", "dominios_permitidos": ""})
        self.assertEqual(r.status_code, 302)
        p = ProvedorOIDC.objects.get(nome="Microsoft 365")
        self.assertEqual(p.obter_segredo(), "s3gredo-novo")
        self.assertNotIn(b"s3gredo-novo", bytes(p.client_secret))
        self.assertEqual(p.slug, "microsoft-365")
        r = self.client.post(reverse("identidade:oidc_novo"), {"tipo": "microsoft", "nome": "Sem segredo", "client_id": "c", "tenant": TENANT})
        self.assertEqual(r.status_code, 200)
        r = self.client.post(reverse("identidade:ldap_novo"), {
            "nome": "AD", "ativo": "on", "servidor": "ad.emp.local", "porta": 636, "seguranca": "ldaps", "base_dn": "dc=emp,dc=local",
            "filtro_usuario": "(sAMAccountName={usuario})", "atributo_login": "sAMAccountName", "atributo_email": "mail",
            "atributo_nome": "displayName", "atributo_grupos": "memberOf", "timeout": 10, "senha_servico": "bind-pass",
            "criar_usuarios": "on", "bind_dn": "cn=svc", "mapa_grupos": "", "grupo_obrigatorio": "", "ca_pem": ""})
        self.assertEqual(r.status_code, 302, getattr(r, "context", None) and r.context["form"].errors)
        self.assertEqual(ConfiguracaoLDAP.objects.get(nome="AD").obter_senha(), "bind-pass")
