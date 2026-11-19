"""Rotina diária do RH: cria períodos aquisitivos e avisa sobre férias prestes a vencer."""
from django.contrib.auth.models import User
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Colaborador, notificar

from . import ferias, salarios, treinamentos
from .models import PeriodoAquisitivo

AVISAR_FALTANDO = (60, 30, 15, 7, 1)   # dias para o fim do período concessivo


def executar(hoje=None):
    hoje = hoje or timezone.localdate()
    novos = sum(ferias.gerar_periodos(c) for c in Colaborador.objects.exclude(status="desligado").filter(
        tipo_contrato__in=["clt", "temporario"]))
    rh = list(User.objects.filter(is_active=True).filter(
        Q(is_superuser=True) | Q(user_permissions__codename="aprovar_solicitacaoferias") |
        Q(groups__permissions__codename="aprovar_solicitacaoferias")).distinct())
    avisos = 0
    for p in PeriodoAquisitivo.objects.filter(fim__lt=hoje).select_related("colaborador"):
        if p.saldo <= 0 or p.colaborador.status == "desligado":
            continue
        faltam = (p.limite_concessivo - hoje).days
        if faltam in AVISAR_FALTANDO:
            destinos = list(rh) + ([p.colaborador.usuario] if p.colaborador.usuario else [])
            notificar(destinos, f"Férias de {p.colaborador} vencem em {faltam} dia(s) ({p.saldo} dias de saldo).",
                      url=reverse("rh:periodoaquisitivo_detail", args=[p.pk]))
            avisos += 1
    return {"periodos": novos, "avisos": avisos, "treinamentos": treinamentos.alertar_reciclagem(hoje),
            "salarios": salarios.aplicar_vigentes(hoje)}
