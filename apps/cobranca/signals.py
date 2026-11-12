from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.financeiro.models import ContaReceber


@receiver(post_save, sender=ContaReceber)
def sincronizar_status_cobranca(sender, instance, **kwargs):
    """Quando o título é pago/cancelado, as cobranças acompanham."""
    from .models import Cobranca

    if instance.status == "pago":
        Cobranca.objects.filter(conta_receber=instance).exclude(status="paga").update(status="paga")
    elif instance.status == "cancelado":
        Cobranca.objects.filter(conta_receber=instance).exclude(status="cancelada").update(status="cancelada")
    elif instance.status in ("aberto", "parcial"):
        Cobranca.objects.filter(conta_receber=instance, status="paga").update(status="enviada")
