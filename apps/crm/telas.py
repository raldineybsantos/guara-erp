from django.core.exceptions import ValidationError

from apps.core.crud import Crud, FormBase

from .models import Campanha, MetaVenda, Oportunidade


class OportunidadeForm(FormBase):
    class Meta:
        model = Oportunidade
        fields = ["titulo", "cliente", "prospect_empresa", "prospect_documento", "contato", "email", "telefone",
                  "origem", "campanha", "estagio", "valor", "probabilidade", "previsao_fechamento", "responsavel", "descricao"]

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


class CampanhaCrud(Crud):
    model = Campanha
    list_display = ("nome", "canal", "inicio", "fim", "leads", "ganhas", "conversao", "custo_realizado", "status")
    search_fields = ("nome", "descricao")
    filter_fields = ("status", "canal")
    detail_fields = ["nome", "canal", "status", "inicio", "fim", "responsavel", "orcamento", "custo_realizado",
                     "leads", "ganhas", "conversao", "valor_ganho", "custo_por_lead", "retorno", "descricao"]
    related = (("oportunidades", "Oportunidades da campanha", ["numero", "titulo", "valor", "estagio"]),)
    icon = "megaphone"
    menu = "CRM"
    menu_order = 4
    can_delete = True


class MetaVendaForm(FormBase):
    class Meta:
        model = MetaVenda
        fields = ["empresa", "vendedor", "competencia", "valor_meta", "observacoes"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["vendedor"].label_from_instance = lambda u: u.get_full_name() or u.username

    def clean(self):
        d = super().clean()
        if d.get("vendedor") and d.get("competencia"):
            ja = MetaVenda.objects.filter(vendedor=d["vendedor"], competencia=d["competencia"].replace(day=1),
                                          empresa=d.get("empresa")).exclude(pk=self.instance.pk)
            if ja.exists():
                raise ValidationError("Já existe meta deste vendedor para o mês.")
        return d


class MetaVendaCrud(Crud):
    model = MetaVenda
    form_class = MetaVendaForm
    list_display = ("competencia", "vendedor", "valor_meta", "realizado", "percentual", "pipeline", "projecao")
    search_fields = ("vendedor__first_name", "vendedor__username")
    filter_fields = ("vendedor",)
    detail_fields = ["vendedor", "competencia", "valor_meta", "realizado", "percentual", "pipeline", "projecao",
                     "observacoes"]
    icon = "bullseye"
    menu = "CRM"
    menu_label = "Metas de venda"
    menu_order = 3
    can_delete = True
