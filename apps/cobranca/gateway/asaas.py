# Guará ERP — gestão empresarial livre
# Copyright (C) 2026 Raldiney Santos e colaboradores
# SPDX-License-Identifier: GPL-3.0-or-later
"""Cliente da API v3 do Asaas (boleto e PIX). Autenticação pelo header access_token; o sandbox e a produção têm
chaves e URLs diferentes. Nenhum dado de cartão passa por aqui."""
import datetime
from decimal import Decimal

import requests
from django.conf import settings
from django.utils import timezone

from apps.core.validators import somente_digitos

from .base import ABERTO, CANCELADO, ESTORNADO, OUTRO, PAGO, GatewayErro, GatewayParcial, Pagamento  # noqa: F401

BASES = {"sandbox": "https://api-sandbox.asaas.com/v3", "producao": "https://api.asaas.com/v3"}
TIMEOUT = 20


class Asaas:
    TIPOS = ("pix", "boleto", "cartao")

    def __init__(self, config):
        if config.ambiente == "producao" and not getattr(settings, "COBRANCA_GATEWAY_PRODUCAO", False):
            raise GatewayErro("Produção do gateway está travada (COBRANCA_GATEWAY_PRODUCAO).")
        self.base = BASES[config.ambiente]
        self.chave = config.obter_chave()
        if not self.chave:
            raise GatewayErro("A chave da API do gateway não foi informada.")

    def _req(self, metodo, caminho, json=None, params=None):
        try:
            r = requests.request(metodo, self.base + caminho, json=json, params=params, timeout=TIMEOUT, headers={
                "access_token": self.chave, "Content-Type": "application/json", "User-Agent": "GuaraERP/1.0"})
        except requests.RequestException as exc:
            raise GatewayErro(f"não foi possível falar com o gateway: {exc.__class__.__name__}") from exc
        try:
            corpo = r.json()
        except ValueError:
            corpo = {}
        if r.status_code >= 400:
            erros = corpo.get("errors") or []
            msg = "; ".join(e.get("description", "") for e in erros if isinstance(e, dict))[:300]
            if r.status_code == 401:
                msg = "o gateway recusou a chave da API (confira a chave e o ambiente)"
            raise GatewayErro(msg or f"o gateway respondeu {r.status_code}", r.status_code)
        return corpo

    def testar(self):
        return self._req("GET", "/customers", params={"limit": 1})

    def criar_cliente(self, nome, cpf_cnpj, email="", telefone="", referencia=""):
        dados = {"name": nome[:100], "cpfCnpj": cpf_cnpj, "externalReference": referencia}
        if email:
            dados["email"] = email
        if telefone:
            dados["mobilePhone"] = telefone
        return self._req("POST", "/customers", json=dados)

    def criar_cobranca(self, cliente_id, tipo, valor, vencimento, descricao, referencia, multa_pct, juros_pct):
        tipo_asaas = {"pix": "PIX", "boleto": "BOLETO", "cartao": "CREDIT_CARD"}[tipo]
        dados = {"customer": cliente_id, "billingType": tipo_asaas, "value": float(valor),
                 "dueDate": vencimento.isoformat(), "description": descricao[:500], "externalReference": referencia}
        if tipo != "cartao":
            if multa_pct:
                dados["fine"] = {"value": float(multa_pct)}
            if juros_pct:
                dados["interest"] = {"value": float(juros_pct)}
        return self._req("POST", "/payments", json=dados)

    def linha_digitavel(self, pagamento_id):
        return self._req("GET", f"/payments/{pagamento_id}/identificationField")

    def qrcode_pix(self, pagamento_id):
        return self._req("GET", f"/payments/{pagamento_id}/pixQrCode")

    def consultar(self, pagamento_id):
        return self._req("GET", f"/payments/{pagamento_id}")

    def cancelar(self, pagamento_id):
        return self._req("DELETE", f"/payments/{pagamento_id}")


    # ---- contrato comum (ver base.py) ---------------------------------------------------------------
    def emitir(self, cobranca, cfg, vencimento, descricao, referencia, cliente, url_aviso=""):
        """Cria a cobrança e devolve os dados guardados na Cobranca. Cartão: o pagador digita o cartão na página do Asaas."""
        from ..models import ClienteGateway

        existente = ClienteGateway.objects.filter(config=cfg, cliente=cliente).first()
        if existente:
            cliente_id = existente.externo_id
        else:
            doc = somente_digitos(cliente.documento)
            if len(doc) not in (11, 14):
                raise GatewayErro("O cliente precisa ter CPF ou CNPJ cadastrado para emitir boleto, PIX ou cartão.")
            r = self.criar_cliente(cliente.nome, doc, cliente.email_cobranca or cliente.email,
                                   somente_digitos(cliente.telefone), f"cliente-{cliente.pk}")
            ClienteGateway.objects.create(config=cfg, cliente=cliente, externo_id=r["id"])
            cliente_id = r["id"]
        p = self.criar_cobranca(cliente_id, cobranca.tipo, cobranca.valor, vencimento, descricao, referencia,
                                cfg.multa_pct, cfg.juros_mes_pct)
        return {"id": p["id"], "status": p.get("status", ""), "url_pagamento": p.get("invoiceUrl", "") or "",
                "url_boleto": p.get("bankSlipUrl", "") or "", "nosso_numero": str(p.get("nossoNumero") or "")}

    def detalhes(self, cobranca):
        """Linha digitável (boleto) ou QR/copia-e-cola (PIX). Cartão não tem."""
        if cobranca.tipo == "boleto":
            r = self.linha_digitavel(cobranca.gateway_id)
            return {"linha_digitavel": (r.get("identificationField") or "")[:60],
                    "nosso_numero": str(r.get("nossoNumero") or cobranca.nosso_numero)}
        if cobranca.tipo == "pix":
            r = self.qrcode_pix(cobranca.gateway_id)
            return {"pix_copia_cola": r.get("payload") or cobranca.pix_copia_cola,
                    "pix_qrcode_png": r.get("encodedImage") or ""}
        return {}

    def pagamento(self, gateway_id):
        return self.normalizar(self.consultar(gateway_id))

    @staticmethod
    def normalizar(p) -> Pagamento:
        status, tipo = p.get("status"), p.get("billingType")
        if p.get("deleted"):
            sit = CANCELADO
        elif status == "RECEIVED":
            sit = PAGO
        elif status == "CONFIRMED" and tipo == "CREDIT_CARD":
            sit = PAGO                     # cartão: aprovado e capturado; o dinheiro cai depois, mas o cliente já pagou
        elif status in ("REFUNDED", "REFUND_REQUESTED", "REFUND_IN_PROGRESS", "CHARGEBACK_REQUESTED",
                        "CHARGEBACK_DISPUTE", "AWAITING_CHARGEBACK_REVERSAL"):
            sit = ESTORNADO
        elif status in ("PENDING", "OVERDUE", "CONFIRMED", "AWAITING_RISK_ANALYSIS"):
            sit = ABERTO
        else:
            sit = OUTRO
        bruto = p.get("paymentDate") or p.get("confirmedDate") or p.get("clientPaymentDate") or ""
        try:
            data = datetime.date.fromisoformat(bruto[:10])
        except ValueError:
            data = timezone.localdate()
        valor = Decimal(str(p["value"])) if p.get("value") is not None else Decimal("0")
        liquido = Decimal(str(p["netValue"])) if p.get("netValue") is not None else None
        forma = {"PIX": "pix", "CREDIT_CARD": "cartao_credito"}.get(tipo, "boleto")
        return Pagamento(id=p.get("id", ""), referencia=p.get("externalReference") or "", situacao=sit, bruto=status or "",
                         valor=valor, data=data, liquido=liquido, forma=forma, recibo=p.get("transactionReceiptUrl", "") or "")
