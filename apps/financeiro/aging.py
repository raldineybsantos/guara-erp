"""Aging (idade dos títulos em aberto) de contas a receber e a pagar, por parceiro e faixa de vencimento."""
import datetime
from collections import OrderedDict
from decimal import Decimal

FAIXAS = [("a_vencer", "A vencer", None, 0), ("f1", "1 a 30", 1, 30), ("f2", "31 a 60", 31, 60),
          ("f3", "61 a 90", 61, 90), ("f4", "91 a 180", 91, 180), ("f5", "Mais de 180", 181, None)]


def chave_faixa(atraso):
    """Atraso em dias (<= 0 = a vencer) -> chave da faixa."""
    if atraso <= 0:
        return "a_vencer"
    for chave, _, de, ate in FAIXAS[1:]:
        if atraso >= de and (ate is None or atraso <= ate):
            return chave


def gerar(titulos, data=None):
    """`titulos`: queryset de títulos em aberto. Devolve {linhas: [...], totais: {...}, faixas: FAIXAS}."""
    data = data or datetime.date.today()
    zero = {c: Decimal("0") for c, *_ in FAIXAS}
    linhas = OrderedDict()
    totais = dict(zero, total=Decimal("0"))
    for t in titulos:
        parceiro = getattr(t, "cliente", None) or getattr(t, "fornecedor", None)
        chave = parceiro.pk if parceiro else 0
        linha = linhas.setdefault(chave, {"parceiro": parceiro, **zero, "total": Decimal("0"), "titulos": 0})
        faixa = chave_faixa((data - t.vencimento).days)
        saldo = t.saldo
        linha[faixa] += saldo
        linha["total"] += saldo
        linha["titulos"] += 1
        totais[faixa] += saldo
        totais["total"] += saldo
    ordenadas = sorted(linhas.values(), key=lambda x: -x["total"])
    return {"linhas": ordenadas, "totais": totais, "faixas": FAIXAS, "data": data}
