"""Gateway Efí (API Cobranças simulada) e cartão de crédito por página hospedada (Efí e Asaas)."""
import base64
import datetime
import io
import json
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.cobranca.gateway import efi, servico
from apps.cobranca.gateway.base import GatewayErro
from apps.cobranca.models import Cobranca, ConfiguracaoGateway, EventoGateway
from apps.core.models import Cliente, Empresa, LogAuditoria, Notificacao
from apps.financeiro.models import ContaBancaria, ContaReceber, Movimentacao
from tests.test_gateway import AsaasFalso, Resposta

HOJE = timezone.localdate()
BASE_URL = "https://erp.exemplo.com.br"
CLIENT_ID, SEGREDO_CLIENTE = "Client_Id_abc", "Client_Secret_xyz"


class EfiFalso:
    """Estado mínimo da API Cobranças da Efí."""

    def __init__(self):
        self.cobrancas, self.chamadas, self.autorizacoes, self.falhas, self.avisos = {}, [], 0, {}, {}
        self.n = 0
        self.token_valido = "tok-1"

    # requests.post só é usado em /v1/authorize
    def post(self, url, json=None, timeout=None, headers=None):
        assert url.endswith("/v1/authorize")
        esperado = base64.b64encode(f"{CLIENT_ID}:{SEGREDO_CLIENTE}".encode()).decode()
        self.autorizacoes += 1
        if headers["Authorization"] != f"Basic {esperado}" or json != {"grant_type": "client_credentials"}:
            return Resposta(401, {"error": "invalid_client"})
        self.token_valido = f"tok-{self.autorizacoes}"
        return Resposta(200, {"access_token": self.token_valido, "expires_in": 600, "token_type": "Bearer"})

    def __call__(self, metodo, url, json=None, params=None, timeout=None, headers=None):
        if headers["Authorization"] != f"Bearer {self.token_valido}":
            return Resposta(401, {"error": "unauthorized"})
        caminho = url.split("efipay.com.br", 1)[1]
        self.chamadas.append((metodo, caminho, json))
        for padrao, (status, corpo) in self.falhas.items():
            if padrao in caminho:
                return Resposta(status, corpo)
        if (metodo, caminho) in (("POST", "/v1/charge/one-step"), ("POST", "/v1/charge/one-step/link")):
            self.n += 1
            cid = 5000 + self.n
            total = sum(i["value"] * i["amount"] for i in json["items"])
            c = {"charge_id": cid, "total": total, "custom_id": json["metadata"]["custom_id"], "_req": json,
                 "notification_url": json["metadata"]["notification_url"], "created_at": "2026-11-16 10:00:00"}
            if caminho.endswith("/link"):
                c.update(status="link", payment={"method": "credit_card"}, payment_url=f"https://sandbox.efi/pay/{cid}")
                resp = {"charge_id": cid, "status": "link", "total": total, "payment_url": c["payment_url"]}
            else:
                bb = {"barcode": "00190.00009 02759.288000 21932.978170 1 87890000015000", "link": f"https://sandbox.efi/b/{cid}",
                      "pix": {"qrcode": "00020126580014br.gov.bcb.pix0136abc-efi", "qrcode_image": "data:image/svg+xml;base64,AAA"},
                      "expire_at": json["payment"]["banking_billet"]["expire_at"]}
                c.update(status="waiting", payment={"method": "banking_billet", "banking_billet": bb})
                resp = {"charge_id": cid, "status": "waiting", "total": total, "barcode": bb["barcode"], "link": bb["link"],
                        "billet_link": bb["link"], "pix": bb["pix"], "expire_at": bb["expire_at"]}
            self.cobrancas[cid] = c
            return Resposta(200, {"code": 200, "data": resp})
        partes = caminho.strip("/").split("/")
        if partes[:2] == ["v1", "charge"] and len(partes) >= 3:
            c = self.cobrancas.get(int(partes[2]))
            if c is None:
                return Resposta(404, {"code": 3500034, "error_description": "charge não encontrada"})
            if len(partes) == 4 and partes[3] == "cancel":
                c["status"] = "canceled"
                return Resposta(200, {"code": 200})
            return Resposta(200, {"code": 200, "data": {k: v for k, v in c.items() if not k.startswith("_")}})
        if partes[:2] == ["v1", "notification"]:
            ev = self.avisos.get(partes[2])
            if ev is None:
                return Resposta(404, {"code": 400, "error_description": "notification inválida"})
            return Resposta(200, {"code": 200, "data": ev})
        raise AssertionError((metodo, caminho))

    def pagar(self, cid, **extra):
        self.cobrancas[cid]["status"] = "paid"
        self.cobrancas[cid].update(extra)

    def avisar(self, token, cid, atual="paid", anterior="waiting", n=1, **extra):
        self.avisos[token] = [{"id": n, "type": "charge", "custom_id": self.cobrancas[cid]["custom_id"],
                               "identifiers": {"charge_id": cid}, "status": {"current": atual, "previous": anterior},
                               "created_at": "2026-11-17 09:12:23", **extra}]


@override_settings(ERP_URL_BASE=BASE_URL, COBRANCA_GATEWAY_PRODUCAO=False)
class EfiBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_erp", stdout=io.StringIO())

    def setUp(self):
        cache.clear()
        self.api = EfiFalso()
        for alvo, f in (("requests.request", self.api), ("requests.post", self.api.post)):
            p = mock.patch(f"apps.cobranca.gateway.efi.{alvo}", side_effect=f)
            p.start()
            self.addCleanup(p.stop)
        self.empresa = Empresa.padrao() or Empresa.objects.create(razao_social="Minha Empresa", cnpj="11222333000181")
        self.banco = ContaBancaria.objects.create(nome="Efí", saldo_inicial=Decimal("0"), empresa=self.empresa)
        self.cfg = ConfiguracaoGateway(empresa=self.empresa, conta_bancaria=self.banco, provedor="efi", client_id=CLIENT_ID)
        self.cfg.definir_chave(SEGREDO_CLIENTE)
        self.segredo = self.cfg.novo_token_webhook()
        self.cfg.save()
        self.cli = Cliente.objects.create(nome="Cliente A", documento="11222333000181", email="a@cliente.com",
                                          telefone="(11) 99999-0000")
        self.titulo = ContaReceber.objects.create(cliente=self.cli, descricao="Mensalidade", valor=Decimal("150.00"),
                                                  vencimento=HOJE + datetime.timedelta(days=10), empresa=self.empresa)
        self.adm = User.objects.create_superuser("adm", "a@x.com", "x")

    def emitir(self, tipo="boleto", titulo=None, provedor=""):
        titulo = titulo or self.titulo
        cob = Cobranca.objects.create(conta_receber=titulo, tipo=tipo, valor=titulo.saldo, vencimento=titulo.vencimento)
        servico.emitir(cob, provedor)
        cob.refresh_from_db()
        return cob

    def aviso(self, token="tk1", segredo=None):
        return self.client.post(reverse("cobranca:webhook_efi", args=[segredo or self.segredo]), {"notification": token})


class AutenticacaoEmissaoTests(EfiBase):
    def test_token_oauth_e_reaproveitado_e_renovado_se_expirar(self):
        self.emitir("boleto")
        self.emitir("boleto", titulo=ContaReceber.objects.create(
            cliente=self.cli, descricao="Outra", valor=Decimal("10"), vencimento=HOJE + datetime.timedelta(days=5), empresa=self.empresa))
        self.assertEqual(self.api.autorizacoes, 1)
        self.api.token_valido = "expirou-no-servidor"
        efi.Efi(self.cfg).pagamento(5001)
        self.assertEqual(self.api.autorizacoes, 2)

    def test_credenciais_erradas_dao_mensagem_clara(self):
        self.cfg.client_id = "errado"
        with self.assertRaisesMessage(GatewayErro, "recusou as credenciais"):
            efi.Efi(self.cfg).testar()

    def test_testar_conexao(self):
        self.assertEqual(efi.Efi(self.cfg).testar(), {"ok": True})

    def test_sem_credenciais(self):
        self.cfg.client_id = ""
        with self.assertRaises(GatewayErro):
            efi.Efi(self.cfg)

    def test_boleto_corpo_valor_em_centavos_e_aviso_https(self):
        cob = self.emitir("boleto")
        req = self.api.cobrancas[int(cob.gateway_id)]["_req"]
        self.assertEqual(req["items"], [{"name": "Mensalidade", "value": 15000, "amount": 1}])
        self.assertEqual(req["metadata"]["custom_id"], f"guara-{cob.pk}")
        self.assertEqual(req["metadata"]["notification_url"], f"{BASE_URL}/cobranca/webhook/efi/{self.segredo}/")
        bb = req["payment"]["banking_billet"]
        self.assertEqual(bb["customer"]["juridical_person"]["cnpj"], "11222333000181")
        self.assertEqual(bb["customer"]["phone_number"], "11999990000")
        self.assertEqual(cob.gateway_provedor, "efi")

    def test_cpf_vai_como_pessoa_fisica(self):
        self.cli.documento = "52998224725"
        self.cli.save()
        cob = self.emitir("boleto")
        cli = self.api.cobrancas[int(cob.gateway_id)]["_req"]["payment"]["banking_billet"]["customer"]
        self.assertEqual(cli["cpf"], "52998224725")
        self.assertNotIn("juridical_person", cli)

    def test_boleto_guarda_linha_link_e_qr_pix_png(self):
        cob = self.emitir("boleto")
        self.assertTrue(cob.linha_digitavel.startswith("00190.00009"))
        self.assertTrue(cob.url_boleto.startswith("https://sandbox.efi/b/"))
        self.assertEqual(cob.pix_copia_cola, "00020126580014br.gov.bcb.pix0136abc-efi")
        self.assertEqual(base64.b64decode(cob.pix_qrcode_png)[:8], b"\x89PNG\r\n\x1a\n")

    def test_valores_quebrados_em_centavos(self):
        t = ContaReceber.objects.create(cliente=self.cli, descricao="X", valor=Decimal("1234.56"),
                                        vencimento=HOJE + datetime.timedelta(days=3), empresa=self.empresa)
        cob = self.emitir("boleto", titulo=t)
        self.assertEqual(self.api.cobrancas[int(cob.gateway_id)]["total"], 123456)

    def test_sem_erp_url_base_https_recusa_e_nada_e_criado(self):
        with override_settings(ERP_URL_BASE=""):
            cob = Cobranca.objects.create(conta_receber=self.titulo, tipo="boleto", valor=150, vencimento=self.titulo.vencimento)
            with self.assertRaisesMessage(GatewayErro, "ERP_URL_BASE"):
                servico.emitir(cob)
        self.assertEqual(self.api.cobrancas, {})
        with override_settings(ERP_URL_BASE="http://inseguro.local"):
            with self.assertRaisesMessage(GatewayErro, "HTTPS"):
                servico.emitir(cob)

    def test_pix_puro_nao_e_oferecido_pela_efi(self):
        cob = Cobranca.objects.create(conta_receber=self.titulo, tipo="pix", valor=150, vencimento=self.titulo.vencimento)
        with self.assertRaisesMessage(GatewayErro, "Nenhum gateway ativo desta empresa emite"):
            servico.emitir(cob)

    def test_producao_travada(self):
        self.cfg.ambiente = "producao"
        with self.assertRaisesMessage(GatewayErro, "travada"):
            efi.Efi(self.cfg)
        with override_settings(COBRANCA_GATEWAY_PRODUCAO=True):
            self.assertEqual(efi.Efi(self.cfg).base, "https://cobrancas.api.efipay.com.br")
        self.assertEqual(efi.Efi(ConfiguracaoGateway(provedor="efi", client_id="a", ambiente="sandbox", chave_api=self.cfg.chave_api)).base,
                         "https://cobrancas-h.api.efipay.com.br")

    def test_vencimento_no_passado_e_empurrado_para_hoje(self):
        self.titulo.vencimento = HOJE - datetime.timedelta(days=5)
        self.titulo.save()
        cob = self.emitir("boleto")
        self.assertEqual(cob.vencimento, HOJE)
        self.assertIn("vencimento original", self.api.cobrancas[int(cob.gateway_id)]["_req"]["payment"]["banking_billet"]["message"])


class CartaoTests(EfiBase):
    def test_cartao_efi_usa_link_hospedado_sem_dados_de_cartao(self):
        cob = self.emitir("cartao")
        req = self.api.cobrancas[int(cob.gateway_id)]["_req"]
        self.assertEqual(req["settings"]["payment_method"], "credit_card")
        self.assertEqual(cob.url_pagamento, f"https://sandbox.efi/pay/{cob.gateway_id}")
        texto = json.dumps(req).lower()
        for proibido in ("payment_token", "card_number", "cvv", "credit_card\":"):
            self.assertNotIn(proibido, texto)
        self.assertEqual(cob.linha_digitavel, "")
        self.assertEqual(cob.gateway_status, "link")

    def test_cartao_pago_baixa_como_cartao_de_credito(self):
        cob = self.emitir("cartao")
        self.api.pagar(int(cob.gateway_id))
        self.api.avisar("tkc", int(cob.gateway_id), n=3)
        self.assertEqual(self.aviso("tkc").status_code, 200)
        self.titulo.refresh_from_db()
        self.assertEqual((self.titulo.status, self.titulo.forma_pagamento), ("pago", "cartao_credito"))

    def test_asaas_cartao_cria_cobranca_credit_card_e_guarda_link(self):
        cfg = ConfiguracaoGateway(empresa=self.empresa, conta_bancaria=self.banco, provedor="asaas")
        cfg.definir_chave("$aact_chave")
        cfg.novo_token_webhook()
        cfg.save()
        falso = AsaasFalso()
        with mock.patch("apps.cobranca.gateway.asaas.requests.request", side_effect=falso):
            cob = self.emitir("cartao", provedor="asaas")
        self.assertEqual(cob.gateway_provedor, "asaas")
        self.assertEqual(falso.pagamentos[cob.gateway_id]["billingType"], "CREDIT_CARD")
        self.assertNotIn("fine", falso.pagamentos[cob.gateway_id]["_req"])
        self.assertTrue(cob.url_pagamento.startswith("https://sandbox.asaas.com/i/"))

    def test_asaas_cartao_confirmado_baixa_mas_boleto_confirmado_nao(self):
        cfg = ConfiguracaoGateway(empresa=self.empresa, conta_bancaria=self.banco, provedor="asaas")
        cfg.definir_chave("$aact_chave")
        token = cfg.novo_token_webhook()
        cfg.save()
        falso = AsaasFalso()
        with mock.patch("apps.cobranca.gateway.asaas.requests.request", side_effect=falso):
            cartao = self.emitir("cartao", provedor="asaas")
            outro = ContaReceber.objects.create(cliente=self.cli, descricao="B", valor=Decimal("80"),
                                                vencimento=HOJE + datetime.timedelta(days=4), empresa=self.empresa)
            boleto = self.emitir("boleto", titulo=outro, provedor="asaas")
            falso.pagamentos[cartao.gateway_id].update(status="CONFIRMED", netValue=146.0, confirmedDate=HOJE.isoformat())
            falso.pagamentos[boleto.gateway_id].update(status="CONFIRMED")
            for pid, n in ((cartao.gateway_id, "e1"), (boleto.gateway_id, "e2")):
                corpo = {"id": n, "event": "PAYMENT_CONFIRMED", "payment": {"id": pid}}
                r = self.client.post(reverse("cobranca:webhook_asaas"), json.dumps(corpo), content_type="application/json",
                                     HTTP_ASAAS_ACCESS_TOKEN=token)
                self.assertEqual(r.status_code, 200)
        self.titulo.refresh_from_db()
        outro.refresh_from_db()
        self.assertEqual((self.titulo.status, self.titulo.forma_pagamento), ("pago", "cartao_credito"))
        self.assertNotEqual(outro.status, "pago")
        c = Cobranca.objects.get(pk=cartao.pk)
        self.assertEqual(c.taxa, Decimal("4.00"))               # 150 - 146 líquido


class AvisoEBaixaTests(EfiBase):
    def test_aviso_de_pagamento_baixa_pelo_valor_consultado_e_data_do_evento(self):
        cob = self.emitir("boleto")
        cid = int(cob.gateway_id)
        self.api.pagar(cid)
        self.api.avisar("tk1", cid)
        r = self.aviso("tk1")
        self.assertEqual(r.status_code, 200)
        cob.refresh_from_db()
        self.titulo.refresh_from_db()
        self.assertEqual((cob.status, self.titulo.status, self.titulo.forma_pagamento), ("paga", "pago", "boleto"))
        self.assertEqual(cob.pago_em, datetime.date(2026, 11, 17))
        self.assertEqual(Movimentacao.objects.filter(conta_bancaria=self.banco, valor=Decimal("150.00")).count(), 1)
        self.assertEqual(self.banco.saldo_atual if hasattr(self.banco, "saldo_atual") else Decimal("150"), Decimal("150"))
        self.assertTrue(Notificacao.objects.filter(texto__contains="baixado automaticamente").exists())
        self.assertTrue(LogAuditoria.objects.filter(descricao__startswith="Baixa automática pelo gateway").exists())

    def test_o_mesmo_aviso_nao_baixa_duas_vezes(self):
        cob = self.emitir("boleto")
        cid = int(cob.gateway_id)
        self.api.pagar(cid)
        self.api.avisar("tk1", cid)
        self.aviso("tk1")
        self.aviso("tk1")
        self.assertEqual(Movimentacao.objects.filter(conta_bancaria=self.banco).count(), 1)
        self.assertEqual(EventoGateway.objects.filter(config=self.cfg).count(), 1)

    def test_aviso_dizendo_pago_mas_a_api_diz_aguardando_nao_baixa(self):
        cob = self.emitir("boleto")
        cid = int(cob.gateway_id)
        self.api.avisar("tk-mentiroso", cid, atual="paid")          # a consulta à cobrança ainda diz 'waiting'
        self.assertEqual(self.aviso("tk-mentiroso").status_code, 200)
        self.titulo.refresh_from_db()
        self.assertNotEqual(self.titulo.status, "pago")
        self.assertEqual(Movimentacao.objects.filter(conta_bancaria=self.banco).count(), 0)

    def test_segredo_errado_recusado_sem_chamar_a_efi(self):
        antes = len(self.api.chamadas)
        r = self.aviso("tk", segredo="segredo-inventado")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(len(self.api.chamadas), antes)
        self.assertTrue(LogAuditoria.objects.filter(descricao__startswith="Aviso da Efí recusado").exists())

    def test_token_de_aviso_inexistente_e_ignorado_com_200(self):
        self.assertEqual(self.aviso("token-forjado").status_code, 200)
        self.assertEqual(EventoGateway.objects.count(), 0)

    def test_efi_fora_do_ar_pede_nova_tentativa(self):
        self.api.falhas["/v1/notification/"] = (503, {})
        self.assertEqual(self.aviso("tk").status_code, 500)

    def test_aviso_sem_token_ou_por_get(self):
        r = self.client.post(reverse("cobranca:webhook_efi", args=[self.segredo]), {})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.client.get(reverse("cobranca:webhook_efi", args=[self.segredo])).status_code, 405)

    def test_referencia_que_nao_confere_nao_baixa_e_libera_reenvio(self):
        cob = self.emitir("boleto")
        cid = int(cob.gateway_id)
        self.api.pagar(cid)
        self.api.cobrancas[cid]["custom_id"] = "guara-999999"
        self.api.avisar("tk1", cid)
        self.assertEqual(self.aviso("tk1").status_code, 500)
        self.assertEqual(EventoGateway.objects.count(), 0)
        self.titulo.refresh_from_db()
        self.assertNotEqual(self.titulo.status, "pago")

    def test_cobranca_cancelada_na_efi_cancela_no_erp(self):
        cob = self.emitir("boleto")
        cid = int(cob.gateway_id)
        self.api.cobrancas[cid]["status"] = "canceled"
        self.api.avisar("tk1", cid, atual="canceled")
        self.aviso("tk1")
        cob.refresh_from_db()
        self.assertEqual(cob.status, "cancelada")

    def test_baixa_manual_no_painel_da_efi_so_avisa_o_financeiro(self):
        cob = self.emitir("boleto")
        cid = int(cob.gateway_id)
        self.api.cobrancas[cid]["status"] = "settled"
        self.api.avisar("tk1", cid, atual="settled")
        self.aviso("tk1")
        self.titulo.refresh_from_db()
        self.assertNotEqual(self.titulo.status, "pago")
        self.assertTrue(Notificacao.objects.filter(texto__contains="baixa manual").exists())

    def test_estorno_so_avisa(self):
        cob = self.emitir("boleto")
        cid = int(cob.gateway_id)
        self.api.pagar(cid)
        self.api.avisar("tk1", cid)
        self.aviso("tk1")
        self.api.cobrancas[cid]["status"] = "refunded"
        self.api.avisar("tk2", cid, atual="refunded", anterior="paid", n=2)
        self.aviso("tk2")
        self.titulo.refresh_from_db()
        self.assertEqual(self.titulo.status, "pago")                     # não desfaz sozinho
        self.assertTrue(Notificacao.objects.filter(texto__contains="NÃO foi desfeita").exists())

    def test_aviso_de_cobranca_desconhecida_e_de_outra_empresa(self):
        self.api.cobrancas[777] = {"charge_id": 777, "total": 100, "custom_id": "guara-1", "status": "paid"}
        self.api.avisar("tkx", 777)
        self.aviso("tkx")
        self.assertEqual(EventoGateway.objects.get().resultado, "cobrança desconhecida")


class ConciliacaoTests(EfiBase):
    def test_sincronizar_baixa_pagamento_cujo_aviso_se_perdeu(self):
        cob = self.emitir("boleto")
        self.api.pagar(int(cob.gateway_id))
        self.assertEqual(servico.sincronizar()["baixadas"], 1)
        self.titulo.refresh_from_db()
        self.assertEqual(self.titulo.status, "pago")
        self.assertEqual(servico.sincronizar()["baixadas"], 0)

    def test_cancelar_no_gateway_chama_a_efi(self):
        cob = self.emitir("boleto")
        servico.cancelar_no_gateway(cob)
        self.assertEqual(self.api.cobrancas[int(cob.gateway_id)]["status"], "canceled")
        cob.refresh_from_db()
        self.assertEqual(cob.status, "cancelada")

    def test_dois_gateways_na_empresa_cada_cobranca_volta_ao_seu(self):
        asaas_cfg = ConfiguracaoGateway(empresa=self.empresa, conta_bancaria=self.banco, provedor="asaas")
        asaas_cfg.definir_chave("$aact")
        asaas_cfg.novo_token_webhook()
        asaas_cfg.save()
        cob = self.emitir("boleto", provedor="efi")
        self.api.pagar(int(cob.gateway_id))
        from apps.cobranca.gateway.asaas import Asaas
        with mock.patch.object(Asaas, "_req", side_effect=AssertionError("o Asaas não deveria ser consultado")):
            servico.sincronizar()
        self.titulo.refresh_from_db()
        self.assertEqual(self.titulo.status, "pago")

    def test_consultar_pela_tela(self):
        cob = self.emitir("boleto")
        self.api.pagar(int(cob.gateway_id))
        self.client.force_login(self.adm)
        self.client.post(reverse("cobranca:atualizar_do_gateway", args=[cob.pk]))
        self.titulo.refresh_from_db()
        self.assertEqual(self.titulo.status, "pago")


class TelasTests(EfiBase):
    def test_tela_de_gateways_mostra_endereco_de_aviso_da_efi(self):
        self.client.force_login(self.adm)
        r = self.client.get(reverse("cobranca:gateways"))
        self.assertContains(r, f"{BASE_URL}/cobranca/webhook/efi/{self.segredo}/")
        self.assertContains(r, "não há webhook para cadastrar")

    def test_testar_chave_pela_tela(self):
        self.client.force_login(self.adm)
        r = self.client.post(reverse("cobranca:gateway_testar", args=[self.cfg.pk]), follow=True)
        self.assertContains(r, "Conexão com o gateway OK")

    def test_gerar_oferece_boleto_e_cartao_da_efi_mas_nao_pix(self):
        self.client.force_login(self.adm)
        r = self.client.get(reverse("cobranca:gerar_cobranca", args=[self.titulo.pk]))
        self.assertContains(r, "Boleto registrado — Efí")
        self.assertContains(r, "Cartão de crédito (link de pagamento) — Efí")
        self.assertNotContains(r, "PIX com QR Code —")

    def test_gerar_cartao_pela_tela(self):
        self.client.force_login(self.adm)
        r = self.client.post(reverse("cobranca:gerar_cobranca", args=[self.titulo.pk]), {"opcao": "cartao|efi"})
        cob = Cobranca.objects.get(conta_receber=self.titulo)
        self.assertRedirects(r, cob.get_absolute_url(), fetch_redirect_response=False)
        self.assertEqual((cob.tipo, cob.gateway_provedor), ("cartao", "efi"))

    def test_formulario_exige_client_id_da_efi(self):
        from django.core.exceptions import ValidationError
        c = ConfiguracaoGateway(empresa=self.empresa, conta_bancaria=self.banco, provedor="efi", client_id="")
        with self.assertRaises(ValidationError):
            c.clean()
