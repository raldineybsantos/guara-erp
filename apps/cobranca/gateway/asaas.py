# Guará ERP — gestão empresarial livre
# Copyright (C) 2026 Raldiney Santos e colaboradores
# SPDX-License-Identifier: GPL-3.0-or-later
"""Cliente da API v3 do Asaas (boleto e PIX). Autenticação pelo header access_token; o sandbox e a produção têm
chaves e URLs diferentes. Nenhum dado de cartão passa por aqui."""
import requests
from django.conf import settings

BASES = {"sandbox": "https://api-sandbox.asaas.com/v3", "producao": "https://api.asaas.com/v3"}
TIMEOUT = 20


class GatewayErro(Exception):
    """Falha na chamada ao gateway; a mensagem pode ser mostrada ao usuário."""

    def __init__(self, mensagem, status=None):
        super().__init__(mensagem)
        self.status = status


class GatewayParcial(GatewayErro):
    """A cobrança foi criada no gateway, mas faltou buscar boleto/QR. Não se deve desfazer o que já existe lá."""


class Asaas:
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
        dados = {"customer": cliente_id, "billingType": "PIX" if tipo == "pix" else "BOLETO", "value": float(valor),
                 "dueDate": vencimento.isoformat(), "description": descricao[:500], "externalReference": referencia}
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
