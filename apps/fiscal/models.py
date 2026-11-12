import datetime
from decimal import Decimal

from django.db import models, transaction
from django.db.models import Max
from django.utils import timezone

from apps.core.models import Cliente, Fornecedor, ModeloBase, PorEmpresaMixin
from apps.core.validators import validar_xml

D2 = Decimal("0.01")


class Produto(ModeloBase):
    TIPOS = [("produto", "Produto"), ("servico", "Serviço")]
    codigo = models.CharField("código", max_length=40, unique=True)
    descricao = models.CharField("descrição", max_length=200)
    tipo = models.CharField(max_length=10, choices=TIPOS, default="produto")
    unidade = models.CharField(max_length=6, default="UN")
    ncm = models.CharField("NCM", max_length=10, blank=True)
    cest = models.CharField("CEST", max_length=10, blank=True)
    codigo_servico = models.CharField("código de serviço (LC 116)", max_length=10, blank=True)
    cfop_padrao = models.CharField("CFOP padrão", max_length=4, blank=True)
    preco = models.DecimalField("preço", max_digits=14, decimal_places=2, default=0)
    aliquota_icms = models.DecimalField("alíquota ICMS %", max_digits=5, decimal_places=2, default=0)
    aliquota_ipi = models.DecimalField("alíquota IPI %", max_digits=5, decimal_places=2, default=0)
    aliquota_pis = models.DecimalField("alíquota PIS %", max_digits=5, decimal_places=2, default=0)
    aliquota_cofins = models.DecimalField("alíquota COFINS %", max_digits=5, decimal_places=2, default=0)
    aliquota_iss = models.DecimalField("alíquota ISS %", max_digits=5, decimal_places=2, default=0)
    ativo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "produto / serviço"
        verbose_name_plural = "produtos e serviços"
        ordering = ["descricao"]

    def __str__(self):
        return f"{self.codigo} - {self.descricao}"


class NotaFiscal(ModeloBase, PorEmpresaMixin):
    TIPOS = [("saida", "Saída (venda/prestação)"), ("entrada", "Entrada (compra)")]
    MODELOS = [("55", "NF-e (55)"), ("65", "NFC-e (65)"), ("nfse", "NFS-e"), ("outro", "Outro")]
    STATUS = [("rascunho", "Rascunho"), ("emitida", "Emitida/autorizada"), ("cancelada", "Cancelada")]

    tipo = models.CharField(max_length=10, choices=TIPOS, default="saida")
    modelo = models.CharField(max_length=6, choices=MODELOS, default="55")
    numero = models.PositiveIntegerField("número", null=True, blank=True)
    serie = models.CharField("série", max_length=5, default="1")
    data_emissao = models.DateField("data de emissão", default=timezone.localdate)
    natureza_operacao = models.CharField("natureza da operação", max_length=100, default="Venda de mercadoria")
    cliente = models.ForeignKey(Cliente, null=True, blank=True, on_delete=models.PROTECT, related_name="notas")
    fornecedor = models.ForeignKey(Fornecedor, null=True, blank=True, on_delete=models.PROTECT, related_name="notas")
    chave_acesso = models.CharField("chave de acesso", max_length=44, blank=True, db_index=True)
    protocolo = models.CharField("protocolo de autorização", max_length=30, blank=True)
    status = models.CharField(max_length=10, choices=STATUS, default="rascunho")
    valor_frete = models.DecimalField("frete", max_digits=14, decimal_places=2, default=0)
    valor_desconto = models.DecimalField("desconto", max_digits=14, decimal_places=2, default=0)
    xml = models.FileField("XML", upload_to="fiscal/xml/%Y/%m/", blank=True, validators=[validar_xml])
    observacoes = models.TextField("informações complementares", blank=True)
    # totais calculados a partir dos itens
    valor_produtos = models.DecimalField("total dos itens", max_digits=14, decimal_places=2, default=0, editable=False)
    valor_icms = models.DecimalField("ICMS", max_digits=14, decimal_places=2, default=0, editable=False)
    valor_ipi = models.DecimalField("IPI", max_digits=14, decimal_places=2, default=0, editable=False)
    valor_pis = models.DecimalField("PIS", max_digits=14, decimal_places=2, default=0, editable=False)
    valor_cofins = models.DecimalField("COFINS", max_digits=14, decimal_places=2, default=0, editable=False)
    valor_iss = models.DecimalField("ISS", max_digits=14, decimal_places=2, default=0, editable=False)
    valor_total = models.DecimalField("valor total", max_digits=14, decimal_places=2, default=0, editable=False)

    class Meta:
        verbose_name = "nota fiscal"
        verbose_name_plural = "notas fiscais"
        ordering = ["-data_emissao", "-numero"]
        permissions = [("emitir_notafiscal", "Pode emitir/cancelar notas fiscais")]

    @property
    def link_xml(self):
        from django.urls import reverse
        from django.utils.html import format_html
        if not self.xml:
            return ""
        return format_html('<a href="{}"><i class="bi bi-filetype-xml"></i> Baixar XML</a>',
                           reverse("fiscal:baixar_xml", args=[self.pk]))
    link_xml.fget.short_description = "XML"

    def __str__(self):
        num = self.numero or "s/n"
        return f"{self.get_modelo_display()} {num}/{self.serie}"

    @property
    def participante(self):
        return self.cliente if self.tipo == "saida" else self.fornecedor
    participante.fget.short_description = "destinatário / emitente"

    def recalcular(self):
        tot = {k: Decimal(0) for k in ("produtos", "icms", "ipi", "pis", "cofins", "iss")}
        for item in self.itens.all():
            tot["produtos"] += item.valor_total
            tot["icms"] += item.valor_icms
            tot["ipi"] += item.valor_ipi
            tot["pis"] += item.valor_pis
            tot["cofins"] += item.valor_cofins
            tot["iss"] += item.valor_iss
        for k, v in tot.items():
            setattr(self, f"valor_{k}", v.quantize(D2))
        self.valor_total = (tot["produtos"] + tot["ipi"] + self.valor_frete - self.valor_desconto).quantize(D2)
        NotaFiscal.objects.filter(pk=self.pk).update(
            valor_produtos=self.valor_produtos, valor_icms=self.valor_icms, valor_ipi=self.valor_ipi,
            valor_pis=self.valor_pis, valor_cofins=self.valor_cofins, valor_iss=self.valor_iss,
            valor_total=self.valor_total,
        )

    @transaction.atomic
    def emitir(self):
        """Numera e marca como emitida. A transmissão à SEFAZ/prefeitura é feita por integração externa."""
        if self.status != "rascunho":
            raise ValueError("Apenas notas em rascunho podem ser emitidas.")
        if not self.itens.exists():
            raise ValueError("A nota não possui itens.")
        if not self.numero:
            ultimo = (NotaFiscal.objects.select_for_update()
                      .filter(empresa=self.empresa, modelo=self.modelo, serie=self.serie, tipo="saida")
                      .aggregate(m=Max("numero"))["m"]) or 0
            self.numero = ultimo + 1
        self.status = "emitida"
        self.save()

    def gerar_titulo(self, vencimento=None):
        """Cria a conta a receber (saída) ou a pagar (entrada) correspondente à nota."""
        from apps.financeiro.models import ContaPagar, ContaReceber

        dados = dict(empresa=self.empresa, descricao=f"{self}"[:200], documento=str(self.numero or ""),
                     valor=self.valor_total, data_emissao=self.data_emissao,
                     vencimento=vencimento or self.data_emissao + datetime.timedelta(days=30))
        if self.tipo == "saida":
            return ContaReceber.objects.create(cliente=self.cliente, **dados)
        prazo = self.fornecedor.prazo_pagamento_dias if self.fornecedor else 30
        dados["vencimento"] = vencimento or self.data_emissao + datetime.timedelta(days=prazo)
        return ContaPagar.objects.create(fornecedor=self.fornecedor, **dados)


class ItemNota(models.Model):
    nota = models.ForeignKey(NotaFiscal, on_delete=models.CASCADE, related_name="itens")
    produto = models.ForeignKey(Produto, null=True, blank=True, on_delete=models.PROTECT)
    descricao = models.CharField("descrição", max_length=200, blank=True)
    cfop = models.CharField("CFOP", max_length=4, blank=True)
    ncm = models.CharField("NCM", max_length=10, blank=True)
    quantidade = models.DecimalField(max_digits=14, decimal_places=4, default=1)
    valor_unitario = models.DecimalField("valor unitário", max_digits=14, decimal_places=4, default=0)
    aliquota_icms = models.DecimalField("ICMS %", max_digits=5, decimal_places=2, null=True, blank=True)
    aliquota_ipi = models.DecimalField("IPI %", max_digits=5, decimal_places=2, null=True, blank=True)
    aliquota_pis = models.DecimalField("PIS %", max_digits=5, decimal_places=2, null=True, blank=True)
    aliquota_cofins = models.DecimalField("COFINS %", max_digits=5, decimal_places=2, null=True, blank=True)
    aliquota_iss = models.DecimalField("ISS %", max_digits=5, decimal_places=2, null=True, blank=True)

    class Meta:
        verbose_name = "item da nota"
        verbose_name_plural = "itens da nota"

    def __str__(self):
        return self.descricao

    def save(self, *args, **kwargs):
        p = self.produto
        if p:
            self.descricao = self.descricao or p.descricao
            self.cfop = self.cfop or p.cfop_padrao
            self.ncm = self.ncm or p.ncm
            if not self.valor_unitario:
                self.valor_unitario = p.preco
            for imp in ("icms", "ipi", "pis", "cofins", "iss"):
                if getattr(self, f"aliquota_{imp}") is None:
                    setattr(self, f"aliquota_{imp}", getattr(p, f"aliquota_{imp}"))
        for imp in ("icms", "ipi", "pis", "cofins", "iss"):
            if getattr(self, f"aliquota_{imp}") is None:
                setattr(self, f"aliquota_{imp}", Decimal(0))
        super().save(*args, **kwargs)

    @property
    def valor_total(self):
        return ((self.quantidade or 0) * (self.valor_unitario or 0)).quantize(D2)
    valor_total.fget.short_description = "valor total"

    def _imposto(self, imp):
        return (self.valor_total * (getattr(self, f"aliquota_{imp}") or 0) / 100).quantize(D2)

    valor_icms = property(lambda self: self._imposto("icms"))
    valor_ipi = property(lambda self: self._imposto("ipi"))
    valor_pis = property(lambda self: self._imposto("pis"))
    valor_cofins = property(lambda self: self._imposto("cofins"))
    valor_iss = property(lambda self: self._imposto("iss"))
