from django.apps import AppConfig


class IdentidadeConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.identidade"
    verbose_name = "Identidade e acesso (LDAP/SSO)"
