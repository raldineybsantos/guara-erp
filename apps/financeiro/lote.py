"""Liquidação em lote (FINA565): baixa de vários títulos, todos pelo saldo, numa só operação atômica,
com conferência do total e possibilidade de estorno do lote."""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from apps.core.models import ModeloBase, PorEmpresaMixin
from apps.core.numeracao import NumeradoMixin

from .models import FORMAS_PAGAMENTO, ContaBancaria, ContaPagar, ContaReceber, Movimentacao

MODELOS = {"pagar": ContaPagar, "receber": ContaReceber}
LIMITE_LOTE = 200


class LiquidacaoLote(NumeradoMixin, ModeloBase, PorEmpresaMixin):
    PREFIXO = "LQL"
    TIPOS = [("pagar", "Contas a pagar"), ("receber", "Contas a receber")]
    STATUS = [("efetivada", "Efetivada"), ("estornada", "Estornada")]

    tipo = models.CharField(max_length=8, choices=TIPOS)
    conta_bancaria = models.ForeignKey(ContaBancaria, verbose_name="conta bancária", on_delete=models.PROTECT,
                                       related_name="+")
    data = models.DateField(default=timezone.localdate)
    forma_pagamento = models.CharField("forma de pagamento", max_length=20, choices=FORMAS_PAGAMENTO, blank=True)
    total = models.DecimalField("total dos títulos", max_digits=14, decimal_places=2, default=0, editable=False)
    liquido = models.DecimalField("total movimentado na conta", max_digits=14, decimal_places=2, default=0,
                                  editable=False)
    quantidade = models.PositiveIntegerField(default=0, editable=False)
    status = models.CharField(max_length=10, choices=STATUS, default="efetivada", editable=False)

    class Meta:
        verbose_name = "liquidação em lote"
        verbose_name_plural = "liquidações em lote"
        ordering = ["-data", "-id"]
        permissions = [("liquidar_lote", "Pode liquidar títulos em lote e estornar lotes")]

    @classmethod
    @transaction.atomic
    def liquidar(cls, tipo, ids, conta_bancaria, data, total_conferido, forma_pagamento="", usuario=None):
        """Baixa cada título pelo saldo. Tudo ou nada: qualquer erro desfaz o lote inteiro."""
        if tipo not in MODELOS:
            raise ValidationError("Tipo inválido.")
        ids = list(dict.fromkeys(ids))
        if not ids:
            raise ValidationError("Selecione ao menos um título.")
        if len(ids) > LIMITE_LOTE:
            raise ValidationError(f"O lote aceita no máximo {LIMITE_LOTE} títulos.")
        modelo = MODELOS[tipo]
        titulos = list(modelo.objects.select_for_update().filter(pk__in=ids).order_by("vencimento", "pk"))
        if len(titulos) != len(ids):
            raise ValidationError("Algum título selecionado não existe mais.")
        empresas = {t.empresa_id for t in titulos}
        if len(empresas) > 1:
            raise ValidationError("O lote não pode misturar títulos de empresas diferentes.")
        if conta_bancaria.empresa_id and empresas != {conta_bancaria.empresa_id} and None not in empresas:
            raise ValidationError("A conta bancária pertence a outra empresa.")
        if not conta_bancaria.ativa:
            raise ValidationError("A conta bancária está inativa.")
        total = sum((t.saldo for t in titulos), Decimal("0"))
        if total_conferido is None or Decimal(total_conferido) != total:
            raise ValidationError(f"O total conferido não bate: os títulos somam {total}.")
        lote = cls(tipo=tipo, conta_bancaria=conta_bancaria, data=data, forma_pagamento=forma_pagamento,
                   empresa_id=next(iter(empresas)))
        if usuario is not None and getattr(usuario, "pk", None):
            lote.criado_por = usuario
        lote.save()
        liquido = Decimal("0")
        for t in titulos:
            if t.status in ("pago", "cancelado", "renegociado") or t.saldo <= 0:
                raise ValidationError(f"“{t}” não está em aberto.")
            mov = t.baixar(t.saldo, data, conta_bancaria, forma_pagamento, f"Lote {lote.numero}")
            liquido += mov.valor
            ItemLiquidacao.objects.create(lote=lote, valor=mov.valor,
                                          movimentacao=mov, **{tipo == "pagar" and "contapagar" or "contareceber": t})
        lote.total, lote.liquido, lote.quantidade = total, liquido, len(titulos)
        lote.save(update_fields=["total", "liquido", "quantidade", "atualizado_em"])
        return lote

    @transaction.atomic
    def estornar(self):
        if self.status != "efetivada":
            raise ValidationError("O lote já foi estornado.")
        itens = list(self.itens.select_related("contapagar", "contareceber", "movimentacao"))
        for it in itens:
            t = it.titulo
            if t.movimentacoes.count() != 1:
                raise ValidationError(f"“{t}” tem outros pagamentos além deste lote. Estorne o título individualmente.")
            if t.movimentacoes.get().pk != it.movimentacao_id:
                raise ValidationError(f"“{t}” teve a baixa alterada depois do lote.")
        for it in itens:
            it.titulo.estornar()
        self.status = "estornada"
        self.save(update_fields=["status", "atualizado_em"])


class ItemLiquidacao(models.Model):
    lote = models.ForeignKey(LiquidacaoLote, on_delete=models.CASCADE, related_name="itens")
    contapagar = models.ForeignKey(ContaPagar, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    contareceber = models.ForeignKey(ContaReceber, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    valor = models.DecimalField("valor movimentado", max_digits=14, decimal_places=2)
    movimentacao = models.ForeignKey(Movimentacao, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        verbose_name = "item da liquidação"
        verbose_name_plural = "itens da liquidação"
        ordering = ["id"]

    def __str__(self):
        return str(self.titulo)

    @property
    def titulo(self):
        return self.contapagar or self.contareceber
