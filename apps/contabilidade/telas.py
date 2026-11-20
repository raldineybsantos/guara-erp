from apps.core.crud import Crud

from .models import (MESES, Bem, ContratoContabil, ContaContabil, Depreciacao, Lancamento, LinhaOrcamento, Orcamento,
                     RegraContabil)


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


class BemCrud(Crud):
    model = Bem
    fields = ["empresa", "codigo", "descricao", "categoria", "data_aquisicao", "valor_aquisicao", "valor_residual",
              "vida_util_meses", "inicio_depreciacao", "centro_custo", "fornecedor", "contrapartida", "localizacao",
              "observacoes"]
    list_display = ("codigo", "descricao", "categoria", "valor_aquisicao", "depreciacao_acumulada", "valor_contabil",
                    "status")
    search_fields = ("codigo", "descricao", "localizacao")
    filter_fields = ("categoria", "status", "centro_custo")
    detail_fields = ["codigo", "descricao", "categoria", "status", "data_aquisicao", "valor_aquisicao",
                     "valor_residual", "vida_util_meses", "inicio_depreciacao", "parcela_mensal",
                     "depreciacao_acumulada", "valor_contabil", "centro_custo", "fornecedor", "contrapartida",
                     "localizacao", "data_baixa", "valor_venda", "motivo_baixa", "observacoes", "empresa"]
    related = (("depreciacoes", "Depreciações lançadas", ["competencia", "valor"]),)
    actions = (("contabilidade:bem_baixar", "Baixar bem", "box-arrow-down", "contabilidade.change_bem"),)
    icon = "building-gear"
    menu = "Contabilidade"
    menu_label = "Ativo fixo"
    menu_order = 12


class OrcamentoCrud(Crud):
    model = Orcamento
    fields = ["empresa", "nome", "ano", "controle", "responsavel", "ativo", "observacoes"]
    inline = (LinhaOrcamento, ["tipo", "centro_custo", "plano_conta", "valor_anual", *MESES], 3)
    list_display = ("nome", "ano", "controle", "responsavel", "ativo")
    filter_fields = ("ano", "controle", "ativo")
    actions = (("contabilidade:orcado_realizado", "Orçado × realizado", "bar-chart-steps", "contabilidade.view_orcamento"),)
    icon = "calculator"
    menu = "Contabilidade"
    menu_label = "Orçamentos"
    menu_order = 13


class ContratoContabilCrud(Crud):
    model = ContratoContabil
    fields = ["empresa", "contrato", "natureza", "conta_resultado", "conta_balanco", "valor_mensal",
              "inicio_apropriacao", "fim_apropriacao", "centro_custo", "ativo"]
    list_display = ("contrato", "natureza", "valor_mensal", "inicio_apropriacao", "total_apropriado", "ativo")
    search_fields = ("contrato__numero", "contrato__titulo")
    filter_fields = ("natureza", "ativo")
    detail_fields = ["contrato", "natureza", "conta_resultado", "conta_balanco", "valor_mensal", "inicio_apropriacao",
                     "fim_apropriacao", "centro_custo", "total_apropriado", "ativo", "empresa"]
    related = (("apropriacoes", "Apropriações lançadas", ["competencia", "valor"]),)
    icon = "file-earmark-ruled"
    menu = "Contabilidade"
    menu_label = "Contratos (apropriação)"
    menu_order = 12
