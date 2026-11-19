"""Borderô de impostos (FINA241): recolhe várias guias de tributos retidos de uma vez, com multa e juros por atraso."""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from apps.core.models import ModeloBase, PorEmpresaMixin
from apps.core.numeracao import NumeradoMixin

from .models import ContaBancaria, GuiaTributo

LIMITE = 100


class BorderoImposto(NumeradoMixin, ModeloBase, PorEmpresaMixin):
    PREFIXO = "BIM"
    STATUS = [("efetivado", "Recolhido"), ("estornado", "Estornado")]

    conta_bancaria = models.ForeignKey(ContaBancaria, verbose_name="conta pagadora", on_delete=models.PROTECT,
                                       related_name="+")
    data = models.DateField("data do recolhimento", default=timezone.localdate)
    selic_pct = models.DecimalField("SELIC acumulada (%)", max_digits=6, decimal_places=2, default=0,
                                    help_text="Usada nos juros das guias vencidas. A SELIC acumulada vem da Receita Federal.")
    total = models.DecimalField("total recolhido", max_digits=14, decimal_places=2, default=0, editable=False)
    status = models.CharField(max_length=10, choices=STATUS, default="efetivado", editable=False)

    class Meta:
        verbose_name = "borderô de impostos"
        verbose_name_plural = "borderôs de impostos"
        ordering = ["-data", "-id"]
        permissions = [("recolher_bordero_imposto", "Pode recolher guias de tributos em lote")]

    @classmethod
    @transaction.atomic
    def recolher(cls, guias_ids, conta_bancaria, data, selic_pct=0, usuario=None):
        ids = list(dict.fromkeys(guias_ids))
        if not ids:
            raise ValidationError("Selecione ao menos uma guia.")
        if len(ids) > LIMITE:
            raise ValidationError(f"O borderô aceita no máximo {LIMITE} guias.")
        if not conta_bancaria.ativa:
            raise ValidationError("A conta bancária está inativa.")
        guias = list(GuiaTributo.objects.select_for_update().filter(pk__in=ids).order_by("vencimento", "pk"))
        if len(guias) != len(ids):
            raise ValidationError("Alguma guia selecionada não existe mais.")
        empresas = {g.empresa_id for g in guias}
        if len(empresas) > 1:
            raise ValidationError("O borderô não pode misturar guias de empresas diferentes.")
        b = cls(conta_bancaria=conta_bancaria, data=data, selic_pct=Decimal(selic_pct or 0), empresa_id=next(iter(empresas)))
        if usuario is not None and getattr(usuario, "pk", None):
            b.criado_por = usuario
        b.save()
        total = Decimal("0")
        for g in guias:
            if g.status != "aberta":
                raise ValidationError(f"A guia {g} já foi recolhida.")
            multa, juros = g.calcular_encargos(data, b.selic_pct)
            g.recolher(conta_bancaria, data, multa, juros)
            total += g.total
            ItemBorderoImposto.objects.create(bordero=b, guia=g, valor=g.valor, multa=multa, juros=juros)
        b.total = total
        b.save(update_fields=["total", "atualizado_em"])
        return b

    @transaction.atomic
    def estornar(self):
        if self.status != "efetivado":
            raise ValidationError("O borderô já foi estornado.")
        for it in self.itens.select_related("guia"):
            it.guia.estornar_recolhimento()
        self.status = "estornado"
        self.save(update_fields=["status", "atualizado_em"])


class ItemBorderoImposto(models.Model):
    bordero = models.ForeignKey(BorderoImposto, on_delete=models.CASCADE, related_name="itens")
    guia = models.ForeignKey(GuiaTributo, on_delete=models.PROTECT, related_name="itens_bordero")
    valor = models.DecimalField(max_digits=14, decimal_places=2)
    multa = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    juros = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    class Meta:
        verbose_name = "guia do borderô"
        verbose_name_plural = "guias do borderô"
        ordering = ["id"]

    def __str__(self):
        return str(self.guia)

    @property
    def total(self):
        return self.valor + self.multa + self.juros
