"""Numeração anual sequencial de documentos: PREFIXO-AAAA-NNNN (ex.: PC-2026-0001)."""

from django.db import models, transaction
from django.db.models import Max
from django.utils import timezone


def proximo_numero(modelo, prefixo, campo="numero"):
    ano = timezone.localdate().year
    inicio = f"{prefixo}-{ano}-"
    ultimo = (modelo.objects.select_for_update().filter(**{f"{campo}__startswith": inicio})
              .aggregate(m=Max(campo))["m"])
    seq = int(ultimo.rsplit("-", 1)[1]) + 1 if ultimo else 1
    return f"{inicio}{seq:04d}"


class NumeradoMixin(models.Model):
    PREFIXO = ""
    numero = models.CharField("número", max_length=20, unique=True, editable=False)

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.numero:
            return super().save(*args, **kwargs)
        with transaction.atomic():
            self.numero = proximo_numero(type(self), self.PREFIXO)
            return super().save(*args, **kwargs)

    def __str__(self):
        return self.numero
