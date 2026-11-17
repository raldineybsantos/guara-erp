# Guará ERP — gestão empresarial livre
# Copyright (C) 2026 Raldiney Santos e colaboradores
# SPDX-License-Identifier: GPL-3.0-or-later
from django.conf import settings
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.text import slugify


def _cifrar(texto):
    from apps.fiscal import cofre

    return cofre.cifrar(texto.encode())


def _decifrar(dados):
    from apps.fiscal import cofre

    return cofre.decifrar(dados).decode()


class ConfiguracaoLDAP(models.Model):
    """Diretório LDAP / Active Directory. O usuário digita login e senha no ERP; o ERP confere a senha no diretório
    (a senha não é guardada). Só conexões com TLS e certificado validado."""

    SEGURANCA = [("ldaps", "LDAPS (TLS direto, porta 636)"), ("starttls", "StartTLS (porta 389)")]

    nome = models.CharField(max_length=80, unique=True)
    ativo = models.BooleanField(default=True)
    servidor = models.CharField("servidor", max_length=200, help_text="Nome DNS do servidor, ex.: ad.empresa.com.br "
                                "(precisa constar no certificado).")
    porta = models.PositiveIntegerField(default=636)
    seguranca = models.CharField("segurança", max_length=8, choices=SEGURANCA, default="ldaps")
    ca_pem = models.TextField("certificado da CA (PEM)", blank=True,
                              help_text="Só se o servidor usa uma CA interna da empresa. Cole o certificado raiz.")
    bind_dn = models.CharField("conta de serviço (DN)", max_length=300, blank=True,
                               help_text="Conta só de leitura que procura o usuário. Ex.: CN=svc-erp,OU=Serviço,DC=empresa,DC=local")
    bind_senha = models.BinaryField(editable=False, default=b"")
    base_dn = models.CharField("base de busca (DN)", max_length=300, help_text="Ex.: OU=Usuários,DC=empresa,DC=local")
    filtro_usuario = models.CharField("filtro do usuário", max_length=300,
                                      default="(&(objectClass=user)(sAMAccountName={usuario}))",
                                      help_text="{usuario} é substituído pelo login digitado (já protegido). OpenLDAP: (&(objectClass=inetOrgPerson)(uid={usuario}))")
    atributo_login = models.CharField("atributo do login", max_length=50, default="sAMAccountName")
    atributo_email = models.CharField("atributo do e-mail", max_length=50, default="mail")
    atributo_nome = models.CharField("atributo do nome", max_length=50, default="displayName")
    atributo_grupos = models.CharField("atributo dos grupos", max_length=50, default="memberOf")
    grupo_obrigatorio = models.CharField("grupo exigido para entrar (DN)", max_length=300, blank=True,
                                         help_text="Se preenchido, só quem for membro deste grupo consegue entrar.")
    mapa_grupos = models.TextField("grupos do diretório → grupos do ERP", blank=True,
                                   help_text="Uma linha por regra: DN do grupo => nome do grupo do ERP. "
                                             "Ex.: CN=Financeiro,OU=Grupos,DC=empresa,DC=local => Financeiro")
    criar_usuarios = models.BooleanField("criar usuários no primeiro acesso", default=True)
    vincular_existentes = models.BooleanField(
        "vincular a usuários locais de mesmo login", default=False,
        help_text="Desligado por segurança: um usuário local que já existe com o mesmo login NÃO entra pelo diretório.")
    timeout = models.PositiveIntegerField("tempo limite (s)", default=10)

    class Meta:
        verbose_name = "diretório LDAP/AD"
        verbose_name_plural = "diretórios LDAP/AD"
        ordering = ["nome"]

    def __str__(self):
        return self.nome

    def definir_senha(self, texto):
        self.bind_senha = _cifrar(texto) if texto else b""

    def obter_senha(self):
        return _decifrar(self.bind_senha) if self.bind_senha else ""

    @property
    def mapa(self):
        """Lista de (dn_minúsculo, nome_do_grupo_do_erp)."""
        regras = []
        for linha in self.mapa_grupos.splitlines():
            if "=>" in linha:
                dn, grupo = linha.rsplit("=>", 1)
                if dn.strip() and grupo.strip():
                    regras.append((dn.strip().lower(), grupo.strip()))
        return regras

    def clean(self):
        if "{usuario}" not in (self.filtro_usuario or ""):
            raise ValidationError({"filtro_usuario": "O filtro precisa conter {usuario}."})
        existentes = set(Group.objects.values_list("name", flat=True))
        for _, g in self.mapa:
            if g not in existentes:
                raise ValidationError({"mapa_grupos": f"O grupo do ERP '{g}' não existe."})


class ProvedorOIDC(models.Model):
    """Login único (SSO) por OpenID Connect: Microsoft Entra ID, Google ou qualquer provedor compatível
    (Keycloak, Okta, Authentik...). Fluxo de código com PKCE; o token é validado (assinatura, emissor, público, validade, nonce)."""

    TIPOS = [("microsoft", "Microsoft (Entra ID / Microsoft 365)"), ("google", "Google (Workspace / Gmail)"),
             ("generico", "Outro provedor OpenID Connect")]

    tipo = models.CharField(max_length=10, choices=TIPOS)
    nome = models.CharField("nome no botão", max_length=60)
    slug = models.SlugField(unique=True, editable=False)
    ativo = models.BooleanField(default=True)
    client_id = models.CharField("ID do aplicativo (client id)", max_length=200)
    client_secret = models.BinaryField(editable=False, default=b"")
    tenant = models.CharField("tenant (Microsoft)", max_length=200, blank=True,
                              help_text="ID do diretório (GUID) ou domínio, ex.: empresa.onmicrosoft.com. "
                                        "Não use common/organizations/consumers.")
    emissor = models.URLField("emissor (issuer)", blank=True,
                              help_text="Só para 'Outro provedor'. Ex.: https://sso.empresa.com.br/realms/erp")
    dominios_permitidos = models.CharField("domínios de e-mail permitidos", max_length=300, blank=True,
                                           help_text="Separados por vírgula. Ex.: empresa.com.br. No Google, vale também o domínio do Workspace (hd).")
    criar_usuarios = models.BooleanField("criar usuários no primeiro acesso", default=False)
    vincular_por_email = models.BooleanField(
        "vincular a usuário local pelo e-mail", default=False,
        help_text="Permite que um usuário já cadastrado (com o mesmo e-mail) passe a entrar por este provedor.")
    grupo_padrao = models.ForeignKey(Group, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
                                     verbose_name="grupo dos novos usuários",
                                     help_text="Sem grupo, o usuário novo entra sem acesso a nenhum módulo até um administrador liberar.")
    exigir_2fa_erp = models.BooleanField(
        "exigir também o 2FA do ERP", default=False,
        help_text="Por padrão confia-se no MFA do provedor. Marque para exigir o código do ERP também nos perfis obrigados.")

    class Meta:
        verbose_name = "provedor de login único (OIDC)"
        verbose_name_plural = "provedores de login único (OIDC)"
        ordering = ["nome"]

    def __str__(self):
        return self.nome

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.nome) or self.tipo
            slug, i = base, 2
            while ProvedorOIDC.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug, i = f"{base}-{i}", i + 1
            self.slug = slug
        super().save(*args, **kwargs)

    def definir_segredo(self, texto):
        self.client_secret = _cifrar(texto) if texto else b""

    def obter_segredo(self):
        return _decifrar(self.client_secret) if self.client_secret else ""

    @property
    def dominios(self):
        return {d.strip().lower().lstrip("@") for d in self.dominios_permitidos.split(",") if d.strip()}

    def clean(self):
        erros = {}
        if self.tipo == "microsoft":
            t = (self.tenant or "").strip().lower()
            if not t:
                erros["tenant"] = "Informe o tenant (ID do diretório ou domínio)."
            elif t in ("common", "organizations", "consumers"):
                erros["tenant"] = "Use o tenant específico da empresa: 'common' aceitaria contas de qualquer organização."
        if self.tipo == "generico" and not self.emissor.startswith("https://"):
            erros["emissor"] = "Informe o emissor com https://."
        if self.criar_usuarios and self.tipo != "microsoft" and not self.dominios:
            erros["dominios_permitidos"] = ("Para criar usuários automaticamente informe os domínios permitidos; "
                                            "sem isso qualquer conta do provedor poderia entrar.")
        if erros:
            raise ValidationError(erros)


class IdentidadeExterna(models.Model):
    """Liga um usuário do ERP a uma identidade de fora (LDAP ou provedor OIDC) pelo identificador estável."""

    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="identidades")
    origem = models.CharField(max_length=60, help_text="ldap:<id> ou oidc:<slug>")
    subject = models.CharField(max_length=300)
    email = models.CharField(max_length=254, blank=True)
    criado_em = models.DateTimeField(default=timezone.now)
    ultimo_login = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "identidade externa"
        verbose_name_plural = "identidades externas"
        constraints = [models.UniqueConstraint(fields=["origem", "subject"], name="identidade_unica")]

    def __str__(self):
        return f"{self.usuario} ← {self.origem}"


class ProvedorSAML(models.Model):
    """Login único por SAML 2.0 (Entra ID, Okta, ADFS, Keycloak, Google Workspace SAML...). O ERP é o *Service Provider*.
    Só aceita resposta com assinatura válida do certificado do provedor, para um pedido que o ERP fez (sem IdP-initiated)."""

    nome = models.CharField("nome no botão", max_length=60)
    slug = models.SlugField(unique=True, editable=False)
    ativo = models.BooleanField(default=True)
    idp_entity_id = models.CharField("identificador do provedor (Entity ID / Issuer)", max_length=300)
    idp_sso_url = models.URLField("endereço de login do provedor (SSO URL, binding Redirect)", max_length=500)
    idp_certificados = models.TextField(
        "certificado(s) de assinatura do provedor (PEM)",
        help_text="Cole o certificado público que o provedor usa para assinar. Pode colar dois (troca de certificado), um após o outro.")
    atributo_email = models.CharField("atributo do e-mail", max_length=200, blank=True,
                                      help_text="Em branco: tenta e-mail/mail/emailaddress e, por último, o NameID.")
    atributo_nome = models.CharField("atributo do nome", max_length=200, blank=True)
    dominios_permitidos = models.CharField("domínios de e-mail permitidos", max_length=300, blank=True)
    criar_usuarios = models.BooleanField("criar usuários no primeiro acesso", default=False)
    vincular_por_email = models.BooleanField("vincular a usuário local pelo e-mail", default=False)
    grupo_padrao = models.ForeignKey(Group, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
                                     verbose_name="grupo dos novos usuários")
    exigir_2fa_erp = models.BooleanField("exigir também o 2FA do ERP", default=False)

    class Meta:
        verbose_name = "provedor SAML 2.0"
        verbose_name_plural = "provedores SAML 2.0"
        ordering = ["nome"]

    def __str__(self):
        return self.nome

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.nome) or "saml"
            slug, i = base, 2
            while ProvedorSAML.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug, i = f"{base}-{i}", i + 1
            self.slug = slug
        super().save(*args, **kwargs)

    @property
    def dominios(self):
        return {d.strip().lower().lstrip("@") for d in self.dominios_permitidos.split(",") if d.strip()}

    @property
    def certificados(self):
        """Lista de PEMs (um por bloco BEGIN/END)."""
        import re

        return re.findall(r"-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----", self.idp_certificados or "", re.S)

    def clean(self):
        erros = {}
        if not (self.idp_sso_url or "").startswith("https://"):
            erros["idp_sso_url"] = "O endereço de login precisa começar com https://."
        if not self.certificados:
            erros["idp_certificados"] = "Cole o certificado PEM (-----BEGIN CERTIFICATE-----...)."
        else:
            from cryptography import x509

            for pem in self.certificados:
                try:
                    x509.load_pem_x509_certificate(pem.encode())
                except ValueError:
                    erros["idp_certificados"] = "Certificado inválido."
        if self.criar_usuarios and not self.dominios:
            erros["dominios_permitidos"] = "Para criar usuários automaticamente informe os domínios permitidos."
        if erros:
            raise ValidationError(erros)


class PedidoSAML(models.Model):
    """Pedido de login em andamento. Fica no banco (e não na sessão) porque o provedor devolve o usuário por POST de outro
    site, e o cookie de sessão (SameSite=Lax) não acompanha esse POST."""

    relay = models.CharField(max_length=64, unique=True)
    id_pedido = models.CharField(max_length=64)
    provedor = models.ForeignKey(ProvedorSAML, on_delete=models.CASCADE, related_name="+")
    proximo = models.CharField(max_length=300, default="/")
    vinculo = models.CharField("segredo do navegador", max_length=64)
    expira_em = models.DateTimeField(db_index=True)


class AssercaoSAML(models.Model):
    """IDs de asserções já aceitas: uma asserção só vale uma vez (impede reenvio de resposta capturada)."""

    id_assercao = models.CharField(max_length=200, unique=True)
    expira_em = models.DateTimeField(db_index=True)


class TokenSCIM(models.Model):
    """Credencial que o provedor de identidade (Entra ID, Okta...) usa para criar/atualizar/desativar usuários por SCIM 2.0.
    Só a impressão digital (SHA-256) fica guardada; o token aparece uma única vez, ao ser criado."""

    nome = models.CharField(max_length=80)
    impressao = models.CharField(max_length=64, unique=True, editable=False)
    ativo = models.BooleanField(default=True)
    grupo_padrao = models.ForeignKey(Group, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
                                     verbose_name="grupo dos usuários criados",
                                     help_text="Sem grupo, o usuário criado não tem acesso a nenhum módulo até um administrador liberar.")
    criado_em = models.DateTimeField(default=timezone.now, editable=False)
    ultimo_uso = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "token SCIM"
        verbose_name_plural = "tokens SCIM"
        ordering = ["nome"]

    def __str__(self):
        return self.nome

    @staticmethod
    def impressao_de(token: str) -> str:
        import hashlib

        return hashlib.sha256(token.encode()).hexdigest()

    @classmethod
    def novo(cls, nome, grupo=None):
        """Devolve (objeto, token_em_texto)."""
        import secrets

        token = "scim_" + secrets.token_urlsafe(36)
        return cls.objects.create(nome=nome, impressao=cls.impressao_de(token), grupo_padrao=grupo), token
