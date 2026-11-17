from apps.core.crud import Crud, FormBase

from .models import CaracteristicaInspecao, Inspecao, NaoConformidade, PlanoInspecao, ResultadoInspecao


class InspecaoCrud(Crud):
    model = Inspecao
    fields = ["produto", "quantidade", "armazem", "fornecedor", "resultado"]
    inline = (ResultadoInspecao, ["caracteristica", "valor", "atendeu", "observacao"], 0)
    inline_no_detalhe = False
    related = (("resultados", "Plano de inspeção — resultados", ["caracteristica", "valor", "atendeu", "observacao"]),)
    list_display = ("numero", "produto", "quantidade", "fornecedor", "origem", "status", "criado_em")
    search_fields = ("numero", "produto__descricao", "fornecedor__nome")
    filter_fields = ("status", "origem")
    detail_fields = ["numero", "produto", "quantidade", "quantidade_aprovada", "armazem", "origem", "recebimento",
                     "fornecedor", "status", "inspetor", "data_inspecao", "resultado", "nao_conformidade"]
    actions = (("qualidade:concluir_inspecao", "Registrar resultado", "clipboard-check",
                "qualidade.change_inspecao"),)
    can_delete = False
    icon = "search"
    menu = "Qualidade"
    menu_order = 1


class NaoConformidadeForm(FormBase):
    class Meta:
        model = NaoConformidade
        fields = ["titulo", "origem", "gravidade", "produto", "fornecedor", "cliente", "descricao", "responsavel",
                  "prazo", "acao_imediata", "causa_raiz", "acao_corretiva", "verificacao_eficacia", "status"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["responsavel"].label_from_instance = lambda u: u.get_full_name() or u.username
        self.fields["prazo"].help_text = "Em branco: calculado pela gravidade (crítica 2 dias ... baixa 30)."


class NaoConformidadeCrud(Crud):
    model = NaoConformidade
    form_class = NaoConformidadeForm
    list_display = ("numero", "titulo", "origem", "gravidade", "responsavel", "prazo", "status")
    search_fields = ("numero", "titulo", "descricao", "fornecedor__nome")
    filter_fields = ("status", "gravidade", "origem", "fornecedor")
    icon = "exclamation-octagon"
    menu = "Qualidade"
    menu_order = 2


class PlanoInspecaoCrud(Crud):
    model = PlanoInspecao
    fields = ["produto", "observacoes", "ativo"]
    inline = (CaracteristicaInspecao, ["descricao", "tipo", "minimo", "maximo", "unidade"], 3)
    list_display = ("produto", "ativo")
    search_fields = ("produto__codigo", "produto__descricao")
    icon = "rulers"
    menu = "Qualidade"
    menu_label = "Planos de inspeção"
    menu_order = 3
    can_delete = True
