from django.dispatch import receiver

from apps.ged.signals import documento_assinado


@receiver(documento_assinado)
def contrato_assinado(sender, documento, assinatura, **kwargs):
    """Cada assinatura digital no documento de um contrato aprovado confere se já pode ficar vigente."""
    from .models import Evento

    for contrato in documento.contratos.all():
        Evento.objects.create(contrato=contrato, tipo="assinatura", usuario=assinatura.usuario,
                              descricao=f"Assinatura digital de {assinatura.titular} ({assinatura.get_tipo_display()}).")
        contrato.conferir_assinaturas(assinatura.usuario)
