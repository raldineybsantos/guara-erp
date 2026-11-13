"""Cálculo de prazos de SLA em horário comercial (ou 24x7).

SERVICEDESK_HORARIO = "08:00-18:00" conta só de segunda a sexta nesse horário; "24x7" conta corrido.
"""

import datetime

from django.conf import settings
from django.utils import timezone


def _janela():
    h = getattr(settings, "SERVICEDESK_HORARIO", "08:00-18:00").strip()
    if h.lower() in ("", "0", "24x7"):
        return None
    ini, fim = (datetime.time.fromisoformat(x.strip()) for x in h.split("-"))
    return ini, fim


def _expediente(dia, janela):
    tz = timezone.get_current_timezone()
    return (timezone.make_aware(datetime.datetime.combine(dia, janela[0]), tz),
            timezone.make_aware(datetime.datetime.combine(dia, janela[1]), tz))


def somar(inicio, segundos):
    """Instante em que se completam ``segundos`` de atendimento a partir de ``inicio``."""
    janela = _janela()
    if janela is None:
        return inicio + datetime.timedelta(seconds=segundos)
    atual = timezone.localtime(inicio)
    restante = datetime.timedelta(seconds=segundos)
    for _ in range(3650):
        ini, fim = _expediente(atual.date(), janela)
        if atual.weekday() >= 5 or atual >= fim:
            atual = ini + datetime.timedelta(days=1)
            continue
        if atual < ini:
            atual = ini
        disponivel = fim - atual
        if restante <= disponivel:
            return atual + restante
        restante -= disponivel
        atual = ini + datetime.timedelta(days=1)
    return atual


def entre(a, b):
    """Segundos de atendimento entre dois instantes (0 se b <= a)."""
    if b <= a:
        return 0
    janela = _janela()
    if janela is None:
        return int((b - a).total_seconds())
    total, atual, b = 0, timezone.localtime(a), timezone.localtime(b)
    while atual < b:
        ini, fim = _expediente(atual.date(), janela)
        if atual.weekday() < 5:
            s, e = max(atual, ini), min(b, fim)
            if e > s:
                total += (e - s).total_seconds()
        atual = ini + datetime.timedelta(days=1)
    return int(total)
