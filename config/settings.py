# Guará ERP — gestão empresarial livre
# Copyright (C) 2026 Raldiney Santos e colaboradores
# SPDX-License-Identifier: GPL-3.0-or-later
# Este programa é software livre: redistribua/modifique sob os termos da GNU GPL v3 ou posterior.
# Distribuído SEM NENHUMA GARANTIA. Veja o arquivo LICENSE.
"""Configurações do Guará ERP.

Todas as configurações sensíveis vêm de variáveis de ambiente (arquivo .env),
para que o mesmo código rode em Linux, Windows ou Docker.
"""
import os
import sys
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name, default=False):
    return os.getenv(name, str(default)).lower() in ("1", "true", "yes", "sim", "on")


DEBUG = env_bool("DJANGO_DEBUG", False)
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "")
if not SECRET_KEY or SECRET_KEY.startswith("troque") or len(SECRET_KEY) < 32:
    if DEBUG or "test" in sys.argv or "collectstatic" in sys.argv:
        SECRET_KEY = SECRET_KEY or "chave-somente-para-desenvolvimento-nao-usar-em-producao"
    else:
        raise ImproperlyConfigured(
            "Defina DJANGO_SECRET_KEY no .env com pelo menos 32 caracteres aleatórios "
            "(ex.: python -c \"import secrets; print(secrets.token_urlsafe(50))\").")
ALLOWED_HOSTS = [h.strip() for h in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]
CSRF_TRUSTED_ORIGINS = [o.strip() for o in os.getenv("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "django_filters",
    "apps.core",
    "apps.financeiro",
    "apps.cobranca",
    "apps.fiscal",
    "apps.workflow",
    "apps.ged",
    "apps.tarefas",
    "apps.juridico",
    "apps.compras",
    "apps.estoque",
    "apps.relatorios",
    "apps.assistente",
    "apps.bancario",
    "apps.contabilidade",
    "apps.servicedesk",
    "apps.sst",
    "apps.rh",
    "apps.seguranca",
    "apps.identidade",
    "apps.crm",
    "apps.projetos",
    "apps.manutencao",
    "apps.qualidade",
    "apps.producao",
    "apps.vendas",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.seguranca.middleware.MFAMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.core.middleware.AuditoriaMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.menu",
                "apps.core.context_processors.avisos",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("POSTGRES_DB", "erp"),
        "USER": os.getenv("POSTGRES_USER", "erp"),
        "PASSWORD": os.getenv("POSTGRES_PASSWORD", "erp"),
        "HOST": os.getenv("POSTGRES_HOST", "localhost"),
        "PORT": os.getenv("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": 60,
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Sao_Paulo"
USE_I18N = True
USE_TZ = True
USE_THOUSAND_SEPARATOR = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}

MEDIA_URL = "media/"
MEDIA_ROOT = Path(os.getenv("MEDIA_ROOT", BASE_DIR / "media"))
FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024  # campos de formulário (arquivos têm limite próprio)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",    # contas locais (sempre disponíveis: acesso de emergência)
    "apps.identidade.ldap_auth.LDAPBackend",        # LDAP/AD, conforme configurado na tela Login corporativo
]
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"

# Link para o pgAdmin (administração do PostgreSQL pela web)
PGADMIN_URL = os.getenv("PGADMIN_URL", "http://localhost:5050")

# ---------------------------------------------------------------- segurança
# Sessão expira após 8h e ao fechar o navegador
SESSION_COOKIE_AGE = int(os.getenv("SESSAO_HORAS", "8")) * 3600
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"

# Bloqueio de login: N falhas em X minutos bloqueia o usuário (e o IP após 3×N)
LOGIN_MAX_TENTATIVAS = int(os.getenv("LOGIN_MAX_TENTATIVAS", "5"))
LOGIN_BLOQUEIO_MINUTOS = int(os.getenv("LOGIN_BLOQUEIO_MINUTOS", "15"))
# Verificação em duas etapas (TOTP). Superusuários e os grupos abaixo são obrigados; os demais podem ativar.
# (Desligada só na suíte de testes; os testes de 2FA a religam com override_settings.)
MFA_ATIVO = env_bool("MFA_ATIVO", True) and "test" not in sys.argv
MFA_GRUPOS_OBRIGATORIOS = [g.strip() for g in os.getenv("MFA_GRUPOS_OBRIGATORIOS", "Administradores,Financeiro").split(",") if g.strip()]
MFA_EMISSOR = os.getenv("MFA_EMISSOR", "Guará ERP")
# Backup automático (veja docs/INSTALACAO.md): cifrado, com retenção e cópia externa.
BACKUP_DIR = os.getenv("BACKUP_DIR", str(BASE_DIR / "backups"))
BACKUP_COPIA_DIR = os.getenv("BACKUP_COPIA_DIR", "")          # disco externo / NAS montado
BACKUP_COMANDO_COPIA = os.getenv("BACKUP_COMANDO_COPIA", "")  # ex.: rclone copy {arquivo} nuvem:guara
BACKUP_SENHA = os.getenv("BACKUP_SENHA", "")                  # frase longa; sem ela usa-se a chave do cofre
BACKUP_HORA = os.getenv("BACKUP_HORA", "02:00")
BACKUP_INCLUIR_MEDIA = env_bool("BACKUP_INCLUIR_MEDIA", True)
BACKUP_RETENCAO_DIARIOS = int(os.getenv("BACKUP_RETENCAO_DIARIOS", "7"))
BACKUP_RETENCAO_SEMANAIS = int(os.getenv("BACKUP_RETENCAO_SEMANAIS", "4"))
BACKUP_RETENCAO_MENSAIS = int(os.getenv("BACKUP_RETENCAO_MENSAIS", "12"))
# Antivírus (ClamAV via clamd). Falha fechada: se o ClamAV não responder, o upload é recusado.
ANTIVIRUS_ATIVO = env_bool("ANTIVIRUS_ATIVO", True) and "test" not in sys.argv
CLAMAV_HOST = os.getenv("CLAMAV_HOST", "localhost")
CLAMAV_PORT = int(os.getenv("CLAMAV_PORT", "3310"))
CLAMAV_SOCKET = os.getenv("CLAMAV_SOCKET", "")            # alternativa ao TCP (Linux), ex.: /var/run/clamav/clamd.ctl
ANTIVIRUS_TIMEOUT = int(os.getenv("ANTIVIRUS_TIMEOUT", "120"))
ANTIVIRUS_LIMITE_MB = int(os.getenv("ANTIVIRUS_LIMITE_MB", "100"))   # deve ser <= StreamMaxLength do clamd
# Gateway de pagamento (boleto/PIX com baixa automática). Produção travada até você liberar (como a NFS-e).
COBRANCA_GATEWAY_PRODUCAO = env_bool("COBRANCA_GATEWAY_PRODUCAO", False)
# Só ative se o ERP estiver atrás de um proxy reverso (nginx/Caddy/Traefik) que define o cabeçalho
CONFIAR_X_FORWARDED_FOR = env_bool("CONFIAR_X_FORWARDED_FOR", False)

# Assinatura digital: pasta com os certificados raiz/intermediários da ICP-Brasil (.crt/.pem)
# para validar a cadeia (baixe em https://www.gov.br/iti/pt-br/assuntos/repositorio) e carimbo do tempo opcional.
ICP_BRASIL_CAS_DIR = os.getenv("ICP_BRASIL_CAS_DIR", str(BASE_DIR / "certificados" / "icp-brasil"))
ASSINATURA_TSA_URL = os.getenv("ASSINATURA_TSA_URL", "")

# Auditoria e logs de erro. Erros já tratados (resolvidos/ignorados) são apagados depois de N dias;
# a trilha de auditoria nunca é apagada pelo sistema (exporte e arquive conforme a sua política).
LOG_ERRO_RETENCAO_DIAS = int(os.getenv("LOG_ERRO_RETENCAO_DIAS", "180"))
# Telas de dados pessoais cuja consulta (não só a alteração) fica na trilha.
AUDITORIA_LEITURA = ["core.colaborador"]

# Compras: tolerância de preço na conferência em três vias (%). As alçadas de aprovação
# (coordenador, gerente, diretor) ficam na tela Compras → Alçadas de aprovação.
COMPRAS_TOLERANCIA_PRECO_PCT = os.getenv("COMPRAS_TOLERANCIA_PRECO_PCT", "2")

# Estoque: 1 permite saldo negativo (não recomendado; o padrão bloqueia a saída sem saldo).
ESTOQUE_PERMITE_NEGATIVO = os.getenv("ESTOQUE_PERMITE_NEGATIVO", "0") == "1"
# Vendas: desconto máximo por item sem aprovação (%).
VENDAS_DESCONTO_MAXIMO_PCT = os.getenv("VENDAS_DESCONTO_MAXIMO_PCT", "10")
# Manutenção: dias de antecedência para abrir a OS preventiva.
MANUTENCAO_ANTECEDENCIA_DIAS = int(os.getenv("MANUTENCAO_ANTECEDENCIA_DIAS", "7"))
# Projetos: custo-hora = salário x fator de encargos / horas do mês.
PROJETOS_HORAS_MES = os.getenv("PROJETOS_HORAS_MES", "220")
PROJETOS_ENCARGOS_FATOR = os.getenv("PROJETOS_ENCARGOS_FATOR", "1.0")
# SST: antecedência do alerta de ASO e reciclagem de NR vencendo (dias).
SST_ALERTA_DIAS = int(os.getenv("SST_ALERTA_DIAS", "30"))
# Chave que cifra o certificado A1 guardado no banco. Sem ela, usa-se a SECRET_KEY (trocar qualquer uma
# das duas obriga a reenviar o certificado).
FISCAL_CHAVE_COFRE = os.getenv("FISCAL_CHAVE_COFRE", "")
# Emissor Nacional da NFS-e. Em produção só transmite com FISCAL_NFSE_NACIONAL_PRODUCAO=1: libere depois de emitir
# notas de teste em homologação (produção restrita). O XSD oficial da DPS (opcional) confere o XML antes do envio.
FISCAL_NFSE_NACIONAL_PRODUCAO = env_bool("FISCAL_NFSE_NACIONAL_PRODUCAO", False)
FISCAL_NFSE_NACIONAL_XSD = os.getenv("FISCAL_NFSE_NACIONAL_XSD", "")
FISCAL_NFSE_NACIONAL_TIMEOUT = int(os.getenv("FISCAL_NFSE_NACIONAL_TIMEOUT", "60"))
FISCAL_NFSE_NACIONAL_CA_BUNDLE = os.getenv("FISCAL_NFSE_NACIONAL_CA_BUNDLE", "")
# Conciliação bancária: diferença máxima de dias entre a data do banco e a do ERP para casar sozinho.
CONCILIACAO_TOLERANCIA_DIAS = int(os.getenv("CONCILIACAO_TOLERANCIA_DIAS", "3"))
# Service Desk: horário em que o SLA corre (seg a sex) ou "24x7"; dias para fechar sozinho o chamado
# resolvido que o usuário não confirmou.
SERVICEDESK_HORARIO = os.getenv("SERVICEDESK_HORARIO", "08:00-18:00")
SERVICEDESK_DIAS_FECHAMENTO = int(os.getenv("SERVICEDESK_DIAS_FECHAMENTO", "3"))

# Tamanho máximo de arquivos enviados (GED, XML)
ARQUIVO_MAX_MB = int(os.getenv("ARQUIVO_MAX_MB", "50"))

# HTTPS (ative quando houver certificado / proxy reverso com TLS)
if env_bool("DJANGO_HTTPS", False):
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = env_bool("DJANGO_SSL_REDIRECT", True)
    SECURE_HSTS_SECONDS = int(os.getenv("DJANGO_HSTS_SECONDS", "31536000"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = False
    if CONFIAR_X_FORWARDED_FOR:
        SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# E-mail (régua de cobrança). Sem EMAIL_HOST, as mensagens vão para o console/log.
if os.getenv("EMAIL_HOST"):
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = os.getenv("EMAIL_HOST")
    EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
    EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
    EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
    EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "erp@localhost")
# Endereço do ERP usado nos links dos e-mails (ex.: https://erp.empresa.com.br)
ERP_URL_BASE = os.getenv("ERP_URL_BASE", "").rstrip("/")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"},
                 # erros gravados no banco (Administração → Logs de erro)
                 "erros_banco": {"class": "apps.core.logs.LogErroHandler", "level": "ERROR"}},
    "root": {"handlers": ["console", "erros_banco"], "level": os.getenv("LOG_LEVEL", "INFO")},
    # a validação de cadeia sem as raízes ICP-Brasil gera avisos longos e esperados
    "loggers": {"pyhanko": {"level": "ERROR"}, "pyhanko_certvalidator": {"level": "CRITICAL"}},
}
