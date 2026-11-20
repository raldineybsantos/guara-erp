"""Estoque: armazéns, saldo por produto e armazém com custo médio, movimentos (kardex) e inventário.

Todo movimento passa por ``entrada``/``saida``/``transferir``/``ajustar``: elas travam o saldo, recalculam o
custo médio ponderado e gravam o movimento com o saldo resultante. Movimentos não se editam nem se apagam;
um erro se corrige com outro movimento.
"""

import datetime
from decimal import Decimal

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from apps.core.middleware import usuario_atual
from apps.core.models import ModeloBase, PorEmpresaMixin
from apps.core.numeracao import NumeradoMixin
from apps.core.formatos import qtd as fq

Q4 = Decimal("0.0001")


class Armazem(ModeloBase, PorEmpresaMixin):
    TIPOS = [("normal", "Normal"), ("quarentena", "Quarentena (aguarda inspeção)"), ("terceiros", "Em terceiros")]
    codigo = models.CharField("código", max_length=10)
    nome = models.CharField(max_length=100)
    tipo = models.CharField(max_length=12, choices=TIPOS, default="normal")
    padrao = models.BooleanField("padrão", default=False,
                                 help_text="Recebe as entradas de compra e atende as vendas quando nada for informado.")
    ativo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "armazém"
        verbose_name_plural = "armazéns"
        ordering = ["codigo"]
        constraints = [models.UniqueConstraint(fields=["empresa", "codigo"], name="armazem_codigo_por_empresa")]

    def __str__(self):
        return f"{self.codigo} - {self.nome}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.padrao:   # um armazém padrão por empresa
            Armazem.objects.exclude(pk=self.pk).filter(padrao=True, empresa=self.empresa).update(padrao=False)

    @classmethod
    def _da_empresa(cls, empresa):
        from apps.core.middleware import empresa_atual
        from apps.core.models import Empresa

        empresa = empresa or empresa_atual() or Empresa.padrao()
        return cls.objects.filter(ativo=True, empresa=empresa), empresa

    @classmethod
    def principal(cls, empresa=None):
        """Armazém padrão da empresa (a do documento; sem informar, a empresa ativa ou a matriz)."""
        qs, empresa = cls._da_empresa(empresa)
        a = qs.filter(padrao=True).first() or qs.filter(tipo="normal").first()
        if a is None:
            cod = "01" if not cls.objects.filter(empresa=empresa, codigo="01").exists() else "ALM"
            a = cls.objects.create(codigo=cod, nome="Almoxarifado", padrao=True, empresa=empresa)
        return a

    @classmethod
    def quarentena(cls, empresa=None):
        qs, empresa = cls._da_empresa(empresa)
        a = qs.filter(tipo="quarentena").first()
        return a or cls.objects.create(codigo="QUA", nome="Quarentena", tipo="quarentena", empresa=empresa)


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
               ("manutencao", "Manutenção"), ("epi", "Entrega de EPI"), ("uniforme", "Entrega de uniforme"), ("qualidade", "Qualidade")]
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

    @property
    def empresa(self):
        return self.armazem.empresa
    object_id = models.PositiveBigIntegerField(null=True, blank=True, editable=False)
    referencia = GenericForeignKey("content_type", "object_id")

    class Meta:
        verbose_name = "movimento de estoque"
        verbose_name_plural = "movimentos (kardex)"
        ordering = ["-data", "-id"]
        indexes = [models.Index(fields=["produto", "armazem", "data"])]

    def __str__(self):
        return f"{self.get_tipo_display()} {fq(self.quantidade)} {self.produto.codigo} ({self.armazem.codigo})"

    @property
    def valor(self):
        return (self.quantidade * self.custo_unitario).quantize(Decimal("0.01"))


class Endereco(ModeloBase):
    """Posição física dentro do armazém (rua-prateleira-nível)."""

    armazem = models.ForeignKey(Armazem, verbose_name="armazém", on_delete=models.PROTECT, related_name="enderecos")
    codigo = models.CharField("código", max_length=30)
    descricao = models.CharField("descrição", max_length=120, blank=True)
    ativo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "endereço"
        verbose_name_plural = "endereços"
        ordering = ["armazem__codigo", "codigo"]
        constraints = [models.UniqueConstraint(fields=["armazem", "codigo"], name="endereco_unico_no_armazem")]

    def __str__(self):
        return f"{self.armazem.codigo}/{self.codigo}"


class Lote(ModeloBase):
    produto = models.ForeignKey("fiscal.Produto", on_delete=models.PROTECT, related_name="lotes")
    codigo = models.CharField("lote", max_length=40)
    fabricacao = models.DateField("fabricação", null=True, blank=True)
    validade = models.DateField(null=True, blank=True)
    bloqueado = models.BooleanField(default=False, help_text="Lote bloqueado não é consumido nem vendido.")
    motivo_bloqueio = models.CharField("motivo do bloqueio", max_length=200, blank=True)
    observacoes = models.TextField("observações", blank=True)

    class Meta:
        verbose_name = "lote"
        verbose_name_plural = "lotes"
        ordering = ["validade", "codigo"]
        constraints = [models.UniqueConstraint(fields=["produto", "codigo"], name="lote_unico_por_produto")]

    def __str__(self):
        return f"{self.produto.codigo} · lote {self.codigo}"

    @property
    def vencido(self):
        return self.validade is not None and self.validade < timezone.localdate()

    @property
    def dias_para_vencer(self):
        return None if self.validade is None else (self.validade - timezone.localdate()).days

    @property
    def quantidade(self):
        return self.saldos.aggregate(t=models.Sum("quantidade"))["t"] or Decimal("0")

    def clean(self):
        if self.fabricacao and self.validade and self.validade < self.fabricacao:
            raise ValidationError({"validade": "A validade não pode ser anterior à fabricação."})
        if self.bloqueado and not self.motivo_bloqueio:
            raise ValidationError({"motivo_bloqueio": "Informe o motivo do bloqueio."})


class SaldoLote(models.Model):
    """Parte do saldo de um produto num armazém, por lote e/ou endereço. Para produto rastreado a soma destas
    linhas é igual ao saldo do armazém."""

    produto = models.ForeignKey("fiscal.Produto", on_delete=models.PROTECT, related_name="saldos_lote")
    armazem = models.ForeignKey(Armazem, verbose_name="armazém", on_delete=models.PROTECT,
                                related_name="saldos_lote")
    lote = models.ForeignKey(Lote, null=True, blank=True, on_delete=models.PROTECT, related_name="saldos")
    endereco = models.ForeignKey(Endereco, verbose_name="endereço", null=True, blank=True,
                                 on_delete=models.PROTECT, related_name="saldos")
    quantidade = models.DecimalField(max_digits=16, decimal_places=4, default=0)

    class Meta:
        verbose_name = "saldo por lote/endereço"
        verbose_name_plural = "saldos por lote e endereço"
        ordering = ["produto__descricao", "lote__validade", "lote__codigo"]

    def __str__(self):
        return f"{self.produto.codigo} {self.lote.codigo if self.lote else ''} {self.endereco or ''}".strip()


class MovimentoLote(models.Model):
    """De quais lotes/endereços saiu ou entrou cada movimento."""

    movimento = models.ForeignKey(Movimento, on_delete=models.CASCADE, related_name="partes")
    lote = models.ForeignKey(Lote, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    endereco = models.ForeignKey(Endereco, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    quantidade = models.DecimalField(max_digits=16, decimal_places=4)


# Origens que podem mexer em lote vencido ou bloqueado (descarte, quarentena, contagem).
ORIGENS_LIVRES = ("transferencia", "inventario", "ajuste", "qualidade")


def _rastreia(produto):
    return bool(getattr(produto, "controla_lote", False) or getattr(produto, "controla_endereco", False))


def _linha(produto, armazem, lote, endereco):
    """Linha de saldo (travada) para a combinação; cria se não existir (NULL não entra em restrição única)."""
    qs = SaldoLote.objects.select_for_update(of=("self",)).filter(produto=produto, armazem=armazem, lote=lote, endereco=endereco)
    linha = qs.first()
    if linha is None:
        linha = SaldoLote.objects.create(produto=produto, armazem=armazem, lote=lote, endereco=endereco)
    return linha


def _lote_da_entrada(produto, lote, validade, fabricacao, documento, origem, data):
    if not produto.controla_lote:
        return None
    if isinstance(lote, Lote):
        if lote.produto_id != produto.pk:
            raise ValidationError("O lote informado é de outro produto.")
        return lote
    codigo = (str(lote).strip() if lote else "") or f"{documento or origem}-{(data or timezone.now()):%Y%m%d}"
    obj, criado = Lote.objects.get_or_create(produto=produto, codigo=codigo[:40],
                                             defaults={"validade": validade, "fabricacao": fabricacao})
    if not criado:
        if validade and obj.validade and validade != obj.validade:
            raise ValidationError(f"O lote {obj.codigo} já existe com validade {obj.validade:%d/%m/%Y}.")
        if validade and not obj.validade:
            obj.validade = validade
            obj.save()
    return obj


def _rastrear_entrada(mov, produto, armazem, quantidade, lote, validade, fabricacao, endereco, partes):
    if partes is None:
        partes = [(_lote_da_entrada(produto, lote, validade, fabricacao, mov.documento, mov.origem, mov.data),
                   endereco, quantidade)]
    for lote_obj, end, q in partes:
        if end is not None and end.armazem_id != armazem.pk:
            raise ValidationError(f"O endereço {end} não pertence ao armazém {armazem.codigo}.")
        linha = _linha(produto, armazem, lote_obj, end)
        linha.quantidade += q
        linha.save()
        MovimentoLote.objects.create(movimento=mov, lote=lote_obj, endereco=end, quantidade=q)


def _alocar(produto, armazem, quantidade, lote, endereco, origem):
    """Escolhe as linhas que atendem a saída: o lote pedido ou, sem pedido, FEFO (vence antes, sai antes)."""
    livre = origem in ORIGENS_LIVRES
    qs = SaldoLote.objects.select_for_update(of=("self",)).filter(produto=produto, armazem=armazem, quantidade__gt=0)
    if lote is not None:
        if not isinstance(lote, Lote):
            lote = Lote.objects.filter(produto=produto, codigo=str(lote).strip()).first()
            if lote is None:
                raise ValidationError("Lote não encontrado para este produto.")
        if not livre and lote.bloqueado:
            raise ValidationError(f"O lote {lote.codigo} está bloqueado: {lote.motivo_bloqueio or 'sem motivo informado'}.")
        if not livre and lote.vencido:
            raise ValidationError(f"O lote {lote.codigo} venceu em {lote.validade:%d/%m/%Y}.")
        qs = qs.filter(lote=lote)
    elif not livre:
        qs = qs.filter(models.Q(lote__isnull=True) | models.Q(lote__bloqueado=False)).exclude(
            lote__validade__lt=timezone.localdate())
    if endereco is not None:
        qs = qs.filter(endereco=endereco)
    qs = qs.order_by(models.F("lote__validade").asc(nulls_last=True), "lote_id", "endereco_id", "id")
    restante, partes = Decimal(quantidade), []
    for linha in qs:
        q = min(linha.quantidade, restante)
        partes.append((linha, q))
        restante -= q
        if restante <= 0:
            break
    if restante > 0 and not getattr(settings, "ESTOQUE_PERMITE_NEGATIVO", False):
        raise ValidationError(
            f"Saldo utilizável insuficiente de {produto}"
            + ("" if lote is not None or livre else " (lotes vencidos ou bloqueados não entram)")
            + f": faltam {fq(restante)}.")
    return partes


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
            referencia=None, data=None, lote=None, validade=None, fabricacao=None, endereco=None, partes=None):
    """Soma ao saldo e recalcula o custo médio ponderado. Produto com controle de lote/endereço registra de
    qual lote e endereço (sem lote informado, cria um com o documento e a data)."""
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
    mov = _registrar("entrada", origem, produto, armazem, quantidade, custo, s, documento, observacao,
                     referencia, data)
    if _rastreia(produto):
        _rastrear_entrada(mov, produto, armazem, quantidade, lote, validade, fabricacao, endereco, partes)
    return mov


@transaction.atomic
def saida(produto, quantidade, armazem=None, origem="consumo", documento="", observacao="", referencia=None,
          data=None, lote=None, endereco=None):
    """Baixa pelo custo médio. Não deixa o saldo ficar negativo (ESTOQUE_PERMITE_NEGATIVO=1 libera).
    Produto rastreado sai do lote pedido ou, sem pedido, do que vence primeiro (FEFO)."""
    _validar(produto, quantidade)
    quantidade = Decimal(quantidade)
    armazem = armazem or Armazem.principal()
    s = _saldo_travado(produto, armazem)
    if s.quantidade < quantidade and not getattr(settings, "ESTOQUE_PERMITE_NEGATIVO", False):
        raise ValidationError(f"Saldo insuficiente de {produto} em {armazem.codigo}: há {fq(s.quantidade)}, "
                              f"pedido {fq(quantidade)}.")
    partes = _alocar(produto, armazem, quantidade, lote, endereco, origem) if _rastreia(produto) else []
    s.quantidade -= quantidade
    s.save()
    mov = _registrar("saida", origem, produto, armazem, quantidade, s.custo_medio, s, documento, observacao,
                     referencia, data)
    for linha, q in partes:
        linha.quantidade -= q
        linha.save()
        MovimentoLote.objects.create(movimento=mov, lote=linha.lote, endereco=linha.endereco, quantidade=q)
    return mov


@transaction.atomic
def transferir(produto, quantidade, origem_armazem, destino_armazem, documento="", observacao="", referencia=None):
    if origem_armazem == destino_armazem:
        raise ValidationError("Origem e destino são o mesmo armazém.")
    mov = saida(produto, quantidade, origem_armazem, "transferencia", documento,
                observacao or f"Para {destino_armazem.codigo}", referencia)
    partes = [(p.lote, None, p.quantidade) for p in mov.partes.all()] or None
    entrada(produto, quantidade, mov.custo_unitario, destino_armazem, "transferencia", documento,
            observacao or f"De {origem_armazem.codigo}", referencia, partes=partes)
    return mov


@transaction.atomic
def ajustar(produto, armazem, nova_quantidade, origem="ajuste", documento="", observacao="", referencia=None,
            lote=None, validade=None, endereco=None):
    """Leva o saldo à quantidade informada (inventário). A diferença entra pelo custo médio atual."""
    s = _saldo_travado(produto, armazem)
    dif = Decimal(nova_quantidade) - s.quantidade
    if dif > 0:
        return entrada(produto, dif, s.custo_medio, armazem, origem, documento, observacao, referencia,
                       lote=lote, validade=validade, endereco=endereco)
    if dif < 0:
        return saida(produto, -dif, armazem, origem, documento, observacao, referencia, lote=lote,
                     endereco=endereco)
    return None


@transaction.atomic
def enderecar(produto, quantidade, armazem, lote, de, para):
    """Muda quantidade de um endereço para outro (ou de 'sem endereço' para um endereço) dentro do armazém.
    Não altera o saldo nem o custo: só a localização."""
    quantidade = Decimal(quantidade)
    if quantidade <= 0:
        raise ValidationError("A quantidade deve ser maior que zero.")
    if de == para:
        raise ValidationError("Origem e destino são o mesmo endereço.")
    if para is not None and para.armazem_id != armazem.pk:
        raise ValidationError(f"O endereço {para} não pertence ao armazém {armazem.codigo}.")
    if lote is not None and not isinstance(lote, Lote):
        lote = Lote.objects.filter(produto=produto, codigo=str(lote).strip()).first()
    origem = SaldoLote.objects.select_for_update(of=("self",)).filter(produto=produto, armazem=armazem, lote=lote,
                                                          endereco=de).first()
    if origem is None or origem.quantidade < quantidade:
        raise ValidationError(f"Não há {fq(quantidade)} de {produto} nesse lote e endereço de origem.")
    destino = _linha(produto, armazem, lote, para)
    origem.quantidade -= quantidade
    destino.quantidade += quantidade
    origem.save()
    destino.save()
    return destino


def lotes_a_vencer(dias=30, armazem=None):
    """Linhas de saldo com lote que vence em até `dias` dias (já vencidos primeiro)."""
    limite = timezone.localdate() + datetime.timedelta(days=dias)
    qs = SaldoLote.objects.filter(quantidade__gt=0, lote__validade__lte=limite).select_related(
        "produto", "lote", "armazem", "endereco")
    if armazem is not None:
        qs = qs.filter(armazem=armazem)
    return qs.order_by("lote__validade", "produto__codigo")


def saldo_disponivel(produto, armazem=None):
    qs = Saldo.objects.filter(produto=produto)
    qs = qs.filter(armazem=armazem) if armazem else qs.filter(armazem__tipo="normal")
    total = qs.aggregate(t=models.Sum("quantidade"))["t"] or Decimal(0)
    if getattr(produto, "controla_lote", False):   # lote vencido ou bloqueado não está disponível
        ruim = SaldoLote.objects.filter(produto=produto, armazem__in=qs.values("armazem")).filter(
            models.Q(lote__bloqueado=True) | models.Q(lote__validade__lt=timezone.localdate()))
        total -= ruim.aggregate(t=models.Sum("quantidade"))["t"] or Decimal(0)
    return total


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
                       f"Inventário: sistema {fq(item.quantidade_sistema)}, contado {fq(item.quantidade_contada)}",
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
