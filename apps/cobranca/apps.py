from django.apps import AppConfig


class CobrancaConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.cobranca"
    verbose_name = "Cobrança"

    def ready(self):
        from . import signals  # noqa: F401
