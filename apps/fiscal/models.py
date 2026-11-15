import datetime
from decimal import Decimal

from django.db import models, transaction
from django.db.models import Max
from django.utils import timezone

from apps.core.models import Cliente, Fornecedor, ModeloBase, PorEmpresaMixin
from apps.core.validators import somente_digitos, validar_xml

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
    codigo_tributacao_nacional = models.CharField(
        "código de tributação nacional (NFS-e)", max_length=8, blank=True,
        help_text="6 dígitos: item, subitem e desdobro da lista da LC 116 (ex.: 01.07.01 = 010701). "
                  "Exigido na NFS-e do Emissor Nacional.")
    codigo_tributacao_municipal = models.CharField("código de tributação municipal (NFS-e)", max_length=3,
                                                   blank=True, help_text="Só se o município exigir.")
    codigo_nbs = models.CharField("código NBS", max_length=14, blank=True,
                                  help_text="Nomenclatura Brasileira de Serviços, 9 dígitos. Informe quando o "
                                            "município ou o serviço exigir.")
    cfop_padrao = models.CharField("CFOP padrão", max_length=4, blank=True)
    preco = models.DecimalField("preço", max_digits=14, decimal_places=2, default=0)
    aliquota_icms = models.DecimalField("alíquota ICMS %", max_digits=5, decimal_places=2, default=0)
    aliquota_ipi = models.DecimalField("alíquota IPI %", max_digits=5, decimal_places=2, default=0)
    aliquota_pis = models.DecimalField("alíquota PIS %", max_digits=5, decimal_places=2, default=0)
    aliquota_cofins = models.DecimalField("alíquota COFINS %", max_digits=5, decimal_places=2, default=0)
    aliquota_iss = models.DecimalField("alíquota ISS %", max_digits=5, decimal_places=2, default=0)
    estoque_minimo = models.DecimalField("estoque mínimo", max_digits=14, decimal_places=4, default=0,
                                         help_text="Abaixo disso o produto aparece no alerta de reposição.")
    inspecionar_recebimento = models.BooleanField(
        "inspecionar no recebimento", default=False,
        help_text="Entradas de compra vão para o armazém de quarentena até a Qualidade aprovar.")
    ativo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "produto / serviço"
        verbose_name_plural = "produtos e serviços"
        ordering = ["descricao"]

    def __str__(self):
        return f"{self.codigo} - {self.descricao}"

    def clean(self):
        from django.core.exceptions import ValidationError

        erros = {}
        for campo, tamanhos, nome in (("codigo_tributacao_nacional", (6,), "6 dígitos"),
                                      ("codigo_tributacao_municipal", (3,), "3 dígitos"),
                                      ("codigo_nbs", (9,), "9 dígitos")):
            valor = somente_digitos(getattr(self, campo))
            if valor and len(valor) not in tamanhos:
                erros[campo] = f"Informe {nome} (pode digitar com pontos)."
            setattr(self, campo, valor)
        if erros:
            raise ValidationError(erros)

    def save(self, *args, **kwargs):
        for campo in ("codigo_tributacao_nacional", "codigo_tributacao_municipal", "codigo_nbs"):
            setattr(self, campo, somente_digitos(getattr(self, campo)))
        super().save(*args, **kwargs)


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
    chave_acesso = models.CharField("chave de acesso", max_length=50, blank=True, db_index=True)
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

    SITUACOES = [("", "Não transmitida"), ("autorizada", "Autorizada"), ("rejeitada", "Rejeitada"),
                 ("cancelada", "Cancelada no fisco")]
    situacao_fiscal = models.CharField("situação no fisco", max_length=10, choices=SITUACOES, blank=True,
                                       editable=False)

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
    def serie_da_empresa(self):
        """Série configurada para a empresa da nota (cada empresa/filial tem numeração própria)."""
        config = ConfiguracaoFiscal.objects.filter(empresa=self.empresa).first() if self.empresa_id else None
        if config is None:
            return self.serie
        return config.serie_nfse if self.modelo == "nfse" else config.serie_nfe

    def emitir(self):
        """Numera e marca como emitida. A transmissão à SEFAZ/prefeitura é feita por integração externa."""
        if self.status != "rascunho":
            raise ValueError("Apenas notas em rascunho podem ser emitidas.")
        if not self.itens.exists():
            raise ValueError("A nota não possui itens.")
        if not self.numero and self.tipo == "saida":
            self.serie = self.serie_da_empresa()
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


# --------------------------------------------------------------------------- transmissão
class ConfiguracaoFiscal(ModeloBase):
    """Por empresa: ambiente, por onde cada tipo de nota é transmitido e o certificado A1 (cifrado)."""

    AMBIENTES = [("homologacao", "Homologação (testes, sem valor fiscal)"), ("producao", "Produção")]
    EMISSORES_NFE = [("simulacao", "Simulação interna (não envia a ninguém)"), ("sefaz", "SEFAZ do estado")]
    EMISSORES_NFSE = [("simulacao", "Simulação interna (não envia a ninguém)"),
                      ("nacional", "Emissor Nacional da NFS-e"), ("sao_paulo", "Prefeitura de São Paulo")]
    empresa = models.OneToOneField("core.Empresa", on_delete=models.CASCADE, related_name="config_fiscal")
    ambiente = models.CharField(max_length=12, choices=AMBIENTES, default="homologacao")
    emissor_nfe = models.CharField("NF-e/NFC-e (mercadoria) vai por", max_length=10, choices=EMISSORES_NFE,
                                   default="simulacao")
    emissor_nfse = models.CharField("NFS-e (serviço) vai por", max_length=10, choices=EMISSORES_NFSE,
                                    default="simulacao")
    serie_nfe = models.CharField("série da NF-e", max_length=5, default="1")
    serie_nfse = models.CharField("série da NFS-e", max_length=5, default="1")
    usar_certificado_matriz = models.BooleanField(
        "usar o certificado da matriz", default=False,
        help_text="Para filiais. A SEFAZ aceita o certificado da matriz (mesma raiz de CNPJ); o Emissor Nacional "
                  "da NFS-e exige o certificado do próprio CNPJ.")
    certificado = models.BinaryField(null=True, blank=True, editable=False)
    senha = models.BinaryField(null=True, blank=True, editable=False)
    certificado_titular = models.CharField("titular do certificado", max_length=200, blank=True, editable=False)
    certificado_validade = models.DateTimeField("certificado válido até", null=True, blank=True, editable=False)

    class Meta:
        verbose_name = "configuração fiscal"
        verbose_name_plural = "configuração fiscal (transmissão)"

    def __str__(self):
        return f"Fiscal de {self.empresa}"

    def _dono_do_certificado(self):
        """Esta configuração, ou a da matriz quando a filial usa o certificado dela."""
        if self.usar_certificado_matriz and self.empresa.matriz_de_id:
            return ConfiguracaoFiscal.objects.filter(empresa_id=self.empresa.matriz_de_id).first()
        return self

    @property
    def status(self):
        dono = self._dono_do_certificado()
        if dono is None or not dono.certificado:
            return "Pendente: sem certificado"
        dias = (dono.certificado_validade - timezone.now()).days
        if dias < 0:
            return "Vencido"
        return f"Atenção: vence em {dias} dias" if dias <= 30 else "Vigente"

    def guardar_certificado(self, pfx: bytes, senha: str):
        """Valida o A1 (senha, validade, CNPJ igual ao da empresa) e guarda cifrado."""
        from django.core.exceptions import ValidationError

        from apps.core.validators import somente_digitos
        from apps.ged.assinatura import ErroAssinatura, carregar_pfx

        from . import cofre

        try:
            _, _, _, dados = carregar_pfx(pfx, senha)
        except ErroAssinatura as e:
            raise ValidationError(str(e)) from None
        cnpj = somente_digitos(self.empresa.cnpj)
        if not dados.cnpj:
            raise ValidationError("O certificado não é um e-CNPJ: a transmissão fiscal exige o certificado da empresa.")
        if dados.cnpj[:8] != cnpj[:8]:
            raise ValidationError(f"O certificado é de outro CNPJ ({dados.cnpj}), não o da empresa ({cnpj}).")
        self.certificado, self.senha = cofre.cifrar(pfx), cofre.cifrar(senha.encode())
        self.certificado_titular, self.certificado_validade = dados.titular[:200], dados.valido_ate
        self.save()
        return dados

    def abrir_certificado(self, com_cadeia=False):
        """(chave, certificado) em memória; com `com_cadeia`, (chave, certificado, cadeia). Revalida a validade
        a cada uso."""
        from django.core.exceptions import ValidationError

        from apps.ged.assinatura import ErroAssinatura, carregar_pfx

        from . import cofre

        dono = self._dono_do_certificado()
        if dono is None or not dono.certificado:
            raise ValidationError(f"Envie o certificado A1 de {self.empresa} (ou da matriz) em Fiscal → "
                                  "Configuração fiscal.")
        try:
            chave, cert, cadeia, _ = carregar_pfx(cofre.decifrar(dono.certificado), cofre.decifrar(dono.senha).decode())
        except ErroAssinatura as e:
            raise ValidationError(str(e)) from None
        if com_cadeia:
            return chave, cert, cadeia
        return chave, cert


class Transmissao(models.Model):
    """Cada tentativa de falar com o fisco fica registrada, com o que foi enviado e o que voltou."""

    OPERACOES = [("autorizacao", "Autorização"), ("cancelamento", "Cancelamento")]
    RESULTADOS = [("autorizado", "Autorizado"), ("rejeitado", "Rejeitado"), ("erro", "Erro de comunicação")]
    nota = models.ForeignKey(NotaFiscal, on_delete=models.PROTECT, related_name="transmissoes")
    operacao = models.CharField("operação", max_length=12, choices=OPERACOES)
    emissor = models.CharField(max_length=10)
    ambiente = models.CharField(max_length=12)
    resultado = models.CharField(max_length=10, choices=RESULTADOS)
    codigo = models.CharField("código", max_length=10, blank=True)
    mensagem = models.CharField(max_length=500, blank=True)
    protocolo = models.CharField(max_length=30, blank=True)
    xml_enviado = models.TextField(blank=True)
    xml_retorno = models.TextField(blank=True)
    data = models.DateTimeField(auto_now_add=True)
    usuario = models.ForeignKey("auth.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        verbose_name = "transmissão"
        verbose_name_plural = "transmissões"
        ordering = ["-data", "-id"]

    @property
    def status(self):
        return self.get_resultado_display()

