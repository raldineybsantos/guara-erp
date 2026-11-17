import datetime
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
    convenio = models.CharField("convênio de pagamentos", max_length=20, blank=True,
                                help_text="Código do convênio informado pelo banco para a remessa CNAB.")
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
    conta_contabil = models.ForeignKey(
        "contabilidade.ContaContabil", verbose_name="conta contábil", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+", limit_choices_to={"sintetica": False},
        help_text="Títulos desta categoria são contabilizados nesta conta de resultado. Em branco = conta "
                  "padrão da regra de contabilização.")
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
        partes = self._partes_retencao(valor)
        retido = sum((v for _, v in partes), Decimal("0"))
        mov = Movimentacao(
            empresa=self.empresa, conta_bancaria=conta_bancaria, data=data, valor=valor - retido,
            tipo=self.tipo_movimento, descricao=f"Baixa: {self}"[:200], plano_conta=self.plano_conta,
            centro_custo=self.centro_custo, observacoes=observacao,
            **{self._meta.model_name: self},
        )
        mov._bruto = valor  # a contabilidade quita o título pelo valor bruto, não pelo líquido pago
        mov.save()
        self._registrar_retencoes(mov, partes)
        self.valor_pago += valor
        self.data_pagamento = data
        self.conta_bancaria = conta_bancaria
        if forma_pagamento:
            self.forma_pagamento = forma_pagamento
        self.atualizar_status()
        self.save()
        return mov

    def _partes_retencao(self, valor):
        """[(Retencao, valor retido nesta baixa)]. Só contas a pagar retêm tributos."""
        return []

    def _registrar_retencoes(self, mov, partes):
        pass

    @property
    def saldo_liquido(self):
        """Quanto sai (ou entra) de dinheiro para quitar o saldo: o saldo menos os tributos ainda a reter."""
        return self.saldo

    def bruto_de_liquido(self, liquido):
        """Converte o dinheiro movimentado (extrato, retorno do banco) no valor baixado do título."""
        return Decimal(liquido)

    def _antes_de_estornar(self):
        pass

    @transaction.atomic
    def estornar(self):
        self._antes_de_estornar()
        for mov in self.movimentacoes.all():
            mov.delete()
        self._depois_de_estornar()
        self.valor_pago = Decimal("0")
        self.data_pagamento = None
        self.status = "aberto"
        self.save()

    def _depois_de_estornar(self):
        pass

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

    @property
    def retencoes_previstas(self):
        return sum((r.valor for r in self.retencoes.all()), Decimal("0"))
    retencoes_previstas.fget.short_description = "tributos retidos (previsto)"

    def _pendente_de_reter(self):
        return [(r, r.valor - r.retido) for r in self.retencoes.select_related("regra")
                if r.valor - r.retido > 0]

    @property
    def saldo_liquido(self):
        return self.saldo - sum((v for _, v in self._pendente_de_reter()), Decimal("0"))

    def bruto_de_liquido(self, liquido):
        liquido = Decimal(liquido)
        if liquido == self.saldo_liquido:
            return self.saldo
        pend = sum((v for _, v in self._pendente_de_reter()), Decimal("0"))
        if not pend or self.saldo <= 0:
            return liquido
        return min((liquido / (1 - pend / self.saldo)).quantize(Decimal("0.01")), self.saldo)

    def _partes_retencao(self, valor):
        """Retenção proporcional ao que está sendo pago; a última baixa leva o que sobrar (sem perder centavo)."""
        partes = []
        for r, pendente in self._pendente_de_reter():
            if valor >= self.saldo:
                parte = pendente
            else:
                parte = min((r.valor * valor / self.valor_total).quantize(Decimal("0.01")), pendente)
            if parte > 0:
                partes.append((r, parte))
        return partes

    def _registrar_retencoes(self, mov, partes):
        if not partes:
            return
        from apps.contabilidade import servicos

        for r, parte in partes:
            guia = GuiaTributo.da_competencia(r.regra, mov.data, self.empresa)
            RetencaoBaixa.objects.create(retencao=r, movimentacao=mov, guia=guia, valor=parte)
            guia.recalcular()
        servicos.lancar("retencao_tributo", sum(v for _, v in partes), mov.data,
                        f"Tributos retidos: {self}", mov, self.centro_custo, empresa=self.empresa)

    def _antes_de_estornar(self):
        pagas = RetencaoBaixa.objects.filter(movimentacao__contapagar=self, guia__status="paga")
        if pagas.exists():
            raise ValidationError("Há tributos retidos deste título em guia já recolhida. "
                                  "Estorne o recolhimento da guia antes de estornar o pagamento.")
        self._guias_afetadas = list(GuiaTributo.objects.filter(itens__movimentacao__contapagar=self).distinct())

    def _depois_de_estornar(self):
        for g in getattr(self, "_guias_afetadas", []):
            g.recalcular()

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
    guia = models.ForeignKey("GuiaTributo", verbose_name="guia de tributo", null=True, blank=True,
                             on_delete=models.CASCADE, related_name="movimentacoes", editable=False)
    conciliado = models.BooleanField(default=False)
    transferencia = models.BooleanField("transferência entre contas", default=False, editable=False)
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


# --------------------------------------------------------------------------- retenção de tributos na fonte
TRIBUTOS = [("IRRF", "IRRF"), ("PIS", "PIS"), ("COFINS", "COFINS"), ("CSLL", "CSLL"), ("INSS", "INSS"),
            ("ISS", "ISS")]


class RegraRetencao(ModeloBase):
    """Tributo retido na fonte ao pagar um fornecedor. Alíquotas, código de receita e dia de vencimento são
    parâmetros da empresa: a legislação muda e varia por tipo de serviço, então quem define é o contador."""

    tributo = models.CharField(max_length=10, choices=TRIBUTOS)
    nome = models.CharField(max_length=120, help_text="Ex.: IRRF sobre serviços de pessoa jurídica.")
    aliquota = models.DecimalField("alíquota (%)", max_digits=6, decimal_places=4)
    codigo_receita = models.CharField("código de receita (DARF)", max_length=10, blank=True,
                                      help_text="Tributos com o mesmo código vão na mesma guia.")
    dia_vencimento = models.PositiveSmallIntegerField("dia de vencimento (mês seguinte)", default=20)
    base_minima = models.DecimalField("base mínima", max_digits=14, decimal_places=2, default=0,
                                      help_text="Abaixo deste valor a retenção não é calculada.")
    ativa = models.BooleanField(default=True)

    class Meta:
        verbose_name = "regra de retenção"
        verbose_name_plural = "regras de retenção"
        ordering = ["tributo", "nome"]

    def __str__(self):
        return f"{self.nome} ({self.aliquota}%)"

    def clean(self):
        if self.aliquota is not None and not (0 < self.aliquota <= 100):
            raise ValidationError({"aliquota": "Informe uma alíquota entre 0 e 100."})
        if not 1 <= (self.dia_vencimento or 0) <= 28:
            raise ValidationError({"dia_vencimento": "Use um dia entre 1 e 28."})


class Retencao(ModeloBase):
    conta_pagar = models.ForeignKey(ContaPagar, verbose_name="conta a pagar", on_delete=models.CASCADE,
                                    related_name="retencoes")
    regra = models.ForeignKey(RegraRetencao, verbose_name="regra", on_delete=models.PROTECT, related_name="+")
    base = models.DecimalField("base de cálculo", max_digits=14, decimal_places=2)
    aliquota = models.DecimalField("alíquota (%)", max_digits=6, decimal_places=4)
    valor = models.DecimalField("valor a reter", max_digits=14, decimal_places=2)

    class Meta:
        verbose_name = "retenção"
        verbose_name_plural = "retenções"
        ordering = ["regra__tributo", "pk"]
        constraints = [models.UniqueConstraint(fields=["conta_pagar", "regra"], name="retencao_unica_por_regra")]

    def __str__(self):
        return f"{self.regra.tributo} {self.valor}"

    @property
    def retido(self):
        return self.baixas.aggregate(t=Sum("valor"))["t"] or Decimal("0")
    retido.fget.short_description = "já retido"


class GuiaTributo(ModeloBase, PorEmpresaMixin):
    """Guia (DARF etc.) com os tributos retidos de um mês, por código de receita."""

    STATUS = [("aberta", "A recolher"), ("paga", "Recolhida")]
    tributo = models.CharField(max_length=10, choices=TRIBUTOS)
    codigo_receita = models.CharField("código de receita", max_length=10, blank=True)
    competencia = models.DateField("competência", help_text="Mês em que o tributo foi retido.")
    vencimento = models.DateField()
    valor = models.DecimalField(max_digits=14, decimal_places=2, default=0, editable=False)
    status = models.CharField(max_length=10, choices=STATUS, default="aberta", editable=False)
    data_pagamento = models.DateField("data do recolhimento", null=True, blank=True, editable=False)
    conta_bancaria = models.ForeignKey(ContaBancaria, verbose_name="conta bancária", null=True, blank=True,
                                       on_delete=models.PROTECT, related_name="+", editable=False)

    class Meta:
        verbose_name = "guia de tributo"
        verbose_name_plural = "guias de tributos retidos"
        ordering = ["vencimento", "tributo"]

    def __str__(self):
        nome = f"DARF {self.codigo_receita}" if self.codigo_receita else self.tributo
        return f"{nome} — {self.competencia:%m/%Y}"

    @classmethod
    def da_competencia(cls, regra, data, empresa):
        comp = data.replace(day=1)
        cod = regra.codigo_receita
        guia = cls.objects.filter(empresa=empresa, competencia=comp, status="aberta",
                                  codigo_receita=cod, **({} if cod else {"tributo": regra.tributo})).first()
        if guia:
            return guia
        prox = (comp + datetime.timedelta(days=32)).replace(day=min(regra.dia_vencimento, 28))
        return cls.objects.create(empresa=empresa, tributo=regra.tributo, codigo_receita=cod, competencia=comp,
                                  vencimento=prox)

    def recalcular(self):
        total = self.itens.aggregate(t=Sum("valor"))["t"] or Decimal("0")
        if total <= 0:
            self.delete()
            return
        self.valor = total
        self.save(update_fields=["valor", "atualizado_em"])

    @transaction.atomic
    def recolher(self, conta_bancaria, data):
        if self.status == "paga":
            raise ValidationError("Guia já recolhida.")
        Movimentacao.objects.create(
            empresa=self.empresa, conta_bancaria=conta_bancaria, data=data, valor=self.valor, tipo="saida",
            descricao=f"Recolhimento: {self}"[:200], guia=self)
        self.status, self.data_pagamento, self.conta_bancaria = "paga", data, conta_bancaria
        self.save(update_fields=["status", "data_pagamento", "conta_bancaria", "atualizado_em"])

    @transaction.atomic
    def estornar_recolhimento(self):
        if self.status != "paga":
            raise ValidationError("A guia não está recolhida.")
        self.movimentacoes.all().delete()
        self.status, self.data_pagamento, self.conta_bancaria = "aberta", None, None
        self.save(update_fields=["status", "data_pagamento", "conta_bancaria", "atualizado_em"])


class RetencaoBaixa(models.Model):
    """O quanto de cada retenção foi efetivamente retido em cada pagamento (some junto com o estorno)."""

    retencao = models.ForeignKey(Retencao, on_delete=models.CASCADE, related_name="baixas")
    movimentacao = models.ForeignKey(Movimentacao, on_delete=models.CASCADE, related_name="retencoes_baixa")
    guia = models.ForeignKey(GuiaTributo, on_delete=models.CASCADE, related_name="itens")
    valor = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        verbose_name = "tributo retido no pagamento"
        verbose_name_plural = "tributos retidos nos pagamentos"


# --------------------------------------------------------------------------- rateio por centro de custo
class RateioTitulo(ModeloBase):
    """Divide o valor de um título entre centros de custo, para a análise gerencial."""

    conta_pagar = models.ForeignKey(ContaPagar, null=True, blank=True, on_delete=models.CASCADE,
                                    related_name="rateios")
    conta_receber = models.ForeignKey(ContaReceber, null=True, blank=True, on_delete=models.CASCADE,
                                      related_name="rateios")
    centro_custo = models.ForeignKey(CentroCusto, verbose_name="centro de custo", on_delete=models.PROTECT,
                                     related_name="+")
    percentual = models.DecimalField("percentual (%)", max_digits=6, decimal_places=2)

    class Meta:
        verbose_name = "rateio"
        verbose_name_plural = "rateios"
        ordering = ["pk"]
        constraints = [models.CheckConstraint(
            condition=(models.Q(conta_pagar__isnull=False, conta_receber__isnull=True)
                       | models.Q(conta_pagar__isnull=True, conta_receber__isnull=False)),
            name="rateio_de_um_so_titulo")]


def partes_por_centro(titulo):
    """[(centro de custo ou None, valor)] — pelo rateio, ou tudo no centro do título. Soma exatamente o valor."""
    linhas = list(titulo.rateios.select_related("centro_custo"))
    if not linhas:
        return [(titulo.centro_custo, titulo.valor)]
    partes, acumulado = [], Decimal("0")
    for i, ln in enumerate(linhas):
        v = (titulo.valor - acumulado) if i == len(linhas) - 1 else (titulo.valor * ln.percentual / 100).quantize(
            Decimal("0.01"))
        acumulado += v
        partes.append((ln.centro_custo, v))
    return partes


def definir_rateio(titulo, linhas):
    """Substitui o rateio. `linhas` = [(centro_custo, percentual)]; vazio remove. Exige total de 100%."""
    linhas = [(c, Decimal(p)) for c, p in linhas if c is not None and p]
    if linhas:
        if sum(p for _, p in linhas) != Decimal("100"):
            raise ValidationError("Os percentuais do rateio devem somar 100%.")
        if len({c.pk for c, _ in linhas}) != len(linhas):
            raise ValidationError("Cada centro de custo só pode aparecer uma vez no rateio.")
        if any(p <= 0 for _, p in linhas):
            raise ValidationError("Os percentuais devem ser maiores que zero.")
    campo = "conta_pagar" if titulo._meta.model_name == "contapagar" else "conta_receber"
    with transaction.atomic():
        titulo.rateios.all().delete()
        for c, p in linhas:
            RateioTitulo.objects.create(centro_custo=c, percentual=p, **{campo: titulo})
