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

from django.conf import settings
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.core.models import LogAuditoria, notificar
from apps.core.validators import somente_digitos
from apps.financeiro.models import ContaReceber, Movimentacao

from ..models import Cobranca, ClienteGateway, ConfiguracaoGateway
from .asaas import Asaas
from .base import ABERTO, CANCELADO, ESTORNADO, OUTRO, PAGO, GatewayErro, GatewayParcial, Pagamento
from .efi import Efi

CENTAVO = Decimal("0.01")


DRIVERS = {"asaas": Asaas, "efi": Efi}


def driver(cfg):
    return DRIVERS[cfg.provedor](cfg)


def config_ativa(empresa):
    return ConfiguracaoGateway.objects.filter(empresa=empresa, ativo=True).order_by("pk").first()


def configs_para(empresa, tipo):
    """Gateways ativos da empresa que emitem este tipo (boleto, pix, cartao)."""
    return [c for c in ConfiguracaoGateway.objects.filter(empresa=empresa, ativo=True).order_by("pk")
            if tipo in DRIVERS[c.provedor].TIPOS]


def config_da(cobranca):
    """O gateway que EMITIU a cobrança (a baixa, o cancelamento e a conciliação voltam sempre a ele)."""
    return ConfiguracaoGateway.objects.filter(empresa=cobranca.conta_receber.empresa, provedor=cobranca.gateway_provedor,
                                              ativo=True).first()


def url_aviso(cfg):
    """Endereço público que o gateway chama (Efí: guardado na própria cobrança, com o segredo no caminho)."""
    base = getattr(settings, "ERP_URL_BASE", "")
    return f"{base}/cobranca/webhook/efi/{cfg.obter_token()}/" if base else ""


def referencia(cobranca):
    return f"guara-{cobranca.pk}"


def _financeiro():
    return User.objects.filter(Q(is_superuser=True) | Q(groups__name__in=["Financeiro", "Administradores"]),
                               is_active=True).distinct()


# ----------------------------------------------------------------------------------------------- emissão
def emitir(cobranca, provedor=""):
    """Cria a cobrança no gateway e guarda boleto/QR/link. Levanta GatewayErro com mensagem para o usuário."""
    titulo = cobranca.conta_receber
    if cobranca.gateway_id:
        raise GatewayErro("Esta cobrança já foi emitida no gateway.")
    if cobranca.tipo not in ("pix", "boleto", "cartao"):
        raise GatewayErro("O gateway emite boleto, PIX e cartão.")
    if cobranca.cliente is None:
        raise GatewayErro("A conta a receber não tem cliente.")
    candidatos = configs_para(titulo.empresa, cobranca.tipo)
    if provedor:
        candidatos = [c for c in candidatos if c.provedor == provedor]
    if not candidatos:
        existe = config_ativa(titulo.empresa)
        raise GatewayErro("Não há gateway de pagamento ativo para esta empresa." if existe is None else
                          f"Nenhum gateway ativo desta empresa emite {cobranca.get_tipo_display()}.")
    cfg = candidatos[0]
    api = driver(cfg)
    hoje = timezone.localdate()
    venc = max(cobranca.vencimento, hoje)
    descricao = titulo.descricao
    if venc != cobranca.vencimento:
        descricao += f" (vencimento original {cobranca.vencimento:%d/%m/%Y})"
    p = api.emitir(cobranca, cfg, venc, descricao, referencia(cobranca), cobranca.cliente, url_aviso(cfg))
    cobranca.gateway_provedor, cobranca.gateway_id = cfg.provedor, p["id"]
    cobranca.gateway_status = p.get("status", "")
    cobranca.url_pagamento, cobranca.url_boleto = p.get("url_pagamento", ""), p.get("url_boleto", "")
    cobranca.nosso_numero = p.get("nosso_numero", "")
    cobranca.vencimento = venc
    cobranca.save()                      # a cobrança já existe no gateway: guarda antes de buscar os detalhes
    try:
        atualizar_dados(cobranca, api)
    except GatewayErro as exc:
        raise GatewayParcial(str(exc)) from exc


def atualizar_dados(cobranca, api=None):
    """Busca (ou rebusca) linha digitável / QR do PIX."""
    if api is None:
        cfg = config_da(cobranca)
        if cfg is None:
            raise GatewayErro("Não há gateway de pagamento ativo para esta empresa.")
        api = driver(cfg)
    for campo, valor in api.detalhes(cobranca).items():
        setattr(cobranca, campo, valor)
    cobranca.save()


def cancelar_no_gateway(cobranca):
    """Cancela a cobrança no gateway (boleto/PIX deixa de poder ser pago) e a marca como cancelada."""
    if cobranca.status == "paga":
        raise GatewayErro("Cobrança já paga: não pode ser cancelada.")
    if cobranca.gateway_id:
        cfg = config_da(cobranca)
        if cfg is None:
            raise GatewayErro("Não há gateway de pagamento ativo para esta empresa.")
        try:
            driver(cfg).cancelar(cobranca.gateway_id)
        except GatewayErro as exc:
            if exc.status != 404:          # 404: já não existe lá
                raise
        cobranca.gateway_status = "DELETED"
    cobranca.status = "cancelada"
    cobranca.save()


# ----------------------------------------------------------------------------------------------- baixa
@transaction.atomic
def registrar_pagamento(cfg, cobranca_id, p: Pagamento):
    """Dá baixa no título a partir do pagamento CONSULTADO na API (p). Devolve um texto com o que aconteceu."""
    cob = Cobranca.objects.select_for_update(of=("self",)).select_related("cliente").get(pk=cobranca_id)
    if p.id != cob.gateway_id or p.referencia != referencia(cob):
        raise GatewayErro("O pagamento informado pelo gateway não confere com a cobrança.")
    if p.situacao != PAGO:
        return f"situação {p.bruto}: sem baixa"
    if cob.pago_em:
        return "já baixada"
    titulo = ContaReceber.objects.select_for_update().get(pk=cob.conta_receber_id)
    valor = p.valor.quantize(CENTAVO)
    data = p.data or timezone.localdate()
    cob.gateway_status = p.bruto
    cob.url_recibo = p.recibo
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
    forma = p.forma
    titulo.baixar(valor, data, cfg.conta_bancaria, forma, observacao=f"Baixa automática ({cfg.get_provedor_display()} {cob.gateway_id})")
    liquido = p.liquido.quantize(CENTAVO) if p.liquido is not None else None
    taxa = max(valor - liquido, Decimal("0")) if liquido is not None else Decimal("0")
    if taxa > 0 and cfg.lancar_tarifa:
        Movimentacao.objects.create(empresa=titulo.empresa, conta_bancaria=cfg.conta_bancaria, data=data, tipo="saida",
                                    valor=taxa, descricao=f"Tarifa do gateway: {titulo}"[:200])
    cob.status, cob.pago_em, cob.valor_liquido, cob.taxa = "paga", data, liquido, taxa
    cob.save()
    rotulo = {"pix": "PIX", "boleto": "BOLETO", "cartao_credito": "CARTÃO"}.get(forma, forma.upper())
    LogAuditoria.registrar("acao", cob, descricao=f"Baixa automática pelo gateway: R$ {valor} em {data:%d/%m/%Y} ({forma})")
    notificar(_financeiro(), f"Recebido R$ {valor} de {cob.cliente} por {rotulo}: título baixado automaticamente.",
              titulo.get_absolute_url())
    return f"baixa de R$ {valor}"


def _avisar_estorno(cob, rotulo):
    cob.gateway_status = rotulo[:30]
    cob.save(update_fields=["gateway_status"])
    notificar(_financeiro(), f"Gateway: {rotulo} na cobrança de {cob.cliente}. A baixa NÃO foi desfeita; "
                             "avalie o estorno do recebimento.", cob.conta_receber.get_absolute_url(), email=True)
    return "avisado ao financeiro"


def aplicar(cfg, cob, p: Pagamento):
    """Aplica na cobrança o que o gateway REALMENTE informou (consultado na API). Devolve texto do resultado."""
    if p.situacao == PAGO:
        return registrar_pagamento(cfg, cob.pk, p)
    if p.situacao == CANCELADO:
        if cob.status != "paga":
            cob.status, cob.gateway_status = "cancelada", "DELETED"
            cob.save()
        return "cancelada"
    if p.situacao == ESTORNADO:
        return _avisar_estorno(cob, p.bruto.upper() or "ESTORNO")
    if p.situacao == OUTRO and p.bruto:
        cob.gateway_status = p.bruto[:30]
        cob.save(update_fields=["gateway_status"])
        notificar(_financeiro(), f"Gateway: a cobrança de {cob.cliente} está '{p.bruto}' (ex.: baixa manual no painel do "
                                 "gateway). O ERP não deu baixa; confira.", cob.conta_receber.get_absolute_url(), email=True)
        return f"situação {p.bruto}: avisado"
    if p.bruto and p.bruto != cob.gateway_status:
        cob.gateway_status = p.bruto[:30]
        cob.save(update_fields=["gateway_status"])
    return f"situação {p.bruto}: sem baixa"


def _consultar_e_baixar(cfg, cob):
    return registrar_pagamento(cfg, cob.pk, driver(cfg).pagamento(cob.gateway_id))


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
    if tipo in ("PAYMENT_RECEIVED", "PAYMENT_CONFIRMED"):
        return aplicar(cfg, cob, driver(cfg).pagamento(cob.gateway_id))
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
        cfg = config_da(cob)
        if cfg is None:
            continue
        try:
            res = aplicar(cfg, cob, driver(cfg).pagamento(cob.gateway_id))
            if res.startswith("baixa"):
                r["baixadas"] += 1
            elif res == "cancelada":
                r["canceladas"] += 1
        except GatewayErro:
            r["erros"] += 1
    mortas = (Cobranca.objects.filter(gateway_id__gt="", status="cancelada").exclude(gateway_status="DELETED")
              .select_related("conta_receber")[:limite])
    for cob in mortas:                   # cancelada no ERP (ex.: título cancelado): garante que não possa mais ser paga
        cfg = config_da(cob)
        if cfg is None:
            continue
        try:
            try:
                driver(cfg).cancelar(cob.gateway_id)
            except GatewayErro as exc:
                if exc.status != 404:
                    raise
            cob.gateway_status = "DELETED"
            cob.save(update_fields=["gateway_status"])
            r["canceladas"] += 1
        except GatewayErro:
            r["erros"] += 1
    return r
