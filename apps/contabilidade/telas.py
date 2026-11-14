from apps.core.crud import Crud

from .models import ContaContabil, Lancamento, RegraContabil


class ContaContabilCrud(Crud):
    model = ContaContabil
    fields = ["codigo", "nome", "natureza", "superior", "sintetica", "ativo"]
    list_display = ("codigo", "nome", "natureza", "sintetica", "ativo")
    search_fields = ("codigo", "nome")
    filter_fields = ("natureza", "sintetica", "ativo")
    paginate_by = 100
    icon = "list-nested"
    menu = "Contabilidade"
    menu_order = 10


class RegraContabilCrud(Crud):
    model = RegraContabil
    fields = ["evento", "conta_debito", "conta_credito", "ativo"]
    list_display = ("evento", "conta_debito", "conta_credito", "ativo")
    can_delete = False
    icon = "signpost-split"
    menu = "Contabilidade"
    menu_order = 11


class LancamentoCrud(Crud):
    model = Lancamento
    list_display = ("numero", "data", "historico", "valor", "origem", "situacao")
    search_fields = ("numero", "historico")
    filter_fields = ("evento",)
    detail_fields = ["numero", "data", "historico", "valor", "origem", "situacao", "referencia", "estorno_de",
                     "criado_por", "criado_em"]
    related = (("partidas", "Partidas", ["conta", "debito", "credito", "centro_custo"]),)
    actions = (("contabilidade:estornar", "Estornar", "arrow-counterclockwise", "contabilidade.add_lancamento"),)
    can_create = can_edit = can_delete = False   # diário imutável: novo pelo formulário próprio, correção por estorno
    icon = "journal-text"
    menu = "Contabilidade"
    menu_label = "Lançamentos (diário)"
    menu_order = 2
