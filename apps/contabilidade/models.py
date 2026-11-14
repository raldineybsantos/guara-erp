"""Contabilidade em razão único: cada evento do ERP (título, baixa, movimento de estoque, faturamento) grava
na hora um lançamento de partidas dobradas. Lançamentos não se editam nem se apagam: corrige-se por estorno."""

import datetime
from decimal import Decimal

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Sum
from django.utils import timezone

from apps.core.middleware import usuario_atual
from apps.core.models import ModeloBase
from apps.core.numeracao import NumeradoMixin

ZERO = Decimal("0")
NATUREZAS = [("ativo", "Ativo"), ("passivo", "Passivo"), ("pl", "Patrimônio líquido"), ("receita", "Receita"),
             ("deducao", "Dedução da receita"), ("custo", "Custo"), ("despesa", "Despesa")]
DEVEDORAS = ("ativo", "deducao", "custo", "despesa")

EVENTOS = [
    ("titulo_pagar", "Conta a pagar lançada (despesa)"),
    ("titulo_pagar_compra", "Conta a pagar de pedido de compra"),
    ("pagamento", "Pagamento de conta a pagar"),
    ("juros_pagos", "Juros/multa pagos a fornecedor"),
    ("desconto_obtido", "Desconto obtido de fornecedor"),
    ("titulo_receber", "Conta a receber lançada (receita)"),
    ("recebimento", "Recebimento de cliente"),
    ("juros_recebidos", "Juros/multa recebidos de cliente"),
    ("desconto_concedido", "Desconto concedido a cliente"),
    ("impostos_venda", "Impostos sobre a venda (faturamento)"),
    ("compra_estoque", "Entrada de compra no estoque"),
    ("compra_despesa", "Recebimento de compra sem estoque (serviço/consumo direto)"),
    ("cmv", "Custo da mercadoria vendida (saída por venda)"),
    ("consumo_estoque", "Consumo interno, manutenção e EPI"),
    ("ajuste_estoque_entrada", "Ajuste de estoque para mais"),
    ("ajuste_estoque_saida", "Ajuste de estoque para menos"),
    ("receita_avulsa", "Entrada bancária sem título"),
    ("despesa_avulsa", "Saída bancária sem título"),
]


class ContaContabil(ModeloBase):
    codigo = models.CharField("código", max_length=20, unique=True)
    nome = models.CharField(max_length=120)
    natureza = models.CharField(max_length=8, choices=NATUREZAS)
    superior = models.ForeignKey("self", verbose_name="conta superior", null=True, blank=True,
                                 on_delete=models.PROTECT, related_name="filhas")
    sintetica = models.BooleanField("sintética (totalizadora)", default=False,
                                    help_text="Contas sintéticas só somam as filhas e não recebem lançamentos.")
    ativo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "conta contábil"
        verbose_name_plural = "plano de contas contábil"
        ordering = ["codigo"]

    def __str__(self):
        return f"{self.codigo} {self.nome}"

    @property
    def devedora(self):
        return self.natureza in DEVEDORAS

    def clean(self):
        if self.sintetica and self.pk and self.partidas.exists():
            raise ValidationError({"sintetica": "Esta conta já tem lançamentos e não pode virar sintética."})


class RegraContabil(models.Model):
    """De-para: qual conta debitar e qual creditar em cada evento automático."""

    evento = models.CharField(max_length=30, choices=EVENTOS, unique=True)
    conta_debito = models.ForeignKey(ContaContabil, verbose_name="debitar", on_delete=models.PROTECT,
                                     related_name="+", limit_choices_to={"sintetica": False})
    conta_credito = models.ForeignKey(ContaContabil, verbose_name="creditar", on_delete=models.PROTECT,
                                      related_name="+", limit_choices_to={"sintetica": False})
    ativo = models.BooleanField(default=True, help_text="Desmarque para não contabilizar este evento.")

    class Meta:
        verbose_name = "regra de contabilização"
        verbose_name_plural = "regras de contabilização"
        ordering = ["evento"]

    def __str__(self):
        return self.get_evento_display()


class PeriodoFechado(models.Model):
    ano = models.PositiveSmallIntegerField()
    mes = models.PositiveSmallIntegerField("mês")
    fechado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    fechado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "período fechado"
        verbose_name_plural = "períodos fechados"
        ordering = ["-ano", "-mes"]
        constraints = [models.UniqueConstraint(fields=["ano", "mes"], name="periodo_contabil_unico")]

    def __str__(self):
        return f"{self.mes:02d}/{self.ano}"

    @classmethod
    def fechado(cls, data):
        return cls.objects.filter(ano=data.year, mes=data.month).exists()

    @classmethod
    def proxima_data_aberta(cls, data):
        """A própria data, ou o 1º dia do primeiro mês aberto depois dela."""
        fechados = set(cls.objects.values_list("ano", "mes"))
        while (data.year, data.month) in fechados:
            data = (data.replace(day=1) + datetime.timedelta(days=32)).replace(day=1)
        return data


class Lancamento(NumeradoMixin, models.Model):
    PREFIXO = "LC"
    data = models.DateField(db_index=True)
    historico = models.CharField("histórico", max_length=250)
    evento = models.CharField(max_length=30, choices=EVENTOS, blank=True, db_index=True,
                              help_text="Vazio = lançamento manual.")
    content_type = models.ForeignKey(ContentType, null=True, blank=True, on_delete=models.SET_NULL)
    object_id = models.PositiveBigIntegerField(null=True, blank=True)
    referencia = GenericForeignKey("content_type", "object_id")
    estorno_de = models.OneToOneField("self", null=True, blank=True, on_delete=models.PROTECT,
                                      related_name="estornado_por")
    criado_em = models.DateTimeField(auto_now_add=True)
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")

    class Meta:
        verbose_name = "lançamento contábil"
        verbose_name_plural = "lançamentos (diário)"
        ordering = ["-data", "-id"]
        indexes = [models.Index(fields=["content_type", "object_id", "evento"])]

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse("contabilidade:lancamento_detail", args=[self.pk])

    @property
    def valor(self):
        return self.partidas.filter(tipo="D").aggregate(t=Sum("valor"))["t"] or ZERO

    @property
    def origem(self):
        return self.get_evento_display() if self.evento else "Manual"

    @property
    def situacao(self):
        if self.estorno_de_id:
            return "Estorno"
        return "Estornado" if hasattr(self, "estornado_por") else "Ativo"

    @transaction.atomic
    def estornar(self, data=None, motivo=""):
        if self.estorno_de_id:
            raise ValidationError("Um estorno não pode ser estornado; faça um novo lançamento.")
        if hasattr(self, "estornado_por"):
            raise ValidationError("Lançamento já estornado.")
        data = PeriodoFechado.proxima_data_aberta(data or max(self.data, timezone.localdate()))
        est = Lancamento.objects.create(data=data, historico=f"Estorno de {self.numero}. {motivo}".strip()[:250],
                                        evento=self.evento, content_type=self.content_type,
                                        object_id=self.object_id, estorno_de=self, criado_por=usuario_atual())
        for p in self.partidas.all():
            Partida.objects.create(lancamento=est, conta=p.conta, tipo="C" if p.tipo == "D" else "D", valor=p.valor,
                                   centro_custo=p.centro_custo)
        return est


class Partida(models.Model):
    TIPOS = [("D", "Débito"), ("C", "Crédito")]
    lancamento = models.ForeignKey(Lancamento, on_delete=models.CASCADE, related_name="partidas")
    conta = models.ForeignKey(ContaContabil, on_delete=models.PROTECT, related_name="partidas")
    tipo = models.CharField(max_length=1, choices=TIPOS)
    valor = models.DecimalField(max_digits=16, decimal_places=2)
    centro_custo = models.ForeignKey("financeiro.CentroCusto", verbose_name="centro de custo", null=True, blank=True,
                                     on_delete=models.PROTECT, related_name="+")

    class Meta:
        verbose_name = "partida"
        verbose_name_plural = "partidas"
        ordering = ["id"]

    @property
    def debito(self):
        return self.valor if self.tipo == "D" else None

    @property
    def credito(self):
        return self.valor if self.tipo == "C" else None
