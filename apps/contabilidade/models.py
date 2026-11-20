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
from apps.core.models import Empresa, ModeloBase, PorEmpresaMixin
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
    ("retencao_tributo", "Tributo retido na fonte ao pagar fornecedor"),
    ("recolhimento_tributo", "Recolhimento de tributo retido (guia)"),
    ("aquisicao_bem", "Aquisição ou implantação de bem do imobilizado"),
    ("depreciacao", "Depreciação mensal do imobilizado"),
    ("baixa_bem_depreciacao", "Baixa de bem: depreciação acumulada"),
    ("baixa_bem_venda", "Baixa de bem: valor recebido na venda"),
    ("compensacao", "Compensação entre contas a pagar e a receber"),
    ("renegociacao_baixa", "Renegociação: baixa do título original"),
    ("pdd_constituicao", "PDD: constituição da provisão"),
    ("pdd_reversao", "PDD: reversão da provisão"),
    ("contingencia_constituicao", "Contingência judicial: constituição da provisão"),
    ("contingencia_reversao", "Contingência judicial: reversão da provisão"),
    ("baixa_bem_perda", "Baixa de bem: perda"),
    ("baixa_bem_ganho", "Baixa de bem: ganho"),
    ("encerramento_exercicio", "Encerramento do exercício (apuração do resultado)"),
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
    empresa = models.ForeignKey(Empresa, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
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
                                        evento=self.evento, content_type=self.content_type, empresa=self.empresa,
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


# --------------------------------------------------------------------------- ativo fixo
CATEGORIAS_BEM = [  # (código, nome, vida útil sugerida em meses)
    ("moveis", "Móveis e utensílios", 120), ("maquinas", "Máquinas e equipamentos", 120),
    ("informatica", "Computadores e periféricos", 60), ("veiculos", "Veículos", 60),
    ("instalacoes", "Instalações", 120), ("edificacoes", "Edificações", 300), ("outros", "Outros", 60),
]
VIDA_SUGERIDA = {c: v for c, _, v in CATEGORIAS_BEM}


class Bem(ModeloBase, PorEmpresaMixin):
    """Bem do imobilizado com depreciação linear mensal. A vida útil sugerida por categoria é só um ponto de
    partida: o contador define a vida útil e o valor residual."""

    STATUS = [("ativo", "Em uso"), ("baixado", "Baixado")]
    codigo = models.CharField("código (plaqueta)", max_length=30, unique=True)
    descricao = models.CharField("descrição", max_length=200)
    categoria = models.CharField(max_length=12, choices=[(c, n) for c, n, _ in CATEGORIAS_BEM], default="moveis")
    data_aquisicao = models.DateField("data de aquisição", default=timezone.localdate)
    valor_aquisicao = models.DecimalField("valor de aquisição", max_digits=14, decimal_places=2)
    valor_residual = models.DecimalField("valor residual", max_digits=14, decimal_places=2, default=0)
    vida_util_meses = models.PositiveSmallIntegerField("vida útil (meses)", default=0,
                                                       help_text="Em branco/zero = sugestão da categoria.")
    inicio_depreciacao = models.DateField("início da depreciação", null=True, blank=True,
                                          help_text="Em branco = mês da aquisição. Depreciação por mês cheio.")
    centro_custo = models.ForeignKey("financeiro.CentroCusto", verbose_name="centro de custo", null=True, blank=True,
                                     on_delete=models.PROTECT, related_name="+")
    fornecedor = models.ForeignKey("core.Fornecedor", null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")
    contrapartida = models.ForeignKey(
        ContaContabil, verbose_name="contrapartida da aquisição", null=True, blank=True, on_delete=models.PROTECT,
        related_name="+", limit_choices_to={"sintetica": False},
        help_text="Conta creditada ao lançar o bem no imobilizado. Em branco = a aquisição não é contabilizada aqui "
                  "(use quando o bem já foi lançado por outro caminho). Em implantação, use o patrimônio líquido.")
    localizacao = models.CharField("localização", max_length=120, blank=True)
    status = models.CharField(max_length=10, choices=STATUS, default="ativo", editable=False, db_index=True)
    data_baixa = models.DateField("data da baixa", null=True, blank=True, editable=False)
    valor_venda = models.DecimalField("valor recebido na baixa", max_digits=14, decimal_places=2, default=0,
                                      editable=False)
    motivo_baixa = models.CharField("motivo da baixa", max_length=200, blank=True, editable=False)
    observacoes = models.TextField("observações", blank=True)

    class Meta:
        verbose_name = "bem do ativo fixo"
        verbose_name_plural = "ativo fixo (bens)"
        ordering = ["codigo"]

    def __str__(self):
        return f"{self.codigo} — {self.descricao}"

    def clean(self):
        erros = {}
        if self.valor_aquisicao is not None and self.valor_aquisicao <= 0:
            erros["valor_aquisicao"] = "O valor de aquisição deve ser maior que zero."
        if (self.valor_residual is not None and self.valor_aquisicao is not None
                and self.valor_residual >= self.valor_aquisicao):
            erros["valor_residual"] = "O valor residual deve ser menor que o de aquisição."
        if self.pk:
            antes = Bem.objects.filter(pk=self.pk).first()
            if antes and antes.status == "baixado":
                raise ValidationError("Bem baixado não pode ser alterado.")
            if antes and antes.depreciacoes.exists():
                for campo in ("valor_aquisicao", "valor_residual", "vida_util_meses", "inicio_depreciacao",
                              "data_aquisicao"):
                    novo = getattr(self, campo) or None
                    if novo is not None and novo != getattr(antes, campo):
                        erros[campo] = "Já há depreciação lançada: estorne-a antes de alterar este dado."
        if erros:
            raise ValidationError(erros)

    def save(self, *args, **kwargs):
        if not self.vida_util_meses:
            self.vida_util_meses = VIDA_SUGERIDA.get(self.categoria, 60)
        self.inicio_depreciacao = (self.inicio_depreciacao or self.data_aquisicao).replace(day=1)
        novo = self._state.adding
        super().save(*args, **kwargs)
        if novo:
            from . import patrimonio
            patrimonio.contabilizar_aquisicao(self)

    @property
    def base_depreciavel(self):
        return self.valor_aquisicao - self.valor_residual

    @property
    def parcela_mensal(self):
        return (self.base_depreciavel / self.vida_util_meses).quantize(Decimal("0.01"))
    parcela_mensal.fget.short_description = "depreciação mensal"

    @property
    def depreciacao_acumulada(self):
        return self.depreciacoes.aggregate(t=Sum("valor"))["t"] or ZERO
    depreciacao_acumulada.fget.short_description = "depreciação acumulada"

    @property
    def valor_contabil(self):
        return self.valor_aquisicao - self.depreciacao_acumulada
    valor_contabil.fget.short_description = "valor contábil"


class Depreciacao(models.Model):
    bem = models.ForeignKey(Bem, on_delete=models.CASCADE, related_name="depreciacoes")
    competencia = models.DateField("competência", help_text="Primeiro dia do mês.")
    valor = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        verbose_name = "depreciação"
        verbose_name_plural = "depreciações"
        ordering = ["-competencia", "bem__codigo"]
        constraints = [models.UniqueConstraint(fields=["bem", "competencia"], name="depreciacao_por_mes")]

    def __str__(self):
        return f"{self.bem.codigo} {self.competencia:%m/%Y}: {self.valor}"


# --------------------------------------------------------------------------- orçamento
MESES = [f"m{i:02d}" for i in range(1, 13)]


class Orcamento(ModeloBase, PorEmpresaMixin):
    CONTROLES = [("nenhum", "Só acompanhar"), ("alertar", "Avisar o responsável ao estourar"),
                 ("bloquear", "Bloquear novo título que estoure")]
    nome = models.CharField(max_length=100)
    ano = models.PositiveSmallIntegerField()
    controle = models.CharField("controle das despesas", max_length=10, choices=CONTROLES, default="nenhum",
                                help_text="Vale para contas a pagar, comparando o acumulado do ano até o mês do "
                                          "título com o orçado. O bloqueio atua nas telas de cadastro.")
    responsavel = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="responsável", null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="+")
    ativo = models.BooleanField(default=True)
    observacoes = models.TextField("observações", blank=True)

    class Meta:
        verbose_name = "orçamento"
        verbose_name_plural = "orçamentos"
        ordering = ["-ano", "nome"]

    def __str__(self):
        return f"{self.nome} ({self.ano})"


class LinhaOrcamento(models.Model):
    TIPOS = [("despesa", "Despesa"), ("receita", "Receita")]
    orcamento = models.ForeignKey(Orcamento, on_delete=models.CASCADE, related_name="linhas")
    tipo = models.CharField(max_length=8, choices=TIPOS, default="despesa")
    centro_custo = models.ForeignKey("financeiro.CentroCusto", verbose_name="centro de custo", null=True, blank=True,
                                     on_delete=models.PROTECT, related_name="+",
                                     help_text="Em branco = qualquer centro de custo.")
    plano_conta = models.ForeignKey("financeiro.PlanoConta", verbose_name="categoria", null=True, blank=True,
                                    on_delete=models.PROTECT, related_name="+",
                                    help_text="Inclui as categorias filhas. Em branco = qualquer categoria.")
    valor_anual = models.DecimalField("valor anual", max_digits=14, decimal_places=2, default=0,
                                      help_text="Se os meses ficarem em branco, divide por igual nos 12 meses.")
    m01 = models.DecimalField("jan", max_digits=14, decimal_places=2, default=0)
    m02 = models.DecimalField("fev", max_digits=14, decimal_places=2, default=0)
    m03 = models.DecimalField("mar", max_digits=14, decimal_places=2, default=0)
    m04 = models.DecimalField("abr", max_digits=14, decimal_places=2, default=0)
    m05 = models.DecimalField("mai", max_digits=14, decimal_places=2, default=0)
    m06 = models.DecimalField("jun", max_digits=14, decimal_places=2, default=0)
    m07 = models.DecimalField("jul", max_digits=14, decimal_places=2, default=0)
    m08 = models.DecimalField("ago", max_digits=14, decimal_places=2, default=0)
    m09 = models.DecimalField("set", max_digits=14, decimal_places=2, default=0)
    m10 = models.DecimalField("out", max_digits=14, decimal_places=2, default=0)
    m11 = models.DecimalField("nov", max_digits=14, decimal_places=2, default=0)
    m12 = models.DecimalField("dez", max_digits=14, decimal_places=2, default=0)

    class Meta:
        verbose_name = "linha do orçamento"
        verbose_name_plural = "linhas do orçamento"
        ordering = ["tipo", "centro_custo__codigo", "plano_conta__codigo", "pk"]

    def __str__(self):
        return f"{self.get_tipo_display()}: {self.centro_custo or 'todos os centros'} / {self.plano_conta or 'todas as categorias'}"

    def clean(self):
        if self.centro_custo_id is None and self.plano_conta_id is None:
            raise ValidationError("Informe um centro de custo, uma categoria ou os dois.")
        if self.plano_conta_id and self.plano_conta.tipo != self.tipo:
            raise ValidationError({"plano_conta": "A categoria é de outro tipo (receita × despesa)."})

    def save(self, *args, **kwargs):
        meses = self.valores()
        if self.valor_anual and not any(meses):
            base = (Decimal(self.valor_anual) / 12).quantize(Decimal("0.01"))
            for m in MESES[:-1]:
                setattr(self, m, base)
            self.m12 = Decimal(self.valor_anual) - base * 11
        super().save(*args, **kwargs)

    def valores(self):
        return [getattr(self, m) or ZERO for m in MESES]

    @property
    def total(self):
        return sum(self.valores(), ZERO)
    total.fget.short_description = "total do ano"

    def ate_mes(self, mes):
        return sum(self.valores()[:mes], ZERO)

    @property
    def especificidade(self):
        return (self.centro_custo_id is not None) + (self.plano_conta_id is not None)
