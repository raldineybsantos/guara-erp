"""Orçado × realizado. O realizado vem dos títulos (contas a pagar e a receber, sem os cancelados), no mês de
competência (ou da emissão), repartidos pelo rateio de centros de custo quando houver."""

import datetime
from collections import OrderedDict
from decimal import Decimal

from django.utils import timezone

from apps.financeiro.models import ContaPagar, ContaReceber, partes_por_centro

from .models import MESES, Orcamento

ZERO = Decimal("0")
MODELO = {"despesa": ContaPagar, "receita": ContaReceber}


def _casa(linha, centro, categoria):
    if linha.centro_custo_id and (centro is None or centro.pk != linha.centro_custo_id):
        return False
    if linha.plano_conta_id:
        if categoria is None or not categoria.codigo.startswith(linha.plano_conta.codigo):
            return False
    return True


def _linha_do_titulo(linhas, tipo, centro, categoria):
    candidatas = [ln for ln in linhas if ln.tipo == tipo and _casa(ln, centro, categoria)]
    return max(candidatas, key=lambda ln: ln.especificidade) if candidatas else None


def _titulos(orc, tipo, excluir_pk=None):
    inicio, fim = datetime.date(orc.ano, 1, 1), datetime.date(orc.ano, 12, 31)
    qs = (MODELO[tipo].objects.exclude(status="cancelado").select_related("plano_conta", "centro_custo")
          .prefetch_related("rateios__centro_custo"))
    if orc.empresa_id:
        qs = qs.filter(empresa=orc.empresa_id)
    if excluir_pk:
        qs = qs.exclude(pk=excluir_pk)
    for t in qs:
        base = t.competencia or t.data_emissao
        if inicio <= base <= fim:
            yield t, base


def realizado(orc):
    """({linha.pk: [12 meses]}, {tipo: [12 meses] do que não cabe em nenhuma linha})."""
    linhas = list(orc.linhas.select_related("centro_custo", "plano_conta"))
    por_linha = {ln.pk: [ZERO] * 12 for ln in linhas}
    sem = {"despesa": [ZERO] * 12, "receita": [ZERO] * 12}
    for tipo in ("despesa", "receita"):
        for t, base in _titulos(orc, tipo):
            for centro, valor in partes_por_centro(t):
                ln = _linha_do_titulo(linhas, tipo, centro, t.plano_conta)
                alvo = por_linha[ln.pk] if ln else sem[tipo]
                alvo[base.month - 1] += valor
    return por_linha, sem


def quadro(orc, ate_mes):
    """Linhas do relatório, acumulado de janeiro até `ate_mes`."""
    por_linha, sem = realizado(orc)
    saida = []
    for ln in orc.linhas.select_related("centro_custo", "plano_conta"):
        orcado = ln.ate_mes(ate_mes)
        real = sum(por_linha[ln.pk][:ate_mes], ZERO)
        saida.append(_linha(str(ln), ln.tipo, orcado, real, ln.total))
    for tipo, vals in sem.items():
        real = sum(vals[:ate_mes], ZERO)
        if real:
            saida.append(_linha(f"Sem orçamento ({'despesas' if tipo == 'despesa' else 'receitas'})", tipo, ZERO,
                                real, ZERO))
    return saida


def _linha(nome, tipo, orcado, real, ano):
    variacao = real - orcado
    pct = (real / orcado * 100).quantize(Decimal("0.1")) if orcado else None
    # despesa acima do orçado e receita abaixo do orçado são o lado ruim
    ruim = (variacao > 0) if tipo == "despesa" else (variacao < 0 and orcado > 0)
    return {"nome": nome, "tipo": tipo, "orcado": orcado, "realizado": real, "variacao": variacao, "pct": pct,
            "ruim": ruim, "orcado_ano": ano}


def totais(linhas):
    out = {}
    for tipo in ("despesa", "receita"):
        sel = [x for x in linhas if x["tipo"] == tipo]
        out[tipo] = {"orcado": sum((x["orcado"] for x in sel), ZERO), "realizado": sum((x["realizado"] for x in sel), ZERO)}
        out[tipo]["variacao"] = out[tipo]["realizado"] - out[tipo]["orcado"]
    return out


def orcamento_do(titulo):
    base = titulo.competencia or titulo.data_emissao
    qs = Orcamento.objects.filter(ativo=True, ano=base.year).exclude(controle="nenhum")
    if titulo.empresa_id:
        qs = qs.filter(empresa_id__in=[titulo.empresa_id])
    return qs.first()


def estouro(titulo):
    """Se salvar este título de contas a pagar estoura o orçamento (acumulado do ano até o mês dele).
    Devolve {orcamento, linha, orcado, realizado, excesso} ou None."""
    orc = orcamento_do(titulo)
    if orc is None or titulo.valor is None:
        return None
    base = titulo.competencia or titulo.data_emissao
    linhas = list(orc.linhas.select_related("centro_custo", "plano_conta"))
    partes = partes_por_centro(titulo) if titulo.pk else [(titulo.centro_custo, titulo.valor)]
    achado = {}
    for centro, valor in partes:
        ln = _linha_do_titulo(linhas, "despesa", centro, titulo.plano_conta)
        if ln:
            achado[ln.pk] = (ln, achado.get(ln.pk, (None, ZERO))[1] + valor)
    if not achado:
        return None
    por_linha, _ = realizado_sem(orc, titulo.pk, linhas)
    for ln, novo in achado.values():
        real = sum(por_linha[ln.pk][:base.month], ZERO) + novo
        orcado = ln.ate_mes(base.month)
        if real > orcado:
            return {"orcamento": orc, "linha": ln, "orcado": orcado, "realizado": real, "excesso": real - orcado}
    return None


def realizado_sem(orc, excluir_pk, linhas):
    por_linha = {ln.pk: [ZERO] * 12 for ln in linhas}
    for t, base in _titulos(orc, "despesa", excluir_pk):
        for centro, valor in partes_por_centro(t):
            ln = _linha_do_titulo(linhas, "despesa", centro, t.plano_conta)
            if ln:
                por_linha[ln.pk][base.month - 1] += valor
    return por_linha, None
