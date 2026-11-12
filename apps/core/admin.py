"""Registra todos os modelos do ERP no Django Admin (edição direta dos dados pela web)."""
from django.apps import apps
from django.contrib import admin

APPS_ERP = ["core", "financeiro", "cobranca", "fiscal", "workflow", "ged"]


def admin_generico(model):
    campos = [f.name for f in model._meta.fields if f.name not in ("id",) and not f.many_to_many][:7]
    texto = [f.name for f in model._meta.fields if f.get_internal_type() in ("CharField", "EmailField")][:5]
    filtros = [f.name for f in model._meta.fields
               if f.choices or f.get_internal_type() == "BooleanField"][:5]
    return type(f"{model.__name__}Admin", (admin.ModelAdmin,), {
        "list_display": campos or ["__str__"], "search_fields": texto, "list_filter": filtros,
        "list_per_page": 50, "save_on_top": True,
    })


for label in APPS_ERP:
    for model in apps.get_app_config(label).get_models():
        if not admin.site.is_registered(model):
            admin.site.register(model, admin_generico(model))
