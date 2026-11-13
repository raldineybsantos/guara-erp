"""Estoque: armazéns, saldo por produto e armazém com custo médio, movimentos (kardex) e inventário.

Todo movimento passa por ``entrada``/``saida``/``transferir``/``ajustar``: elas travam o saldo, recalculam o
custo médio ponderado e gravam o movimento com o saldo resultante. Movimentos não se editam nem se apagam;
um erro se corrige com outro movimento.
"""

from decimal import Decimal

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from apps.core.middleware import usuario_atual
from apps.core.models import ModeloBase
from apps.core.numeracao import NumeradoMixin

Q4 = Decimal("0.0001")


class Armazem(ModeloBase):
    TIPOS = [("normal", "Normal"), ("quarentena", "Quarentena (aguarda inspeção)"), ("terceiros", "Em terceiros")]
    codigo = models.CharField("código", max_length=10, unique=True)
    nome = models.CharField(max_length=100)
    tipo = models.CharField(max_length=12, choices=TIPOS, default="normal")
    padrao = models.BooleanField("padrão", default=False,
                                 help_text="Recebe as entradas de compra e atende as vendas quando nada for informado.")
    ativo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "armazém"
        verbose_name_plural = "armazéns"
        ordering = ["codigo"]

    def __str__(self):
        return f"{self.codigo} - {self.nome}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.padrao:
            Armazem.objects.exclude(pk=self.pk).filter(padrao=True).update(padrao=False)

    @classmethod
    def principal(cls):
        a = cls.objects.filter(padrao=True, ativo=True).first() or cls.objects.filter(tipo="normal", ativo=True).first()
        return a or cls.objects.create(codigo="01", nome="Almoxarifado", padrao=True)

    @classmethod
    def quarentena(cls):
        a = cls.objects.filter(tipo="quarentena", ativo=True).first()
        return a or cls.objects.create(codigo="QUA", nome="Quarentena", tipo="quarentena")


class Saldo(models.Model):
    produto = models.ForeignKey("fiscal.Produto", on_delete=models.PROTECT, related_name="saldos")
    armazem = models.ForeignKey(Armazem, verbose_name="armazém", on_delete=models.PROTECT, related_name="saldos")
    quantidade = models.DecimalField(max_digits=16, decimal_places=4, default=0)
    custo_medio = models.DecimalField("custo médio", max_digits=16, decimal_places=4, default=0)
    atualizado_em = models.DateTimeField("atualizado em", auto_now=True)

    class Meta:
        verbose_name = "saldo em estoque"
        verbose_name_plural = "saldos em estoque"
        ordering = ["produto__descricao", "armazem__codigo"]
        constraints = [models.UniqueConstraint(fields=["produto", "armazem"], name="saldo_produto_armazem")]

    def __str__(self):
        return f"{self.produto} em {self.armazem.codigo}"

    @property
    def valor(self):
        return (self.quantidade * self.custo_medio).quantize(Decimal("0.01"))

    def abaixo_do_minimo(self):
        return self.produto.estoque_minimo > 0 and self.quantidade < self.produto.estoque_minimo


class Movimento(models.Model):
    TIPOS = [("entrada", "Entrada"), ("saida", "Saída")]
    ORIGENS = [("compra", "Compra"), ("venda", "Venda"), ("producao", "Produção"), ("consumo", "Consumo"),
               ("transferencia", "Transferência"), ("inventario", "Inventário"), ("ajuste", "Ajuste manual"),
               ("manutencao", "Manutenção"), ("epi", "Entrega de EPI"), ("qualidade", "Qualidade")]
    data = models.DateTimeField(default=timezone.now, db_index=True)
    tipo = models.CharField(max_length=7, choices=TIPOS)
    origem = models.CharField(max_length=13, choices=ORIGENS)
    produto = models.ForeignKey("fiscal.Produto", on_delete=models.PROTECT, related_name="movimentos")
    armazem = models.ForeignKey(Armazem, verbose_name="armazém", on_delete=models.PROTECT, related_name="movimentos")
    quantidade = models.DecimalField(max_digits=16, decimal_places=4)
    custo_unitario = models.DecimalField("custo unitário", max_digits=16, decimal_places=4, default=0)
    saldo_apos = models.DecimalField("saldo após", max_digits=16, decimal_places=4)
    custo_medio_apos = models.DecimalField("custo médio após", max_digits=16, decimal_places=4)
    documento = models.CharField(max_length=60, blank=True)
    observacao = models.CharField("observação", max_length=200, blank=True)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="+")
    content_type = models.ForeignKey(ContentType, null=True, blank=True, on_delete=models.SET_NULL, editable=False)
    object_id = models.PositiveBigIntegerField(null=True, blank=True, editable=False)
    referencia = GenericForeignKey("content_type", "object_id")

    class Meta:
        verbose_name = "movimento de estoque"
        verbose_name_plural = "movimentos (kardex)"
        ordering = ["-data", "-id"]
        indexes = [models.Index(fields=["produto", "armazem", "data"])]

    def __str__(self):
        return f"{self.get_tipo_display()} {self.quantidade:g} {self.produto.codigo} ({self.armazem.codigo})"

    @property
    def valor(self):
        return (self.quantidade * self.custo_unitario).quantize(Decimal("0.01"))


# --------------------------------------------------------------------------- serviços
def _saldo_travado(produto, armazem):
    saldo, _ = Saldo.objects.get_or_create(produto=produto, armazem=armazem)
    return Saldo.objects.select_for_update().get(pk=saldo.pk)


def _registrar(tipo, origem, produto, armazem, quantidade, custo, saldo, documento, observacao, referencia, data):
    mov = Movimento(tipo=tipo, origem=origem, produto=produto, armazem=armazem, quantidade=quantidade,
                    custo_unitario=custo, saldo_apos=saldo.quantidade, custo_medio_apos=saldo.custo_medio,
                    documento=str(documento)[:60], observacao=observacao[:200], usuario=usuario_atual(),
                    data=data or timezone.now())
    if referencia is not None:
        mov.content_type = ContentType.objects.get_for_model(referencia)
        mov.object_id = referencia.pk
    mov.save()
    return mov


def _validar(produto, quantidade):
    if quantidade is None or Decimal(quantidade) <= 0:
        raise ValidationError("A quantidade deve ser maior que zero.")
    if getattr(produto, "tipo", "produto") == "servico":
        raise ValidationError(f"{produto} é serviço e não tem estoque.")


@transaction.atomic
def entrada(produto, quantidade, custo_unitario, armazem=None, origem="ajuste", documento="", observacao="",
            referencia=None, data=None):
    """Soma ao saldo e recalcula o custo médio ponderado."""
    _validar(produto, quantidade)
    quantidade, custo = Decimal(quantidade), Decimal(custo_unitario or 0)
    armazem = armazem or Armazem.principal()
    s = _saldo_travado(produto, armazem)
    total = s.quantidade + quantidade
    if s.quantidade <= 0:
        s.custo_medio = custo  # saldo zerado ou negativo: o custo da entrada passa a valer
    else:
        s.custo_medio = ((s.quantidade * s.custo_medio + quantidade * custo) / total).quantize(Q4)
    s.quantidade = total
    s.save()
    return _registrar("entrada", origem, produto, armazem, quantidade, custo, s, documento, observacao,
                      referencia, data)


@transaction.atomic
def saida(produto, quantidade, armazem=None, origem="consumo", documento="", observacao="", referencia=None,
          data=None):
    """Baixa pelo custo médio. Não deixa o saldo ficar negativo (ESTOQUE_PERMITE_NEGATIVO=1 libera)."""
    _validar(produto, quantidade)
    quantidade = Decimal(quantidade)
    armazem = armazem or Armazem.principal()
    s = _saldo_travado(produto, armazem)
    if s.quantidade < quantidade and not getattr(settings, "ESTOQUE_PERMITE_NEGATIVO", False):
        raise ValidationError(f"Saldo insuficiente de {produto} em {armazem.codigo}: há {s.quantidade:g}, "
                              f"pedido {quantidade:g}.")
    s.quantidade -= quantidade
    s.save()
    return _registrar("saida", origem, produto, armazem, quantidade, s.custo_medio, s, documento, observacao,
                      referencia, data)


@transaction.atomic
def transferir(produto, quantidade, origem_armazem, destino_armazem, documento="", observacao="", referencia=None):
    if origem_armazem == destino_armazem:
        raise ValidationError("Origem e destino são o mesmo armazém.")
    mov = saida(produto, quantidade, origem_armazem, "transferencia", documento,
                observacao or f"Para {destino_armazem.codigo}", referencia)
    entrada(produto, quantidade, mov.custo_unitario, destino_armazem, "transferencia", documento,
            observacao or f"De {origem_armazem.codigo}", referencia)
    return mov


@transaction.atomic
def ajustar(produto, armazem, nova_quantidade, origem="ajuste", documento="", observacao="", referencia=None):
    """Leva o saldo à quantidade informada (inventário). A diferença entra pelo custo médio atual."""
    s = _saldo_travado(produto, armazem)
    dif = Decimal(nova_quantidade) - s.quantidade
    if dif > 0:
        return entrada(produto, dif, s.custo_medio, armazem, origem, documento, observacao, referencia)
    if dif < 0:
        return saida(produto, -dif, armazem, origem, documento, observacao, referencia)
    return None


def saldo_disponivel(produto, armazem=None):
    qs = Saldo.objects.filter(produto=produto)
    qs = qs.filter(armazem=armazem) if armazem else qs.filter(armazem__tipo="normal")
    return qs.aggregate(t=models.Sum("quantidade"))["t"] or Decimal(0)


def custo_medio(produto, armazem=None):
    s = Saldo.objects.filter(produto=produto, armazem=armazem or Armazem.principal()).first()
    return s.custo_medio if s else Decimal(0)


# --------------------------------------------------------------------------- inventário
class Inventario(NumeradoMixin, ModeloBase):
    PREFIXO = "INV"
    STATUS = [("aberto", "Em contagem"), ("encerrado", "Encerrado")]
    armazem = models.ForeignKey(Armazem, verbose_name="armazém", on_delete=models.PROTECT)
    data = models.DateField(default=timezone.localdate)
    observacoes = models.TextField("observações", blank=True)
    status = models.CharField(max_length=10, choices=STATUS, default="aberto", editable=False)

    class Meta:
        verbose_name = "inventário"
        verbose_name_plural = "inventários"
        ordering = ["-numero"]

    def carregar_itens(self):
        """Uma linha por produto com saldo no armazém, com a quantidade do sistema fotografada."""
        existentes = set(self.itens.values_list("produto_id", flat=True))
        for s in Saldo.objects.filter(armazem=self.armazem).exclude(produto_id__in=existentes):
            ItemInventario.objects.create(inventario=self, produto=s.produto, quantidade_sistema=s.quantidade)

    @transaction.atomic
    def encerrar(self):
        if self.status != "aberto":
            raise ValidationError("Inventário já encerrado.")
        ajustes = 0
        for item in self.itens.select_related("produto"):
            if item.quantidade_contada is None:
                raise ValidationError(f"Falta a contagem de {item.produto}.")
            if ajustar(item.produto, self.armazem, item.quantidade_contada, "inventario", self.numero,
                       f"Inventário: sistema {item.quantidade_sistema:g}, contado {item.quantidade_contada:g}",
                       self):
                ajustes += 1
        self.status = "encerrado"
        self.save(update_fields=["status"])
        return ajustes


class ItemInventario(models.Model):
    inventario = models.ForeignKey(Inventario, on_delete=models.CASCADE, related_name="itens")
    produto = models.ForeignKey("fiscal.Produto", on_delete=models.PROTECT, limit_choices_to={"tipo": "produto"})
    quantidade_sistema = models.DecimalField("no sistema", max_digits=16, decimal_places=4, default=0, editable=False)
    quantidade_contada = models.DecimalField("contado", max_digits=16, decimal_places=4, null=True, blank=True)

    class Meta:
        verbose_name = "item contado"
        verbose_name_plural = "itens da contagem"
        ordering = ["produto__descricao"]

    def save(self, *args, **kwargs):
        if not self.pk:
            s = Saldo.objects.filter(produto_id=self.produto_id, armazem_id=self.inventario.armazem_id).first()
            self.quantidade_sistema = s.quantidade if s else Decimal(0)
        super().save(*args, **kwargs)

    @property
    def diferenca(self):
        return None if self.quantidade_contada is None else self.quantidade_contada - self.quantidade_sistema
