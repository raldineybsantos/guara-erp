"""Formatação de valores para mensagens e textos (padrão brasileiro: R$ 1.234,56)."""
from decimal import Decimal

from django.utils.formats import number_format


def moeda(valor):
    if valor is None or valor == "":
        return ""
    return "R$ " + number_format(Decimal(str(valor)), 2, force_grouping=True)
