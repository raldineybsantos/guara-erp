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
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
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
# Só ative se o ERP estiver atrás de um proxy reverso (nginx/Caddy/Traefik) que define o cabeçalho
CONFIAR_X_FORWARDED_FOR = env_bool("CONFIAR_X_FORWARDED_FOR", False)

# Assinatura digital: pasta com os certificados raiz/intermediários da ICP-Brasil (.crt/.pem)
# para validar a cadeia (baixe em https://www.gov.br/iti/pt-br/assuntos/repositorio) e carimbo do tempo opcional.
ICP_BRASIL_CAS_DIR = os.getenv("ICP_BRASIL_CAS_DIR", str(BASE_DIR / "certificados" / "icp-brasil"))
ASSINATURA_TSA_URL = os.getenv("ASSINATURA_TSA_URL", "")

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

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": os.getenv("LOG_LEVEL", "INFO")},
    # a validação de cadeia sem as raízes ICP-Brasil gera avisos longos e esperados
    "loggers": {"pyhanko": {"level": "ERROR"}, "pyhanko_certvalidator": {"level": "CRITICAL"}},
}
