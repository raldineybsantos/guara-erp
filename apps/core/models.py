from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from .middleware import empresa_atual, usuario_atual
from .validators import formatar_documento, somente_digitos, validar_cnpj, validar_cpf, validar_cpf_cnpj

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
    matriz_de = models.ForeignKey("self", verbose_name="filial de", null=True, blank=True, on_delete=models.PROTECT,
                                  related_name="filiais", limit_choices_to={"matriz_de__isnull": True},
                                  help_text="Preencha nas filiais. Em branco = empresa independente ou matriz.")
    codigo_municipio = models.CharField("código do município (IBGE)", max_length=7, blank=True,
                                        help_text="7 dígitos. Exigido nas notas fiscais (ex.: São Paulo = 3550308).")
    cnae = models.CharField("CNAE principal", max_length=9, blank=True)
    usuarios = models.ManyToManyField(settings.AUTH_USER_MODEL, verbose_name="usuários com acesso", blank=True,
                                      related_name="empresas_permitidas",
                                      help_text="Quem pode trabalhar nesta empresa. Em branco = todos os usuários.")
    ativa = models.BooleanField(default=True)

    class Meta:
        verbose_name = "empresa"
        verbose_name_plural = "empresas"
        ordering = ["razao_social"]

    def __str__(self):
        return self.nome_fantasia or self.razao_social

    def save(self, *args, **kwargs):
        if self.matriz_de_id:
            self.matriz = False
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if self.matriz_de_id and self.matriz_de_id == self.pk:
            raise ValidationError({"matriz_de": "Uma empresa não pode ser filial dela mesma."})
        if self.codigo_municipio and (not self.codigo_municipio.isdigit() or len(self.codigo_municipio) != 7):
            raise ValidationError({"codigo_municipio": "O código IBGE do município tem 7 dígitos."})

    @classmethod
    def padrao(cls):
        return cls.objects.filter(ativa=True).order_by("-matriz", "pk").first()

    @classmethod
    def acessiveis(cls, user):
        """Empresas em que o usuário pode trabalhar: as sem restrição e as que o listam."""
        qs = cls.objects.filter(ativa=True)
        if user is None or not getattr(user, "is_authenticated", False):
            return qs.none()
        if user.is_superuser:
            return qs
        return qs.filter(models.Q(usuarios__isnull=True) | models.Q(usuarios=user)).distinct()

    def acessivel_para(self, user):
        return Empresa.acessiveis(user).filter(pk=self.pk).exists()


class PorEmpresaMixin(models.Model):
    """Registros vinculados a uma empresa; usa a empresa padrão quando vazio."""

    empresa = models.ForeignKey(Empresa, on_delete=models.PROTECT, null=True, blank=True)

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self.empresa_id:
            self.empresa = empresa_atual() or Empresa.padrao()
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
class ParceiroNegocio(ModeloBase, EnderecoMixin):
    """Cadastro único de pessoas e empresas com quem a empresa negocia (como o Business Partner do SAP).

    Um mesmo CPF/CNPJ existe uma vez só; ser cliente ou fornecedor são papéis desse cadastro
    (tabelas filhas ``Cliente`` e ``Fornecedor``), que podem coexistir.
    """

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
        verbose_name = "parceiro de negócios"
        verbose_name_plural = "parceiros de negócios"
        ordering = ["nome"]
        constraints = [
            models.UniqueConstraint(
                fields=["documento"], condition=~models.Q(documento=""), name="parceiro_documento_unico",
                violation_error_message=(
                    "Este CPF/CNPJ já está cadastrado como parceiro de negócios. Abra o cadastro existente "
                    "e use “Tornar cliente” ou “Tornar fornecedor” para adicionar o papel."
                ),
            ),
        ]

    def __str__(self):
        return self.nome_fantasia or self.nome

    def clean(self):
        super().clean()
        # Só dígitos, para a checagem de duplicidade valer com ou sem pontuação.
        self.documento = somente_digitos(self.documento)

    def save(self, *args, **kwargs):
        self.documento = somente_digitos(self.documento)
        super().save(*args, **kwargs)

    @property
    def documento_formatado(self):
        return formatar_documento(self.documento)

    @property
    def parceiro(self):
        """O cadastro-mãe (útil em Cliente/Fornecedor, onde ``self`` é o papel)."""
        return ParceiroNegocio.objects.get(pk=self.parceironegocio_id) if hasattr(self, "parceironegocio_id") \
            else self

    def papeis(self):
        pn = self.parceiro
        return [rotulo for rotulo, modelo in (("Cliente", Cliente), ("Fornecedor", Fornecedor))
                if modelo.objects.filter(parceironegocio_id=pn.pk).exists()]

    def papeis_texto(self):
        return ", ".join(self.papeis()) or "—"
    papeis_texto.short_description = "papéis"

    def adicionar_papel(self, papel, **campos):
        """Torna o parceiro cliente ou fornecedor, sem duplicar o cadastro. Devolve o papel."""
        modelo = {"cliente": Cliente, "fornecedor": Fornecedor}[papel]
        pn = self.parceiro
        existente = modelo.objects.filter(parceironegocio_id=pn.pk).first()
        if existente:
            return existente
        obj = modelo(parceironegocio=pn, **campos)
        for f in ParceiroNegocio._meta.concrete_fields:
            setattr(obj, f.attname, getattr(pn, f.attname))
        obj.save()
        return obj


class PapelParceiro(models.Model):
    """Comportamento comum dos papéis Cliente e Fornecedor."""

    class Meta:
        abstract = True

    def delete(self, *args, **kwargs):
        # Excluir só o papel: o cadastro-mãe fica se ainda for usado pelo outro papel.
        outro = Fornecedor if isinstance(self, Cliente) else Cliente
        if outro.objects.filter(parceironegocio_id=self.parceironegocio_id).exists():
            kwargs["keep_parents"] = True
        return super().delete(*args, **kwargs)


class Fornecedor(PapelParceiro, ParceiroNegocio):
    # O código do papel mantém a coluna "id" histórica; o cadastro-mãe fica em parceironegocio.
    codigo = models.BigAutoField("código", primary_key=True, db_column="id")
    parceironegocio = models.OneToOneField(
        ParceiroNegocio, on_delete=models.CASCADE, parent_link=True, related_name="fornecedor",
        verbose_name="parceiro de negócios",
    )
    categoria = models.CharField(max_length=80, blank=True, help_text="Ex.: matéria-prima, serviços, TI")
    prazo_pagamento_dias = models.PositiveIntegerField("prazo de pagamento (dias)", default=30)
    banco = models.CharField(max_length=60, blank=True)
    agencia = models.CharField("agência", max_length=10, blank=True)
    conta = models.CharField(max_length=20, blank=True)
    chave_pix = models.CharField("chave PIX", max_length=100, blank=True)

    class Meta:
        verbose_name = "fornecedor"
        verbose_name_plural = "fornecedores"
        ordering = ["nome"]


class Cliente(PapelParceiro, ParceiroNegocio):
    codigo = models.BigAutoField("código", primary_key=True, db_column="id")
    parceironegocio = models.OneToOneField(
        ParceiroNegocio, on_delete=models.CASCADE, parent_link=True, related_name="cliente",
        verbose_name="parceiro de negócios",
    )
    limite_credito = models.DecimalField("limite de crédito", max_digits=14, decimal_places=2, default=0)
    email_cobranca = models.EmailField("e-mail de cobrança", blank=True)

    class Meta:
        verbose_name = "cliente"
        verbose_name_plural = "clientes"
        ordering = ["nome"]


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


# --------------------------------------------------------------------------- avisos
class Notificacao(models.Model):
    """Aviso para um usuário (sino da barra superior). Opcionalmente também vai por e-mail."""

    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notificacoes")
    texto = models.CharField(max_length=250)
    url = models.CharField(max_length=250, blank=True)
    lida = models.BooleanField(default=False, db_index=True)
    criada_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "aviso"
        verbose_name_plural = "avisos"
        ordering = ["-criada_em"]

    def __str__(self):
        return self.texto


def notificar(usuarios, texto, url="", email=False):
    """Cria o aviso para cada usuário (ignora vazios e repetidos) e, se pedido, manda e-mail."""
    from django.core.mail import send_mail

    vistos = set()
    for u in usuarios:
        if u is None or u.pk in vistos or not u.is_active:
            continue
        vistos.add(u.pk)
        Notificacao.objects.create(usuario=u, texto=texto[:250], url=url)
        if email and u.email:
            try:
                base = getattr(settings, "ERP_URL_BASE", "")
                send_mail(f"[Guará ERP] {texto[:120]}", f"{texto}\n\n{base}{url}", None, [u.email],
                          fail_silently=True)
            except Exception:  # aviso por e-mail nunca derruba a operação
                pass
    return len(vistos)
