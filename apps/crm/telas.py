from apps.core.crud import Crud, FormBase

from .models import Oportunidade


class OportunidadeForm(FormBase):
    class Meta:
        model = Oportunidade
        fields = ["titulo", "cliente", "prospect_empresa", "prospect_documento", "contato", "email", "telefone",
                  "origem", "estagio", "valor", "probabilidade", "previsao_fechamento", "responsavel", "descricao"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["responsavel"].label_from_instance = lambda u: u.get_full_name() or u.username
        self.fields["estagio"].choices = [c for c in self.fields["estagio"].choices
                                          if c[0] not in ("ganha", "perdida")]


class OportunidadeCrud(Crud):
    model = Oportunidade
    form_class = OportunidadeForm
    list_display = ("numero", "titulo", "conta", "valor", "probabilidade", "previsao_fechamento", "responsavel",
                    "status")
    search_fields = ("numero", "titulo", "prospect_empresa", "cliente__nome", "contato")
    filter_fields = ("estagio", "origem", "responsavel")
    detail_fields = ["numero", "titulo", "conta", "status", "valor", "probabilidade", "valor_ponderado",
                     "previsao_fechamento", "responsavel", "origem", "contato", "email", "telefone", "descricao",
                     "motivo_perda", "fechada_em", "pedido_venda"]
    related = (("interacoes", "Interações", ["data", "tipo", "resumo", "proximo_passo", "usuario"]),)
    actions = (
        ("crm:interagir", "Registrar interação", "chat-left-text", "crm.change_oportunidade"),
        ("crm:ganhar", "Ganhar (gera pedido)", "trophy", "crm.change_oportunidade"),
        ("crm:perder", "Perder", "x-circle", "crm.change_oportunidade"),
    )
    icon = "funnel"
    menu = "CRM"
    menu_order = 2
