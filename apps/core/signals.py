"""Auditoria automática: registra criação, alteração e exclusão nos módulos do ERP."""
from decimal import Decimal

from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

AUDITADOS = {"core", "financeiro", "cobranca", "fiscal", "workflow", "ged"}
IGNORAR = {"criado_em", "atualizado_em"}


def _auditado(sender):
    meta = sender._meta
    return meta.app_label in AUDITADOS and meta.model_name != "logauditoria"


def _valores(obj):
    dados = {}
    for f in obj._meta.concrete_fields:
        if f.name in IGNORAR:
            continue
        v = getattr(obj, f.attname)
        if isinstance(v, Decimal):
            v = f"{v:.{f.decimal_places}f}"
        elif hasattr(v, "isoformat"):
            v = v.isoformat()
        elif hasattr(v, "name") and not isinstance(v, (str, int)):  # FieldFile
            v = v.name
        dados[f.name] = v
    return dados


@receiver(pre_save)
def guardar_estado_anterior(sender, instance, **kwargs):
    if not _auditado(sender) or not instance.pk:
        return
    antigo = sender.objects.filter(pk=instance.pk).first()
    instance._estado_anterior = _valores(antigo) if antigo else None


@receiver(post_save)
def registrar_salvamento(sender, instance, created, raw=False, **kwargs):
    if raw or not _auditado(sender):
        return
    from .models import LogAuditoria

    if created:
        LogAuditoria.registrar("criar", instance)
        return
    anterior = getattr(instance, "_estado_anterior", None) or {}
    atual = _valores(instance)
    diff = {k: [anterior.get(k), v] for k, v in atual.items() if anterior.get(k) != v}
    if diff:
        LogAuditoria.registrar("alterar", instance, alteracoes=diff)


@receiver(post_delete)
def registrar_exclusao(sender, instance, **kwargs):
    if not _auditado(sender):
        return
    from .models import LogAuditoria

    LogAuditoria.registrar("excluir", instance)
