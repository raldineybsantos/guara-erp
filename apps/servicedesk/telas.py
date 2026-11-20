from apps.core.crud import Crud, FormBase

from .models import Area, ArtigoConhecimento, Chamado, Mudanca, Servico


def _nome_usuario(form, *campos):
    for c in campos:
        if c in form.fields:
            form.fields[c].label_from_instance = lambda u: u.get_full_name() or u.username


class AreaForm(FormBase):
    class Meta:
        model = Area
        fields = ["nome", "grupo", "gestor", "icone", "ativo"]

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        _nome_usuario(self, "gestor")


class AreaCrud(Crud):
    model = Area
    form_class = AreaForm
    list_display = ("nome", "grupo", "gestor", "ativo")
    related = (("servicos", "Serviços", ["nome", "sla_solucao_horas", "aprovacao", "ativo"]),)
    icon = "diagram-3"
    menu = "Service Desk"
    menu_label = "Áreas de atendimento"
    menu_order = 20


class ServicoForm(FormBase):
    class Meta:
        model = Servico
        fields = ["area", "nome", "descricao", "palavras_chave", "instrucoes", "prioridade_padrao",
                  "sla_resposta_horas", "sla_solucao_horas", "aprovacao", "aprovador", "ativo"]

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        _nome_usuario(self, "aprovador")


class ServicoCrud(Crud):
    model = Servico
    form_class = ServicoForm
    list_display = ("area", "nome", "prioridade_padrao", "sla_resposta_horas", "sla_solucao_horas", "aprovacao",
                    "ativo")
    search_fields = ("nome", "descricao", "palavras_chave")
    filter_fields = ("area", "aprovacao", "ativo")
    icon = "list-check"
    menu = "Service Desk"
    menu_label = "Catálogo de serviços"
    menu_order = 21


class ChamadoForm(FormBase):
    class Meta:
        model = Chamado
        fields = ["servico", "titulo", "descricao", "prioridade"]

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        sid = self.initial.get("servico") or self.data.get("servico")
        try:
            s = Servico.objects.get(pk=sid) if sid else None
        except (Servico.DoesNotExist, ValueError):
            s = None
        if s and s.instrucoes:
            self.fields["descricao"].help_text = s.instrucoes


class ChamadoCrud(Crud):
    model = Chamado
    form_class = ChamadoForm
    list_display = ("numero", "titulo", "area", "solicitante", "prioridade", "analista", "prazo_solucao",
                    "status_sla", "status")
    search_fields = ("numero", "titulo", "descricao")
    filter_fields = ("status", "area", "prioridade", "servico")
    detail_template = "servicedesk/chamado_detail.html"
    can_edit = False      # o chamado anda pelas ações (assumir, responder, resolver...)
    can_delete = False
    icon = "headset"
    menu = "Service Desk"
    menu_label = "Chamados"
    menu_order = 3

    @classmethod
    def get_queryset(cls, request):
        return super().get_queryset(request).filter(pk__in=Chamado.objects.visiveis_para(request.user).values("pk"))

    @classmethod
    def after_save(cls, request, obj, created):
        if created:   # aberto pelo catálogo: a IA também tenta o primeiro atendimento
            from . import ia_triagem

            ia_triagem.primeiro_atendimento(obj)

    @classmethod
    def extra_detalhe(cls, request, obj):
        equipe = obj.area.atende(request.user)
        sugestoes = ArtigoConhecimento.buscar(f"{obj.titulo} {obj.descricao}", request.user) \
            if obj.status in Chamado.ATIVOS else []
        return {"equipe": equipe, "sugestoes": sugestoes}


class ArtigoForm(FormBase):
    class Meta:
        model = ArtigoConhecimento
        fields = ["titulo", "area", "servico", "sintoma", "solucao", "palavras_chave", "publico", "status"]


class ArtigoCrud(Crud):
    model = ArtigoConhecimento
    form_class = ArtigoForm
    list_display = ("titulo", "area", "publico", "visualizacoes", "util_sim", "util_nao", "status")
    search_fields = ("titulo", "sintoma", "solucao", "palavras_chave")
    filter_fields = ("area", "publico", "status")
    detail_template = "servicedesk/artigo_detail.html"
    icon = "book"
    menu = "Service Desk"
    menu_label = "Biblioteca de soluções"
    menu_order = 5

    @classmethod
    def get_queryset(cls, request):
        u = request.user
        qs = super().get_queryset(request)
        return qs if u.has_perm("servicedesk.change_artigoconhecimento") else qs.filter(
            pk__in=ArtigoConhecimento.visiveis_para(u).values("pk"))


class MudancaForm(FormBase):
    class Meta:
        model = Mudanca
        fields = ["titulo", "area", "tipo", "risco", "descricao", "justificativa", "impacto", "plano_implantacao",
                  "plano_testes", "plano_retorno", "janela_inicio", "janela_fim", "responsavel", "chamados"]

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        _nome_usuario(self, "responsavel")
        self.fields["chamados"].queryset = Chamado.objects.exclude(status__in=("fechado", "cancelado", "recusado"))
        self.fields["chamados"].required = False

    def clean(self):
        d = super().clean()
        from django.core.exceptions import ValidationError

        if self.instance.pk and self.instance.status not in ("rascunho", "recusada"):
            raise ValidationError("Mudança em aprovação ou aprovada não pode ser alterada.")
        return d


class MudancaCrud(Crud):
    model = Mudanca
    form_class = MudancaForm
    list_display = ("numero", "titulo", "area", "tipo", "risco", "janela_inicio", "responsavel", "status")
    search_fields = ("numero", "titulo", "descricao")
    filter_fields = ("status", "tipo", "risco", "area")
    detail_template = "servicedesk/mudanca_detail.html"
    acao_status = {
        "servicedesk:mudanca_enviar": ('rascunho', 'recusada'),
        "servicedesk:mudanca_executar": ('aprovada',),
        "servicedesk:mudanca_concluir": ('execucao',),
    }
    actions = (
        ("servicedesk:mudanca_enviar", "Enviar para aprovação", "send-check", "servicedesk.change_mudanca"),
        ("servicedesk:mudanca_executar", "Iniciar execução", "play-circle", "servicedesk.change_mudanca"),
        ("servicedesk:mudanca_concluir", "Registrar resultado", "flag", "servicedesk.change_mudanca"),
    )
    icon = "arrow-repeat"
    menu = "Service Desk"
    menu_label = "Mudanças (RDM)"
    menu_order = 6
