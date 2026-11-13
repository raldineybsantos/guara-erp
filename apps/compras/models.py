"""Compras: solicitação → cotação → pedido (com alçada de aprovação) → recebimento → conferência em três vias.

A conferência em três vias (pedido × recebimento × nota fiscal) libera a conta a pagar só quando as
três batem, como o Logistics Invoice Verification do SAP.
"""

import datetime
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Max, Sum
from django.utils import timezone

from apps.core.middleware import usuario_atual
from apps.core.models import Departamento, Fornecedor, ModeloBase, PorEmpresaMixin

D2 = Decimal("0.01")


def proximo_numero(modelo, prefixo):
    ano = timezone.localdate().year
    inicio = f"{prefixo}-{ano}-"
    ultimo = (modelo.objects.select_for_update().filter(numero__startswith=inicio)
              .aggregate(m=Max("numero"))["m"])
    seq = int(ultimo.rsplit("-", 1)[1]) + 1 if ultimo else 1
    return f"{inicio}{seq:04d}"


class NumeradoMixin(models.Model):
    PREFIXO = ""
    numero = models.CharField("número", max_length=20, unique=True, editable=False)

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.numero:
            return super().save(*args, **kwargs)
        with transaction.atomic():
            self.numero = proximo_numero(type(self), self.PREFIXO)
            return super().save(*args, **kwargs)

    def __str__(self):
        return self.numero


# --------------------------------------------------------------------------- solicitação
class SolicitacaoCompra(NumeradoMixin, ModeloBase, PorEmpresaMixin):
    PREFIXO = "SC"
    STATUS = [("aberta", "Aberta"), ("em_cotacao", "Em cotação"), ("atendida", "Atendida (pedido gerado)"),
              ("cancelada", "Cancelada")]
    solicitante = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+",
                                    null=True, blank=True)
    departamento = models.ForeignKey(Departamento, null=True, blank=True, on_delete=models.PROTECT)
    data_necessidade = models.DateField("necessário até", null=True, blank=True)
    justificativa = models.TextField()
    status = models.CharField(max_length=12, choices=STATUS, default="aberta", editable=False, db_index=True)

    class Meta:
        verbose_name = "solicitação de compra"
        verbose_name_plural = "solicitações de compra"
        ordering = ["-numero"]

    def save(self, *args, **kwargs):
        if not self.solicitante_id:
            self.solicitante = usuario_atual()
        super().save(*args, **kwargs)

    def gerar_cotacao(self, fornecedores, prazo_resposta=None):
        """Cria a cotação com uma linha por item × fornecedor, para cada um preencher o preço."""
        if self.status not in ("aberta", "em_cotacao"):
            raise ValidationError("Só solicitações abertas podem ir para cotação.")
        itens = list(self.itens.all())
        if not itens:
            raise ValidationError("A solicitação não tem itens.")
        if not fornecedores:
            raise ValidationError("Escolha ao menos um fornecedor.")
        with transaction.atomic():
            cot = Cotacao.objects.create(solicitacao=self, empresa=self.empresa, prazo_resposta=prazo_resposta,
                                         descricao=f"Cotação da {self.numero}")
            for forn in fornecedores:
                for item in itens:
                    ItemCotacao.objects.create(cotacao=cot, fornecedor=forn, produto=item.produto,
                                               descricao=item.descricao, quantidade=item.quantidade)
            self.status = "em_cotacao"
            self.save(update_fields=["status"])
        return cot


class ItemSolicitacao(models.Model):
    solicitacao = models.ForeignKey(SolicitacaoCompra, on_delete=models.CASCADE, related_name="itens")
    produto = models.ForeignKey("fiscal.Produto", null=True, blank=True, on_delete=models.PROTECT)
    descricao = models.CharField("descrição", max_length=200, blank=True)
    quantidade = models.DecimalField(max_digits=14, decimal_places=4, default=1)
    unidade = models.CharField(max_length=6, default="UN")

    class Meta:
        verbose_name = "item"
        verbose_name_plural = "itens"

    def __str__(self):
        return self.descricao or str(self.produto or "")

    def save(self, *args, **kwargs):
        if self.produto_id and not self.descricao:
            self.descricao = self.produto.descricao
        super().save(*args, **kwargs)


# --------------------------------------------------------------------------- cotação
class Cotacao(NumeradoMixin, ModeloBase, PorEmpresaMixin):
    PREFIXO = "COT"
    STATUS = [("aberta", "Aberta"), ("concluida", "Concluída"), ("cancelada", "Cancelada")]
    solicitacao = models.ForeignKey(SolicitacaoCompra, null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="cotacoes", verbose_name="solicitação")
    descricao = models.CharField("descrição", max_length=200)
    prazo_resposta = models.DateField("prazo de resposta", null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS, default="aberta", editable=False)

    class Meta:
        verbose_name = "cotação"
        verbose_name_plural = "cotações"
        ordering = ["-numero"]

    def mapa(self):
        """Comparativo por fornecedor: total cotado, itens com preço, prazo; o menor total completo vem primeiro."""
        linhas = {}
        n_itens = 0
        for item in self.itens.select_related("fornecedor"):
            m = linhas.setdefault(item.fornecedor_id, {"fornecedor": item.fornecedor, "itens": [], "total": Decimal(0),
                                                       "cotados": 0, "prazo": 0})
            m["itens"].append(item)
            if item.valor_unitario:
                m["cotados"] += 1
                m["total"] += item.total
            m["prazo"] = max(m["prazo"], item.prazo_entrega_dias or 0)
        n_itens = max((len(m["itens"]) for m in linhas.values()), default=0)
        resultado = sorted(linhas.values(), key=lambda m: (m["cotados"] < n_itens, m["total"] or Decimal("1e18")))
        for i, m in enumerate(resultado):
            m["completo"] = m["cotados"] == n_itens and n_itens > 0
            m["melhor"] = i == 0 and m["completo"]
        return resultado

    def escolher(self, fornecedor, usuario=None):
        """Gera o pedido com os itens do fornecedor vencedor e conclui a cotação."""
        if self.status != "aberta":
            raise ValidationError("A cotação não está aberta.")
        itens = [i for i in self.itens.filter(fornecedor=fornecedor) if i.valor_unitario]
        if not itens:
            raise ValidationError("Este fornecedor não informou preços.")
        with transaction.atomic():
            pedido = PedidoCompra.objects.create(
                empresa=self.empresa, fornecedor=fornecedor, cotacao=self, solicitacao=self.solicitacao,
                comprador=usuario, previsao_entrega=timezone.localdate() + datetime.timedelta(
                    days=max(i.prazo_entrega_dias or 0 for i in itens)),
                condicao_pagamento=str(fornecedor.prazo_pagamento_dias or 30),
            )
            for i in itens:
                ItemPedido.objects.create(pedido=pedido, produto=i.produto, descricao=i.descricao,
                                          quantidade=i.quantidade, valor_unitario=i.valor_unitario)
            pedido.recalcular()
            self.status = "concluida"
            self.save(update_fields=["status"])
            if self.solicitacao_id:
                self.solicitacao.status = "atendida"
                self.solicitacao.save(update_fields=["status"])
        return pedido


class ItemCotacao(models.Model):
    cotacao = models.ForeignKey(Cotacao, on_delete=models.CASCADE, related_name="itens")
    fornecedor = models.ForeignKey(Fornecedor, on_delete=models.PROTECT, related_name="+")
    produto = models.ForeignKey("fiscal.Produto", null=True, blank=True, on_delete=models.PROTECT)
    descricao = models.CharField("descrição", max_length=200, blank=True)
    quantidade = models.DecimalField(max_digits=14, decimal_places=4, default=1)
    valor_unitario = models.DecimalField("valor unitário", max_digits=14, decimal_places=4, default=0)
    prazo_entrega_dias = models.PositiveSmallIntegerField("prazo de entrega (dias)", default=0)

    class Meta:
        verbose_name = "preço cotado"
        verbose_name_plural = "preços cotados"
        ordering = ["fornecedor_id", "id"]

    @property
    def total(self):
        return (self.quantidade * self.valor_unitario).quantize(D2)


# --------------------------------------------------------------------------- pedido
class PedidoCompra(NumeradoMixin, ModeloBase, PorEmpresaMixin):
    PREFIXO = "PC"
    STATUS = [("rascunho", "Rascunho"), ("em_aprovacao", "Em aprovação"), ("aprovado", "Aprovado"),
              ("recusado", "Recusado"), ("parcial", "Recebido parcialmente"), ("recebido", "Recebido"),
              ("cancelado", "Cancelado")]
    EDITAVEIS = ("rascunho", "recusado")
    fornecedor = models.ForeignKey(Fornecedor, on_delete=models.PROTECT, related_name="pedidos_compra")
    solicitacao = models.ForeignKey(SolicitacaoCompra, null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="pedidos", verbose_name="solicitação")
    cotacao = models.ForeignKey(Cotacao, null=True, blank=True, on_delete=models.PROTECT, related_name="pedidos",
                                verbose_name="cotação")
    comprador = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                  related_name="+")
    data_pedido = models.DateField("data do pedido", default=timezone.localdate)
    previsao_entrega = models.DateField("previsão de entrega", null=True, blank=True)
    condicao_pagamento = models.CharField(
        "condição de pagamento", max_length=40, default="30",
        help_text="Dias após a nota, separados por barra. Ex.: 30 ou 30/60/90.")
    centro_custo = models.ForeignKey("financeiro.CentroCusto", verbose_name="centro de custo", null=True, blank=True,
                                     on_delete=models.PROTECT, related_name="+")
    plano_conta = models.ForeignKey("financeiro.PlanoConta", verbose_name="categoria (plano de contas)", null=True,
                                    blank=True, on_delete=models.PROTECT, related_name="+")
    valor_frete = models.DecimalField("frete", max_digits=14, decimal_places=2, default=0)
    observacoes = models.TextField("observações ao fornecedor", blank=True)
    valor_itens = models.DecimalField("total dos itens", max_digits=14, decimal_places=2, default=0, editable=False)
    valor_total = models.DecimalField("valor total", max_digits=14, decimal_places=2, default=0, editable=False)
    status = models.CharField(max_length=12, choices=STATUS, default="rascunho", editable=False, db_index=True)
    motivo_status = models.TextField("motivo", blank=True, editable=False)

    class Meta:
        verbose_name = "pedido de compra"
        verbose_name_plural = "pedidos de compra"
        ordering = ["-numero"]

    def save(self, *args, **kwargs):
        if not self.pk and not self.comprador_id:
            self.comprador = usuario_atual()
        super().save(*args, **kwargs)

    def recalcular(self):
        itens = sum((i.total for i in self.itens.all()), Decimal(0))
        self.valor_itens = itens.quantize(D2)
        self.valor_total = (itens + (self.valor_frete or 0)).quantize(D2)
        PedidoCompra.objects.filter(pk=self.pk).update(valor_itens=self.valor_itens, valor_total=self.valor_total)

    def parcelas_dias(self):
        dias = [int(p) for p in self.condicao_pagamento.replace(",", "/").split("/") if p.strip().isdigit()]
        return dias or [30]

    def _mudar(self, status, motivo=""):
        self.status = status
        self.motivo_status = motivo
        self.save(update_fields=["status", "motivo_status", "atualizado_em"])

    # ---------------------------------------------------------------- alçada
    def enviar_para_aprovacao(self, usuario):
        """Aplica a alçada: até o limite automático aprova direto; acima, vai ao workflow de compras
        (e, acima do limite da diretoria, também à diretoria)."""
        from apps.workflow.models import Processo

        if self.status not in self.EDITAVEIS:
            raise ValidationError("Só pedidos em rascunho ou recusados podem ir para aprovação.")
        self.recalcular()
        if not self.itens.exists() or self.valor_total <= 0:
            raise ValidationError("O pedido não tem itens com valor.")
        if self.valor_total <= Decimal(str(settings.COMPRAS_ALCADA_AUTOMATICA)):
            self._mudar("aprovado", f"Aprovado automaticamente (até R$ {settings.COMPRAS_ALCADA_AUTOMATICA}).")
            return None
        codigo = ("aprovacao-compra-diretoria"
                  if self.valor_total > Decimal(str(settings.COMPRAS_ALCADA_DIRETORIA)) else "aprovacao-compra")
        processo = Processo.objects.filter(codigo=codigo, ativo=True).first()
        if processo is None:
            raise ValidationError(f"Processo '{codigo}' não encontrado. Rode `python manage.py setup_erp`.")
        inst = processo.iniciar(f"Aprovar pedido {self.numero} — {self.fornecedor}"[:200], usuario,
                                f"Fornecedor: {self.fornecedor}\nValor total: R$ {self.valor_total}\n"
                                f"Itens: {self.itens.count()}", objeto=self)
        self._mudar("em_aprovacao")
        return inst

    def workflow_aprovado(self, instancia):
        self._mudar("aprovado")

    def workflow_recusado(self, instancia, comentario):
        self._mudar("recusado", comentario)

    def workflow_cancelado(self, instancia, comentario):
        self._mudar("rascunho", "Solicitação de aprovação cancelada.")

    def cancelar(self, motivo):
        if self.status in ("parcial", "recebido", "cancelado"):
            raise ValidationError("Pedido já recebido ou cancelado.")
        self._mudar("cancelado", motivo)

    # ---------------------------------------------------------------- recebimento
    def atualizar_status_recebimento(self):
        itens = list(self.itens.all())
        if all(i.quantidade_recebida >= i.quantidade for i in itens):
            novo = "recebido"
        elif any(i.quantidade_recebida > 0 for i in itens):
            novo = "parcial"
        else:
            novo = "aprovado"
        if novo != self.status:
            self._mudar(novo)

    @transaction.atomic
    def receber(self, quantidades, nota_fiscal=None, data=None, observacoes="", usuario=None):
        """Registra a entrada física. ``quantidades`` = {item_id: quantidade}. Devolve o recebimento já conferido."""
        if self.status not in ("aprovado", "parcial"):
            raise ValidationError("Só pedidos aprovados podem ser recebidos.")
        itens = {i.pk: i for i in self.itens.select_for_update()}
        linhas = [(itens[int(k)], Decimal(q)) for k, q in quantidades.items() if Decimal(q or 0) > 0 and int(k) in itens]
        if not linhas:
            raise ValidationError("Informe a quantidade recebida de ao menos um item.")
        for item, q in linhas:
            if q > item.saldo:
                raise ValidationError(f"{item}: recebendo {q}, mas o saldo do pedido é {item.saldo}.")
        rec = RecebimentoCompra.objects.create(pedido=self, empresa=self.empresa, nota_fiscal=nota_fiscal,
                                               data=data or timezone.localdate(), observacoes=observacoes,
                                               recebido_por=usuario)
        for item, q in linhas:
            ItemRecebimento.objects.create(recebimento=rec, item_pedido=item, quantidade=q)
            item.quantidade_recebida += q
            item.save(update_fields=["quantidade_recebida"])
        self.atualizar_status_recebimento()
        rec.conferir()
        return rec


class ItemPedido(models.Model):
    pedido = models.ForeignKey(PedidoCompra, on_delete=models.CASCADE, related_name="itens")
    produto = models.ForeignKey("fiscal.Produto", null=True, blank=True, on_delete=models.PROTECT)
    descricao = models.CharField("descrição", max_length=200, blank=True)
    quantidade = models.DecimalField(max_digits=14, decimal_places=4, default=1)
    valor_unitario = models.DecimalField("valor unitário", max_digits=14, decimal_places=4, default=0)
    quantidade_recebida = models.DecimalField("recebido", max_digits=14, decimal_places=4, default=0, editable=False)

    class Meta:
        verbose_name = "item"
        verbose_name_plural = "itens"
        ordering = ["id"]

    def __str__(self):
        return self.descricao or str(self.produto or f"item {self.pk}")

    def save(self, *args, **kwargs):
        if self.produto_id and not self.descricao:
            self.descricao = self.produto.descricao
        super().save(*args, **kwargs)

    @property
    def total(self):
        return (self.quantidade * self.valor_unitario).quantize(D2)

    @property
    def saldo(self):
        return self.quantidade - self.quantidade_recebida


# --------------------------------------------------------------------------- recebimento e conferência
class RecebimentoCompra(ModeloBase, PorEmpresaMixin):
    CONFERENCIA = [("pendente", "Aguardando nota"), ("ok", "Conferido"), ("divergente", "Divergente")]
    pedido = models.ForeignKey(PedidoCompra, on_delete=models.PROTECT, related_name="recebimentos")
    data = models.DateField(default=timezone.localdate)
    nota_fiscal = models.ForeignKey("fiscal.NotaFiscal", null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="recebimentos_compra", limit_choices_to={"tipo": "entrada"},
                                    help_text="Nota de entrada do fornecedor. Pode ser vinculada depois.")
    recebido_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                     related_name="+", editable=False)
    observacoes = models.TextField("observações", blank=True)
    conferencia = models.CharField("conferência", max_length=10, choices=CONFERENCIA, default="pendente",
                                   editable=False)
    divergencias = models.TextField("divergências", blank=True, editable=False)
    conta_pagar = models.ForeignKey("financeiro.ContaPagar", null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+", editable=False)

    class Meta:
        verbose_name = "recebimento"
        verbose_name_plural = "recebimentos"
        ordering = ["-data", "-id"]

    def __str__(self):
        return f"Recebimento {self.pk} do {self.pedido}"

    @property
    def status(self):  # mostrado como etiqueta colorida nas listas
        return self.get_conferencia_display()

    def conferir(self):
        """Pedido × recebimento × nota. Libera a conta a pagar só se as três baterem."""
        tolerancia = Decimal(str(settings.COMPRAS_TOLERANCIA_PRECO_PCT)) / 100
        nota = self.nota_fiscal
        div = []
        if nota is None:
            self.conferencia, self.divergencias = "pendente", "Sem nota fiscal vinculada."
            self.save(update_fields=["conferencia", "divergencias"])
            return self.conferencia

        pedido = self.pedido
        if nota.fornecedor_id and nota.fornecedor_id != pedido.fornecedor_id:
            div.append(f"Fornecedor da nota ({nota.fornecedor}) é diferente do pedido ({pedido.fornecedor}).")

        recebidos = list(self.itens.select_related("item_pedido__produto"))
        itens_nota = list(nota.itens.all())
        por_produto = {}
        for i in itens_nota:
            if i.produto_id:
                por_produto.setdefault(i.produto_id, []).append(i)

        for r in recebidos:
            ip = r.item_pedido
            notas_item = por_produto.get(ip.produto_id) if ip.produto_id else None
            if not notas_item:
                continue  # sem produto em comum: vale a checagem pelo total, abaixo
            q_nota = sum((i.quantidade for i in notas_item), Decimal(0))
            if q_nota != r.quantidade:
                div.append(f"{ip}: nota fatura {q_nota:g}, foram recebidos {r.quantidade:g}.")
            preco_nota = notas_item[0].valor_unitario
            if abs(preco_nota - ip.valor_unitario) > ip.valor_unitario * tolerancia + D2:
                div.append(f"{ip}: preço na nota R$ {preco_nota:.2f}, no pedido R$ {ip.valor_unitario:.2f}.")

        esperado = sum((r.quantidade * r.item_pedido.valor_unitario for r in recebidos), Decimal(0)).quantize(D2)
        faturado = sum((i.quantidade * i.valor_unitario for i in itens_nota), Decimal(0)).quantize(D2)
        if abs(faturado - esperado) > esperado * tolerancia + D2:
            div.append(f"Total dos produtos na nota R$ {faturado:.2f}; esperado pelo pedido e recebimento "
                       f"R$ {esperado:.2f} (tolerância {settings.COMPRAS_TOLERANCIA_PRECO_PCT}%).")

        self.conferencia = "divergente" if div else "ok"
        self.divergencias = "\n".join(div)
        self._liberar_pagamento(nota)
        self.save(update_fields=["conferencia", "divergencias", "conta_pagar"])
        if div:
            self._abrir_tarefa_divergencia()
        return self.conferencia

    def _liberar_pagamento(self, nota):
        from apps.financeiro.models import ContaPagar

        contas = ContaPagar.objects.filter(fornecedor_id=nota.fornecedor_id, documento=str(nota.numero or ""))
        if not contas.exists():
            # Uma conta por parcela da condição de pagamento do pedido (ex.: 30/60/90).
            dias = self.pedido.parcelas_dias()
            nota.recalcular()
            total, n = nota.valor_total, len(dias)
            base = (total / n).quantize(D2)
            ids = []
            for i, d in enumerate(dias, start=1):
                t = nota.gerar_titulo(vencimento=nota.data_emissao + datetime.timedelta(days=d))
                valor = base if i < n else total - base * (n - 1)
                ContaPagar.objects.filter(pk=t.pk).update(
                    valor=valor, parcela=i, total_parcelas=n, centro_custo=self.pedido.centro_custo,
                    plano_conta=self.pedido.plano_conta, observacoes=f"Pedido {self.pedido.numero}")
                ids.append(t.pk)
            contas = ContaPagar.objects.filter(pk__in=ids).order_by("vencimento")
        contas.update(aprovado=self.conferencia == "ok")
        self.conta_pagar = contas.first()

    def _abrir_tarefa_divergencia(self):
        from django.contrib.auth.models import Group
        from django.contrib.contenttypes.models import ContentType

        from apps.tarefas.models import Atividade

        ct = ContentType.objects.get_for_model(self)
        if Atividade.objects.filter(content_type=ct, object_id=self.pk).exclude(status__in=("concluida", "cancelada")
                                                                                 ).exists():
            return
        Atividade.objects.create(
            titulo=f"Divergência na nota do {self.pedido.numero}"[:200],
            descricao=f"{self}\n\n{self.divergencias}\n\nA conta a pagar está bloqueada até a correção.",
            grupo=Group.objects.filter(name="Compras").first(), prioridade="alta",
            prazo=timezone.localdate() + datetime.timedelta(days=2), content_type=ct, object_id=self.pk,
            etiquetas="compras, divergência",
        )


class ItemRecebimento(models.Model):
    recebimento = models.ForeignKey(RecebimentoCompra, on_delete=models.CASCADE, related_name="itens")
    item_pedido = models.ForeignKey(ItemPedido, on_delete=models.PROTECT, related_name="recebimentos")
    quantidade = models.DecimalField(max_digits=14, decimal_places=4)

    class Meta:
        verbose_name = "item recebido"
        verbose_name_plural = "itens recebidos"


def total_em_aberto():
    """Valor de pedidos aprovados ainda não recebidos (compromisso de caixa)."""
    return (PedidoCompra.objects.filter(status__in=("aprovado", "parcial"))
            .aggregate(t=Sum("valor_total"))["t"] or Decimal(0))
