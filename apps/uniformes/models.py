"""Catálogo de uniformes: peças com numeração, imagem e estoque por tamanho; conjuntos (kits) por função e entrega
ao colaborador com baixa automática do estoque.

Cada tamanho de uma peça é um Produto do estoque (código UNI-<peça>-<tamanho>): assim compra, recebimento,
inventário, kardex e estoque mínimo funcionam como em qualquer outro item."""
import calendar
from collections import Counter
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db import models, transaction
from django.db.models import Sum
from django.utils import timezone

from apps.core.models import Cargo, Colaborador, Departamento, ModeloBase

NUMERACOES = {
    "letras": ("Letras (PP a XGG)", ["PP", "P", "M", "G", "GG", "XG", "XGG"]),
    "calca": ("Numérica de roupa (36 a 56)", [str(n) for n in range(36, 58, 2)]),
    "calcado": ("Calçado (34 a 48)", [str(n) for n in range(34, 49)]),
    "unico": ("Tamanho único", ["Único"]),
}
NUMERACAO_CHOICES = [(k, v[0]) for k, v in NUMERACOES.items()]
TAMANHO_COLABORADOR_CHOICES = [(k, v[0]) for k, v in NUMERACOES.items() if k != "unico"]


def tamanhos_de(numeracao):
    return NUMERACOES[numeracao][1]


def validar_imagem(arquivo):
    if arquivo.size > 5 * 1024 * 1024:
        raise ValidationError("Imagem maior que 5 MB.")


def somar_meses(data, n):
    m = data.month - 1 + n
    ano, mes = data.year + m // 12, m % 12 + 1
    return data.replace(year=ano, month=mes, day=min(data.day, calendar.monthrange(ano, mes)[1]))


def disponibilidade(produtos_ids):
    """{produto_id: quantidade em armazéns normais}, numa consulta só."""
    from apps.estoque.models import Saldo

    linhas = (Saldo.objects.filter(produto_id__in=list(produtos_ids), armazem__tipo="normal")
              .values("produto_id").annotate(t=Sum("quantidade")))
    return {r["produto_id"]: r["t"] or Decimal(0) for r in linhas}


class ModeloUniforme(ModeloBase):
    """Uma peça do catálogo (ex.: camisa polo azul), com a sua numeração."""
    CATEGORIAS = [("camisa", "Camisa / polo"), ("camiseta", "Camiseta"), ("calca", "Calça"), ("bermuda", "Bermuda"),
                  ("jaleco", "Jaleco"), ("jaqueta", "Jaqueta / agasalho"), ("colete", "Colete"),
                  ("avental", "Avental"), ("calcado", "Calçado / bota"), ("bone", "Boné / touca"),
                  ("meia", "Meia"), ("luva", "Luva"), ("outro", "Outro")]
    GENEROS = [("unissex", "Unissex"), ("masculino", "Masculino"), ("feminino", "Feminino")]
    codigo = models.CharField("código", max_length=20, unique=True,
                              help_text="Curto e sem espaços (ex.: POLO-AZ). Vira parte do código de cada tamanho.")
    nome = models.CharField(max_length=120)
    categoria = models.CharField(max_length=10, choices=CATEGORIAS, default="camisa")
    genero = models.CharField("modelagem", max_length=10, choices=GENEROS, default="unissex")
    numeracao = models.CharField("numeração", max_length=10, choices=NUMERACAO_CHOICES, default="letras")
    descricao = models.TextField("descrição", blank=True, help_text="Cor, tecido, bordado/logotipo, uso (ex.: produção).")
    imagem = models.ImageField(upload_to="uniformes/%Y/", blank=True, null=True,
                               validators=[FileExtensionValidator(["jpg", "jpeg", "png", "webp"]), validar_imagem],
                               help_text="JPG, PNG ou WebP, até 5 MB.")
    vida_util_meses = models.PositiveSmallIntegerField("vida útil (meses)", null=True, blank=True,
                                                       help_text="Prazo para a troca; calcula a data de troca na entrega.")
    estoque_minimo = models.DecimalField("estoque mínimo por tamanho", max_digits=10, decimal_places=2, default=0,
                                         help_text="Abaixo disso o tamanho aparece como estoque baixo e no alerta de reposição.")
    ativo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "peça de uniforme"
        verbose_name_plural = "catálogo de uniformes"
        ordering = ["nome"]

    def __str__(self):
        return self.nome

    def clean(self):
        super().clean()
        self.codigo = (self.codigo or "").strip().upper().replace(" ", "-")

    def save(self, *a, **kw):
        self.codigo = (self.codigo or "").strip().upper().replace(" ", "-")
        super().save(*a, **kw)

    def sincronizar_itens(self):
        """Cria o item (e o produto de estoque) de cada tamanho da numeração e mantém nome/mínimo/ativo em dia."""
        novos = 0
        for t in tamanhos_de(self.numeracao):
            item, criado = ItemUniforme.objects.get_or_create(modelo=self, tamanho=t)
            item.sincronizar_produto()
            novos += criado
        for item in self.itens.exclude(tamanho__in=tamanhos_de(self.numeracao)):
            item.sincronizar_produto()
        return novos

    @property
    def itens_ordenados(self):
        ordem = {t: i for i, t in enumerate(tamanhos_de(self.numeracao))}
        return sorted(self.itens.select_related("produto"), key=lambda i: (ordem.get(i.tamanho, 99), i.tamanho))

    def painel(self):
        """Lista de dicts {item, disponivel, situacao} para o catálogo e o detalhe."""
        itens = self.itens_ordenados
        disp = disponibilidade(i.produto_id for i in itens if i.produto_id)
        return [{"item": i, "disponivel": disp.get(i.produto_id, Decimal(0)), "situacao": i.situacao_de(disp.get(i.produto_id, Decimal(0)))}
                for i in itens]

    @property
    def total_em_estoque(self):
        return sum(disponibilidade(self.itens.exclude(produto=None).values_list("produto_id", flat=True)).values(), Decimal(0))
    total_em_estoque.fget.short_description = "total em estoque"

    @property
    def situacao_geral(self):
        sit = {p["situacao"] for p in self.painel()}
        return "sem" if sit == {"sem"} else "baixo" if sit & {"sem", "baixo"} else "ok"


class ItemUniforme(ModeloBase):
    """Um tamanho de uma peça = um produto de estoque."""
    modelo = models.ForeignKey(ModeloUniforme, on_delete=models.CASCADE, related_name="itens")
    tamanho = models.CharField(max_length=8)
    produto = models.ForeignKey("fiscal.Produto", null=True, blank=True, on_delete=models.PROTECT, editable=False,
                                related_name="item_uniforme")

    class Meta:
        verbose_name = "tamanho de uniforme"
        verbose_name_plural = "tamanhos de uniforme"
        unique_together = [("modelo", "tamanho")]
        ordering = ["modelo__nome", "id"]

    def __str__(self):
        return f"{self.modelo.nome} — {self.tamanho}"

    def sincronizar_produto(self):
        from apps.fiscal.models import Produto

        desc = f"{self.modelo.nome} {self.tamanho}"[:200]
        if self.produto_id is None:
            self.produto = Produto.objects.create(
                codigo=f"UNI-{self.modelo.codigo}-{self.tamanho}"[:40], descricao=desc, tipo="produto", unidade="UN",
                estoque_minimo=self.modelo.estoque_minimo, ativo=self.modelo.ativo)
            self.save(update_fields=["produto"])
        else:
            Produto.objects.filter(pk=self.produto_id).update(
                descricao=desc, estoque_minimo=self.modelo.estoque_minimo, ativo=self.modelo.ativo)

    @property
    def disponivel(self):
        return disponibilidade([self.produto_id]).get(self.produto_id, Decimal(0)) if self.produto_id else Decimal(0)

    def situacao_de(self, qtd):
        if qtd <= 0:
            return "sem"
        return "baixo" if self.modelo.estoque_minimo > 0 and qtd < self.modelo.estoque_minimo else "ok"

    @property
    def situacao(self):
        return self.situacao_de(self.disponivel)


class ConjuntoUniforme(ModeloBase):
    """Kit de uniforme de uma função (ex.: conjunto da produção: 3 camisas, 2 calças, 1 bota)."""
    nome = models.CharField(max_length=120, unique=True)
    descricao = models.TextField("descrição", blank=True)
    imagem = models.ImageField(upload_to="uniformes/%Y/", blank=True, null=True,
                               validators=[FileExtensionValidator(["jpg", "jpeg", "png", "webp"]), validar_imagem])
    departamento = models.ForeignKey(Departamento, null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name="+", help_text="Quem usa: deixe em branco para todos.")
    cargo = models.ForeignKey(Cargo, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    ativo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "conjunto de uniforme"
        verbose_name_plural = "conjuntos de uniforme"
        ordering = ["nome"]

    def __str__(self):
        return self.nome

    @property
    def colaboradores_alvo(self):
        qs = Colaborador.objects.exclude(status="desligado")
        if self.departamento_id:
            qs = qs.filter(departamento_id=self.departamento_id)
        if self.cargo_id:
            qs = qs.filter(cargo_id=self.cargo_id)
        return qs

    def cobertura(self):
        """Por peça e tamanho: quantos colaboradores precisam, quanto há em estoque e quanto falta."""
        colabs = list(self.colaboradores_alvo.values_list("pk", flat=True))
        tams = TamanhoColaborador.objects.filter(colaborador_id__in=colabs)
        saida = []
        for peca in self.pecas.select_related("modelo"):
            m = peca.modelo
            painel = m.painel()
            if m.numeracao == "unico":
                linhas = [{**p, "precisam": len(colabs) * peca.quantidade} for p in painel]
                sem_tamanho = 0
            else:
                cont = Counter(t.tamanho for t in tams if t.numeracao == m.numeracao)
                sem_tamanho = len(colabs) - sum(cont.values())
                linhas = [{**p, "precisam": cont.get(p["item"].tamanho, 0) * peca.quantidade} for p in painel]
            for l in linhas:
                l["falta"] = max(Decimal(0), Decimal(l["precisam"]) - l["disponivel"])
            saida.append({"peca": peca, "linhas": linhas, "sem_tamanho": max(sem_tamanho, 0),
                          "falta_total": sum((l["falta"] for l in linhas), Decimal(0))})
        return saida


class PecaConjunto(models.Model):
    conjunto = models.ForeignKey(ConjuntoUniforme, on_delete=models.CASCADE, related_name="pecas")
    modelo = models.ForeignKey(ModeloUniforme, verbose_name="peça", on_delete=models.PROTECT, related_name="conjuntos")
    quantidade = models.PositiveSmallIntegerField("quantidade no conjunto", default=1)

    class Meta:
        verbose_name = "peça do conjunto"
        verbose_name_plural = "peças do conjunto"
        unique_together = [("conjunto", "modelo")]
        ordering = ["id"]

    def __str__(self):
        return f"{self.quantidade}× {self.modelo}"

    def clean(self):
        if self.quantidade < 1:
            raise ValidationError({"quantidade": "A quantidade deve ser de pelo menos 1."})


class TamanhoColaborador(ModeloBase):
    """Numeração de cada colaborador (camisa, calça, calçado): sugere o tamanho na entrega e dimensiona a compra."""
    colaborador = models.ForeignKey(Colaborador, on_delete=models.CASCADE, related_name="tamanhos_uniforme")
    numeracao = models.CharField("tipo de numeração", max_length=10, choices=TAMANHO_COLABORADOR_CHOICES)
    tamanho = models.CharField(max_length=8)

    class Meta:
        verbose_name = "tamanho do colaborador"
        verbose_name_plural = "tamanhos dos colaboradores"
        unique_together = [("colaborador", "numeracao")]
        ordering = ["colaborador__nome", "numeracao"]

    def __str__(self):
        return f"{self.colaborador}: {self.get_numeracao_display()} {self.tamanho}"

    def clean(self):
        if self.numeracao and self.tamanho and self.tamanho not in tamanhos_de(self.numeracao):
            raise ValidationError({"tamanho": f"Tamanho inválido. Use: {', '.join(tamanhos_de(self.numeracao))}."})


class EntregaUniforme(ModeloBase):
    STATUS = [("entregue", "Entregue"), ("estornada", "Estornada")]
    colaborador = models.ForeignKey(Colaborador, on_delete=models.PROTECT, related_name="entregas_uniforme")
    conjunto = models.ForeignKey(ConjuntoUniforme, null=True, blank=True, on_delete=models.SET_NULL, related_name="entregas")
    data = models.DateField("data da entrega", default=timezone.localdate)
    status = models.CharField(max_length=10, choices=STATUS, default="entregue")
    recebido_confirmado = models.BooleanField("colaborador assinou o termo", default=False)
    observacoes = models.CharField("observações", max_length=300, blank=True)

    class Meta:
        verbose_name = "entrega de uniforme"
        verbose_name_plural = "entregas de uniforme"
        ordering = ["-data", "-id"]

    def __str__(self):
        return f"Entrega #{self.pk} — {self.colaborador}"

    @classmethod
    @transaction.atomic
    def registrar(cls, colaborador, linhas, conjunto=None, data=None, observacoes="", usuario=None):
        """`linhas`: lista de (ItemUniforme, quantidade). Baixa o estoque; se faltar qualquer peça, nada é entregue."""
        from apps.estoque import models as est

        if colaborador.status == "desligado":
            raise ValidationError("O colaborador está desligado.")
        agreg = Counter()
        for item, qtd in linhas:
            if qtd and int(qtd) > 0:
                agreg[item] += int(qtd)
        if not agreg:
            raise ValidationError("Escolha ao menos uma peça com quantidade.")
        data = data or timezone.localdate()
        falta = []
        for item, qtd in agreg.items():
            if item.disponivel < qtd:
                falta.append(f"{item} (pedido {qtd}, em estoque {item.disponivel:g})")
        if falta:
            raise ValidationError("Sem estoque suficiente: " + "; ".join(falta) + ".")
        entrega = cls.objects.create(colaborador=colaborador, conjunto=conjunto, data=data, observacoes=observacoes)
        for item, qtd in agreg.items():
            armazem = est.Saldo.objects.filter(produto=item.produto, armazem__tipo="normal", quantidade__gte=qtd) \
                .order_by("-quantidade").select_related("armazem").first()
            if armazem is None:
                raise ValidationError(f"{item}: o estoque está dividido entre armazéns; transfira antes de entregar.")
            est.saida(item.produto, qtd, armazem.armazem, "uniforme", f"UNIF-{entrega.pk}", str(colaborador),
                      referencia=entrega)
            meses = item.modelo.vida_util_meses
            ItemEntregaUniforme.objects.create(entrega=entrega, item=item, quantidade=qtd,
                                               trocar_ate=somar_meses(data, meses) if meses else None)
        return entrega

    @transaction.atomic
    def estornar(self):
        """Devolve as peças ao armazém de onde saíram (entrega feita por engano ou peças devolvidas)."""
        from django.contrib.contenttypes.models import ContentType

        from apps.estoque import models as est

        if self.status != "entregue":
            raise ValidationError("A entrega já foi estornada.")
        ct = ContentType.objects.get_for_model(self)
        for it in self.itens.select_related("item__produto"):
            mov = est.Movimento.objects.filter(content_type=ct, object_id=self.pk, tipo="saida",
                                               produto=it.item.produto).first()
            est.entrada(it.item.produto, it.quantidade, mov.custo_unitario if mov else 0, mov.armazem if mov else None,
                        "uniforme", f"UNIF-{self.pk}", f"Estorno da entrega a {self.colaborador}", referencia=self)
        self.status = "estornada"
        self.save(update_fields=["status", "atualizado_em"])


class ItemEntregaUniforme(models.Model):
    entrega = models.ForeignKey(EntregaUniforme, on_delete=models.CASCADE, related_name="itens")
    item = models.ForeignKey(ItemUniforme, verbose_name="peça e tamanho", on_delete=models.PROTECT, related_name="entregas")
    quantidade = models.PositiveSmallIntegerField(default=1)
    trocar_ate = models.DateField("trocar até", null=True, blank=True)

    class Meta:
        verbose_name = "peça entregue"
        verbose_name_plural = "peças entregues"
        ordering = ["id"]

    def __str__(self):
        return f"{self.quantidade}× {self.item}"
