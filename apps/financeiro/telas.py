from apps.core.crud import Crud

from .filtros import ContaPagarFilter, ContaReceberFilter
from .borderoimposto import BorderoImposto
from .lote import LiquidacaoLote
from .forms import ContaPagarForm, ContaReceberForm
from .models import (ApuracaoPDD, FaixaPDD, Compensacao, ItemRenegociacao, Renegociacao, CentroCusto, ContaBancaria, ContaPagar, ContaReceber, GuiaTributo, Movimentacao, PlanoConta,
                     RegraRetencao)

DETALHE_TITULO = ["descricao", "documento", "parcela", "total_parcelas", "plano_conta", "centro_custo",
                  "valor", "juros", "desconto", "valor_total", "valor_pago", "saldo", "data_emissao",
                  "competencia", "vencimento", "situacao", "data_pagamento", "forma_pagamento",
                  "conta_bancaria", "observacoes", "criado_por", "criado_em"]


class ContaBancariaCrud(Crud):
    model = ContaBancaria
    fields = ["empresa", "nome", "tipo", "banco", "codigo_banco", "agencia", "conta", "convenio",
              "convenio_cobranca", "carteira", "saldo_inicial", "data_saldo_inicial", "ativa"]
    list_display = ("nome", "tipo", "banco", "agencia", "conta", "saldo_atual", "ativa")
    search_fields = ("nome", "banco")
    filter_fields = ("tipo", "ativa")
    icon = "bank"
    menu = "Financeiro"
    menu_order = 10
    related = (("movimentacoes", "Últimas movimentações", ["data", "tipo", "descricao", "valor"]),)


class PlanoContaCrud(Crud):
    model = PlanoConta
    fields = ["codigo", "nome", "tipo", "superior", "conta_contabil", "ativo"]
    list_display = ("codigo", "nome", "tipo", "superior", "ativo")
    search_fields = ("codigo", "nome")
    filter_fields = ("tipo", "ativo")
    icon = "list-columns"
    menu = "Financeiro"
    menu_order = 11
    paginate_by = 100


class CentroCustoCrud(Crud):
    model = CentroCusto
    fields = ["codigo", "nome", "departamento", "ativo"]
    list_display = ("codigo", "nome", "departamento", "ativo")
    search_fields = ("codigo", "nome")
    icon = "bullseye"
    menu = "Financeiro"
    menu_order = 12


class ContaPagarCrud(Crud):
    model = ContaPagar
    form_class = ContaPagarForm
    list_display = ("vencimento", "fornecedor", "descricao", "documento", "valor_total", "saldo", "situacao", "aprovado")
    search_fields = ("descricao", "documento", "fornecedor__nome", "fornecedor__nome_fantasia")
    filterset_class = ContaPagarFilter
    detail_fields = ["fornecedor"] + DETALHE_TITULO[:-2] + ["codigo_barras", "aprovado", "criado_por", "criado_em"]
    related = (
        ("movimentacoes", "Pagamentos", ["data", "conta_bancaria", "valor"]),
        ("compensacoes", "Compensações", ["data", "conta_receber", "valor", "status"]),
        ("retencoes", "Tributos retidos na fonte", ["regra", "base", "aliquota", "valor", "retido"]),
        ("rateios", "Rateio por centro de custo", ["centro_custo", "percentual"]),
    )
    actions = (
        ("financeiro:titulo_baixar_pagar", "Pagar", "cash-coin", "financeiro.baixar_contapagar"),
        ("financeiro:compensar_pagar", "Compensar com contas a receber", "arrow-left-right", "financeiro.add_compensacao"),
        ("financeiro:titulo_retencoes", "Tributos retidos", "percent", "financeiro.change_contapagar"),
        ("financeiro:titulo_ratear_pagar", "Ratear por centro de custo", "diagram-3", "financeiro.change_contapagar"),
        ("financeiro:titulo_estornar_pagar", "Estornar pagamentos", "arrow-counterclockwise", "financeiro.baixar_contapagar"),
        ("financeiro:titulo_cancelar_pagar", "Cancelar título", "x-octagon", "financeiro.change_contapagar"),
        ("workflow:iniciar_aprovacao_pagar", "Enviar para aprovação", "diagram-2", "workflow.add_instancia"),
    )
    icon = "box-arrow-up-right"
    menu = "Financeiro"
    menu_order = 1
    detail_template = "financeiro/titulo_detail.html"


class ContaReceberCrud(Crud):
    model = ContaReceber
    form_class = ContaReceberForm
    list_display = ("vencimento", "cliente", "descricao", "documento", "valor_total", "saldo", "situacao")
    search_fields = ("descricao", "documento", "cliente__nome", "cliente__nome_fantasia")
    filterset_class = ContaReceberFilter
    detail_fields = ["cliente"] + DETALHE_TITULO
    related = (
        ("movimentacoes", "Recebimentos", ["data", "conta_bancaria", "valor"]),
        ("compensacoes", "Compensações", ["data", "conta_pagar", "valor", "status"]),
        ("cobrancas", "Cobranças", ["tipo", "vencimento", "valor", "status"]),
        ("rateios", "Rateio por centro de custo", ["centro_custo", "percentual"]),
    )
    actions = (
        ("financeiro:titulo_baixar_receber", "Receber", "cash-coin", "financeiro.baixar_contareceber"),
        ("financeiro:titulo_ratear_receber", "Ratear por centro de custo", "diagram-3", "financeiro.change_contareceber"),
        ("financeiro:compensar_receber", "Compensar com contas a pagar", "arrow-left-right", "financeiro.add_compensacao"),
        ("financeiro:renegociar", "Renegociar títulos do cliente", "arrow-repeat", "financeiro.add_renegociacao"),
        ("cobranca:gerar_cobranca", "Gerar cobrança", "receipt", "cobranca.add_cobranca"),
        ("financeiro:titulo_estornar_receber", "Estornar recebimentos", "arrow-counterclockwise", "financeiro.baixar_contareceber"),
        ("financeiro:titulo_cancelar_receber", "Cancelar título", "x-octagon", "financeiro.change_contareceber"),
    )
    icon = "box-arrow-in-down-left"
    menu = "Financeiro"
    menu_order = 2
    detail_template = "financeiro/titulo_detail.html"


class MovimentacaoCrud(Crud):
    model = Movimentacao
    fields = ["empresa", "conta_bancaria", "data", "tipo", "valor", "descricao", "plano_conta",
              "centro_custo", "conciliado", "observacoes"]
    list_display = ("data", "conta_bancaria", "tipo", "descricao", "plano_conta", "valor_sinal", "conciliado")
    search_fields = ("descricao",)
    filter_fields = {"conta_bancaria": ["exact"], "tipo": ["exact"], "data": ["gte", "lte"],
                     "plano_conta": ["exact"], "conciliado": ["exact"]}
    icon = "journal-text"
    menu = "Financeiro"
    menu_label = "Extrato / movimentações"
    menu_order = 3


class RegraRetencaoCrud(Crud):
    model = RegraRetencao
    fields = ["tributo", "nome", "aliquota", "codigo_receita", "dia_vencimento", "base_minima", "teto_mensal", "ativa"]
    list_display = ("tributo", "nome", "aliquota", "codigo_receita", "dia_vencimento", "ativa")
    search_fields = ("nome", "codigo_receita")
    filter_fields = ("tributo", "ativa")
    icon = "percent"
    menu = "Financeiro"
    menu_label = "Regras de retenção"
    menu_order = 13


class GuiaTributoCrud(Crud):
    model = GuiaTributo
    list_display = ("vencimento", "tributo", "codigo_receita", "competencia", "valor", "status")
    filter_fields = ("tributo", "status")
    detail_fields = ["tributo", "codigo_receita", "competencia", "vencimento", "valor", "multa", "juros", "status",
                     "data_pagamento", "conta_bancaria", "empresa"]
    related = (("itens", "Retenções que compõem a guia", ["retencao", "movimentacao", "valor"]),)
    actions = (
        ("financeiro:guia_recolher", "Registrar recolhimento", "bank", "financeiro.baixar_contapagar"),
        ("financeiro:guia_estornar", "Estornar recolhimento", "arrow-counterclockwise",
         "financeiro.baixar_contapagar"),
        ("financeiro:guia_darf", "Imprimir DARF", "printer", "financeiro.view_guiatributo"),
    )
    can_create = can_edit = can_delete = False
    icon = "receipt-cutoff"
    menu = "Financeiro"
    menu_label = "Guias de tributos retidos"
    menu_order = 14


class CompensacaoCrud(Crud):
    model = Compensacao
    list_display = ("data", "conta_pagar", "conta_receber", "valor", "status")
    search_fields = ("conta_pagar__descricao", "conta_receber__descricao", "conta_pagar__fornecedor__nome")
    filter_fields = ("status",)
    detail_fields = ["data", "conta_pagar", "conta_receber", "valor", "status", "observacoes", "criado_por",
                     "criado_em"]
    actions = (("financeiro:compensacao_estornar", "Estornar compensação", "arrow-counterclockwise",
                "financeiro.change_compensacao"),)
    can_create = False
    can_edit = False
    can_delete = False
    icon = "arrow-left-right"
    menu = "Financeiro"
    menu_order = 7


class RenegociacaoCrud(Crud):
    model = Renegociacao
    list_display = ("data", "cliente", "total_original", "acrescimo", "desconto", "total_novo", "parcelas", "status")
    search_fields = ("cliente__nome", "cliente__nome_fantasia")
    filter_fields = ("status",)
    detail_fields = ["cliente", "data", "total_original", "acrescimo", "desconto", "total_novo", "parcelas",
                     "primeiro_vencimento", "intervalo_dias", "status", "observacoes", "criado_por", "criado_em"]
    related = (
        ("itens", "Títulos renegociados", ["titulo", "saldo"]),
        ("novos_titulos", "Novas parcelas", ["parcela", "vencimento", "valor", "status"]),
    )
    actions = (("financeiro:renegociacao_desfazer", "Desfazer renegociação", "arrow-counterclockwise",
                "financeiro.change_renegociacao"),)
    can_create = False
    can_edit = False
    can_delete = False
    icon = "arrow-repeat"
    menu = "Financeiro"
    menu_order = 8


class FaixaPDDCrud(Crud):
    model = FaixaPDD
    fields = ["nome", "dias_minimos", "percentual", "ativa"]
    list_display = ("nome", "dias_minimos", "percentual", "ativa")
    icon = "percent"
    menu = "Financeiro"
    menu_order = 13


class ApuracaoPDDCrud(Crud):
    model = ApuracaoPDD
    list_display = ("data", "base_vencida", "provisao_necessaria", "provisao_anterior", "ajuste", "status")
    filter_fields = ("status",)
    detail_fields = ["data", "base_vencida", "provisao_necessaria", "provisao_anterior", "ajuste", "status",
                     "observacoes", "criado_por", "criado_em"]
    actions = (("financeiro:pdd_estornar", "Estornar apuração", "arrow-counterclockwise",
                "financeiro.change_apuracaopdd"),)
    can_create = False
    can_edit = False
    can_delete = False
    icon = "shield-exclamation"
    menu = "Financeiro"
    menu_order = 9


class LiquidacaoLoteCrud(Crud):
    model = LiquidacaoLote
    list_display = ("numero", "tipo", "data", "conta_bancaria", "quantidade", "total", "status")
    search_fields = ("numero",)
    filter_fields = ("tipo", "status")
    detail_fields = ["numero", "tipo", "data", "conta_bancaria", "forma_pagamento", "quantidade", "total", "liquido",
                     "status", "criado_por", "criado_em"]
    related = (("itens", "Títulos liquidados", ["contapagar", "contareceber", "valor"]),)
    actions = (("financeiro:lote_estornar", "Estornar lote", "arrow-counterclockwise", "financeiro.liquidar_lote"),)
    can_create = False
    can_edit = False
    can_delete = False
    icon = "collection"
    menu = "Financeiro"
    menu_label = "Liquidações em lote"
    menu_order = 10


class BorderoImpostoCrud(Crud):
    model = BorderoImposto
    list_display = ("numero", "data", "conta_bancaria", "total", "status")
    search_fields = ("numero",)
    filter_fields = ("status",)
    detail_fields = ["numero", "data", "conta_bancaria", "selic_pct", "total", "status", "criado_por", "criado_em"]
    related = (("itens", "Guias recolhidas", ["guia", "valor", "multa", "juros"]),)
    actions = (("financeiro:bordero_imposto_estornar", "Estornar borderô", "arrow-counterclockwise",
                "financeiro.recolher_bordero_imposto"),)
    can_create = can_edit = can_delete = False
    icon = "receipt"
    menu = "Financeiro"
    menu_label = "Borderôs de impostos"
    menu_order = 15
