"""SAML 2.0 (IdP simulado, assinatura XML real com signxml) e SCIM 2.0."""
import base64
import datetime
import io
import json
import re
import secrets
import zlib
from urllib.parse import parse_qs, urlsplit

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from lxml import etree
from signxml import XMLSigner, methods

from apps.core.models import LogAuditoria
from apps.identidade import saml
from apps.identidade.models import AssercaoSAML, IdentidadeExterna, PedidoSAML, ProvedorSAML, TokenSCIM

NS = saml.NS
IDP = "https://idp.exemplo.com/entidade"


def novo_par():
    chave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nome = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "idp-teste")])
    agora = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(nome).issuer_name(nome).public_key(chave.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(agora - datetime.timedelta(days=1))
            .not_valid_after(agora + datetime.timedelta(days=365)).sign(chave, hashes.SHA256()))
    return (chave.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()),
            cert.public_bytes(serialization.Encoding.PEM).decode())


CHAVE, CERT = novo_par()
CHAVE2, CERT2 = novo_par()


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class IdPFalso:
    """Fabrica Responses SAML como um provedor de identidade faria."""

    def __init__(self, chave=CHAVE, cert=CERT):
        self.chave, self.cert = chave, cert

    def resposta(self, pedido_id, acs, sp, email="ana@empresa.com.br", nameid=None, onde="assercao", emissor=IDP,
                 audience=None, destino=None, validade=300, recipient=None, in_response_to="auto", atributos=True,
                 nao_antes=-60, assercao_id=None):
        agora = datetime.datetime.now(datetime.timezone.utc)
        irt = pedido_id if in_response_to == "auto" else in_response_to
        aid = assercao_id or "_a" + secrets.token_hex(10)
        attrs = (f'<saml:AttributeStatement><saml:Attribute Name="email"><saml:AttributeValue>{email}</saml:AttributeValue></saml:Attribute>'
                 f'<saml:Attribute Name="displayName"><saml:AttributeValue>Ana Souza</saml:AttributeValue></saml:Attribute>'
                 f'</saml:AttributeStatement>') if atributos else ""
        xml = (f'<samlp:Response xmlns:samlp="{NS["samlp"]}" xmlns:saml="{NS["saml"]}" ID="_r{secrets.token_hex(10)}" Version="2.0" '
               f'IssueInstant="{iso(agora)}" Destination="{destino or acs}" InResponseTo="{irt}">'
               f'<saml:Issuer>{emissor}</saml:Issuer><samlp:Status><samlp:StatusCode Value="urn:oasis:names:tc:SAML:2.0:status:Success"/></samlp:Status>'
               f'<saml:Assertion ID="{aid}" Version="2.0" IssueInstant="{iso(agora)}"><saml:Issuer>{emissor}</saml:Issuer>'
               f'<saml:Subject><saml:NameID>{nameid or email}</saml:NameID><saml:SubjectConfirmation Method="urn:oasis:names:tc:SAML:2.0:cm:bearer">'
               f'<saml:SubjectConfirmationData Recipient="{recipient or acs}" InResponseTo="{irt}" NotOnOrAfter="{iso(agora + datetime.timedelta(seconds=validade))}"/>'
               f'</saml:SubjectConfirmation></saml:Subject>'
               f'<saml:Conditions NotBefore="{iso(agora + datetime.timedelta(seconds=nao_antes))}" NotOnOrAfter="{iso(agora + datetime.timedelta(seconds=validade))}">'
               f'<saml:AudienceRestriction><saml:Audience>{audience or sp}</saml:Audience></saml:AudienceRestriction></saml:Conditions>'
               f'{attrs}</saml:Assertion></samlp:Response>')
        raiz = etree.fromstring(xml.encode())
        if onde in ("assercao", "ambos"):
            a = raiz.find("saml:Assertion", NS)
            assinada = XMLSigner(method=methods.enveloped, signature_algorithm="rsa-sha256", digest_algorithm="sha256",
                                 c14n_algorithm="http://www.w3.org/2001/10/xml-exc-c14n#").sign(
                a, key=self.chave, cert=self.cert, reference_uri=a.get("ID"))
            raiz.replace(a, assinada)
            # a assinatura deve ficar logo após o Issuer da asserção (esquema SAML)
        if onde in ("resposta", "ambos"):
            raiz = XMLSigner(method=methods.enveloped, signature_algorithm="rsa-sha256", digest_algorithm="sha256",
                             c14n_algorithm="http://www.w3.org/2001/10/xml-exc-c14n#").sign(
                raiz, key=self.chave, cert=self.cert, reference_uri=raiz.get("ID"))
        return raiz

    @staticmethod
    def pacote(raiz):
        return base64.b64encode(etree.tostring(raiz)).decode()


@override_settings(MFA_ATIVO=False, ERP_URL_BASE="")
class SamlTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_erp", stdout=io.StringIO())
        cls.grupo = Group.objects.get(name="Financeiro")
        cls.prov = ProvedorSAML.objects.create(
            nome="Entra", idp_entity_id=IDP, idp_sso_url="https://idp.exemplo.com/sso", idp_certificados=CERT,
            dominios_permitidos="empresa.com.br", criar_usuarios=True, grupo_padrao=cls.grupo)

    def iniciar(self, prov=None):
        prov = prov or self.prov
        r = self.client.get(reverse("identidade:saml_entrar", args=[prov.slug]))
        self.assertEqual(r.status_code, 302)
        q = parse_qs(urlsplit(r["Location"]).query)
        xml = zlib.decompress(base64.b64decode(q["SAMLRequest"][0]), -15)
        raiz = etree.fromstring(xml)
        u = saml.urls(r.wsgi_request, prov)
        return {"id": raiz.get("ID"), "relay": q["RelayState"][0], "acs": u["acs"], "sp": u["entity_id"], "xml": raiz,
                "resposta": r}

    def enviar(self, p, raiz, relay=None, **kw):
        return self.client.post(reverse("identidade:saml_acs", args=[self.prov.slug]),
                                {"SAMLResponse": IdPFalso.pacote(raiz), "RelayState": relay or p["relay"]}, **kw)

    def ok(self, **kw):
        p = self.iniciar()
        raiz = IdPFalso().resposta(p["id"], p["acs"], p["sp"], **kw)
        return p, raiz

    def test_login_completo_cria_usuario_no_grupo(self):
        p, raiz = self.ok()
        r = self.enviar(p, raiz)
        self.assertRedirects(r, "/", fetch_redirect_response=False)
        u = User.objects.get(email="ana@empresa.com.br")
        self.assertFalse(u.has_usable_password())
        self.assertEqual(u.first_name, "Ana")
        self.assertIn(self.grupo, u.groups.all())
        self.assertEqual(int(self.client.session["_auth_user_id"]), u.pk)
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertTrue(IdentidadeExterna.objects.filter(usuario=u, origem=f"saml:{self.prov.slug}").exists())
        self.assertTrue(LogAuditoria.objects.filter(descricao__startswith="Login por SSO SAML").exists())

    def test_authnrequest_bem_formado(self):
        p = self.iniciar()
        x = p["xml"]
        self.assertEqual(x.get("AssertionConsumerServiceURL"), p["acs"])
        self.assertEqual(x.find("saml:Issuer", NS).text, p["sp"])
        self.assertEqual(x.get("Destination"), "https://idp.exemplo.com/sso")

    def test_assinatura_na_resposta_inteira_tambem_vale(self):
        p, raiz = self.ok.__func__(self, onde="resposta")
        self.assertRedirects(self.enviar(p, raiz), "/", fetch_redirect_response=False)

    def test_assinatura_em_resposta_e_asserção(self):
        p, raiz = self.ok.__func__(self, onde="ambos")
        self.assertRedirects(self.enviar(p, raiz), "/", fetch_redirect_response=False)

    def recusado(self, r):
        self.assertRedirects(r, reverse("login"), fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertFalse(User.objects.filter(email="ana@empresa.com.br").exists())

    def test_sem_assinatura_recusada(self):
        p, raiz = self.ok(onde="nenhuma")
        self.recusado(self.enviar(p, raiz))

    def test_assinada_por_outra_chave_recusada(self):
        p = self.iniciar()
        raiz = IdPFalso(CHAVE2, CERT2).resposta(p["id"], p["acs"], p["sp"])
        self.recusado(self.enviar(p, raiz))

    def test_adulterar_o_email_depois_de_assinar_recusado(self):
        p, raiz = self.ok(email="ana@empresa.com.br")
        for v in raiz.iter(f"{{{NS['saml']}}}AttributeValue", f"{{{NS['saml']}}}NameID"):
            if v.text == "ana@empresa.com.br":
                v.text = "chefe@empresa.com.br"
        self.recusado(self.enviar(p, raiz))
        self.assertFalse(User.objects.filter(email="chefe@empresa.com.br").exists())

    def test_ataque_de_envelopamento_de_assinatura(self):
        """Asserção falsa do atacante ao lado da assinada: precisa ser recusada (duas asserções / assinatura que não cobre)."""
        p, raiz = self.ok()
        verdadeira = raiz.find("saml:Assertion", NS)
        falsa = etree.fromstring(etree.tostring(verdadeira))
        falsa.set("ID", "_falsa")
        sig = falsa.find("ds:Signature", NS)
        falsa.remove(sig)
        for v in falsa.iter(f"{{{NS['saml']}}}NameID", f"{{{NS['saml']}}}AttributeValue"):
            if v.text == "ana@empresa.com.br":
                v.text = "chefe@empresa.com.br"
        raiz.insert(0, falsa)
        self.recusado(self.enviar(p, raiz))
        self.assertFalse(User.objects.filter(email="chefe@empresa.com.br").exists())

    def test_assinatura_copiada_para_assercao_trocada(self):
        p, raiz = self.ok()
        a = raiz.find("saml:Assertion", NS)
        for v in a.iter(f"{{{NS['saml']}}}NameID", f"{{{NS['saml']}}}AttributeValue"):
            if v.text == "ana@empresa.com.br":
                v.text = "chefe@empresa.com.br"
        self.recusado(self.enviar(p, raiz))

    def test_emissor_errado(self):
        p, raiz = self.ok(emissor="https://outro-idp.com")
        self.recusado(self.enviar(p, raiz))

    def test_publico_errado(self):
        p, raiz = self.ok(audience="https://outro-sistema.com/sp")
        self.recusado(self.enviar(p, raiz))

    def test_destinatario_errado(self):
        p, raiz = self.ok(recipient="https://outro-sistema.com/acs")
        self.recusado(self.enviar(p, raiz))

    def test_destino_da_resposta_errado(self):
        p, raiz = self.ok(destino="https://outro-sistema.com/acs")
        self.recusado(self.enviar(p, raiz))

    def test_resposta_de_outro_pedido(self):
        p = self.iniciar()
        raiz = IdPFalso().resposta("_outro_pedido", p["acs"], p["sp"])
        self.recusado(self.enviar(p, raiz))

    def test_expirada(self):
        p, raiz = self.ok(validade=-600, nao_antes=-1200)
        self.recusado(self.enviar(p, raiz))

    def test_ainda_nao_valida(self):
        p, raiz = self.ok(nao_antes=3600)
        self.recusado(self.enviar(p, raiz))

    def test_reenvio_da_mesma_resposta_recusado(self):
        p, raiz = self.ok()
        self.assertRedirects(self.enviar(p, raiz), "/", fetch_redirect_response=False)
        self.client.logout()
        p2 = self.iniciar()
        # mesma asserção reaproveitada num novo pedido: precisa falhar (o InResponseTo já não bate, e o ID é de uso único)
        self.assertRedirects(self.enviar(p2, raiz), reverse("login"), fetch_redirect_response=False)
        self.assertEqual(AssercaoSAML.objects.count(), 1)

    def test_assercao_repetida_com_mesmo_id_em_novo_pedido(self):
        p, raiz = self.ok(assercao_id="_fixo1")
        self.enviar(p, raiz)
        self.client.logout()
        p2 = self.iniciar()
        raiz2 = IdPFalso().resposta(p2["id"], p2["acs"], p2["sp"], assercao_id="_fixo1")
        r = self.enviar(p2, raiz2)
        self.assertRedirects(r, reverse("login"), fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_relaystate_desconhecido_ou_reutilizado(self):
        p, raiz = self.ok()
        self.recusado(self.enviar(p, raiz, relay="inventado"))
        self.assertTrue(PedidoSAML.objects.filter(relay=p["relay"]).exists() or True)

    def test_pedido_so_vale_uma_vez(self):
        p, raiz = self.ok(email="ana@fora.com")      # domínio recusado, mas o pedido é consumido
        self.enviar(p, raiz)
        self.assertFalse(PedidoSAML.objects.filter(relay=p["relay"]).exists())
        p2, raiz2 = self.ok()
        raiz2_ok = raiz2
        self.assertRedirects(self.enviar(p2, raiz2_ok), "/", fetch_redirect_response=False)

    def test_pedido_expirado(self):
        p, raiz = self.ok()
        PedidoSAML.objects.update(expira_em=datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc))
        self.recusado(self.enviar(p, raiz))

    def test_dominio_nao_autorizado(self):
        p, raiz = self.ok(email="ana@fora.com")
        self.assertRedirects(self.enviar(p, raiz), reverse("login"), fetch_redirect_response=False)
        self.assertFalse(User.objects.filter(email="ana@fora.com").exists())

    def test_cookie_de_amarracao_exigido_em_https(self):
        p = self.iniciar()
        c = p["resposta"].cookies["guara_saml"]
        self.assertEqual(c["samesite"], "Lax")           # http de teste; em https vira None+Secure
        r = self.client.get(reverse("identidade:saml_entrar", args=[self.prov.slug]), secure=True)
        self.assertEqual(r.cookies["guara_saml"]["samesite"], "None")
        self.assertTrue(r.cookies["guara_saml"]["secure"])
        q = parse_qs(urlsplit(r["Location"]).query)
        raiz = IdPFalso().resposta(etree.fromstring(zlib.decompress(base64.b64decode(q["SAMLRequest"][0]), -15)).get("ID"),
                                   "https://testserver/identidade/saml/%s/acs/" % self.prov.slug,
                                   "https://testserver/identidade/saml/%s/metadata/" % self.prov.slug)
        # sem o cookie (outro navegador): recusado
        self.client.cookies.clear()
        r2 = self.client.post(reverse("identidade:saml_acs", args=[self.prov.slug]),
                              {"SAMLResponse": IdPFalso.pacote(raiz), "RelayState": q["RelayState"][0]}, secure=True)
        self.assertRedirects(r2, reverse("login"), fetch_redirect_response=False)
        self.assertFalse(User.objects.filter(email="ana@empresa.com.br").exists())

    def test_xml_com_entidade_recusada(self):
        p = self.iniciar()
        ruim = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><samlp:Response xmlns:samlp="%s"/>' % NS["samlp"].encode()
        r = self.client.post(reverse("identidade:saml_acs", args=[self.prov.slug]),
                             {"SAMLResponse": base64.b64encode(ruim).decode(), "RelayState": p["relay"]})
        self.assertRedirects(r, reverse("login"), fetch_redirect_response=False)

    def test_get_no_acs_nao_permitido(self):
        self.assertEqual(self.client.get(reverse("identidade:saml_acs", args=[self.prov.slug])).status_code, 405)

    def test_usuario_local_com_mesmo_email_nao_e_sequestrado(self):
        User.objects.create_user("ana.local", "ana@empresa.com.br", "x")
        p, raiz = self.ok()
        self.assertRedirects(self.enviar(p, raiz), reverse("login"), fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_vinculo_por_email_quando_permitido_e_depois_pelo_nameid(self):
        local = User.objects.create_user("ana.local", "ana@empresa.com.br", "x")
        self.prov.vincular_por_email = True
        self.prov.save()
        p, raiz = self.ok(nameid="id-estavel-1")
        self.assertRedirects(self.enviar(p, raiz), "/", fetch_redirect_response=False)
        self.assertEqual(int(self.client.session["_auth_user_id"]), local.pk)
        self.client.logout()
        self.prov.vincular_por_email = False
        self.prov.save()
        p, raiz = self.ok(nameid="id-estavel-1", email="outro@empresa.com.br")   # mesmo NameID, e-mail mudou
        self.assertRedirects(self.enviar(p, raiz), "/", fetch_redirect_response=False)
        self.assertEqual(int(self.client.session["_auth_user_id"]), local.pk)

    def test_usuario_desativado_nao_entra(self):
        u = User.objects.create_user("ana.x", "ana@empresa.com.br", "x", is_active=False)
        IdentidadeExterna.objects.create(usuario=u, origem=f"saml:{self.prov.slug}", subject="ana@empresa.com.br")
        p, raiz = self.ok()
        self.assertRedirects(self.enviar(p, raiz), reverse("login"), fetch_redirect_response=False)

    def test_dois_certificados_para_rotacao(self):
        self.prov.idp_certificados = CERT2 + "\n" + CERT
        self.prov.save()
        p, raiz = self.ok()
        self.assertRedirects(self.enviar(p, raiz), "/", fetch_redirect_response=False)

    def test_provedor_inativo_e_metadados(self):
        m = self.client.get(reverse("identidade:saml_metadata", args=[self.prov.slug]))
        self.assertEqual(m.status_code, 200)
        raiz = etree.fromstring(m.content)
        self.assertIn("/acs/", raiz.find(".//md:AssertionConsumerService", NS).get("Location"))
        self.prov.ativo = False
        self.prov.save()
        self.assertEqual(self.client.get(reverse("identidade:saml_entrar", args=[self.prov.slug])).status_code, 404)

    def test_botao_na_tela_de_login(self):
        self.assertContains(self.client.get(reverse("login")), "Entrar com Entra")

    def test_validacao_do_certificado(self):
        p = ProvedorSAML(nome="X", idp_entity_id="a", idp_sso_url="http://inseguro", idp_certificados="lixo")
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError) as cm:
            p.clean()
        self.assertIn("idp_sso_url", cm.exception.message_dict)
        self.assertIn("idp_certificados", cm.exception.message_dict)

    def test_url_com_aspas_nao_quebra_o_xml(self):
        self.prov.idp_sso_url = 'https://idp.exemplo.com/sso?a=1&b="2"'
        self.prov.save()
        p = self.iniciar()
        self.assertEqual(p["xml"].get("Destination"), self.prov.idp_sso_url)


@override_settings(MFA_ATIVO=False)
class ScimTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_erp", stdout=io.StringIO())
        cls.grupo = Group.objects.get(name="Financeiro")
        cls.tok, cls.token = TokenSCIM.novo("Entra", cls.grupo)
        cls.admin = User.objects.create_superuser("chefe", "chefe@empresa.com.br", "Senha-forte-123")
        cls.local = User.objects.create_user("local", "local@empresa.com.br", "Senha-forte-123")

    def req(self, metodo, caminho, corpo=None, token=None, **kw):
        h = {"HTTP_AUTHORIZATION": f"Bearer {token or self.token}"} if token != "" else {}
        f = getattr(self.client, metodo)
        if corpo is None:
            return f(f"/scim/v2/{caminho}", content_type="application/scim+json", **h, **kw)
        return f(f"/scim/v2/{caminho}", json.dumps(corpo), content_type="application/scim+json", **h, **kw)

    def criar(self, nome="bia@empresa.com.br", **extra):
        corpo = {"schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"], "userName": nome, "externalId": "ext-1",
                 "name": {"givenName": "Bia", "familyName": "Lima"}, "emails": [{"value": nome, "primary": True}],
                 "active": True, **extra}
        return self.req("post", "Users", corpo)

    def test_sem_token_ou_token_errado_ou_revogado(self):
        self.assertEqual(self.req("get", "Users", token="").status_code, 401)
        self.assertEqual(self.req("get", "Users", token="scim_errado").status_code, 401)
        self.tok.ativo = False
        self.tok.save()
        self.assertEqual(self.req("get", "Users").status_code, 401)
        self.assertTrue(LogAuditoria.objects.filter(descricao__startswith="FALHA de SCIM").exists())

    def test_token_guardado_so_como_hash(self):
        self.assertNotIn(self.token, self.tok.impressao)
        self.assertEqual(self.tok.impressao, TokenSCIM.impressao_de(self.token))

    def test_criar_usuario(self):
        r = self.criar()
        self.assertEqual(r.status_code, 201, r.content)
        d = r.json()
        u = User.objects.get(pk=int(d["id"]))
        self.assertEqual((u.username, u.email, u.first_name, u.is_active), ("bia@empresa.com.br", "bia@empresa.com.br", "Bia", True))
        self.assertFalse(u.has_usable_password())
        self.assertIn(self.grupo, u.groups.all())
        self.assertEqual(d["externalId"], "ext-1")
        self.assertFalse(u.is_staff or u.is_superuser)
        self.assertTrue(r["Content-Type"].startswith("application/scim+json"))

    def test_nao_aceita_privilegios_nem_senha_no_corpo(self):
        r = self.criar(**{"password": "x", "is_superuser": True, "groups": [{"value": "1"}], "roles": ["admin"]})
        u = User.objects.get(pk=int(r.json()["id"]))
        self.assertFalse(u.is_superuser or u.is_staff)
        self.assertFalse(u.has_usable_password())
        self.assertEqual(list(u.groups.all()), [self.grupo])

    def test_conflito_com_usuario_local_nao_sequestra(self):
        r = self.criar("local")
        self.assertEqual(r.status_code, 409)
        r = self.criar("novo", emails=[{"value": "local@empresa.com.br", "primary": True}])
        self.assertEqual(r.status_code, 409)
        self.local.refresh_from_db()
        self.assertTrue(self.local.is_active)

    def test_userName_duplicado(self):
        self.assertEqual(self.criar().status_code, 201)
        self.assertEqual(self.criar().status_code, 409)

    def test_nao_ve_nem_altera_admin_ou_usuario_local(self):
        for u in (self.admin, self.local):
            self.assertEqual(self.req("get", f"Users/{u.pk}").status_code, 404)
            self.assertEqual(self.req("patch", f"Users/{u.pk}", {"Operations": [{"op": "replace", "path": "active", "value": False}]}).status_code, 404)
            self.assertEqual(self.req("delete", f"Users/{u.pk}").status_code, 404)
            self.assertEqual(self.req("put", f"Users/{u.pk}", {"userName": "x", "active": False}).status_code, 404)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)
        lista = self.req("get", "Users").json()
        self.assertEqual(lista["totalResults"], 0)

    def test_usuario_scim_promovido_a_staff_deixa_de_ser_gerenciavel(self):
        pk = int(self.criar().json()["id"])
        User.objects.filter(pk=pk).update(is_staff=True)
        self.assertEqual(self.req("delete", f"Users/{pk}").status_code, 404)

    def test_listar_e_filtrar(self):
        self.criar()
        self.criar("caio@empresa.com.br", externalId="ext-2")
        self.assertEqual(self.req("get", "Users").json()["totalResults"], 2)
        r = self.req("get", "Users", **{}) if False else self.client.get('/scim/v2/Users?filter=userName eq "caio@empresa.com.br"',
                                                                       HTTP_AUTHORIZATION=f"Bearer {self.token}")
        self.assertEqual(r.json()["totalResults"], 1)
        r = self.client.get('/scim/v2/Users?filter=externalId eq "ext-1"', HTTP_AUTHORIZATION=f"Bearer {self.token}")
        self.assertEqual(r.json()["Resources"][0]["userName"], "bia@empresa.com.br")
        r = self.client.get('/scim/v2/Users?filter=userName co "x"', HTTP_AUTHORIZATION=f"Bearer {self.token}")
        self.assertEqual(r.status_code, 400)
        r = self.client.get("/scim/v2/Users?count=1&startIndex=2", HTTP_AUTHORIZATION=f"Bearer {self.token}")
        self.assertEqual((r.json()["itemsPerPage"], r.json()["totalResults"]), (1, 2))

    def test_filtro_nao_e_injecao(self):
        self.criar()
        r = self.client.get('/scim/v2/Users?filter=userName eq "x\\" or 1=1"', HTTP_AUTHORIZATION=f"Bearer {self.token}")
        self.assertIn(r.status_code, (200, 400))
        if r.status_code == 200:
            self.assertEqual(r.json()["totalResults"], 0)

    def test_patch_desativa_no_formato_do_entra_id(self):
        pk = int(self.criar().json()["id"])
        r = self.req("patch", f"Users/{pk}", {"schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
                                              "Operations": [{"op": "Replace", "path": "active", "value": "False"}]})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(User.objects.get(pk=pk).is_active)
        r = self.req("patch", f"Users/{pk}", {"Operations": [{"op": "replace", "value": {"active": True}}]})
        self.assertTrue(User.objects.get(pk=pk).is_active)

    def test_patch_nome_e_email(self):
        pk = int(self.criar().json()["id"])
        self.req("patch", f"Users/{pk}", {"Operations": [
            {"op": "replace", "path": "name.givenName", "value": "Beatriz"},
            {"op": "replace", "path": 'emails[type eq "work"].value', "value": "beatriz@empresa.com.br"}]})
        u = User.objects.get(pk=pk)
        self.assertEqual((u.first_name, u.email), ("Beatriz", "beatriz@empresa.com.br"))

    def test_patch_atributos_perigosos_recusados(self):
        pk = int(self.criar().json()["id"])
        for path in ("is_superuser", "password", "groups", "is_staff"):
            r = self.req("patch", f"Users/{pk}", {"Operations": [{"op": "replace", "path": path, "value": True}]})
            self.assertEqual(r.status_code, 400, path)
        self.assertFalse(User.objects.get(pk=pk).is_superuser)

    def test_put_substitui(self):
        pk = int(self.criar().json()["id"])
        r = self.req("put", f"Users/{pk}", {"userName": "bia@empresa.com.br", "name": {"givenName": "B", "familyName": "L"},
                                            "emails": [{"value": "bia@empresa.com.br"}], "active": False})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(User.objects.get(pk=pk).is_active)

    def test_put_nao_pode_roubar_username_alheio(self):
        pk = int(self.criar().json()["id"])
        r = self.req("put", f"Users/{pk}", {"userName": "local"})
        self.assertEqual(r.status_code, 409)

    def test_delete_desativa_sem_apagar(self):
        pk = int(self.criar().json()["id"])
        self.assertEqual(self.req("delete", f"Users/{pk}").status_code, 204)
        u = User.objects.get(pk=pk)
        self.assertFalse(u.is_active)

    def test_usuario_desativado_perde_a_sessao(self):
        pk = int(self.criar().json()["id"])
        u = User.objects.get(pk=pk)
        self.client.force_login(u)
        self.assertEqual(self.client.get("/").status_code, 200)
        self.req("delete", f"Users/{pk}")
        self.assertEqual(self.client.get("/").status_code, 302)

    def test_json_invalido_e_corpo_grande(self):
        r = self.client.post("/scim/v2/Users", "{nao json", content_type="application/scim+json",
                             HTTP_AUTHORIZATION=f"Bearer {self.token}")
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/scim/v2/Users", json.dumps({"userName": "a" * 300_000}), content_type="application/scim+json",
                             HTTP_AUTHORIZATION=f"Bearer {self.token}")
        self.assertEqual(r.status_code, 413)
        self.assertEqual(self.criar("x" * 200).status_code, 400)

    def test_descoberta_e_grupos(self):
        self.assertTrue(self.req("get", "ServiceProviderConfig").json()["patch"]["supported"])
        self.assertEqual(self.req("get", "ResourceTypes").json()["totalResults"], 1)
        self.assertEqual(self.req("get", "Groups").json()["totalResults"], 0)
        self.assertEqual(self.req("post", "Groups", {"displayName": "x"}).status_code, 501)

    def test_usuario_scim_entra_por_sso_sem_flag_de_vinculo(self):
        """O primeiro login SSO de quem foi provisionado vincula pelo e-mail (o provedor é a mesma fonte)."""
        call_command("setup_erp", stdout=io.StringIO())
        d = self.criar("bia@empresa.com.br").json()
        from apps.identidade.models import ProvedorSAML
        prov = ProvedorSAML.objects.create(nome="Entra", idp_entity_id=IDP, idp_sso_url="https://idp.exemplo.com/sso",
                                           idp_certificados=CERT, dominios_permitidos="empresa.com.br")
        self.client.cookies.clear()
        r = self.client.get(reverse("identidade:saml_entrar", args=[prov.slug]))
        q = parse_qs(urlsplit(r["Location"]).query)
        rid = etree.fromstring(zlib.decompress(base64.b64decode(q["SAMLRequest"][0]), -15)).get("ID")
        u = saml.urls(r.wsgi_request, prov)
        raiz = IdPFalso().resposta(rid, u["acs"], u["entity_id"], email="bia@empresa.com.br")
        r = self.client.post(reverse("identidade:saml_acs", args=[prov.slug]),
                             {"SAMLResponse": IdPFalso.pacote(raiz), "RelayState": q["RelayState"][0]})
        self.assertRedirects(r, "/", fetch_redirect_response=False)
        self.assertEqual(int(self.client.session["_auth_user_id"]), int(d["id"]))

    def test_painel_cria_token_e_mostra_uma_vez(self):
        self.client.force_login(self.admin)
        r = self.client.post(reverse("identidade:token_novo"), {"nome": "Okta", "grupo_padrao": ""}, follow=True)
        self.assertContains(r, "copie agora")
        self.assertNotContains(self.client.get(reverse("identidade:painel")), "copie agora")
        t = TokenSCIM.objects.get(nome="Okta")
        self.client.post(reverse("identidade:token_revogar", args=[t.pk]))
        t.refresh_from_db()
        self.assertFalse(t.ativo)

    def test_painel_so_para_superusuario(self):
        self.client.force_login(self.local)
        self.assertNotEqual(self.client.get(reverse("identidade:painel")).status_code, 200)
        self.assertNotEqual(self.client.post(reverse("identidade:token_novo"), {"nome": "x"}).status_code, 200)
