from django.conf import settings
from django.db import models
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from .middleware import usuario_atual
from .validators import formatar_documento, validar_cnpj, validar_cpf, validar_cpf_cnpj

UF_CHOICES = [(uf, uf) for uf in (
    "AC AL AM AP BA CE DF ES GO MA MG MS MT PA PB PE PI PR RJ RN RO RR RS SC SE SP TO".split()
)]


# --------------------------------------------------------------------------- base
class ModeloBase(models.Model):
    """Campos de controle comuns a todas as tabelas do ERP."""

    criado_em = models.DateTimeField("criado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("atualizado em", auto_now=True)
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="criado por", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+", editable=False,
    )

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self.pk and not self.criado_por_id:
            self.criado_por = usuario_atual()
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        meta = self._meta
        try:
            return reverse(f"{meta.app_label}:{meta.model_name}_detail", args=[self.pk])
        except NoReverseMatch:
            return "#"


class EnderecoMixin(models.Model):
    cep = models.CharField("CEP", max_length=9, blank=True)
    logradouro = models.CharField(max_length=200, blank=True)
    numero = models.CharField("número", max_length=20, blank=True)
    complemento = models.CharField(max_length=100, blank=True)
    bairro = models.CharField(max_length=100, blank=True)
    cidade = models.CharField(max_length=100, blank=True)
    uf = models.CharField("UF", max_length=2, choices=UF_CHOICES, blank=True)

    class Meta:
        abstract = True

    @property
    def endereco_completo(self):
        partes = [self.logradouro, self.numero, self.complemento, self.bairro]
        cidade = f"{self.cidade}/{self.uf}" if self.cidade else ""
        return ", ".join(p for p in partes + [cidade, self.cep] if p)


# --------------------------------------------------------------------------- empresa
class Empresa(ModeloBase, EnderecoMixin):
    REGIMES = [
        ("simples", "Simples Nacional"),
        ("presumido", "Lucro Presumido"),
        ("real", "Lucro Real"),
        ("mei", "MEI"),
    ]
    razao_social = models.CharField("razão social", max_length=200)
    nome_fantasia = models.CharField("nome fantasia", max_length=200, blank=True)
    cnpj = models.CharField("CNPJ", max_length=18, unique=True, validators=[validar_cnpj])
    inscricao_estadual = models.CharField("inscrição estadual", max_length=30, blank=True)
    inscricao_municipal = models.CharField("inscrição municipal", max_length=30, blank=True)
    regime_tributario = models.CharField("regime tributário", max_length=20, choices=REGIMES, default="simples")
    telefone = models.CharField(max_length=30, blank=True)
    email = models.EmailField("e-mail", blank=True)
    chave_pix = models.CharField("chave PIX", max_length=100, blank=True,
                                 help_text="Usada para gerar PIX copia-e-cola nas cobranças.")
    matriz = models.BooleanField(default=True)

    class Meta:
        verbose_name = "empresa"
        verbose_name_plural = "empresas"
        ordering = ["razao_social"]

    def __str__(self):
        return self.nome_fantasia or self.razao_social

    @classmethod
    def padrao(cls):
        return cls.objects.order_by("-matriz", "pk").first()


class PorEmpresaMixin(models.Model):
    """Registros vinculados a uma empresa; usa a empresa padrão quando vazio."""

    empresa = models.ForeignKey(Empresa, on_delete=models.PROTECT, null=True, blank=True)

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self.empresa_id:
            self.empresa = Empresa.padrao()
        super().save(*args, **kwargs)


# --------------------------------------------------------------------------- RH
class Departamento(ModeloBase, PorEmpresaMixin):
    codigo = models.CharField("código", max_length=20, blank=True)
    nome = models.CharField(max_length=120)
    superior = models.ForeignKey("self", verbose_name="departamento superior", null=True, blank=True,
                                 on_delete=models.SET_NULL, related_name="subdepartamentos")
    responsavel = models.ForeignKey("Colaborador", verbose_name="responsável", null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="departamentos_responsavel")
    ativo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "departamento"
        verbose_name_plural = "departamentos"
        ordering = ["nome"]

    def __str__(self):
        return f"{self.codigo} - {self.nome}" if self.codigo else self.nome


class Cargo(ModeloBase):
    nome = models.CharField(max_length=120, unique=True)
    cbo = models.CharField("CBO", max_length=10, blank=True)
    salario_base = models.DecimalField("salário base", max_digits=12, decimal_places=2, default=0)
    descricao = models.TextField("descrição", blank=True)

    class Meta:
        verbose_name = "cargo"
        verbose_name_plural = "cargos"
        ordering = ["nome"]

    def __str__(self):
        return self.nome


class Colaborador(ModeloBase, PorEmpresaMixin, EnderecoMixin):
    CONTRATOS = [("clt", "CLT"), ("pj", "PJ"), ("estagio", "Estágio"),
                 ("temporario", "Temporário"), ("socio", "Sócio")]
    STATUS = [("ativo", "Ativo"), ("ferias", "Férias"), ("afastado", "Afastado"), ("desligado", "Desligado")]

    matricula = models.CharField("matrícula", max_length=20, blank=True)
    nome = models.CharField(max_length=200)
    cpf = models.CharField("CPF", max_length=14, unique=True, validators=[validar_cpf])
    rg = models.CharField("RG", max_length=20, blank=True)
    data_nascimento = models.DateField("data de nascimento", null=True, blank=True)
    email = models.EmailField("e-mail", blank=True)
    telefone = models.CharField(max_length=30, blank=True)
    departamento = models.ForeignKey(Departamento, on_delete=models.PROTECT, null=True, blank=True,
                                     related_name="colaboradores")
    cargo = models.ForeignKey(Cargo, on_delete=models.PROTECT, null=True, blank=True, related_name="colaboradores")
    gestor = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="liderados")
    tipo_contrato = models.CharField("tipo de contrato", max_length=20, choices=CONTRATOS, default="clt")
    data_admissao = models.DateField("data de admissão", default=timezone.localdate)
    data_desligamento = models.DateField("data de desligamento", null=True, blank=True)
    salario = models.DecimalField("salário", max_digits=12, decimal_places=2, default=0)
    status = models.CharField(max_length=20, choices=STATUS, default="ativo")
    banco = models.CharField(max_length=60, blank=True)
    agencia = models.CharField("agência", max_length=10, blank=True)
    conta = models.CharField(max_length=20, blank=True)
    chave_pix = models.CharField("chave PIX", max_length=100, blank=True)
    usuario = models.OneToOneField(settings.AUTH_USER_MODEL, verbose_name="usuário do sistema", null=True,
                                   blank=True, on_delete=models.SET_NULL, related_name="colaborador")
    observacoes = models.TextField("observações", blank=True)

    class Meta:
        verbose_name = "colaborador"
        verbose_name_plural = "colaboradores"
        ordering = ["nome"]

    def __str__(self):
        return self.nome

    @property
    def cpf_formatado(self):
        return formatar_documento(self.cpf)


# --------------------------------------------------------------------------- parceiros
class Parceiro(ModeloBase, EnderecoMixin):
    TIPOS = [("PJ", "Pessoa Jurídica"), ("PF", "Pessoa Física")]
    tipo_pessoa = models.CharField("tipo de pessoa", max_length=2, choices=TIPOS, default="PJ")
    nome = models.CharField("nome / razão social", max_length=200)
    nome_fantasia = models.CharField("nome fantasia", max_length=200, blank=True)
    documento = models.CharField("CPF/CNPJ", max_length=18, validators=[validar_cpf_cnpj], blank=True)
    inscricao_estadual = models.CharField("inscrição estadual", max_length=30, blank=True)
    email = models.EmailField("e-mail", blank=True)
    telefone = models.CharField(max_length=30, blank=True)
    contato = models.CharField("pessoa de contato", max_length=120, blank=True)
    ativo = models.BooleanField(default=True)
    observacoes = models.TextField("observações", blank=True)

    class Meta:
        abstract = True
        ordering = ["nome"]

    def __str__(self):
        return self.nome_fantasia or self.nome

    @property
    def documento_formatado(self):
        return formatar_documento(self.documento)


class Fornecedor(Parceiro):
    categoria = models.CharField(max_length=80, blank=True, help_text="Ex.: matéria-prima, serviços, TI")
    prazo_pagamento_dias = models.PositiveIntegerField("prazo de pagamento (dias)", default=30)
    banco = models.CharField(max_length=60, blank=True)
    agencia = models.CharField("agência", max_length=10, blank=True)
    conta = models.CharField(max_length=20, blank=True)
    chave_pix = models.CharField("chave PIX", max_length=100, blank=True)

    class Meta(Parceiro.Meta):
        verbose_name = "fornecedor"
        verbose_name_plural = "fornecedores"


class Cliente(Parceiro):
    limite_credito = models.DecimalField("limite de crédito", max_digits=14, decimal_places=2, default=0)
    email_cobranca = models.EmailField("e-mail de cobrança", blank=True)

    class Meta(Parceiro.Meta):
        verbose_name = "cliente"
        verbose_name_plural = "clientes"


# --------------------------------------------------------------------------- auditoria
class LogAuditoria(models.Model):
    ACOES = [("criar", "Criou"), ("alterar", "Alterou"), ("excluir", "Excluiu"), ("acao", "Ação")]
    data = models.DateTimeField(auto_now_add=True, db_index=True)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    acao = models.CharField("ação", max_length=10, choices=ACOES)
    modelo = models.CharField(max_length=100, db_index=True)
    objeto_id = models.CharField("ID do objeto", max_length=50, blank=True)
    descricao = models.CharField("descrição", max_length=300)
    alteracoes = models.JSONField("alterações", default=dict, blank=True)
    ip = models.GenericIPAddressField("IP", null=True, blank=True)

    class Meta:
        verbose_name = "log de auditoria"
        verbose_name_plural = "logs de auditoria"
        ordering = ["-data"]

    def __str__(self):
        return f"{timezone.localtime(self.data):%d/%m/%Y %H:%M} {self.usuario} {self.get_acao_display()} {self.modelo}"

    @classmethod
    def registrar(cls, acao, obj, descricao=None, alteracoes=None, usuario=None):
        return cls.objects.create(
            usuario=usuario or usuario_atual(), acao=acao,
            modelo=f"{obj._meta.app_label}.{obj._meta.model_name}",
            objeto_id=str(obj.pk or ""), descricao=(descricao or str(obj))[:300],
            alteracoes=alteracoes or {},
        )
