"""Ativo fixo: lançamento do bem, depreciação linear mensal e baixa, tudo pelo razão único."""

import calendar
import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from . import servicos
from .models import Bem, Depreciacao

D2 = Decimal("0.01")


def _fim_do_mes(comp):
    return comp.replace(day=calendar.monthrange(comp.year, comp.month)[1])


def _proximo_mes(comp):
    return (comp.replace(day=1) + datetime.timedelta(days=32)).replace(day=1)


def contabilizar_aquisicao(bem):
    if bem.contrapartida_id:
        servicos.lancar("aquisicao_bem", bem.valor_aquisicao, bem.data_aquisicao, f"Aquisição do bem {bem}", bem,
                        bem.centro_custo, credito=bem.contrapartida, empresa=bem.empresa)


@transaction.atomic
def depreciar_bem(bem, competencia):
    """Deprecia um mês do bem. Devolve a Depreciacao, ou None se não há o que depreciar nesse mês."""
    comp = competencia.replace(day=1)
    bem = Bem.objects.select_for_update().get(pk=bem.pk)
    if bem.status != "ativo" or comp < bem.inicio_depreciacao:
        return None
    if bem.depreciacoes.filter(competencia=comp).exists():
        return None
    restante = bem.base_depreciavel - bem.depreciacao_acumulada
    if restante <= 0:
        return None
    ultimo = bem.depreciacoes.count() + 1 >= bem.vida_util_meses
    valor = restante if ultimo else min(bem.parcela_mensal, restante)
    if valor <= 0:
        return None
    dep = Depreciacao.objects.create(bem=bem, competencia=comp, valor=valor)
    servicos.lancar("depreciacao", valor, _fim_do_mes(comp), f"Depreciação {comp:%m/%Y}: {bem}", dep,
                    bem.centro_custo, empresa=bem.empresa)
    return dep


def depreciar_ate(competencia, empresa=None, bem=None):
    """Deprecia, para todos os bens em uso, cada mês ainda não depreciado até a competência (inclusive).
    Pode ser repetido: o que já foi depreciado é pulado. Devolve quantos lançamentos de depreciação criou."""
    alvo = competencia.replace(day=1)
    qs = Bem.objects.filter(status="ativo", inicio_depreciacao__lte=alvo)
    if empresa is not None:
        qs = qs.filter(empresa=empresa)
    if bem is not None:
        qs = qs.filter(pk=bem.pk)
    n = 0
    for b in qs:
        comp = b.inicio_depreciacao
        while comp <= alvo:
            if depreciar_bem(b, comp):
                n += 1
            comp = _proximo_mes(comp)
    return n


@transaction.atomic
def estornar_depreciacao(competencia, empresa=None):
    """Desfaz a depreciação de um mês, só se for o último depreciado do bem (mantém a sequência)."""
    comp = competencia.replace(day=1)
    deps = Depreciacao.objects.filter(competencia=comp).select_related("bem")
    if empresa is not None:
        deps = deps.filter(bem__empresa=empresa)
    n = 0
    for dep in deps:
        if dep.bem.depreciacoes.filter(competencia__gt=comp).exists():
            raise ValidationError(f"{dep.bem.codigo} tem depreciação em meses seguintes; estorne do mais recente "
                                  "para o mais antigo.")
        servicos.estornar_de(dep, motivo="Depreciação estornada.")
        dep.delete()
        n += 1
    return n


@transaction.atomic
def baixar_bem(bem, data, valor_venda=Decimal("0"), motivo=""):
    """Baixa o bem: deprecia até o mês anterior, tira o custo e a depreciação do balanço e reconhece o resultado
    (ganho ou perda) contra o valor recebido, que entra no caixa/bancos."""
    bem = Bem.objects.select_for_update().get(pk=bem.pk)
    if bem.status != "ativo":
        raise ValidationError("O bem já foi baixado.")
    if data < bem.data_aquisicao:
        raise ValidationError("A data da baixa não pode ser anterior à aquisição.")
    valor_venda = Decimal(valor_venda or 0).quantize(D2)
    if valor_venda < 0:
        raise ValidationError("O valor recebido não pode ser negativo.")
    anterior = data.replace(day=1) - datetime.timedelta(days=1)
    if anterior >= bem.inicio_depreciacao:
        depreciar_ate(anterior, bem=bem)
    acum = bem.depreciacao_acumulada
    liquido = bem.valor_aquisicao - acum
    hist = f"Baixa do bem {bem}" + (f" — {motivo}" if motivo else "")
    servicos.lancar("baixa_bem_depreciacao", acum, data, hist, bem, bem.centro_custo, empresa=bem.empresa)
    servicos.lancar("baixa_bem_venda", valor_venda, data, hist, bem, bem.centro_custo, empresa=bem.empresa)
    resultado = liquido - valor_venda
    if resultado > 0:
        servicos.lancar("baixa_bem_perda", resultado, data, hist, bem, bem.centro_custo, empresa=bem.empresa)
    elif resultado < 0:
        servicos.lancar("baixa_bem_ganho", -resultado, data, hist, bem, bem.centro_custo, empresa=bem.empresa)
    bem.status, bem.data_baixa, bem.valor_venda, bem.motivo_baixa = "baixado", data, valor_venda, motivo[:200]
    bem.save(update_fields=["status", "data_baixa", "valor_venda", "motivo_baixa", "atualizado_em"])
    return resultado
