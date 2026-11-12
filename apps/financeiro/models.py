from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Sum
from django.utils import timezone

from apps.core.models import Cliente, Departamento, Fornecedor, ModeloBase, PorEmpresaMixin

FORMAS_PAGAMENTO = [
    ("pix", "PIX"), ("boleto", "Boleto"), ("ted", "TED/DOC"), ("dinheiro", "Dinheiro"),
    ("cartao_credito", "Cartão de crédito"), ("cartao_debito", "Cartão de débito"),
    ("debito_automatico", "Débito automático"), ("cheque", "Cheque"),
]


class ContaBancaria(ModeloBase, PorEmpresaMixin):
    TIPOS = [("corrente", "Conta corrente"), ("poupanca", "Poupança"), ("caixa", "Caixa interno"),
             ("investimento", "Investimento"), ("cartao", "Cartão")]
    nome = models.CharField(max_length=100)
    tipo = models.CharField(max_length=20, choices=TIPOS, default="corrente")
    banco = models.CharField(max_length=60, blank=True)
    codigo_banco = models.CharField("código do banco", max_length=5, blank=True)
    agencia = models.CharField("agência", max_length=10, blank=True)
    conta = models.CharField(max_length=20, blank=True)
    saldo_inicial = models.DecimalField("saldo inicial", max_digits=14, decimal_places=2, default=0)
    data_saldo_inicial = models.DateField("data do saldo inicial", default=timezone.localdate)
    ativa = models.BooleanField(default=True)

    class Meta:
        verbose_name = "conta bancária"
        verbose_name_plural = "contas bancárias"
        ordering = ["nome"]

    def __str__(self):
        return self.nome

    @property
    def saldo_atual(self):
        mov = self.movimentacoes.aggregate(
            e=Sum("valor", filter=models.Q(tipo="entrada")),
            s=Sum("valor", filter=models.Q(tipo="saida")),
        )
        return self.saldo_inicial + (mov["e"] or 0) - (mov["s"] or 0)
    saldo_atual.fget.short_description = "saldo atual"


class CentroCusto(ModeloBase):
    codigo = models.CharField("código", max_length=20, unique=True)
    nome = models.CharField(max_length=120)
    departamento = models.ForeignKey(Departamento, null=True, blank=True, on_delete=models.SET_NULL)
    ativo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "centro de custo"
        verbose_name_plural = "centros de custo"
        ordering = ["codigo"]

    def __str__(self):
        return f"{self.codigo} - {self.nome}"


class PlanoConta(ModeloBase):
    """Plano de contas / categorias financeiras (hierárquico)."""

    TIPOS = [("receita", "Receita"), ("despesa", "Despesa")]
    codigo = models.CharField("código", max_length=20, unique=True, help_text="Ex.: 1.01.002")
    nome = models.CharField(max_length=120)
    tipo = models.CharField(max_length=10, choices=TIPOS)
    superior = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="filhas")
    ativo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "plano de contas"
        verbose_name_plural = "plano de contas"
        ordering = ["codigo"]

    def __str__(self):
        return f"{self.codigo} {self.nome}"


class TituloQuerySet(models.QuerySet):
    def em_aberto(self):
        return self.filter(status__in=["aberto", "parcial"])

    def vencidos(self):
        return self.em_aberto().filter(vencimento__lt=timezone.localdate())


class Titulo(ModeloBase, PorEmpresaMixin):
    """Base comum para contas a pagar e a receber."""

    STATUS = [("aberto", "Em aberto"), ("parcial", "Pago parcialmente"), ("pago", "Pago"), ("cancelado", "Cancelado")]

    descricao = models.CharField("descrição", max_length=200)
    documento = models.CharField("nº documento", max_length=60, blank=True)
    plano_conta = models.ForeignKey(PlanoConta, verbose_name="categoria (plano de contas)", null=True, blank=True,
                                    on_delete=models.PROTECT, related_name="+")
    centro_custo = models.ForeignKey(CentroCusto, verbose_name="centro de custo", null=True, blank=True,
                                     on_delete=models.PROTECT, related_name="+")
    valor = models.DecimalField(max_digits=14, decimal_places=2)
    juros = models.DecimalField("juros/multa", max_digits=14, decimal_places=2, default=0)
    desconto = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_pago = models.DecimalField("valor pago", max_digits=14, decimal_places=2, default=0, editable=False)
    data_emissao = models.DateField("emissão", default=timezone.localdate)
    competencia = models.DateField("competência", null=True, blank=True)
    vencimento = models.DateField(db_index=True)
    data_pagamento = models.DateField("data de pagamento", null=True, blank=True, editable=False)
    forma_pagamento = models.CharField("forma de pagamento", max_length=20, choices=FORMAS_PAGAMENTO, blank=True)
    conta_bancaria = models.ForeignKey(ContaBancaria, verbose_name="conta bancária", null=True, blank=True,
                                       on_delete=models.PROTECT, related_name="+")
    parcela = models.PositiveSmallIntegerField(default=1)
    total_parcelas = models.PositiveSmallIntegerField("total de parcelas", default=1)
    status = models.CharField(max_length=10, choices=STATUS, default="aberto", db_index=True, editable=False)
    observacoes = models.TextField("observações", blank=True)

    objects = TituloQuerySet.as_manager()
    tipo_movimento = None  # "entrada" ou "saida"

    class Meta:
        abstract = True
        ordering = ["vencimento", "pk"]

    def __str__(self):
        sufixo = f" ({self.parcela}/{self.total_parcelas})" if self.total_parcelas > 1 else ""
        return f"{self.descricao}{sufixo}"

    @property
    def valor_total(self):
        return self.valor + self.juros - self.desconto
    valor_total.fget.short_description = "valor total"

    @property
    def saldo(self):
        return max(self.valor_total - self.valor_pago, Decimal("0"))
    saldo.fget.short_description = "saldo em aberto"

    @property
    def vencido(self):
        return self.status in ("aberto", "parcial") and self.vencimento < timezone.localdate()

    @property
    def situacao(self):
        return "Vencido" if self.vencido else self.get_status_display()
    situacao.fget.short_description = "situação"

    def clean(self):
        if self.valor is not None and self.valor <= 0:
            raise ValidationError({"valor": "O valor deve ser maior que zero."})

    def atualizar_status(self):
        if self.status == "cancelado":
            return
        if self.valor_pago <= 0:
            self.status = "aberto"
        elif self.valor_pago < self.valor_total:
            self.status = "parcial"
        else:
            self.status = "pago"

    @transaction.atomic
    def baixar(self, valor, data, conta_bancaria, forma_pagamento="", observacao=""):
        """Registra pagamento/recebimento (total ou parcial) e gera a movimentação bancária."""
        valor = Decimal(valor)
        if self.status in ("pago", "cancelado"):
            raise ValidationError("Título já está pago ou cancelado.")
        if valor <= 0 or valor > self.saldo:
            raise ValidationError(f"Valor deve estar entre 0,01 e {self.saldo}.")
        mov = Movimentacao.objects.create(
            empresa=self.empresa, conta_bancaria=conta_bancaria, data=data, valor=valor,
            tipo=self.tipo_movimento, descricao=f"Baixa: {self}"[:200], plano_conta=self.plano_conta,
            centro_custo=self.centro_custo, observacoes=observacao,
            **{self._meta.model_name: self},
        )
        self.valor_pago += valor
        self.data_pagamento = data
        self.conta_bancaria = conta_bancaria
        if forma_pagamento:
            self.forma_pagamento = forma_pagamento
        self.atualizar_status()
        self.save()
        return mov

    @transaction.atomic
    def estornar(self):
        for mov in self.movimentacoes.all():
            mov.delete()
        self.valor_pago = Decimal("0")
        self.data_pagamento = None
        self.status = "aberto"
        self.save()

    def cancelar(self):
        if self.valor_pago > 0:
            raise ValidationError("Estorne os pagamentos antes de cancelar.")
        self.status = "cancelado"
        self.save()


class ContaPagar(Titulo):
    fornecedor = models.ForeignKey(Fornecedor, null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="contas_pagar")
    codigo_barras = models.CharField("código de barras / linha digitável", max_length=60, blank=True)
    aprovado = models.BooleanField(default=False, help_text="Aprovado para pagamento (pode vir do workflow).")
    tipo_movimento = "saida"

    class Meta(Titulo.Meta):
        verbose_name = "conta a pagar"
        verbose_name_plural = "contas a pagar"
        permissions = [("baixar_contapagar", "Pode baixar (pagar) contas a pagar")]


class ContaReceber(Titulo):
    cliente = models.ForeignKey(Cliente, null=True, blank=True, on_delete=models.PROTECT,
                                related_name="contas_receber")
    tipo_movimento = "entrada"

    class Meta(Titulo.Meta):
        verbose_name = "conta a receber"
        verbose_name_plural = "contas a receber"
        permissions = [("baixar_contareceber", "Pode baixar (receber) contas a receber")]


class Movimentacao(ModeloBase, PorEmpresaMixin):
    """Extrato: toda entrada/saída de dinheiro em uma conta bancária."""

    TIPOS = [("entrada", "Entrada"), ("saida", "Saída")]
    conta_bancaria = models.ForeignKey(ContaBancaria, verbose_name="conta bancária", on_delete=models.PROTECT,
                                       related_name="movimentacoes")
    data = models.DateField(default=timezone.localdate, db_index=True)
    tipo = models.CharField(max_length=10, choices=TIPOS)
    valor = models.DecimalField(max_digits=14, decimal_places=2)
    descricao = models.CharField("descrição", max_length=200)
    plano_conta = models.ForeignKey(PlanoConta, verbose_name="categoria", null=True, blank=True,
                                    on_delete=models.PROTECT, related_name="+")
    centro_custo = models.ForeignKey(CentroCusto, verbose_name="centro de custo", null=True, blank=True,
                                     on_delete=models.PROTECT, related_name="+")
    contapagar = models.ForeignKey(ContaPagar, verbose_name="conta a pagar", null=True, blank=True,
                                   on_delete=models.CASCADE, related_name="movimentacoes", editable=False)
    contareceber = models.ForeignKey(ContaReceber, verbose_name="conta a receber", null=True, blank=True,
                                     on_delete=models.CASCADE, related_name="movimentacoes", editable=False)
    conciliado = models.BooleanField(default=False)
    observacoes = models.TextField("observações", blank=True)

    class Meta:
        verbose_name = "movimentação"
        verbose_name_plural = "movimentações (extrato)"
        ordering = ["-data", "-pk"]

    def __str__(self):
        return f"{self.data:%d/%m/%Y} {self.get_tipo_display()} {self.descricao}"

    def clean(self):
        if self.valor is not None and self.valor <= 0:
            raise ValidationError({"valor": "O valor deve ser maior que zero."})

    @property
    def valor_sinal(self):
        return self.valor if self.tipo == "entrada" else -self.valor
    valor_sinal.fget.short_description = "valor"
