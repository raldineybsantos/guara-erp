from django.core.exceptions import ValidationError

from apps.core.crud import Crud, FormBase

from .models import Equipamento, OrdemServico, PecaOS, PlanoManutencao


class EquipamentoCrud(Crud):
    model = Equipamento
    fields = ["codigo", "nome", "tipo", "fabricante", "modelo", "numero_serie", "departamento", "localizacao",
              "data_aquisicao", "critico", "ativo"]
    list_display = ("codigo", "nome", "tipo", "localizacao", "critico", "ativo")
    search_fields = ("codigo", "nome", "numero_serie", "localizacao")
    filter_fields = ("critico", "ativo", "departamento")
    detail_fields = ["codigo", "nome", "tipo", "fabricante", "modelo", "numero_serie", "departamento",
                     "localizacao", "data_aquisicao", "critico", "ativo", "horas_parado"]
    related = (("planos", "Planos de manutenção", ["descricao", "tipo", "periodicidade_dias", "proxima"]),
               ("ordens", "Ordens de serviço", ["numero", "tipo", "status", "abertura", "custo_pecas"]))
    icon = "tools"
    menu = "Manutenção"
    menu_order = 3


class PlanoManutencaoCrud(Crud):
    model = PlanoManutencao
    fields = ["equipamento", "descricao", "tipo", "periodicidade_dias", "proxima", "responsavel", "checklist",
              "ativo"]
    list_display = ("equipamento", "descricao", "tipo", "periodicidade_dias", "proxima", "ativo")
    search_fields = ("descricao", "equipamento__codigo", "equipamento__nome")
    filter_fields = ("tipo", "ativo")
    icon = "calendar-check"
    menu = "Manutenção"
    menu_label = "Planos preventivos"
    menu_order = 2


class OSForm(FormBase):
    class Meta:
        model = OrdemServico
        fields = ["equipamento", "tipo", "prioridade", "descricao", "responsavel", "data_programada"]

    def clean(self):
        d = super().clean()
        if self.instance.pk and self.instance.status in ("concluida", "cancelada"):
            raise ValidationError("Ordem finalizada não pode ser alterada.")
        return d


class OrdemServicoCrud(Crud):
    model = OrdemServico
    form_class = OSForm
    inline = (PecaOS, ["produto", "quantidade"], 1)
    inline_no_detalhe = False
    list_display = ("numero", "equipamento", "tipo", "prioridade", "responsavel", "abertura", "status")
    search_fields = ("numero", "equipamento__codigo", "equipamento__nome", "descricao")
    filter_fields = ("status", "tipo", "prioridade", "equipamento")
    detail_fields = ["numero", "equipamento", "tipo", "prioridade", "status", "descricao", "solicitante",
                     "responsavel", "data_programada", "abertura", "inicio", "conclusao", "servico_executado",
                     "horas_trabalhadas", "custo_mao_obra", "custo_pecas", "custo_total", "plano"]
    related = (("pecas", "Peças", ["produto", "quantidade", "custo_unitario"]),)
    actions = (
        ("manutencao:iniciar", "Iniciar", "play-circle", "manutencao.change_ordemservico"),
        ("manutencao:concluir", "Concluir", "check2-circle", "manutencao.change_ordemservico"),
        ("manutencao:cancelar", "Cancelar", "x-octagon", "manutencao.change_ordemservico"),
    )
    icon = "wrench-adjustable"
    menu = "Manutenção"
    menu_order = 1
