"""Gateway de pagamento (Asaas simulado): emissão de boleto/PIX, webhook seguro, baixa automática e conciliação."""
import io
import json
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.cobranca.gateway import asaas, servico
from apps.cobranca.models import Cobranca, ConfiguracaoGateway, EventoGateway
from apps.core.models import Cliente, Empresa, LogAuditoria, Notificacao
from apps.financeiro.models import ContaBancaria, ContaReceber, Movimentacao

HOJE = timezone.localdate()
TOKEN_HEADER = "HTTP_ASAAS_ACCESS_TOKEN"


class Resposta:
    def __init__(self, status, corpo):
        self.status_code, self._corpo = status, corpo

    def json(self):
        return self._corpo


class AsaasFalso:
    """Estado mínimo do Asaas: clientes e cobranças. Troca o requests.request do cliente."""

    def __init__(self):
        self.pagamentos, self.clientes, self.chamadas, self.falhas = {}, {}, [], {}
        self.n = 0

    def __call__(self, metodo, url, json=None, params=None, timeout=None, headers=None):
        assert headers["access_token"] and headers["User-Agent"]
        caminho = url.split("/v3", 1)[1]
        self.chamadas.append((metodo, caminho, json))
        for padrao, (status, corpo) in self.falhas.items():
            if padrao in caminho:
                return Resposta(status, corpo)
        if (metodo, caminho) == ("GET", "/customers"):
            return Resposta(200, {"data": []})
        if (metodo, caminho) == ("POST", "/customers"):
            self.n += 1
            cid = f"cus_{self.n}"
            self.clientes[cid] = json
            return Resposta(200, {"id": cid})
        if (metodo, caminho) == ("POST", "/payments"):
            self.n += 1
            pid = f"pay_{self.n}"
            self.pagamentos[pid] = {"object": "payment", "id": pid, "customer": json["customer"], "status": "PENDING",
                                    "billingType": json["billingType"], "value": json["value"], "dueDate": json["dueDate"],
                                    "externalReference": json["externalReference"], "invoiceUrl": f"https://sandbox.asaas.com/i/{pid}",
                                    "bankSlipUrl": f"https://sandbox.asaas.com/b/{pid}" if json["billingType"] == "BOLETO" else None,
                                    "nossoNumero": str(1000 + self.n), "deleted": False, "_req": json}
            return Resposta(200, self.pagamentos[pid])
        partes = caminho.strip("/").split("/")
        if partes[0] == "payments" and len(partes) >= 2:
            p = self.pagamentos.get(partes[1])
            if p is None:
                return Resposta(404, {"errors": [{"code": "invalid_object", "description": "Cobrança não encontrada"}]})
            if len(partes) == 3 and partes[2] == "identificationField":
                return Resposta(200, {"identificationField": "00190000090275928800021932978170187890000005000", "nossoNumero": p["nossoNumero"]})
            if len(partes) == 3 and partes[2] == "pixQrCode":
                return Resposta(200, {"encodedImage": "iVBORw0KGgo=", "payload": "00020126580014br.gov.bcb.pix0136abc", "expirationDate": "2027-01-01"})
            if metodo == "DELETE":
                p["deleted"] = True
                return Resposta(200, {"deleted": True, "id": p["id"]})
            return Resposta(200, p)
        raise AssertionError((metodo, caminho))

    def pagar(self, pid, valor=None, liquido=None, **extra):
        p = self.pagamentos[pid]
        valor = float(valor if valor is not None else p["value"])
        p.update(status="RECEIVED", value=valor, netValue=float(liquido if liquido is not None else valor - 1.99),
                 paymentDate=HOJE.isoformat(), transactionReceiptUrl=f"https://sandbox.asaas.com/r/{pid}", **extra)


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_erp", stdout=io.StringIO())

    def setUp(self):
        self.api = AsaasFalso()
        from unittest import mock

        p = mock.patch("apps.cobranca.gateway.asaas.requests.request", side_effect=self.api)
        p.start()
        self.addCleanup(p.stop)
        self.empresa = Empresa.padrao() or Empresa.objects.create(razao_social="Minha Empresa", cnpj="11222333000181")
        self.banco = ContaBancaria.objects.create(nome="Asaas", saldo_inicial=Decimal("0"), empresa=self.empresa)
        self.cfg = ConfiguracaoGateway(empresa=self.empresa, conta_bancaria=self.banco)
        self.cfg.definir_chave("$aact_hmlg_chave-secreta-de-teste")
        self.token = self.cfg.novo_token_webhook()
        self.cfg.save()
        self.cli = Cliente.objects.create(nome="Cliente A", documento="11222333000181", email="a@cliente.com", telefone="(11) 99999-0000")
        self.titulo = ContaReceber.objects.create(cliente=self.cli, descricao="Mensalidade", valor=Decimal("150.00"),
                                                  vencimento=HOJE + __import__("datetime").timedelta(days=10), empresa=self.empresa)
        self.adm = User.objects.create_superuser("adm", "a@x.com", "x")

    def emitir(self, tipo="pix", titulo=None):
        titulo = titulo or self.titulo
        cob = Cobranca.objects.create(conta_receber=titulo, tipo=tipo, valor=titulo.saldo, vencimento=titulo.vencimento)
        servico.emitir(cob)
        cob.refresh_from_db()
        return cob

    def webhook(self, evento, pid, token=None, evento_id="evt_1", **extra):
        corpo = {"id": evento_id, "event": evento, "dateCreated": "2026-11-16 10:00:00", "payment": {"object": "payment", "id": pid, **extra}}
        return self.client.post(reverse("cobranca:webhook_asaas"), data=json.dumps(corpo), content_type="application/json",
                                **{TOKEN_HEADER: self.token if token is None else token})


class EmissaoTests(Base):
    def test_boleto_guarda_linha_digitavel_url_e_nosso_numero(self):
        cob = self.emitir("boleto")
        self.assertTrue(cob.gateway_id.startswith("pay_"))
        self.assertEqual(cob.linha_digitavel, "00190000090275928800021932978170187890000005000")
        self.assertTrue(cob.url_boleto.startswith("https://sandbox.asaas.com/b/"))
        self.assertTrue(cob.url_pagamento)
        self.assertEqual(cob.nosso_numero, self.api.pagamentos[cob.gateway_id]["nossoNumero"])
        req = self.api.pagamentos[cob.gateway_id]["_req"]
        self.assertEqual((req["billingType"], req["value"], req["externalReference"]), ("BOLETO", 150.0, f"guara-{cob.pk}"))
        self.assertEqual((req["fine"], req["interest"]), ({"value": 2.0}, {"value": 1.0}))

    def test_pix_dinamico_substitui_o_estatico_e_guarda_o_qr(self):
        self.empresa.chave_pix = "empresa@pix.com"
        self.empresa.save()
        cob = self.emitir("pix")
        self.assertEqual(cob.pix_copia_cola, "00020126580014br.gov.bcb.pix0136abc")
        self.assertEqual(cob.pix_qrcode_png, "iVBORw0KGgo=")

    def test_cliente_e_criado_uma_vez_com_documento_so_digitos(self):
        self.emitir("pix")
        t2 = ContaReceber.objects.create(cliente=self.cli, descricao="Outra", valor=Decimal("10"), vencimento=HOJE, empresa=self.empresa)
        self.emitir("boleto", t2)
        criacoes = [c for c in self.api.chamadas if c[:2] == ("POST", "/customers")]
        self.assertEqual(len(criacoes), 1)
        self.assertEqual(criacoes[0][2]["cpfCnpj"], "11222333000181")

    def test_vencimento_no_passado_vira_hoje_e_registra_o_original(self):
        t = ContaReceber.objects.create(cliente=self.cli, descricao="Atrasada", valor=Decimal("10"),
                                        vencimento=HOJE - __import__("datetime").timedelta(days=5), empresa=self.empresa)
        cob = self.emitir("boleto", t)
        self.assertEqual(cob.vencimento, HOJE)
        self.assertIn("vencimento original", self.api.pagamentos[cob.gateway_id]["_req"]["description"])

    def test_cliente_sem_documento_e_recusado_sem_chamar_o_gateway(self):
        self.cli.documento = ""
        self.cli.save()
        cob = Cobranca.objects.create(conta_receber=self.titulo, tipo="boleto", valor=150, vencimento=HOJE)
        with self.assertRaisesMessage(asaas.GatewayErro, "CPF ou CNPJ"):
            servico.emitir(cob)
        self.assertEqual([c for c in self.api.chamadas if c[1] == "/payments"], [])

    def test_erro_da_api_vira_mensagem_e_chave_invalida_e_explicada(self):
        self.api.falhas["/payments"] = (400, {"errors": [{"code": "invalid_dueDate", "description": "Data de vencimento inválida"}]})
        cob = Cobranca.objects.create(conta_receber=self.titulo, tipo="boleto", valor=150, vencimento=HOJE)
        with self.assertRaisesMessage(asaas.GatewayErro, "Data de vencimento inválida"):
            servico.emitir(cob)
        self.api.falhas = {"/customers": (401, {"errors": [{"code": "invalid_access_token", "description": "x"}]})}
        self.cli2 = Cliente.objects.create(nome="B", documento="52998224725")
        with self.assertRaisesMessage(asaas.GatewayErro, "recusou a chave"):
            asaas.Asaas(self.cfg).testar()

    def test_nao_emite_duas_vezes(self):
        cob = self.emitir("pix")
        with self.assertRaises(asaas.GatewayErro):
            servico.emitir(cob)

    def test_falha_ao_buscar_o_qr_nao_apaga_o_que_ja_existe_no_gateway(self):
        self.api.falhas["pixQrCode"] = (500, {})
        cob = Cobranca.objects.create(conta_receber=self.titulo, tipo="pix", valor=150, vencimento=HOJE)
        with self.assertRaises(asaas.GatewayParcial):
            servico.emitir(cob)
        cob.refresh_from_db()
        self.assertTrue(cob.gateway_id)
        self.api.falhas = {}
        servico.atualizar_dados(cob)
        self.assertEqual(cob.pix_qrcode_png, "iVBORw0KGgo=")

    @override_settings(COBRANCA_GATEWAY_PRODUCAO=False)
    def test_producao_travada(self):
        self.cfg.ambiente = "producao"
        with self.assertRaises(ValidationError):
            self.cfg.clean()
        self.cfg.save()
        with self.assertRaisesMessage(asaas.GatewayErro, "travada"):
            asaas.Asaas(self.cfg)
        with override_settings(COBRANCA_GATEWAY_PRODUCAO=True):
            self.cfg.clean()
            self.assertEqual(asaas.Asaas(self.cfg).base, "https://api.asaas.com/v3")

    def test_chave_e_token_cifrados_e_fora_da_auditoria(self):
        self.assertNotIn(b"chave-secreta", bytes(ConfiguracaoGateway.objects.get(pk=self.cfg.pk).chave_api))
        self.assertNotIn(self.token.encode(), bytes(ConfiguracaoGateway.objects.get(pk=self.cfg.pk).token_webhook))
        self.assertGreaterEqual(len(self.token), 32)
        self.cfg.multa_pct = Decimal("3")
        self.cfg.save()
        todo = " ".join(f"{l.descricao} {l.alteracoes}" for l in LogAuditoria.objects.all())
        self.assertNotIn("chave-secreta", todo)
        self.assertNotIn(self.token, todo)


class WebhookTests(Base):
    def test_token_ausente_ou_errado_e_recusado_e_nao_baixa_nada(self):
        cob = self.emitir("pix")
        self.api.pagar(cob.gateway_id)
        for token in ("", "errado", self.token + "x", self.token[:-1]):
            r = self.webhook("PAYMENT_RECEIVED", cob.gateway_id, token=token)
            self.assertEqual(r.status_code, 401)
        self.titulo.refresh_from_db()
        self.assertEqual(self.titulo.status, "aberto")
        self.assertTrue(LogAuditoria.objects.filter(modelo="cobranca.webhook").exists())
        self.assertEqual(EventoGateway.objects.count(), 0)

    def test_so_aceita_post_e_corpo_valido(self):
        self.assertEqual(self.client.get(reverse("cobranca:webhook_asaas")).status_code, 405)
        r = self.client.post(reverse("cobranca:webhook_asaas"), data="não é json", content_type="application/json", **{TOKEN_HEADER: self.token})
        self.assertEqual(r.status_code, 400)
        r = self.client.post(reverse("cobranca:webhook_asaas"), data=json.dumps({"event": "X"}), content_type="application/json", **{TOKEN_HEADER: self.token})
        self.assertEqual(r.status_code, 400)       # sem id do evento

    def test_pagamento_baixa_titulo_cria_movimentacao_e_marca_a_cobranca(self):
        cob = self.emitir("pix")
        self.api.pagar(cob.gateway_id, liquido=148.01)
        r = self.webhook("PAYMENT_RECEIVED", cob.gateway_id)
        self.assertEqual(r.status_code, 200)
        self.titulo.refresh_from_db()
        cob.refresh_from_db()
        self.assertEqual((self.titulo.status, self.titulo.valor_pago, self.titulo.forma_pagamento), ("pago", Decimal("150.00"), "pix"))
        self.assertEqual((cob.status, cob.pago_em, cob.valor_liquido, cob.taxa), ("paga", HOJE, Decimal("148.01"), Decimal("1.99")))
        self.assertEqual(cob.url_recibo, f"https://sandbox.asaas.com/r/{cob.gateway_id}")
        entrada = Movimentacao.objects.get(contareceber=self.titulo)
        self.assertEqual((entrada.valor, entrada.conta_bancaria), (Decimal("150.00"), self.banco))
        tarifa = Movimentacao.objects.get(tipo="saida")
        self.assertEqual(tarifa.valor, Decimal("1.99"))
        self.assertEqual(self.banco.saldo_atual, Decimal("148.01"))             # igual ao repasse real
        self.assertTrue(LogAuditoria.objects.filter(descricao__startswith="Baixa automática pelo gateway").exists())
        self.assertTrue(Notificacao.objects.filter(texto__startswith="Recebido R$ 150.00").exists())

    def test_tarifa_pode_nao_ser_lancada(self):
        self.cfg.lancar_tarifa = False
        self.cfg.save()
        cob = self.emitir("boleto")
        self.api.pagar(cob.gateway_id)
        self.webhook("PAYMENT_RECEIVED", cob.gateway_id)
        self.assertFalse(Movimentacao.objects.filter(tipo="saida").exists())

    def test_reenvio_do_mesmo_evento_nao_baixa_duas_vezes(self):
        cob = self.emitir("pix")
        self.api.pagar(cob.gateway_id)
        self.assertEqual(self.webhook("PAYMENT_RECEIVED", cob.gateway_id, evento_id="evt_A").status_code, 200)
        r = self.webhook("PAYMENT_RECEIVED", cob.gateway_id, evento_id="evt_A")
        self.assertEqual(r.json().get("duplicado"), True)
        # evento novo para o mesmo pagamento também não duplica a baixa
        self.webhook("PAYMENT_RECEIVED", cob.gateway_id, evento_id="evt_B")
        self.assertEqual(Movimentacao.objects.filter(contareceber=self.titulo).count(), 1)
        self.assertEqual(EventoGateway.objects.filter(tipo="PAYMENT_RECEIVED").count(), 2)

    def test_aviso_forjado_sem_pagamento_real_nao_baixa(self):
        """Mesmo com o token certo, o ERP confere o pagamento na API: ainda PENDING = nada acontece."""
        cob = self.emitir("pix")
        r = self.webhook("PAYMENT_RECEIVED", cob.gateway_id, status="RECEIVED", value=150.0)
        self.assertEqual(r.status_code, 200)
        self.titulo.refresh_from_db()
        self.assertEqual(self.titulo.status, "aberto")
        self.assertIn("sem baixa", EventoGateway.objects.get().resultado)

    def test_valor_vem_da_api_e_nao_do_aviso(self):
        cob = self.emitir("pix")
        self.api.pagar(cob.gateway_id)
        self.webhook("PAYMENT_RECEIVED", cob.gateway_id, value=1.0, status="RECEIVED")      # aviso diz R$ 1,00
        self.titulo.refresh_from_db()
        self.assertEqual(self.titulo.valor_pago, Decimal("150.00"))

    def test_pagamento_de_outra_cobranca_ou_referencia_trocada_nao_baixa(self):
        cob = self.emitir("pix")
        self.api.pagar(cob.gateway_id)
        self.api.pagamentos[cob.gateway_id]["externalReference"] = "guara-9999"
        r = self.webhook("PAYMENT_RECEIVED", cob.gateway_id)
        self.assertEqual(r.status_code, 500)             # o gateway tentará de novo; nada foi baixado
        self.titulo.refresh_from_db()
        self.assertEqual(self.titulo.status, "aberto")
        self.assertEqual(EventoGateway.objects.count(), 0)    # liberado para o reenvio
        r = self.webhook("PAYMENT_RECEIVED", "pay_desconhecido", evento_id="evt_2")
        self.assertEqual(r.status_code, 200)

    def test_falha_ao_consultar_a_api_devolve_500_e_permite_reenvio(self):
        cob = self.emitir("pix")
        self.api.pagar(cob.gateway_id)
        self.api.falhas[f"/payments/{cob.gateway_id}"] = (500, {})
        self.assertEqual(self.webhook("PAYMENT_RECEIVED", cob.gateway_id).status_code, 500)
        self.api.falhas = {}
        self.assertEqual(self.webhook("PAYMENT_RECEIVED", cob.gateway_id).status_code, 200)
        self.titulo.refresh_from_db()
        self.assertEqual(self.titulo.status, "pago")

    def test_pagamento_com_multa_e_juros_vira_juros_do_titulo(self):
        cob = self.emitir("boleto")
        self.api.pagar(cob.gateway_id, valor=153.50, liquido=151.51)
        self.webhook("PAYMENT_RECEIVED", cob.gateway_id)
        self.titulo.refresh_from_db()
        self.assertEqual((self.titulo.status, self.titulo.juros, self.titulo.valor_pago), ("pago", Decimal("3.50"), Decimal("153.50")))

    def test_titulo_ja_pago_ou_cancelado_nao_e_baixado_e_avisa_o_financeiro(self):
        cob = self.emitir("pix")
        self.titulo.baixar(Decimal("150"), HOJE, self.banco, "dinheiro")
        self.api.pagar(cob.gateway_id)
        self.webhook("PAYMENT_RECEIVED", cob.gateway_id)
        self.assertEqual(Movimentacao.objects.filter(contareceber=self.titulo).count(), 1)
        self.assertTrue(Notificacao.objects.filter(usuario=self.adm, texto__startswith="ATENÇÃO: o gateway recebeu").exists())
        cob.refresh_from_db()
        self.assertIsNone(cob.pago_em)

    def test_cancelamento_vencimento_e_estorno(self):
        cob = self.emitir("pix")
        self.webhook("PAYMENT_OVERDUE", cob.gateway_id, evento_id="e1")
        cob.refresh_from_db()
        self.assertEqual(cob.gateway_status, "OVERDUE")
        self.webhook("PAYMENT_REFUNDED", cob.gateway_id, evento_id="e2")
        self.assertTrue(Notificacao.objects.filter(texto__contains="PAYMENT_REFUNDED").exists())
        self.webhook("PAYMENT_DELETED", cob.gateway_id, evento_id="e3")
        cob.refresh_from_db()
        self.assertEqual((cob.status, cob.gateway_status), ("cancelada", "DELETED"))

    def test_estorno_nao_desfaz_a_baixa_sozinho(self):
        cob = self.emitir("pix")
        self.api.pagar(cob.gateway_id)
        self.webhook("PAYMENT_RECEIVED", cob.gateway_id, evento_id="e1")
        self.webhook("PAYMENT_REFUNDED", cob.gateway_id, evento_id="e2")
        self.titulo.refresh_from_db()
        self.assertEqual(self.titulo.status, "pago")

    def test_token_de_outra_empresa_nao_baixa_titulo_desta(self):
        outra = Empresa.objects.create(razao_social="Outra", cnpj="45723174000110")
        banco2 = ContaBancaria.objects.create(nome="B2", empresa=outra)
        cfg2 = ConfiguracaoGateway(empresa=outra, conta_bancaria=banco2)
        cfg2.definir_chave("k2")
        t2 = cfg2.novo_token_webhook()
        cfg2.save()
        cob = self.emitir("pix")
        self.api.pagar(cob.gateway_id)
        r = self.webhook("PAYMENT_RECEIVED", cob.gateway_id, token=t2)
        self.assertEqual(r.status_code, 200)
        self.assertIn("desconhecida", EventoGateway.objects.get().resultado)
        self.titulo.refresh_from_db()
        self.assertEqual(self.titulo.status, "aberto")


class ConciliacaoTests(Base):
    def test_webhook_perdido_e_recuperado_pela_sincronizacao(self):
        cob = self.emitir("pix")
        self.api.pagar(cob.gateway_id)
        r = servico.sincronizar()
        self.assertEqual(r["baixadas"], 1)
        self.titulo.refresh_from_db()
        self.assertEqual(self.titulo.status, "pago")
        self.assertEqual(servico.sincronizar()["baixadas"], 0)          # já conciliado

    def test_cobranca_cancelada_no_erp_e_cancelada_no_gateway(self):
        cob = self.emitir("boleto")
        self.titulo.cancelar()                      # o sinal marca a cobrança como cancelada só localmente
        cob.refresh_from_db()
        self.assertEqual(cob.status, "cancelada")
        self.assertFalse(self.api.pagamentos[cob.gateway_id]["deleted"])
        r = servico.sincronizar()
        self.assertTrue(self.api.pagamentos[cob.gateway_id]["deleted"])
        self.assertEqual(r["canceladas"], 1)

    def test_cancelar_pela_tela_chama_o_gateway(self):
        cob = self.emitir("boleto")
        servico.cancelar_no_gateway(cob)
        self.assertTrue(self.api.pagamentos[cob.gateway_id]["deleted"])
        self.assertEqual(cob.status, "cancelada")

    def test_nao_cancela_cobranca_paga(self):
        cob = self.emitir("pix")
        self.api.pagar(cob.gateway_id)
        servico.sincronizar()
        cob.refresh_from_db()
        with self.assertRaises(asaas.GatewayErro):
            servico.cancelar_no_gateway(cob)

    def test_comando_e_rotina_diaria(self):
        cob = self.emitir("pix")
        self.api.pagar(cob.gateway_id)
        out = io.StringIO()
        call_command("cobranca_sincronizar", stdout=out)
        self.assertIn("1 baixa", out.getvalue())

    def test_sincronizacao_tolera_erro_do_gateway(self):
        self.emitir("pix")
        self.api.falhas["/payments/"] = (500, {})
        self.assertEqual(servico.sincronizar()["erros"], 1)


class TelasTests(Base):
    def test_gerar_boleto_e_pix_pelo_gateway_e_fallback_estatico(self):
        self.client.force_login(self.adm)
        url = reverse("cobranca:gerar_cobranca", args=[self.titulo.pk])
        self.assertContains(self.client.get(url), "baixa automática")
        r = self.client.post(url, {"tipo": "boleto"})
        cob = Cobranca.objects.get()
        self.assertRedirects(r, cob.get_absolute_url(), fetch_redirect_response=False)
        self.assertTrue(cob.gateway_id)
        self.assertContains(self.client.get(cob.get_absolute_url()), "Linha digitável")
        r = self.client.post(url, {"tipo": "pix_estatico"})
        self.assertEqual(Cobranca.objects.filter(gateway_id="").count(), 1)
        ConfiguracaoGateway.objects.update(ativo=False)
        self.assertContains(self.client.get(url), "PIX estático")

    def test_falha_do_gateway_nao_deixa_cobranca_orfa(self):
        self.client.force_login(self.adm)
        self.api.falhas["/payments"] = (400, {"errors": [{"code": "x", "description": "Valor mínimo é R$ 5,00"}]})
        r = self.client.post(reverse("cobranca:gerar_cobranca", args=[self.titulo.pk]), {"tipo": "pix"}, follow=True)
        self.assertContains(r, "Valor mínimo")
        self.assertEqual(Cobranca.objects.count(), 0)

    def test_configuracao_so_para_superusuario_e_nao_vaza_a_chave(self):
        comum = User.objects.create_user("comum", password="x")
        self.client.force_login(comum)
        self.assertEqual(self.client.get(reverse("cobranca:gateways")).status_code, 302)
        self.client.force_login(self.adm)
        r = self.client.get(reverse("cobranca:gateways"))
        self.assertContains(r, self.token)                   # o token precisa ser copiado para o painel do gateway
        self.assertNotContains(r, "chave-secreta-de-teste")
        self.assertNotContains(self.client.get(reverse("cobranca:gateway_editar", args=[self.cfg.pk])), "chave-secreta-de-teste")
        r = self.client.post(reverse("cobranca:gateway_testar", args=[self.cfg.pk]), follow=True)
        self.assertContains(r, "Conexão com o gateway OK")

    def test_cadastro_pela_tela_cifra_e_gera_token(self):
        self.client.force_login(self.adm)
        ConfiguracaoGateway.objects.all().delete()
        r = self.client.post(reverse("cobranca:gateway_novo"), {
            "empresa": self.empresa.pk, "provedor": "asaas", "ambiente": "sandbox", "ativo": "on", "chave": "$aact_hmlg_nova",
            "conta_bancaria": self.banco.pk, "multa_pct": "2", "juros_mes_pct": "1", "lancar_tarifa": "on"})
        self.assertEqual(r.status_code, 302, getattr(r, "context", None) and r.context["form"].errors)
        cfg = ConfiguracaoGateway.objects.get()
        self.assertEqual(cfg.obter_chave(), "$aact_hmlg_nova")
        self.assertGreaterEqual(len(cfg.obter_token()), 32)
        r = self.client.post(reverse("cobranca:gateway_novo"), {
            "empresa": self.empresa.pk, "provedor": "asaas", "ambiente": "producao", "chave": "x", "conta_bancaria": self.banco.pk,
            "multa_pct": "2", "juros_mes_pct": "1"})
        self.assertEqual(r.status_code, 200)               # produção travada

    def test_novo_token_invalida_o_antigo(self):
        self.client.force_login(self.adm)
        antigo = self.token
        self.client.post(reverse("cobranca:gateway_novo_token", args=[self.cfg.pk]))
        self.assertEqual(self.webhook("PAYMENT_OVERDUE", "pay_1", token=antigo).status_code, 401)
