from apps.core.crud import Crud

from .models import Etapa, Instancia, Processo


class ProcessoCrud(Crud):
    model = Processo
    fields = ["nome", "codigo", "descricao", "departamento", "ativo"]
    list_display = ("nome", "codigo", "departamento", "ativo")
    search_fields = ("nome", "codigo")
    inline = (Etapa, ["ordem", "nome", "tipo", "grupo_responsavel", "usuario_responsavel", "prazo_dias",
                      "instrucoes"], 1)
    detail_template = "workflow/processo_detail.html"
    icon = "diagram-2"
    menu = "Workflow / BPM"
    menu_label = "Modelar processos"
    menu_order = 3


class InstanciaCrud(Crud):
    model = Instancia
    list_display = ("pk", "titulo", "processo", "etapa_atual", "solicitante", "status", "prazo", "criado_em")
    search_fields = ("titulo", "descricao")
    filter_fields = ("processo", "status")
    detail_template = "workflow/instancia_detail.html"
    icon = "kanban"
    menu = "Workflow / BPM"
    menu_label = "Todas as solicitações"
    menu_order = 2
    can_create = can_edit = False

    @classmethod
    def get_queryset(cls, request):
        return Instancia.objects.visiveis_para(request.user).select_related(
            "processo", "etapa_atual", "solicitante")
