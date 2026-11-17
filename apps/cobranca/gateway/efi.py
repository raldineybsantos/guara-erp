# Guará ERP — gestão empresarial livre
# Copyright (C) 2026 Raldiney Santos e colaboradores
# SPDX-License-Identifier: GPL-3.0-or-later
"""Cliente da API Cobranças da Efí (ex-Gerencianet): boleto Bolix (com QR Pix) e cartão de crédito em página hospedada.

Autenticação OAuth2 client_credentials (Basic client_id:client_secret) em /v1/authorize; token vale ~10 min.
A API Cobranças não usa certificado (mTLS). O Pix "puro" da Efí exige certificado e fica de fora: use o Asaas.
Nenhum dado de cartão passa por aqui: o pagador digita na página da Efí (payment_url).
Os avisos da Efí trazem só um token; o ERP o consulta (GET /v1/notification/:token) e depois reconsulta a cobrança."""
import base64
import datetime
import io
from decimal import Decimal

import requests
import segno
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from apps.core.validators import somente_digitos

from .base import ABERTO, CANCELADO, ESTORNADO, OUTRO, PAGO, GatewayErro, Pagamento

BASES = {"sandbox": "https://cobrancas-h.api.efipay.com.br", "producao": "https://cobrancas.api.efipay.com.br"}
TIMEOUT = 20
CENTAVO = Decimal("0.01")


def _centavos(valor) -> int:
    return int((Decimal(str(valor)) * 100).quantize(Decimal("1")))


class Efi:
    TIPOS = ("boleto", "cartao")

    def __init__(self, config):
        if config.ambiente == "producao" and not getattr(settings, "COBRANCA_GATEWAY_PRODUCAO", False):
            raise GatewayErro("Produção do gateway está travada (COBRANCA_GATEWAY_PRODUCAO).")
        self.base = BASES[config.ambiente]
        self.client_id = (config.client_id or "").strip()
        self.segredo = config.obter_chave()
        self._cache = f"efi-token-{config.pk or 0}-{config.ambiente}"
        if not self.client_id or not self.segredo:
            raise GatewayErro("Informe o Client_Id e o Client_Secret da Efí.")

    # ---- HTTP ---------------------------------------------------------------------------------------
    def _token(self, forcar=False):
        t = None if forcar else cache.get(self._cache)
        if t:
            return t
        cred = base64.b64encode(f"{self.client_id}:{self.segredo}".encode()).decode()
        try:
            r = requests.post(self.base + "/v1/authorize", json={"grant_type": "client_credentials"}, timeout=TIMEOUT,
                              headers={"Authorization": f"Basic {cred}", "Content-Type": "application/json",
                                       "User-Agent": "GuaraERP/1.0"})
        except requests.RequestException as exc:
            raise GatewayErro(f"não foi possível falar com a Efí: {exc.__class__.__name__}") from exc
        try:
            corpo = r.json()
        except ValueError:
            corpo = {}
        if r.status_code != 200 or not corpo.get("access_token"):
            raise GatewayErro("a Efí recusou as credenciais (confira Client_Id, Client_Secret, ambiente e se a "
                              "aplicação tem a API Cobranças habilitada)", r.status_code)
        cache.set(self._cache, corpo["access_token"], max(int(corpo.get("expires_in", 600)) - 60, 30))
        return corpo["access_token"]

    def _req(self, metodo, caminho, json=None, params=None, _repetiu=False):
        try:
            r = requests.request(metodo, self.base + caminho, json=json, params=params, timeout=TIMEOUT, headers={
                "Authorization": f"Bearer {self._token()}", "Content-Type": "application/json",
                "User-Agent": "GuaraERP/1.0"})
        except requests.RequestException as exc:
            raise GatewayErro(f"não foi possível falar com a Efí: {exc.__class__.__name__}") from exc
        if r.status_code == 401 and not _repetiu:
            self._token(forcar=True)                                   # token expirou antes da hora
            return self._req(metodo, caminho, json, params, _repetiu=True)
        try:
            corpo = r.json()
        except ValueError:
            corpo = {}
        if r.status_code >= 400:
            msg = ""
            if isinstance(corpo, dict):
                msg = str(corpo.get("error_description") or corpo.get("message") or corpo.get("error") or "")[:300]
                if isinstance(corpo.get("error_description"), dict):
                    msg = str(corpo["error_description"].get("message") or "")[:300]
            raise GatewayErro(msg or f"a Efí respondeu {r.status_code}", r.status_code)
        return corpo

    def testar(self):
        self._token(forcar=True)
        return {"ok": True}

    # ---- contrato comum -----------------------------------------------------------------------------
    def emitir(self, cobranca, cfg, vencimento, descricao, referencia, cliente, url_aviso=""):
        if cobranca.tipo not in self.TIPOS:
            raise GatewayErro("A Efí emite boleto (com QR Pix) e cartão. Para Pix puro use o Asaas.")
        if not url_aviso.startswith("https://"):
            raise GatewayErro("A Efí exige um endereço público HTTPS para avisar os pagamentos: defina ERP_URL_BASE "
                              "(https://...) no .env.")
        item = [{"name": descricao[:255] or "Cobrança", "value": _centavos(cobranca.valor), "amount": 1}]
        meta = {"custom_id": referencia, "notification_url": url_aviso}
        if cobranca.tipo == "cartao":
            corpo = {"items": item, "metadata": meta,
                     "settings": {"payment_method": "credit_card", "expire_at": vencimento.isoformat(),
                                  "request_delivery_address": False, "message": descricao[:80]}}
            email = cliente.email_cobranca or cliente.email
            if email:
                corpo["customer"] = {"email": email}
            d = self._req("POST", "/v1/charge/one-step/link", json=corpo).get("data") or {}
            return {"id": str(d.get("charge_id", "")), "status": d.get("status", ""),
                    "url_pagamento": d.get("payment_url", "") or "", "url_boleto": "", "nosso_numero": ""}
        doc = somente_digitos(cliente.documento)
        if len(doc) == 11:
            cli = {"name": cliente.nome[:100], "cpf": doc}
        elif len(doc) == 14:
            cli = {"juridical_person": {"corporate_name": cliente.nome[:100], "cnpj": doc}, "name": cliente.nome[:100]}
        else:
            raise GatewayErro("O cliente precisa ter CPF ou CNPJ cadastrado para emitir boleto.")
        email = cliente.email_cobranca or cliente.email
        if email:
            cli["email"] = email
        fone = somente_digitos(cliente.telefone)
        if len(fone) in (10, 11):
            cli["phone_number"] = fone
        corpo = {"items": item, "metadata": meta,
                 "payment": {"banking_billet": {"expire_at": vencimento.isoformat(), "customer": cli,
                                                "message": descricao[:100]}}}
        d = self._req("POST", "/v1/charge/one-step", json=corpo).get("data") or {}
        return {"id": str(d.get("charge_id", "")), "status": d.get("status", ""),
                "url_pagamento": d.get("link", "") or "", "url_boleto": d.get("billet_link") or d.get("link") or "",
                "nosso_numero": "", "_barcode": d.get("barcode", ""), "_pix": (d.get("pix") or {}).get("qrcode", "")}

    def detalhes(self, cobranca):
        if cobranca.tipo != "boleto":
            return {}
        d = (self._req("GET", f"/v1/charge/{cobranca.gateway_id}").get("data") or {})
        bb = ((d.get("payment") or {}).get("banking_billet") or {})
        out = {"linha_digitavel": (bb.get("barcode") or "")[:60]}
        pix = (bb.get("pix") or {}).get("qrcode") or ""
        if pix:
            out["pix_copia_cola"] = pix
            buf = io.BytesIO()
            segno.make(pix, error="m").save(buf, kind="png", scale=5, border=2)
            out["pix_qrcode_png"] = base64.b64encode(buf.getvalue()).decode()
        return out

    def pagamento(self, gateway_id, data_evento=None):
        d = self._req("GET", f"/v1/charge/{gateway_id}").get("data") or {}
        return self.normalizar(d, data_evento)

    @staticmethod
    def normalizar(d, data_evento=None) -> Pagamento:
        status = str(d.get("status") or "").lower()
        if status == "paid":
            sit = PAGO
        elif status in ("canceled", "cancelled"):
            sit = CANCELADO
        elif any(x in status for x in ("refund", "contest", "chargeback")):
            sit = ESTORNADO
        elif status in ("new", "link", "waiting", "unpaid", "identified", "approved", "expired"):
            sit = ABERTO
        else:
            sit = OUTRO                      # inclui "settled" (baixa manual no painel da Efí): só avisa o Financeiro
        metodo = str((d.get("payment") or {}).get("method") or "")
        forma = "cartao_credito" if "card" in metodo else "boleto"
        return Pagamento(id=str(d.get("charge_id", "")), referencia=str(d.get("custom_id") or ""), situacao=sit,
                         bruto=status, valor=(Decimal(str(d.get("total", 0))) / 100).quantize(CENTAVO),
                         data=data_evento or timezone.localdate(), liquido=None, forma=forma)

    def cancelar(self, gateway_id):
        return self._req("PUT", f"/v1/charge/{gateway_id}/cancel")

    # ---- avisos -------------------------------------------------------------------------------------
    def eventos(self, token):
        """Consulta o aviso (GET /v1/notification/:token) e devolve a lista de mudanças. Isto também 'confirma' o aviso."""
        lista = self._req("GET", f"/v1/notification/{token}").get("data") or []
        return lista if isinstance(lista, list) else []


def data_do_evento(ev):
    """'2022-02-20 09:12:23' -> date (do horário da Efí); None se ilegível."""
    try:
        return datetime.datetime.strptime(str(ev.get("received_by_bank_at") or ev.get("created_at"))[:19],
                                          "%Y-%m-%d %H:%M:%S").date()
    except ValueError:
        return None
