"""Apropriação mensal de contratos por competência (regime de competência)."""
import calendar
import datetime

from django.core.exceptions import ValidationError
from django.db import transaction

from . import servicos
from .models import ApropriacaoContrato, ContratoContabil


def _proximo_mes(d):
    return (d.replace(day=1) + datetime.timedelta(days=32)).replace(day=1)


def _fim_do_mes(d):
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


@transaction.atomic
def apropriar_mes(cc, comp):
    """Apropria uma competência de um contrato. Devolve a apropriação criada ou None se já existia/fora do prazo."""
    comp = comp.replace(day=1)
    if not cc.ativo or comp < cc.inicio_apropriacao.replace(day=1):
        return None
    fim = cc.ultimo_mes
    if fim and comp > fim.replace(day=1):
        return None
    if cc.apropriacoes.filter(competencia=comp).exists():
        return None
    if cc.natureza == "despesa":
        deb, cred, rot = cc.conta_resultado, cc.conta_balanco, "Despesa"
    else:
        deb, cred, rot = cc.conta_balanco, cc.conta_resultado, "Receita"
    ap = ApropriacaoContrato.objects.create(contrato=cc, competencia=comp, valor=cc.valor_mensal)
    servicos.gravar(_fim_do_mes(comp), f"{rot} de contrato {cc.contrato.numero} — {comp:%m/%Y}", deb, cred,
                    cc.valor_mensal, evento="apropriacao_contrato", referencia=ap, centro_custo=cc.centro_custo,
                    empresa=cc.empresa)
    return ap


def apropriar_ate(competencia, empresa=None):
    """Apropria, para todos os contratos ativos, cada mês ainda pendente até a competência (inclusive)."""
    alvo = competencia.replace(day=1)
    qs = ContratoContabil.objects.filter(ativo=True, inicio_apropriacao__lte=_fim_do_mes(alvo))
    if empresa is not None:
        qs = qs.filter(empresa=empresa)
    n = 0
    for cc in qs.select_related("contrato", "conta_resultado", "conta_balanco"):
        comp = cc.inicio_apropriacao.replace(day=1)
        while comp <= alvo:
            if apropriar_mes(cc, comp):
                n += 1
            comp = _proximo_mes(comp)
    return n


@transaction.atomic
def estornar_competencia(competencia, empresa=None):
    """Desfaz a apropriação de um mês, só se for a última de cada contrato (mantém a sequência)."""
    comp = competencia.replace(day=1)
    aps = ApropriacaoContrato.objects.filter(competencia=comp).select_related("contrato")
    if empresa is not None:
        aps = aps.filter(contrato__empresa=empresa)
    n = 0
    for ap in aps:
        if ap.contrato.apropriacoes.filter(competencia__gt=comp).exists():
            raise ValidationError(f"{ap.contrato} tem apropriação em meses seguintes; estorne do mais recente "
                                  "para o mais antigo.")
        servicos.estornar_de(ap, motivo="Apropriação de contrato estornada.")
        ap.delete()
        n += 1
    return n
