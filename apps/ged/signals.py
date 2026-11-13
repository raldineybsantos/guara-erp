from django.dispatch import Signal

# Enviado depois de cada assinatura digital: sender=Documento, documento=..., assinatura=...
documento_assinado = Signal()
