# Guará ERP — gestão empresarial livre
# Copyright (C) 2026 Raldiney Santos e colaboradores
# SPDX-License-Identifier: GPL-3.0-or-later
"""Emissão de boleto/PIX pelo gateway, baixa automática dos pagamentos e conciliação.

Regras de segurança do dinheiro:
- O webhook só AVISA. Antes de dar baixa, o ERP consulta o pagamento na API do gateway (com a chave) e confere
  identificador, referência da cobrança, situação (RECEIVED) e valor. Um aviso forjado não baixa nada.
- Cada evento é processado uma vez (ID único) e a baixa é protegida contra duplicidade com bloqueio de linha.
- Estorno/chargeback NUNCA desfaz a baixa sozinho: avisa o Financeiro para decidir."""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.core.models import LogAuditoria, notificar
from apps.core.validators import somente_digitos
from apps.financeiro.models import ContaReceber, Movimentacao

from ..models import Cobranca, ClienteGateway, ConfiguracaoGateway
from .asaas import Asaas, GatewayErro, GatewayParcial

CENTAVO = Decimal("0.01")


def config_ativa(empresa):
    return ConfiguracaoGateway.objects.filter(empresa=empresa, ativo=True).first()


def referencia(cobranca):
    return f"guara-{cobranca.pk}"


def _financeiro():
    return User.objects.filter(Q(is_superuser=True) | Q(groups__name__in=["Financeiro", "Administradores"]),
                               is_active=True).distinct()


# ----------------------------------------------------------------------------------------------- emissão
def _cliente_no_gateway(api, cfg, cliente):
    existente = ClienteGateway.objects.filter(config=cfg, cliente=cliente).first()
    if existente:
        return existente.externo_id
    doc = somente_digitos(cliente.documento)
    if len(doc) not in (11, 14):
        raise GatewayErro("O cliente precisa ter CPF ou CNPJ cadastrado para emitir boleto ou PIX.")
    r = api.criar_cliente(cliente.nome, doc, cliente.email_cobranca or cliente.email,
                          somente_digitos(cliente.telefone), f"cliente-{cliente.pk}")
    ClienteGateway.objects.create(config=cfg, cliente=cliente, externo_id=r["id"])
    return r["id"]


def emitir(cobranca):
    """Cria a cobrança no gateway e guarda boleto/QR. Levanta GatewayErro com mensagem para o usuário."""
    titulo = cobranca.conta_receber
    cfg = config_ativa(titulo.empresa)
    if cfg is None:
        raise GatewayErro("Não há gateway de pagamento ativo para esta empresa.")
    if cobranca.gateway_id:
        raise GatewayErro("Esta cobrança já foi emitida no gateway.")
    if cobranca.tipo not in ("pix", "boleto"):
        raise GatewayErro("O gateway emite apenas boleto e PIX.")
    if cobranca.cliente is None:
        raise GatewayErro("A conta a receber não tem cliente.")
    api = Asaas(cfg)
    cliente_id = _cliente_no_gateway(api, cfg, cobranca.cliente)
    hoje = timezone.localdate()
    venc = max(cobranca.vencimento, hoje)
    descricao = titulo.descricao
    if venc != cobranca.vencimento:
        descricao += f" (vencimento original {cobranca.vencimento:%d/%m/%Y})"
    p = api.criar_cobranca(cliente_id, cobranca.tipo, cobranca.valor, venc, descricao, referencia(cobranca),
                           cfg.multa_pct, cfg.juros_mes_pct)
    cobranca.gateway_provedor, cobranca.gateway_id = cfg.provedor, p["id"]
    cobranca.gateway_status = p.get("status", "")
    cobranca.url_pagamento, cobranca.url_boleto = p.get("invoiceUrl", "") or "", p.get("bankSlipUrl", "") or ""
    cobranca.nosso_numero = str(p.get("nossoNumero") or "")
    cobranca.vencimento = venc
    cobranca.save()                      # a cobrança já existe no gateway: guarda antes de buscar os detalhes
    try:
        atualizar_dados(cobranca, api)
    except GatewayErro as exc:
        raise GatewayParcial(str(exc)) from exc


def atualizar_dados(cobranca, api=None):
    """Busca (ou rebusca) linha digitável / QR do PIX e a situação."""
    if api is None:
        cfg = config_ativa(cobranca.conta_receber.empresa)
        if cfg is None:
            raise GatewayErro("Não há gateway de pagamento ativo para esta empresa.")
        api = Asaas(cfg)
    if cobranca.tipo == "boleto":
        r = api.linha_digitavel(cobranca.gateway_id)
        cobranca.linha_digitavel = (r.get("identificationField") or "")[:60]
        cobranca.nosso_numero = str(r.get("nossoNumero") or cobranca.nosso_numero)
    else:
        r = api.qrcode_pix(cobranca.gateway_id)
        cobranca.pix_copia_cola = r.get("payload") or cobranca.pix_copia_cola
        cobranca.pix_qrcode_png = r.get("encodedImage") or ""
    cobranca.save()


def cancelar_no_gateway(cobranca):
    """Cancela a cobrança no gateway (boleto/PIX deixa de poder ser pago) e a marca como cancelada."""
    if cobranca.status == "paga":
        raise GatewayErro("Cobrança já paga: não pode ser cancelada.")
    if cobranca.gateway_id:
        cfg = config_ativa(cobranca.conta_receber.empresa)
        if cfg is None:
            raise GatewayErro("Não há gateway de pagamento ativo para esta empresa.")
        try:
            Asaas(cfg).cancelar(cobranca.gateway_id)
        except GatewayErro as exc:
            if exc.status != 404:          # 404: já não existe lá
                raise
        cobranca.gateway_status = "DELETED"
    cobranca.status = "cancelada"
    cobranca.save()


# ----------------------------------------------------------------------------------------------- baixa
def _data(p):
    bruto = p.get("paymentDate") or p.get("clientPaymentDate") or ""
    try:
        return datetime.date.fromisoformat(bruto[:10])
    except ValueError:
        return timezone.localdate()


@transaction.atomic
def registrar_pagamento(cfg, cobranca_id, p):
    """Dá baixa no título a partir do pagamento CONSULTADO na API (p). Devolve um texto com o que aconteceu."""
    cob = Cobranca.objects.select_for_update(of=("self",)).select_related("cliente").get(pk=cobranca_id)
    if p.get("id") != cob.gateway_id or p.get("externalReference") != referencia(cob):
        raise GatewayErro("O pagamento informado pelo gateway não confere com a cobrança.")
    if p.get("status") != "RECEIVED":
        return f"situação {p.get('status')}: sem baixa"
    if cob.pago_em:
        return "já baixada"
    titulo = ContaReceber.objects.select_for_update().get(pk=cob.conta_receber_id)
    valor = Decimal(str(p["value"])).quantize(CENTAVO)
    data = _data(p)
    cob.gateway_status = "RECEIVED"
    cob.url_recibo = p.get("transactionReceiptUrl", "") or ""
    if titulo.status in ("pago", "cancelado"):
        cob.save(update_fields=["gateway_status", "url_recibo"])
        motivo = "o título já estava pago" if titulo.status == "pago" else "o título estava cancelado"
        notificar(_financeiro(), f"ATENÇÃO: o gateway recebeu R$ {valor} de {cob.cliente} mas {motivo}. "
                                 "Nenhuma baixa foi feita; avalie a devolução.", titulo.get_absolute_url(), email=True)
        LogAuditoria.registrar("seguranca", cob, descricao=f"Pagamento no gateway sem baixa ({motivo}): R$ {valor}")
        return f"sem baixa: {motivo}"
    if valor > titulo.saldo:                       # pagou com multa/juros: registra a diferença como juros do título
        titulo.juros += valor - titulo.saldo
        titulo.save()
    forma = "pix" if p.get("billingType") == "PIX" else "boleto"
    titulo.baixar(valor, data, cfg.conta_bancaria, forma, observacao=f"Baixa automática ({cfg.get_provedor_display()} {cob.gateway_id})")
    liquido = Decimal(str(p["netValue"])).quantize(CENTAVO) if p.get("netValue") is not None else None
    taxa = max(valor - liquido, Decimal("0")) if liquido is not None else Decimal("0")
    if taxa > 0 and cfg.lancar_tarifa:
        Movimentacao.objects.create(empresa=titulo.empresa, conta_bancaria=cfg.conta_bancaria, data=data, tipo="saida",
                                    valor=taxa, descricao=f"Tarifa do gateway: {titulo}"[:200])
    cob.status, cob.pago_em, cob.valor_liquido, cob.taxa = "paga", data, liquido, taxa
    cob.save()
    LogAuditoria.registrar("acao", cob, descricao=f"Baixa automática pelo gateway: R$ {valor} em {data:%d/%m/%Y} ({forma})")
    notificar(_financeiro(), f"Recebido R$ {valor} de {cob.cliente} por {forma.upper()}: título baixado automaticamente.",
              titulo.get_absolute_url())
    return f"baixa de R$ {valor}"


def _consultar_e_baixar(cfg, cob):
    return registrar_pagamento(cfg, cob.pk, Asaas(cfg).consultar(cob.gateway_id))


def processar_evento(cfg, corpo):
    """Trata um aviso do gateway já autenticado. Devolve o resultado (texto curto); levanta em caso de falha a repetir."""
    tipo = str(corpo.get("event", ""))
    pid = str((corpo.get("payment") or {}).get("id", ""))
    if not pid:
        return "sem pagamento"
    cob = Cobranca.objects.filter(gateway_provedor=cfg.provedor, gateway_id=pid,
                                  conta_receber__empresa=cfg.empresa).first()
    if cob is None:
        return "cobrança desconhecida"
    if tipo == "PAYMENT_RECEIVED":
        return _consultar_e_baixar(cfg, cob)
    if tipo == "PAYMENT_DELETED":
        if cob.status != "paga":
            cob.status, cob.gateway_status = "cancelada", "DELETED"
            cob.save()
        return "cancelada"
    if tipo == "PAYMENT_OVERDUE":
        cob.gateway_status = "OVERDUE"
        cob.save(update_fields=["gateway_status"])
        return "vencida"
    if tipo in ("PAYMENT_REFUNDED", "PAYMENT_PARTIALLY_REFUNDED", "PAYMENT_CHARGEBACK_REQUESTED",
                "PAYMENT_CHARGEBACK_DISPUTE", "PAYMENT_AWAITING_CHARGEBACK_REVERSAL"):
        cob.gateway_status = tipo.replace("PAYMENT_", "")[:30]
        cob.save(update_fields=["gateway_status"])
        notificar(_financeiro(), f"Gateway: {tipo} na cobrança de {cob.cliente}. A baixa NÃO foi desfeita; "
                                 "avalie o estorno do recebimento.", cob.conta_receber.get_absolute_url(), email=True)
        return "avisado ao financeiro"
    return "evento sem ação"


# ----------------------------------------------------------------------------------------------- conciliação
def sincronizar(limite=300):
    """Rede de segurança para webhooks perdidos: consulta as cobranças em aberto e as canceladas ainda vivas no gateway."""
    r = {"baixadas": 0, "canceladas": 0, "erros": 0}
    abertas = (Cobranca.objects.filter(gateway_id__gt="", status__in=["gerada", "enviada"])
               .select_related("conta_receber")[:limite])
    for cob in abertas:
        cfg = config_ativa(cob.conta_receber.empresa)
        if cfg is None:
            continue
        try:
            p = Asaas(cfg).consultar(cob.gateway_id)
            if p.get("deleted"):
                cob.status, cob.gateway_status = "cancelada", "DELETED"
                cob.save()
                r["canceladas"] += 1
            elif p.get("status") == "RECEIVED":
                if registrar_pagamento(cfg, cob.pk, p).startswith("baixa"):
                    r["baixadas"] += 1
            elif p.get("status") and p["status"] != cob.gateway_status:
                cob.gateway_status = p["status"]
                cob.save(update_fields=["gateway_status"])
        except GatewayErro:
            r["erros"] += 1
    mortas = (Cobranca.objects.filter(gateway_id__gt="", status="cancelada").exclude(gateway_status="DELETED")
              .select_related("conta_receber")[:limite])
    for cob in mortas:                   # cancelada no ERP (ex.: título cancelado): garante que não possa mais ser paga
        cfg = config_ativa(cob.conta_receber.empresa)
        if cfg is None:
            continue
        try:
            try:
                Asaas(cfg).cancelar(cob.gateway_id)
            except GatewayErro as exc:
                if exc.status != 404:
                    raise
            cob.gateway_status = "DELETED"
            cob.save(update_fields=["gateway_status"])
            r["canceladas"] += 1
        except GatewayErro:
            r["erros"] += 1
    return r
