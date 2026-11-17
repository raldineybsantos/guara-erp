from apps.core.crud import Crud, FormBase

from .models import ApontamentoHoras, Projeto, TarefaProjeto


class ProjetoForm(FormBase):
    class Meta:
        model = Projeto
        fields = ["nome", "cliente", "gerente", "departamento", "centro_custo", "inicio", "fim_previsto", "orcamento",
                  "status", "descricao"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["gerente"].label_from_instance = lambda u: u.get_full_name() or u.username


class ProjetoCrud(Crud):
    model = Projeto
    form_class = ProjetoForm
    inline = (TarefaProjeto, ["eap", "nome", "responsavel", "inicio", "fim", "horas_previstas", "percentual"], 2)
    inline_no_detalhe = False
    list_display = ("numero", "nome", "cliente", "gerente", "fim_previsto", "percentual_concluido", "status")
    search_fields = ("numero", "nome", "cliente__nome")
    filter_fields = ("status", "gerente", "cliente")
    detail_fields = ["numero", "nome", "cliente", "gerente", "status", "inicio", "fim_previsto", "orcamento",
                     "custo_mao_obra", "custo_despesas", "custo_realizado", "saldo_orcamento", "compras_comprometidas",
                     "vendas_faturadas", "vendas_em_carteira", "resultado", "horas_previstas",
                     "horas_realizadas", "percentual_concluido", "centro_custo", "departamento", "descricao"]
    related = (("tarefas", "EAP", ["eap", "nome", "responsavel", "inicio", "fim", "horas_previstas",
                                   "horas_realizadas", "percentual"]),
               ("pedidos_compra", "Compras do projeto", ["numero", "fornecedor", "valor_total", "status"]),
               ("pedidos_venda", "Vendas do projeto", ["numero", "cliente", "valor_total", "status"]),
               ("apontamentos", "Horas apontadas", ["data", "usuario", "tarefa", "horas", "custo", "descricao"]))
    icon = "kanban"
    menu = "Projetos"
    menu_order = 1


class ApontamentoForm(FormBase):
    class Meta:
        model = ApontamentoHoras
        fields = ["projeto", "tarefa", "data", "horas", "descricao"]


class ApontamentoHorasCrud(Crud):
    model = ApontamentoHoras
    form_class = ApontamentoForm
    list_display = ("data", "usuario", "projeto", "tarefa", "horas", "descricao")
    search_fields = ("descricao", "projeto__nome", "projeto__numero")
    filter_fields = ("projeto",)
    icon = "stopwatch"
    menu = "Projetos"
    menu_label = "Minhas horas"
    menu_order = 2

    @classmethod
    def get_queryset(cls, request):
        qs = super().get_queryset(request)
        # Cada um vê e corrige só as próprias horas; o gerente de projeto vê todas.
        return qs if request.user.has_perm("projetos.change_projeto") else qs.filter(usuario=request.user)
