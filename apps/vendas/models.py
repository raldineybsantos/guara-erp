"""Vendas: pedido de venda → análise de crédito e estoque → faturamento (NF de saída, contas a receber e
baixa do estoque, numa transação só)."""

import datetime
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from apps.core.middleware import usuario_atual
from apps.core.models import Cliente, ModeloBase, PorEmpresaMixin
from apps.core.numeracao import NumeradoMixin

D2 = Decimal("0.01")


class PedidoVenda(NumeradoMixin, ModeloBase, PorEmpresaMixin):
    PREFIXO = "PV"
    STATUS = [("rascunho", "Rascunho"), ("bloqueado", "Bloqueado (crédito)"), ("aprovado", "Aprovado"),
              ("faturado", "Faturado"), ("cancelado", "Cancelado")]
    EDITAVEIS = ("rascunho", "bloqueado")
    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT, related_name="pedidos_venda")
    vendedor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                 related_name="+")
    data_pedido = models.DateField("data do pedido", default=timezone.localdate)
    previsao_entrega = models.DateField("previsão de entrega", null=True, blank=True)
    condicao_pagamento = models.CharField("condição de pagamento", max_length=40, default="30",
                                          help_text="Dias após a nota, separados por barra. Ex.: 0, 30 ou 30/60/90.")
    armazem = models.ForeignKey("estoque.Armazem", verbose_name="armazém de saída", null=True, blank=True,
                                on_delete=models.PROTECT, limit_choices_to={"tipo": "normal"},
                                help_text="Em branco = armazém padrão.")
    natureza_operacao = models.CharField("natureza da operação", max_length=100, default="Venda de mercadoria")
    valor_frete = models.DecimalField("frete", max_digits=14, decimal_places=2, default=0)
    observacoes = models.TextField("observações (vão para a nota)", blank=True)
    valor_itens = models.DecimalField("total dos itens", max_digits=14, decimal_places=2, default=0, editable=False)
    valor_total = models.DecimalField("valor total", max_digits=14, decimal_places=2, default=0, editable=False)
    status = models.CharField(max_length=10, choices=STATUS, default="rascunho", editable=False, db_index=True)
    motivo_status = models.TextField("motivo", blank=True, editable=False)
    nota_fiscal = models.ForeignKey("fiscal.NotaFiscal", null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="+", editable=False)

    class Meta:
        verbose_name = "pedido de venda"
        verbose_name_plural = "pedidos de venda"
        ordering = ["-numero"]
        permissions = [("liberar_credito_pedidovenda", "Pode liberar pedido bloqueado por crédito")]

    def save(self, *args, **kwargs):
        if not self.pk and not self.vendedor_id:
            self.vendedor = usuario_atual()
        super().save(*args, **kwargs)

    def recalcular(self):
        itens = sum((i.total for i in self.itens.all()), Decimal(0))
        self.valor_itens = itens.quantize(D2)
        self.valor_total = (itens + (self.valor_frete or 0)).quantize(D2)
        PedidoVenda.objects.filter(pk=self.pk).update(valor_itens=self.valor_itens, valor_total=self.valor_total)

    def _mudar(self, status, motivo=""):
        self.status, self.motivo_status = status, motivo
        self.save(update_fields=["status", "motivo_status", "atualizado_em"])

    def parcelas_dias(self):
        dias = [int(p) for p in self.condicao_pagamento.replace(",", "/").split("/") if p.strip().isdigit()]
        return dias or [30]

    # ---------------------------------------------------------------- análise
    def faltas_estoque(self):
        from apps.estoque.models import saldo_disponivel

        faltas = []
        for i in self.itens.select_related("produto"):
            if i.produto.tipo != "produto":
                continue
            disp = saldo_disponivel(i.produto, self.armazem)
            if disp < i.quantidade:
                faltas.append(f"{i.produto}: pedido {i.quantidade:g}, disponível {disp:g}")
        return faltas

    def credito_em_uso(self):
        from apps.financeiro.models import ContaReceber

        abertos = sum((t.saldo for t in ContaReceber.objects.em_aberto().filter(cliente=self.cliente)), Decimal(0))
        outros = (PedidoVenda.objects.filter(cliente=self.cliente, status="aprovado").exclude(pk=self.pk)
                  .aggregate(t=models.Sum("valor_total"))["t"] or Decimal(0))
        return abertos + outros

    def confirmar(self):
        """Confere itens e estoque; se passar do limite de crédito do cliente, fica bloqueado para liberação."""
        if self.status not in self.EDITAVEIS:
            raise ValidationError("Só pedidos em rascunho ou bloqueados podem ser confirmados.")
        self.recalcular()
        if not self.itens.exists():
            raise ValidationError("O pedido não tem itens.")
        faltas = self.faltas_estoque()
        if faltas:
            raise ValidationError("Estoque insuficiente: " + "; ".join(faltas))
        limite = self.cliente.limite_credito or Decimal(0)
        em_uso = self.credito_em_uso()
        if limite > 0 and em_uso + self.valor_total > limite:
            self._mudar("bloqueado", f"Limite de crédito R$ {limite:.2f}; em aberto R$ {em_uso:.2f} + "
                                     f"este pedido R$ {self.valor_total:.2f}.")
            return False
        self._mudar("aprovado")
        return True

    def liberar_credito(self, usuario, justificativa):
        if self.status != "bloqueado":
            raise ValidationError("O pedido não está bloqueado por crédito.")
        self._mudar("aprovado", f"Crédito liberado por {usuario.get_full_name() or usuario}: {justificativa}")

    def cancelar(self, motivo):
        if self.status in ("faturado", "cancelado"):
            raise ValidationError("Pedido faturado ou já cancelado. Para devolver, emita nota de devolução.")
        self._mudar("cancelado", motivo)

    # ---------------------------------------------------------------- faturamento
    @transaction.atomic
    def faturar(self):
        """Gera e numera a NF de saída, as contas a receber (uma por parcela) e baixa o estoque."""
        from apps.estoque import models as est
        from apps.financeiro.models import ContaReceber
        from apps.fiscal.models import ItemNota, NotaFiscal

        pedido = PedidoVenda.objects.select_for_update().get(pk=self.pk)
        if pedido.status != "aprovado":
            raise ValidationError("Só pedidos aprovados podem ser faturados.")
        faltas = self.faltas_estoque()
        if faltas:
            raise ValidationError("Estoque insuficiente: " + "; ".join(faltas))
        nota = NotaFiscal.objects.create(
            empresa=self.empresa, tipo="saida", modelo="55", cliente=self.cliente,
            natureza_operacao=self.natureza_operacao, valor_frete=self.valor_frete,
            observacoes=f"Pedido {self.numero}. {self.observacoes}".strip())
        for i in self.itens.select_related("produto"):
            ItemNota.objects.create(nota=nota, produto=i.produto, descricao=i.descricao, quantidade=i.quantidade,
                                    valor_unitario=i.valor_unitario_liquido)
        nota.recalcular()
        nota.emitir()
        dias = self.parcelas_dias()
        n, base = len(dias), (nota.valor_total / len(dias)).quantize(D2)
        for k, d in enumerate(dias, start=1):
            t = nota.gerar_titulo(vencimento=nota.data_emissao + datetime.timedelta(days=d))
            ContaReceber.objects.filter(pk=t.pk).update(
                valor=base if k < n else nota.valor_total - base * (n - 1), parcela=k, total_parcelas=n,
                observacoes=f"Pedido {self.numero}")
        for i in self.itens.select_related("produto"):
            if i.produto.tipo == "produto":
                est.saida(i.produto, i.quantidade, self.armazem, "venda", f"NF {nota.numero}",
                          f"Pedido {self.numero}", referencia=self)
        self.nota_fiscal = nota
        self.save(update_fields=["nota_fiscal"])
        self._mudar("faturado")
        return nota


class ItemPedidoVenda(models.Model):
    pedido = models.ForeignKey(PedidoVenda, on_delete=models.CASCADE, related_name="itens")
    produto = models.ForeignKey("fiscal.Produto", on_delete=models.PROTECT, limit_choices_to={"ativo": True})
    descricao = models.CharField("descrição", max_length=200, blank=True)
    quantidade = models.DecimalField(max_digits=14, decimal_places=4, default=1)
    valor_unitario = models.DecimalField("valor unitário", max_digits=14, decimal_places=4, default=0,
                                         help_text="Em branco/zero = preço do cadastro do produto.")
    desconto_pct = models.DecimalField("desconto %", max_digits=5, decimal_places=2, default=0)

    class Meta:
        verbose_name = "item"
        verbose_name_plural = "itens"
        ordering = ["id"]

    def __str__(self):
        return self.descricao or str(self.produto)

    def save(self, *args, **kwargs):
        if not self.descricao:
            self.descricao = self.produto.descricao
        if not self.valor_unitario:
            self.valor_unitario = self.produto.preco
        super().save(*args, **kwargs)

    def clean(self):
        limite = Decimal(str(settings.VENDAS_DESCONTO_MAXIMO_PCT))
        if self.desconto_pct and self.desconto_pct > limite:
            raise ValidationError({"desconto_pct": f"Desconto acima do máximo permitido ({limite}%)."})

    @property
    def valor_unitario_liquido(self):
        fator = 1 - Decimal(str(self.desconto_pct or 0)) / 100
        return (Decimal(str(self.valor_unitario or 0)) * fator).quantize(Decimal("0.0001"))

    @property
    def total(self):
        return (Decimal(str(self.quantidade)) * self.valor_unitario_liquido).quantize(D2)
