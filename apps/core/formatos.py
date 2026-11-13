from decimal import Decimal


def qtd(valor):
    """Quantidade sem zeros à direita: Decimal('20.0000') -> '20'; Decimal('4.2500') -> '4.25'."""
    if valor is None:
        return ""
    d = Decimal(str(valor)).normalize()
    return format(d, "f")
