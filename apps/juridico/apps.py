from django.apps import AppConfig


class JuridicoConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.juridico"
    verbose_name = "Jurídico"

    def ready(self):
        from . import signals  # noqa: F401
