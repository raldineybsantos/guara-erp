from apps.core.crud import Crud, FormBase

from .models import (AvaliacaoRisco, EntregaEPI, ExameOcupacional, Incidente, LicencaAmbiental, MembroCIPA,
                     ReuniaoCIPA, ResiduoGerado, TreinamentoSST)


class IncidenteForm(FormBase):
    class Meta:
        model = Incidente
        fields = ["data_hora", "tipo", "gravidade", "departamento", "local", "descricao", "envolvidos", "causas",
                  "acoes", "cat_emitida", "numero_cat", "status"]


class IncidenteCrud(Crud):
    model = Incidente
    form_class = IncidenteForm
    list_display = ("numero", "data_hora", "tipo", "gravidade", "local", "cat_emitida", "status")
    search_fields = ("numero", "local", "descricao")
    filter_fields = ("tipo", "gravidade", "status", "departamento")
    icon = "exclamation-triangle"
    menu = "SST / EHS"
    menu_order = 1

    @classmethod
    def get_queryset(cls, request):
        qs = super().get_queryset(request)
        # Qualquer pessoa registra; só o SESMT vê todos (dados de colaboradores acidentados).
        return qs if request.user.has_perm("sst.ver_todos_incidente") else qs.filter(criado_por=request.user)


class AvaliacaoRiscoCrud(Crud):
    model = AvaliacaoRisco
    fields = ["departamento", "atividade", "perigo", "tipo", "probabilidade", "severidade", "medidas_controle",
              "responsavel", "revisar_em"]
    list_display = ("departamento", "atividade", "perigo", "tipo", "nivel", "status", "revisar_em")
    search_fields = ("atividade", "perigo")
    filter_fields = ("tipo", "departamento")
    detail_fields = ["departamento", "atividade", "perigo", "tipo", "probabilidade", "severidade", "nivel",
                     "classificacao", "medidas_controle", "responsavel", "revisar_em"]
    icon = "shield-exclamation"
    menu = "SST / EHS"
    menu_label = "Inventário de riscos (PGR)"
    menu_order = 2


class EntregaEPICrud(Crud):
    model = EntregaEPI
    fields = ["colaborador", "descricao", "ca", "produto", "quantidade", "data_entrega", "validade",
              "recebido_confirmado"]
    list_display = ("data_entrega", "colaborador", "descricao", "ca", "quantidade", "validade", "recebido_confirmado")
    search_fields = ("colaborador__nome", "descricao", "ca")
    filter_fields = ("recebido_confirmado",)
    can_delete = False   # ficha de EPI é prova de entrega
    icon = "cone-striped"
    menu = "SST / EHS"
    menu_label = "Entregas de EPI"
    menu_order = 3


class ExameOcupacionalCrud(Crud):
    model = ExameOcupacional
    fields = ["colaborador", "tipo", "data", "resultado", "validade", "medico"]
    list_display = ("data", "colaborador", "tipo", "resultado", "validade")
    search_fields = ("colaborador__nome",)
    filter_fields = ("tipo", "resultado")
    icon = "clipboard2-pulse"
    menu = "SST / EHS"
    menu_label = "ASOs"
    menu_order = 4


class TreinamentoSSTCrud(Crud):
    model = TreinamentoSST
    fields = ["colaborador", "norma", "descricao", "data", "carga_horaria", "validade", "instrutor"]
    list_display = ("data", "colaborador", "norma", "descricao", "validade")
    search_fields = ("colaborador__nome", "norma", "descricao")
    icon = "mortarboard"
    menu = "SST / EHS"
    menu_label = "Treinamentos (NRs)"
    menu_order = 5


class MembroCIPACrud(Crud):
    model = MembroCIPA
    list_display = ("colaborador", "funcao", "representacao", "inicio", "fim")
    search_fields = ("colaborador__nome",)
    filter_fields = ("funcao", "representacao")
    icon = "people-fill"
    menu = "SST / EHS"
    menu_label = "CIPA — membros"
    menu_order = 6
    can_delete = True


class ReuniaoCIPACrud(Crud):
    model = ReuniaoCIPA
    list_display = ("data", "tipo", "pauta")
    search_fields = ("pauta", "ata")
    filter_fields = ("tipo",)
    detail_fields = ["data", "tipo", "pauta", "ata", "presentes"]
    icon = "journal-text"
    menu = "SST / EHS"
    menu_label = "CIPA — reuniões"
    menu_order = 7
    can_delete = True


class LicencaAmbientalCrud(Crud):
    model = LicencaAmbiental
    list_display = ("descricao", "orgao", "numero", "validade", "status")
    search_fields = ("descricao", "orgao", "numero")
    filter_fields = ("status",)
    icon = "tree"
    menu = "SST / EHS"
    menu_label = "Licenças ambientais"
    menu_order = 8
    can_delete = True


class ResiduoGeradoCrud(Crud):
    model = ResiduoGerado
    list_display = ("data", "residuo", "classe", "quantidade", "unidade", "destinadora", "mtr")
    search_fields = ("residuo", "destinadora", "mtr")
    filter_fields = ("classe", "departamento")
    icon = "recycle"
    menu = "SST / EHS"
    menu_label = "Resíduos"
    menu_order = 9
    can_delete = True
