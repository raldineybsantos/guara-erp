"""Projetos: EAP (tarefas com responsável, datas, horas e avanço), apontamento de horas e custo realizado
(horas × custo-hora do colaborador + despesas lançadas no centro de custo do projeto) contra o orçamento."""

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Sum
from django.utils import timezone

from apps.core.middleware import usuario_atual
from apps.core.models import ModeloBase, PorEmpresaMixin
from apps.core.numeracao import NumeradoMixin

D2 = Decimal("0.01")


class Projeto(NumeradoMixin, ModeloBase, PorEmpresaMixin):
    PREFIXO = "PRJ"
    STATUS = [("planejamento", "Planejamento"), ("andamento", "Em andamento"), ("pausado", "Pausado"),
              ("concluido", "Concluído"), ("cancelado", "Cancelado")]
    nome = models.CharField(max_length=150)
    cliente = models.ForeignKey("core.Cliente", null=True, blank=True, on_delete=models.PROTECT,
                                related_name="projetos")
    gerente = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="gerente do projeto",
                                on_delete=models.PROTECT, related_name="+")
    departamento = models.ForeignKey("core.Departamento", null=True, blank=True, on_delete=models.PROTECT)
    centro_custo = models.ForeignKey("financeiro.CentroCusto", verbose_name="centro de custo", null=True, blank=True,
                                     on_delete=models.PROTECT, related_name="+",
                                     help_text="Contas a pagar neste centro de custo contam como despesa do projeto.")
    inicio = models.DateField("início", default=timezone.localdate)
    fim_previsto = models.DateField("término previsto", null=True, blank=True)
    orcamento = models.DecimalField("orçamento (R$)", max_digits=14, decimal_places=2, default=0)
    descricao = models.TextField("escopo", blank=True)
    status = models.CharField(max_length=12, choices=STATUS, default="planejamento", db_index=True)

    class Meta:
        verbose_name = "projeto"
        verbose_name_plural = "projetos"
        ordering = ["-numero"]

    def __str__(self):
        return f"{self.numero} {self.nome}"

    # ------------------------------------------------------------- indicadores
    def horas_previstas(self):
        return self.tarefas.aggregate(t=Sum("horas_previstas"))["t"] or Decimal(0)
    horas_previstas.short_description = "horas previstas"

    def horas_realizadas(self):
        return self.apontamentos.aggregate(t=Sum("horas"))["t"] or Decimal(0)
    horas_realizadas.short_description = "horas apontadas"

    def custo_mao_obra(self):
        return sum((a.custo for a in self.apontamentos.all()), Decimal(0)).quantize(D2)
    custo_mao_obra.short_description = "custo de mão de obra"

    def custo_despesas(self):
        if not self.centro_custo_id:
            return Decimal(0)
        from apps.financeiro.models import ContaPagar

        return (ContaPagar.objects.filter(centro_custo_id=self.centro_custo_id).exclude(status="cancelado")
                .aggregate(t=Sum("valor"))["t"] or Decimal(0))
    custo_despesas.short_description = "despesas (contas a pagar)"

    def custo_realizado(self):
        return self.custo_mao_obra() + self.custo_despesas()
    custo_realizado.short_description = "custo realizado"

    def compras_comprometidas(self):
        """Pedidos de compra do projeto já aprovados (inclui o que ainda não foi recebido nem pago)."""
        return (self.pedidos_compra.filter(status__in=["aprovado", "parcial", "recebido"])
                .aggregate(t=Sum("valor_total"))["t"] or Decimal(0))
    compras_comprometidas.short_description = "compras aprovadas"

    def vendas_faturadas(self):
        return self.pedidos_venda.filter(status="faturado").aggregate(t=Sum("valor_total"))["t"] or Decimal(0)
    vendas_faturadas.short_description = "vendas faturadas"

    def vendas_em_carteira(self):
        return (self.pedidos_venda.filter(status__in=["aprovado", "bloqueado"])
                .aggregate(t=Sum("valor_total"))["t"] or Decimal(0))
    vendas_em_carteira.short_description = "vendas a faturar"

    def resultado(self):
        """Receita faturada menos custo realizado (mão de obra + despesas pagas ou a pagar)."""
        return self.vendas_faturadas() - self.custo_realizado()
    resultado.short_description = "resultado (faturado − custo)"

    def saldo_orcamento(self):
        return self.orcamento - self.custo_realizado()
    saldo_orcamento.short_description = "saldo do orçamento"

    def percentual_concluido(self):
        """Avanço ponderado pelas horas previstas de cada tarefa (média simples se não houver horas)."""
        tarefas = list(self.tarefas.all())
        if not tarefas:
            return Decimal(0)
        peso = sum((t.horas_previstas for t in tarefas), Decimal(0))
        if peso:
            return (sum((t.horas_previstas * t.percentual for t in tarefas), Decimal(0)) / peso).quantize(D2)
        return (Decimal(sum(t.percentual for t in tarefas)) / len(tarefas)).quantize(D2)
    percentual_concluido.short_description = "% concluído"


class TarefaProjeto(models.Model):
    projeto = models.ForeignKey(Projeto, on_delete=models.CASCADE, related_name="tarefas")
    eap = models.CharField("EAP", max_length=20, blank=True, help_text="Ex.: 1, 1.1, 1.2")
    nome = models.CharField(max_length=150)
    responsavel = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="responsável", null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="+")
    inicio = models.DateField("início", null=True, blank=True)
    fim = models.DateField(null=True, blank=True)
    horas_previstas = models.DecimalField("horas previstas", max_digits=8, decimal_places=2, default=0)
    percentual = models.PositiveSmallIntegerField("% concluído", default=0)

    class Meta:
        verbose_name = "tarefa (EAP)"
        verbose_name_plural = "tarefas (EAP)"
        ordering = ["projeto", "eap", "id"]

    def __str__(self):
        return f"{self.eap} {self.nome}".strip()

    def clean(self):
        if self.percentual > 100:
            raise ValidationError({"percentual": "Máximo 100%."})
        if self.inicio and self.fim and self.fim < self.inicio:
            raise ValidationError({"fim": "O fim não pode ser antes do início."})

    def horas_realizadas(self):
        return self.apontamentos.aggregate(t=Sum("horas"))["t"] or Decimal(0)


def custo_hora_de(usuario):
    """Salário do colaborador (ou o salário base do cargo) dividido pelas horas do mês."""
    colab = getattr(usuario, "colaborador", None) if usuario else None
    if colab is None:
        return Decimal(0)
    salario = colab.salario or (colab.cargo.salario_base if colab.cargo_id else Decimal(0))
    return (Decimal(salario) * Decimal(str(settings.PROJETOS_ENCARGOS_FATOR)) /
            Decimal(str(settings.PROJETOS_HORAS_MES))).quantize(D2)


class ApontamentoHoras(models.Model):
    projeto = models.ForeignKey(Projeto, on_delete=models.PROTECT, related_name="apontamentos",
                                limit_choices_to={"status": "andamento"})
    tarefa = models.ForeignKey(TarefaProjeto, null=True, blank=True, on_delete=models.SET_NULL,
                               related_name="apontamentos")
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+",
                                editable=False)
    data = models.DateField(default=timezone.localdate)
    horas = models.DecimalField(max_digits=5, decimal_places=2)
    descricao = models.CharField("o que foi feito", max_length=200)
    custo_hora = models.DecimalField("custo-hora", max_digits=10, decimal_places=2, default=0, editable=False)

    class Meta:
        verbose_name = "apontamento de horas"
        verbose_name_plural = "apontamentos de horas"
        ordering = ["-data", "-id"]

    def __str__(self):
        return f"{self.data:%d/%m} {self.horas}h {self.projeto.numero}"

    def clean(self):
        if self.horas is not None and not (0 < self.horas <= 24):
            raise ValidationError({"horas": "Informe entre 0 e 24 horas."})
        if self.tarefa_id and self.projeto_id and self.tarefa.projeto_id != self.projeto_id:
            raise ValidationError({"tarefa": "A tarefa é de outro projeto."})

    def save(self, *args, **kwargs):
        if not self.usuario_id:
            self.usuario = usuario_atual()
        if not self.pk:
            self.custo_hora = custo_hora_de(self.usuario)  # fotografa o custo do dia do apontamento
        super().save(*args, **kwargs)

    @property
    def custo(self):
        return (self.horas * self.custo_hora).quantize(D2)
