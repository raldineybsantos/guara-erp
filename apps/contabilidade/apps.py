from django.apps import AppConfig


class ContabilidadeConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.contabilidade"
    verbose_name = "Contabilidade"

    def ready(self):
        from . import signals  # noqa: F401
