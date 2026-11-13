"""Produção: estrutura do produto (lista de materiais), ordem de produção e apontamento.

O apontamento consome os componentes do estoque pelo custo médio e dá entrada no produto acabado pelo custo
dos componentes consumidos. As faltas de componentes viram solicitação de compra (MRP simples).
"""

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from apps.core.models import ModeloBase, PorEmpresaMixin
from apps.core.numeracao import NumeradoMixin
from apps.core.formatos import qtd as fq

Q4 = Decimal("0.0001")


class EstruturaProduto(ModeloBase):
    produto = models.OneToOneField("fiscal.Produto", on_delete=models.PROTECT, related_name="estrutura",
                                   limit_choices_to={"tipo": "produto"})
    quantidade_base = models.DecimalField("quantidade base", max_digits=14, decimal_places=4, default=1,
                                          help_text="Os componentes abaixo produzem esta quantidade do produto.")
    observacoes = models.TextField("observações / roteiro", blank=True)
    ativo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "estrutura de produto"
        verbose_name_plural = "estruturas (lista de materiais)"
        ordering = ["produto__descricao"]

    def __str__(self):
        return f"Estrutura de {self.produto}"

    def explodir(self, quantidade):
        """[(componente, quantidade necessária)] para produzir ``quantidade`` do produto, com a perda."""
        fator = Decimal(str(quantidade)) / self.quantidade_base
        return [(c.componente, (c.quantidade * fator * (1 + c.perda_pct / 100)).quantize(Q4))
                for c in self.componentes.select_related("componente")]


class ComponenteEstrutura(models.Model):
    estrutura = models.ForeignKey(EstruturaProduto, on_delete=models.CASCADE, related_name="componentes")
    componente = models.ForeignKey("fiscal.Produto", on_delete=models.PROTECT, related_name="+",
                                   limit_choices_to={"tipo": "produto"})
    quantidade = models.DecimalField(max_digits=14, decimal_places=4)
    perda_pct = models.DecimalField("perda %", max_digits=5, decimal_places=2, default=0)

    class Meta:
        verbose_name = "componente"
        verbose_name_plural = "componentes"

    def clean(self):
        if self.estrutura_id and self.componente_id == self.estrutura.produto_id:
            raise ValidationError({"componente": "Um produto não pode ser componente dele mesmo."})


class OrdemProducao(NumeradoMixin, ModeloBase, PorEmpresaMixin):
    PREFIXO = "OP"
    STATUS = [("planejada", "Planejada"), ("liberada", "Liberada"), ("encerrada", "Encerrada"),
              ("cancelada", "Cancelada")]
    produto = models.ForeignKey("fiscal.Produto", on_delete=models.PROTECT, related_name="ordens_producao",
                                limit_choices_to={"tipo": "produto"})
    quantidade = models.DecimalField(max_digits=14, decimal_places=4)
    armazem = models.ForeignKey("estoque.Armazem", verbose_name="armazém", null=True, blank=True,
                                on_delete=models.PROTECT, limit_choices_to={"tipo": "normal"},
                                help_text="Consome os componentes e recebe o acabado. Em branco = padrão.")
    data_prevista = models.DateField("entrega prevista", null=True, blank=True)
    observacoes = models.TextField("observações", blank=True)
    status = models.CharField(max_length=10, choices=STATUS, default="planejada", editable=False, db_index=True)
    quantidade_produzida = models.DecimalField("produzido", max_digits=14, decimal_places=4, default=0,
                                               editable=False)
    custo_total = models.DecimalField("custo apurado", max_digits=14, decimal_places=2, default=0, editable=False)

    class Meta:
        verbose_name = "ordem de produção"
        verbose_name_plural = "ordens de produção"
        ordering = ["-numero"]

    @property
    def saldo(self):
        return self.quantidade - self.quantidade_produzida

    @property
    def custo_unitario(self):
        return (self.custo_total / self.quantidade_produzida).quantize(Q4) if self.quantidade_produzida else None

    def _estrutura(self):
        e = EstruturaProduto.objects.filter(produto=self.produto, ativo=True).first()
        if e is None or not e.componentes.exists():
            raise ValidationError(f"{self.produto} não tem estrutura (lista de materiais) ativa.")
        return e

    def necessidades(self, quantidade=None):
        from apps.estoque.models import saldo_disponivel

        linhas = []
        for comp, nec in self._estrutura().explodir(quantidade if quantidade is not None else self.saldo):
            disp = saldo_disponivel(comp, self.armazem)
            linhas.append({"componente": comp, "necessario": nec, "disponivel": disp,
                           "falta": max(nec - disp, Decimal(0))})
        return linhas

    def necessidades_tela(self):
        """Para a ficha da ordem: necessidades do saldo, ou vazio se não houver estrutura."""
        if self.status in ("encerrada", "cancelada"):
            return []
        try:
            return self.necessidades()
        except ValidationError:
            return []

    def liberar(self):
        if self.status != "planejada":
            raise ValidationError("Só ordens planejadas podem ser liberadas.")
        self._estrutura()
        self.status = "liberada"
        self.save(update_fields=["status"])

    def cancelar(self):
        if self.status == "encerrada" or self.quantidade_produzida:
            raise ValidationError("Ordem com produção apontada não pode ser cancelada; encerre-a.")
        self.status = "cancelada"
        self.save(update_fields=["status"])

    def encerrar(self):
        if self.status != "liberada":
            raise ValidationError("Só ordens liberadas podem ser encerradas.")
        self.status = "encerrada"
        self.save(update_fields=["status"])

    @transaction.atomic
    def apontar(self, quantidade, usuario=None, observacao=""):
        """Consome os componentes da quantidade produzida e dá entrada no acabado pelo custo consumido."""
        from apps.estoque import models as est

        if self.status != "liberada":
            raise ValidationError("Libere a ordem antes de apontar a produção.")
        quantidade = Decimal(str(quantidade))
        if quantidade <= 0:
            raise ValidationError("Informe a quantidade produzida.")
        faltas = [n for n in self.necessidades(quantidade) if n["falta"] > 0]
        if faltas:
            raise ValidationError("Faltam componentes: " + "; ".join(
                f"{n['componente']} (falta {fq(n['falta'])})" for n in faltas))
        custo = Decimal(0)
        for comp, qtd in self._estrutura().explodir(quantidade):
            mov = est.saida(comp, qtd, self.armazem, "producao", self.numero, f"Consumo na {self.numero}",
                            referencia=self)
            custo += qtd * mov.custo_unitario
        unit = (custo / quantidade).quantize(Q4)
        est.entrada(self.produto, quantidade, unit, self.armazem, "producao", self.numero,
                    f"Produção da {self.numero}", referencia=self)
        ApontamentoProducao.objects.create(ordem=self, quantidade=quantidade, custo_unitario=unit, usuario=usuario,
                                           observacao=observacao)
        self.quantidade_produzida += quantidade
        self.custo_total = (self.custo_total + custo).quantize(Decimal("0.01"))
        if self.quantidade_produzida >= self.quantidade:
            self.status = "encerrada"
        self.save(update_fields=["quantidade_produzida", "custo_total", "status"])
        return unit

    def gerar_solicitacao_compra(self, usuario):
        """MRP simples: abre uma solicitação com o que falta de componente para o saldo da ordem."""
        from apps.compras.models import ItemSolicitacao, SolicitacaoCompra

        faltas = [n for n in self.necessidades() if n["falta"] > 0]
        if not faltas:
            raise ValidationError("Não falta nenhum componente para esta ordem.")
        sc = SolicitacaoCompra.objects.create(
            justificativa=f"Componentes para a {self.numero} ({fq(self.quantidade)} x {self.produto}).",
            solicitante=usuario, data_necessidade=self.data_prevista, empresa=self.empresa)
        for n in faltas:
            ItemSolicitacao.objects.create(solicitacao=sc, produto=n["componente"], quantidade=n["falta"],
                                           unidade=n["componente"].unidade)
        return sc


class ApontamentoProducao(models.Model):
    ordem = models.ForeignKey(OrdemProducao, on_delete=models.CASCADE, related_name="apontamentos")
    data = models.DateTimeField(default=timezone.now)
    quantidade = models.DecimalField(max_digits=14, decimal_places=4)
    custo_unitario = models.DecimalField("custo unitário", max_digits=14, decimal_places=4)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="+")
    observacao = models.CharField("observação", max_length=200, blank=True)

    class Meta:
        verbose_name = "apontamento"
        verbose_name_plural = "apontamentos"
        ordering = ["-data"]
