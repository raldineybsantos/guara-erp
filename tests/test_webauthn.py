"""Chaves de segurança WebAuthn: autenticador de software (ES256, attestation 'none') contra o fluxo real."""
import base64
import hashlib
import io
import json
import os
import struct
import time

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.core.models import LogAuditoria
from apps.seguranca import mfa
from apps.seguranca.models import ChaveSeguranca

SENHA = "Senha-forte-123"
RP = "testserver"
ORIGEM = "http://testserver"


def b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def desb64u(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class Autenticador:
    """Chave FIDO2 simulada: cria credenciais e assina desafios como um YubiKey faria."""

    def __init__(self):
        self.chave = ec.generate_private_key(ec.SECP256R1())
        self.cid = os.urandom(32)
        self.contador = 0

    def _cose(self):
        n = self.chave.public_key().public_numbers()
        return cbor2.dumps({1: 2, 3: -7, -1: 1, -2: n.x.to_bytes(32, "big"), -3: n.y.to_bytes(32, "big")})

    def cadastrar(self, opcoes, origem=ORIGEM, rp=RP):
        cd = json.dumps({"type": "webauthn.create", "challenge": opcoes["challenge"], "origin": origem}).encode()
        dados = (hashlib.sha256(rp.encode()).digest() + bytes([0x45]) + struct.pack(">I", self.contador) + bytes(16)
                 + struct.pack(">H", len(self.cid)) + self.cid + self._cose())
        ao = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": dados})
        return {"id": b64u(self.cid), "rawId": b64u(self.cid), "type": "public-key",
                "response": {"clientDataJSON": b64u(cd), "attestationObject": b64u(ao), "transports": ["usb"]},
                "clientExtensionResults": {}}

    def assinar(self, opcoes, origem=ORIGEM, rp=RP, contador=None, cid=None):
        self.contador = self.contador + 1 if contador is None else contador
        cd = json.dumps({"type": "webauthn.get", "challenge": opcoes["challenge"], "origin": origem}).encode()
        dados = hashlib.sha256(rp.encode()).digest() + bytes([0x05]) + struct.pack(">I", self.contador)
        sig = self.chave.sign(dados + hashlib.sha256(cd).digest(), ec.ECDSA(hashes.SHA256()))
        c = cid or self.cid
        return {"id": b64u(c), "rawId": b64u(c), "type": "public-key",
                "response": {"clientDataJSON": b64u(cd), "authenticatorData": b64u(dados), "signature": b64u(sig),
                             "userHandle": None}, "clientExtensionResults": {}}


@override_settings(MFA_ATIVO=True)
class WebAuthnTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_erp", stdout=io.StringIO())
        cls.admin = User.objects.create_superuser("chefe", "c@x.com", SENHA)
        cls.comum = User.objects.create_user("zé", "z@x.com", SENHA)
        cls.outro = User.objects.create_user("ana", "a@x.com", SENHA)

    def entrar(self, usuario):
        self.client.logout()
        self.assertEqual(self.client.post(reverse("login"), {"username": usuario, "password": SENHA}).status_code, 302)

    def post(self, nome, corpo=None, **kw):
        return self.client.post(reverse(f"seguranca:{nome}"), json.dumps(corpo or {}), content_type="application/json", **kw)

    def cadastrar(self, usuario="zé", nome="YubiKey", aut=None):
        aut = aut or Autenticador()
        self.entrar(usuario)
        op = self.post("chave_registrar_opcoes").json()
        r = self.post("chave_registrar_confirmar", {"nome": nome, "credencial": aut.cadastrar(op)})
        return aut, r

    def test_cadastro_guarda_so_a_chave_publica_e_audita(self):
        aut, r = self.cadastrar()
        self.assertEqual(r.status_code, 200, r.content)
        c = ChaveSeguranca.objects.get(usuario=self.comum)
        self.assertEqual(bytes(c.credencial_id), aut.cid)
        self.assertEqual(c.nome, "YubiKey")
        self.assertTrue(mfa.tem_fator(self.comum))
        self.assertTrue(LogAuditoria.objects.filter(modelo="auth.mfa", descricao__startswith="Chave de segurança cadastrada").exists())

    def test_primeira_chave_gera_codigos_de_recuperacao_uma_vez(self):
        _, r = self.cadastrar()
        self.assertEqual(r.json()["destino"], "/seguranca/2fa/chaves/codigos/")
        pagina = self.client.get(r.json()["destino"])
        self.assertEqual(len(pagina.context["codigos"]), 10)
        self.assertRedirects(self.client.get(r.json()["destino"]), "/seguranca/2fa/", fetch_redirect_response=False)

    def test_login_com_chave_libera_a_sessao(self):
        aut, _ = self.cadastrar()
        self.entrar("zé")
        r = self.client.get("/cadastros/")
        self.assertIn("/seguranca/2fa/verificar/", r["Location"])
        self.assertContains(self.client.get(reverse("seguranca:mfa_verificar")), "Usar chave de segurança")
        op = self.post("chave_entrar_opcoes").json()
        self.assertEqual(self.post("chave_entrar_verificar", {"credencial": aut.assinar(op)}).status_code, 200)
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertTrue(LogAuditoria.objects.filter(descricao__startswith="2FA verificado com chave").exists())

    def test_assinatura_nao_pode_ser_repetida(self):
        aut, _ = self.cadastrar()
        self.entrar("zé")
        op = self.post("chave_entrar_opcoes").json()
        cred = aut.assinar(op)
        self.assertEqual(self.post("chave_entrar_verificar", {"credencial": cred}).status_code, 200)
        self.assertEqual(self.post("chave_entrar_verificar", {"credencial": cred}).status_code, 403)
        # em outra sessão, o mesmo conteúdo não vale (desafio de uso único)
        self.entrar("zé")
        self.post("chave_entrar_opcoes")
        self.assertEqual(self.post("chave_entrar_verificar", {"credencial": cred}).status_code, 400)

    def test_origem_errada_recusada_anti_phishing(self):
        aut, _ = self.cadastrar()
        self.entrar("zé")
        op = self.post("chave_entrar_opcoes").json()
        r = self.post("chave_entrar_verificar", {"credencial": aut.assinar(op, origem="https://ern-falso.example")})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.client.get("/").status_code, 302)

    def test_dominio_errado_recusado(self):
        aut, _ = self.cadastrar()
        self.entrar("zé")
        op = self.post("chave_entrar_opcoes").json()
        self.assertEqual(self.post("chave_entrar_verificar", {"credencial": aut.assinar(op, rp="outro.com")}).status_code, 400)

    def test_assinatura_de_outra_chave_recusada(self):
        self.cadastrar()
        self.entrar("zé")
        op = self.post("chave_entrar_opcoes").json()
        impostora = Autenticador()
        cred = impostora.assinar(op)
        self.assertEqual(self.post("chave_entrar_verificar", {"credencial": cred}).status_code, 400)

    def test_chave_de_outro_usuario_nao_vale(self):
        aut_ana, _ = self.cadastrar("ana", "Ana")
        self.cadastrar("zé", "Zé")
        self.entrar("zé")
        op = self.post("chave_entrar_opcoes").json()
        self.assertEqual(self.post("chave_entrar_verificar", {"credencial": aut_ana.assinar(op)}).status_code, 400)

    def test_contador_que_nao_avanca_indica_clone(self):
        aut, _ = self.cadastrar()
        for contador, esperado in ((5, 200), (5, 400), (3, 400)):
            self.entrar("zé")
            op = self.post("chave_entrar_opcoes").json()
            r = self.post("chave_entrar_verificar", {"credencial": aut.assinar(op, contador=contador)})
            self.assertEqual(r.status_code, esperado, contador)

    def test_desafio_expirado(self):
        aut, _ = self.cadastrar()
        self.entrar("zé")
        op = self.post("chave_entrar_opcoes").json()
        s = self.client.session
        s["webauthn_desafio"]["ate"] = time.time() - 1
        s.save()
        self.assertEqual(self.post("chave_entrar_verificar", {"credencial": aut.assinar(op)}).status_code, 400)

    def test_falhas_repetidas_bloqueiam(self):
        aut, _ = self.cadastrar()
        self.entrar("zé")
        for _ in range(6):
            self.post("chave_entrar_opcoes")
            self.post("chave_entrar_verificar", {"credencial": {"id": "x"}})
        self.assertTrue(mfa.bloqueado(self.comum))
        op = self.post("chave_entrar_opcoes").json()
        self.assertEqual(self.post("chave_entrar_verificar", {"credencial": aut.assinar(op)}).status_code, 400)

    def test_cadastro_exige_sessao_liberada_se_ja_tem_fator(self):
        self.cadastrar()
        self.entrar("zé")                      # tem chave, ainda não verificou
        self.assertEqual(self.post("chave_registrar_opcoes").status_code, 403)
        self.assertEqual(self.post("chave_registrar_confirmar", {}).status_code, 403)

    def test_mesma_chave_nao_cadastra_duas_vezes(self):
        aut, _ = self.cadastrar()
        self.entrar("zé")
        op = self.post("chave_entrar_opcoes").json()
        self.post("chave_entrar_verificar", {"credencial": aut.assinar(op)})
        op = self.post("chave_registrar_opcoes").json()
        self.assertEqual(self.post("chave_registrar_confirmar", {"nome": "dup", "credencial": aut.cadastrar(op)}).status_code, 400)
        self.assertEqual(ChaveSeguranca.objects.filter(usuario=self.comum).count(), 1)

    def test_endpoints_exigem_login_e_post(self):
        self.client.logout()
        self.assertEqual(self.post("chave_registrar_opcoes").status_code, 302)
        self.assertEqual(self.post("chave_entrar_opcoes").status_code, 302)
        self.entrar("ana")
        self.assertEqual(self.client.get(reverse("seguranca:chave_registrar_opcoes")).status_code, 405)

    def test_confirmar_sem_desafio_ou_lixo(self):
        self.entrar("zé")
        self.assertEqual(self.post("chave_registrar_confirmar", {"nome": "x", "credencial": {"id": "a"}}).status_code, 400)
        self.post("chave_registrar_opcoes")
        self.assertEqual(self.post("chave_registrar_confirmar", {"nome": "x", "credencial": {"id": "a"}}).status_code, 400)

    def test_remover_exige_senha_e_respeita_obrigatoriedade(self):
        aut, _ = self.cadastrar("chefe", "Admin")           # superusuário: 2FA obrigatório
        c = ChaveSeguranca.objects.get(usuario=self.admin)
        url = reverse("seguranca:chave_remover", args=[c.pk])
        self.client.post(url, {"senha": "errada"})
        self.assertTrue(ChaveSeguranca.objects.filter(pk=c.pk).exists())
        self.client.post(url, {"senha": SENHA})             # última chave de quem é obrigado: recusa
        self.assertTrue(ChaveSeguranca.objects.filter(pk=c.pk).exists())
        # usuário comum pode remover a sua, com a senha
        self.cadastrar("zé", "Zé")
        z = ChaveSeguranca.objects.get(usuario=self.comum)
        self.client.post(reverse("seguranca:chave_remover", args=[z.pk]), {"senha": SENHA})
        self.assertFalse(ChaveSeguranca.objects.filter(pk=z.pk).exists())
        self.assertFalse(mfa.tem_fator(self.comum))

    def test_nao_remove_chave_alheia(self):
        self.cadastrar("ana", "Ana")
        a = ChaveSeguranca.objects.get(usuario=self.outro)
        self.cadastrar("zé", "Zé")
        r = self.client.post(reverse("seguranca:chave_remover", args=[a.pk]), {"senha": SENHA})
        self.assertEqual(r.status_code, 404)

    def test_codigo_de_recuperacao_vale_para_quem_so_tem_chave(self):
        _, r = self.cadastrar()
        codigos = self.client.get(r.json()["destino"]).context["codigos"]
        self.entrar("zé")
        r = self.client.post(reverse("seguranca:mfa_verificar"), {"codigo": codigos[0]})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_redefinir_pelo_admin_remove_as_chaves(self):
        self.cadastrar("zé", "Zé")
        self.entrar("chefe")
        mfa.redefinir(self.comum, self.admin)
        self.assertEqual(ChaveSeguranca.objects.filter(usuario=self.comum).count(), 0)

    def test_primeiro_acesso_obrigatorio_pode_cadastrar_chave(self):
        self.entrar("chefe")
        self.assertEqual(self.client.get("/").status_code, 302)       # sem fator: só alcança a tela de cadastro
        self.assertContains(self.client.get(reverse("seguranca:mfa_ativar")), "chave de segurança")
        aut, r = self.cadastrar("chefe", "Admin")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.get("/").status_code, 200)
