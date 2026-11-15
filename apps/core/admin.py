"""Registra todos os modelos do ERP no Django Admin (edição direta dos dados pela web)."""
from django.apps import apps
from django.contrib import admin

APPS_ERP = ["core", "financeiro", "cobranca", "fiscal", "workflow", "ged", "tarefas", "juridico", "compras", "estoque", "vendas", "producao", "qualidade", "manutencao", "projetos", "crm", "sst", "rh", "servicedesk", "contabilidade", "bancario", "assistente", "relatorios"]


def admin_generico(model):
    campos = [f.name for f in model._meta.fields if f.name not in ("id",) and not f.many_to_many][:7]
    texto = [f.name for f in model._meta.fields if f.get_internal_type() in ("CharField", "EmailField")][:5]
    filtros = [f.name for f in model._meta.fields
               if f.choices or f.get_internal_type() == "BooleanField"][:5]
    return type(f"{model.__name__}Admin", (admin.ModelAdmin,), {
        "list_display": campos or ["__str__"], "search_fields": texto, "list_filter": filtros,
        "list_per_page": 50, "save_on_top": True,
    })


SOMENTE_LEITURA = {"core.logauditoria", "workflow.historico", "cobranca.historicocobranca", "ged.assinatura",
                   "juridico.evento", "contabilidade.lancamento", "contabilidade.partida", "estoque.movimento", "fiscal.transmissao", "fiscal.configuracaofiscal", "assistente.configuracaoia", "assistente.vinculocanal"}


class SomenteLeituraAdmin(admin.ModelAdmin):
    """Trilhas de auditoria não podem ser alteradas nem apagadas, nem por administradores."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


for label in APPS_ERP:
    for model in apps.get_app_config(label).get_models():
        if admin.site.is_registered(model):
            continue
        base = admin_generico(model)
        if model._meta.label_lower in SOMENTE_LEITURA:
            base = type(base.__name__, (SomenteLeituraAdmin, base), {})
        admin.site.register(model, base)
