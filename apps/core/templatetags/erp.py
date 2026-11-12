from django import template

from apps.core.crud import moeda as _moeda

register = template.Library()


@register.filter
def moeda(valor):
    return _moeda(valor)


@register.filter
def tamanho_arquivo(n):
    n = n or 0
    for un in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {un}" if un == "B" else f"{n:.1f} {un}"
        n /= 1024
    return f"{n:.1f} TB"


@register.simple_tag
def status_cor(valor):
    v = str(valor).lower()
    if any(x in v for x in ("vencido", "rejeit", "cancel", "atras", "deslig")):
        return "danger"
    if any(x in v for x in ("pago", "conclu", "emitida", "ativo", "aprov", "paga")):
        return "success"
    if any(x in v for x in ("parcial", "andamento", "enviada", "ferias", "férias")):
        return "warning"
    return "secondary"


@register.filter(name="badge")
def badge_filter(texto):
    from apps.core.crud import badge

    return badge(texto)
