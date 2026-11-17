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
from apps.core.formatos import qtd as fq

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
    tabela_preco = models.ForeignKey("TabelaPreco", verbose_name="tabela de preço", null=True, blank=True,
                                     on_delete=models.PROTECT, related_name="+",
                                     help_text="Em branco = a tabela do cliente ou a padrão vigente.")
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
        if not self.tabela_preco_id and self.cliente_id and self.status in self.EDITAVEIS:
            self.tabela_preco = TabelaPreco.para_cliente(self.cliente, self.data_pedido, self.empresa)
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
                faltas.append(f"{i.produto}: pedido {fq(i.quantidade)}, disponível {fq(disp)}")
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
        from apps.contabilidade.servicos import lancar, provisao_adiada, sincronizar_titulo
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
            with provisao_adiada():
                t = nota.gerar_titulo(vencimento=nota.data_emissao + datetime.timedelta(days=d))
            ContaReceber.objects.filter(pk=t.pk).update(
                valor=base if k < n else nota.valor_total - base * (n - 1), parcela=k, total_parcelas=n,
                observacoes=f"Pedido {self.numero}")
            sincronizar_titulo(ContaReceber.objects.get(pk=t.pk))
        lancar("impostos_venda", nota.valor_icms + nota.valor_pis + nota.valor_cofins + nota.valor_iss,
               nota.data_emissao, f"Impostos da {nota} — pedido {self.numero}", nota)
        for i in self.itens.select_related("produto"):
            if i.produto.tipo == "produto":
                est.saida(i.produto, i.quantidade, self.armazem, "venda", f"NF {nota.numero}",
                          f"Pedido {self.numero}", referencia=self)
        self.nota_fiscal = nota
        self.save(update_fields=["nota_fiscal"])
        self._mudar("faturado")
        Comissao.gerar(self)
        return nota

    def titulos(self):
        from apps.financeiro.models import ContaReceber

        return ContaReceber.objects.filter(observacoes=f"Pedido {self.numero}", cliente=self.cliente).exclude(
            status="cancelado")


class ItemPedidoVenda(models.Model):
    pedido = models.ForeignKey(PedidoVenda, on_delete=models.CASCADE, related_name="itens")
    produto = models.ForeignKey("fiscal.Produto", on_delete=models.PROTECT, limit_choices_to={"ativo": True})
    descricao = models.CharField("descrição", max_length=200, blank=True)
    quantidade = models.DecimalField(max_digits=14, decimal_places=4, default=1)
    valor_unitario = models.DecimalField("valor unitário", max_digits=14, decimal_places=4, default=0,
                                         help_text="Em branco/zero = preço da tabela de preço do pedido ou, sem ela, do cadastro do produto.")
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
            tabela = self.pedido.tabela_preco
            preco = tabela.preco_para(self.produto, self.quantidade, self.pedido.data_pedido) if tabela else None
            self.valor_unitario = preco if preco is not None else self.produto.preco
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


# --------------------------------------------------------------------------- tabela de preço
class TabelaPreco(ModeloBase, PorEmpresaMixin):
    """Preços por produto e faixa de quantidade, com vigência. Pode ser a padrão ou exclusiva de alguns clientes."""

    nome = models.CharField(max_length=100)
    inicio = models.DateField("vigência desde", default=timezone.localdate)
    fim = models.DateField("vigência até", null=True, blank=True, help_text="Em branco = sem data de término.")
    padrao = models.BooleanField("tabela padrão", default=False,
                                 help_text="Usada quando o cliente não tem tabela própria.")
    clientes = models.ManyToManyField(Cliente, blank=True, related_name="tabelas_preco",
                                      verbose_name="exclusiva dos clientes",
                                      help_text="Vazio = vale para qualquer cliente (se for a padrão).")
    ativa = models.BooleanField(default=True)

    class Meta:
        verbose_name = "tabela de preço"
        verbose_name_plural = "tabelas de preço"
        ordering = ["nome"]

    def __str__(self):
        return self.nome

    def clean(self):
        if self.fim and self.inicio and self.fim < self.inicio:
            raise ValidationError({"fim": "O fim da vigência não pode ser anterior ao início."})

    def vigente(self, data):
        return self.ativa and self.inicio <= data and (self.fim is None or data <= self.fim)

    @classmethod
    def para_cliente(cls, cliente, data, empresa=None):
        """A tabela exclusiva do cliente (a de vigência mais recente) ou, sem ela, a padrão vigente."""
        base = cls.objects.filter(ativa=True, inicio__lte=data).filter(
            models.Q(fim__isnull=True) | models.Q(fim__gte=data))
        if empresa is not None:
            base = base.filter(models.Q(empresa=empresa) | models.Q(empresa__isnull=True))
        propria = base.filter(clientes=cliente).order_by("-inicio", "-pk").first()
        if propria:
            return propria
        return base.filter(padrao=True, clientes__isnull=True).order_by("-inicio", "-pk").first()

    def preco_para(self, produto, quantidade, data=None):
        """Preço da faixa de maior quantidade mínima que a quantidade alcança; None se o produto não está na tabela."""
        if not self.vigente(data or timezone.localdate()):
            return None
        q = Decimal(str(quantidade or 0))
        item = (self.itens.filter(produto=produto, quantidade_minima__lte=q)
                .order_by("-quantidade_minima").first())
        return item.preco if item else None


class ItemTabelaPreco(models.Model):
    tabela = models.ForeignKey(TabelaPreco, on_delete=models.CASCADE, related_name="itens")
    produto = models.ForeignKey("fiscal.Produto", on_delete=models.PROTECT, limit_choices_to={"ativo": True})
    quantidade_minima = models.DecimalField("a partir de (qtd)", max_digits=14, decimal_places=4, default=1)
    preco = models.DecimalField("preço", max_digits=14, decimal_places=4)

    class Meta:
        verbose_name = "preço"
        verbose_name_plural = "preços"
        ordering = ["produto__descricao", "quantidade_minima"]
        constraints = [models.UniqueConstraint(fields=["tabela", "produto", "quantidade_minima"],
                                               name="preco_unico_por_faixa")]

    def __str__(self):
        return f"{self.produto} — {self.preco}"

    def clean(self):
        if self.preco is not None and self.preco <= 0:
            raise ValidationError({"preco": "O preço deve ser maior que zero."})
        if self.quantidade_minima is not None and self.quantidade_minima <= 0:
            raise ValidationError({"quantidade_minima": "A quantidade mínima deve ser maior que zero."})


# --------------------------------------------------------------------------- comissão de vendedores
MOMENTOS = [("faturamento", "Devida no faturamento"), ("recebimento", "Devida conforme o cliente paga")]


class RegraComissao(ModeloBase):
    """Percentual de comissão. A regra do vendedor vale sobre a geral (sem vendedor)."""

    vendedor = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="vendedor", null=True, blank=True,
                                 on_delete=models.CASCADE, related_name="+",
                                 help_text="Em branco = regra geral, para quem não tem regra própria.")
    percentual = models.DecimalField("percentual (%)", max_digits=5, decimal_places=2)
    momento = models.CharField("quando a comissão é devida", max_length=12, choices=MOMENTOS, default="recebimento")
    ativa = models.BooleanField(default=True)

    class Meta:
        verbose_name = "regra de comissão"
        verbose_name_plural = "regras de comissão"
        ordering = ["vendedor__username"]
        constraints = [models.UniqueConstraint(fields=["vendedor"], name="regra_comissao_por_vendedor")]

    def __str__(self):
        return f"{self.vendedor or 'Geral'}: {self.percentual}%"

    def clean(self):
        if self.percentual is not None and not (0 <= self.percentual <= 100):
            raise ValidationError({"percentual": "Informe entre 0 e 100."})

    @classmethod
    def para(cls, vendedor):
        base = cls.objects.filter(ativa=True)
        return base.filter(vendedor=vendedor).first() or base.filter(vendedor__isnull=True).first()


class Comissao(ModeloBase):
    """Comissão de um pedido faturado: base, percentual e o que já foi liberado, lançado e pago."""

    pedido = models.OneToOneField(PedidoVenda, on_delete=models.PROTECT, related_name="comissao")
    vendedor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    base = models.DecimalField("base (itens do pedido)", max_digits=14, decimal_places=2)
    percentual = models.DecimalField("percentual (%)", max_digits=5, decimal_places=2)
    valor = models.DecimalField("comissão total", max_digits=14, decimal_places=2)
    momento = models.CharField("quando é devida", max_length=12, choices=MOMENTOS)
    valor_lancado = models.DecimalField("já lançado no contas a pagar", max_digits=14, decimal_places=2, default=0,
                                        editable=False)

    class Meta:
        verbose_name = "comissão"
        verbose_name_plural = "comissões"
        ordering = ["-pk"]
        permissions = [("pagar_comissao", "Pode lançar comissões no contas a pagar")]

    def __str__(self):
        return f"Comissão {self.pedido.numero} — {self.vendedor}"

    @classmethod
    def gerar(cls, pedido):
        """Chamado ao faturar. Sem vendedor ou sem regra, não há comissão."""
        if not pedido.vendedor_id or cls.objects.filter(pedido=pedido).exists():
            return None
        regra = RegraComissao.para(pedido.vendedor)
        if regra is None or regra.percentual <= 0:
            return None
        base = pedido.valor_itens
        valor = (base * regra.percentual / 100).quantize(D2)
        if valor <= 0:
            return None
        return cls.objects.create(pedido=pedido, vendedor=pedido.vendedor, base=base,
                                  percentual=regra.percentual, valor=valor, momento=regra.momento)

    @property
    def proporcao_recebida(self):
        titulos = list(self.pedido.titulos())
        total = sum((t.valor_total for t in titulos), Decimal("0"))
        if not total:
            return Decimal("0")
        return min(sum((t.valor_pago for t in titulos), Decimal("0")) / total, Decimal("1"))

    @property
    def valor_devido(self):
        """Quanto da comissão já pode ser paga ao vendedor."""
        if self.momento == "faturamento":
            return self.valor
        return (self.valor * self.proporcao_recebida).quantize(D2)
    valor_devido.fget.short_description = "devida até agora"

    @property
    def a_lancar(self):
        return max(self.valor_devido - self.valor_lancado, Decimal("0"))
    a_lancar.fget.short_description = "a lançar"

    @transaction.atomic
    def lancar_conta_pagar(self, vencimento):
        """Cria a conta a pagar do que já é devido e ainda não foi lançado."""
        from apps.financeiro.models import ContaPagar

        c = Comissao.objects.select_for_update().get(pk=self.pk)
        valor = c.a_lancar
        if valor <= 0:
            raise ValidationError("Não há comissão devida a lançar neste momento.")
        titulo = ContaPagar.objects.create(
            empresa=c.pedido.empresa, descricao=f"Comissão {c.pedido.numero} — {c.vendedor}"[:200],
            documento=c.pedido.numero, valor=valor, vencimento=vencimento,
            observacoes=f"Comissão do pedido {c.pedido.numero}: {c.percentual}% sobre {c.base}.")
        c.valor_lancado += valor
        c.save(update_fields=["valor_lancado", "atualizado_em"])
        self.valor_lancado = c.valor_lancado
        return titulo
