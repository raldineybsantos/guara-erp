from apps.core.crud import Crud

from .filtros import ContaPagarFilter, ContaReceberFilter
from .forms import ContaPagarForm, ContaReceberForm
from .models import (CentroCusto, ContaBancaria, ContaPagar, ContaReceber, GuiaTributo, Movimentacao, PlanoConta,
                     RegraRetencao)

DETALHE_TITULO = ["descricao", "documento", "parcela", "total_parcelas", "plano_conta", "centro_custo",
                  "valor", "juros", "desconto", "valor_total", "valor_pago", "saldo", "data_emissao",
                  "competencia", "vencimento", "situacao", "data_pagamento", "forma_pagamento",
                  "conta_bancaria", "observacoes", "criado_por", "criado_em"]


class ContaBancariaCrud(Crud):
    model = ContaBancaria
    fields = ["empresa", "nome", "tipo", "banco", "codigo_banco", "agencia", "conta", "convenio",
              "saldo_inicial", "data_saldo_inicial", "ativa"]
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
        ("retencoes", "Tributos retidos na fonte", ["regra", "base", "aliquota", "valor", "retido"]),
        ("rateios", "Rateio por centro de custo", ["centro_custo", "percentual"]),
    )
    actions = (
        ("financeiro:titulo_baixar_pagar", "Pagar", "cash-coin", "financeiro.baixar_contapagar"),
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
        ("cobrancas", "Cobranças", ["tipo", "vencimento", "valor", "status"]),
        ("rateios", "Rateio por centro de custo", ["centro_custo", "percentual"]),
    )
    actions = (
        ("financeiro:titulo_baixar_receber", "Receber", "cash-coin", "financeiro.baixar_contareceber"),
        ("financeiro:titulo_ratear_receber", "Ratear por centro de custo", "diagram-3", "financeiro.change_contareceber"),
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
    fields = ["tributo", "nome", "aliquota", "codigo_receita", "dia_vencimento", "base_minima", "ativa"]
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
    detail_fields = ["tributo", "codigo_receita", "competencia", "vencimento", "valor", "status",
                     "data_pagamento", "conta_bancaria", "empresa"]
    related = (("itens", "Retenções que compõem a guia", ["retencao", "movimentacao", "valor"]),)
    actions = (
        ("financeiro:guia_recolher", "Registrar recolhimento", "bank", "financeiro.baixar_contapagar"),
        ("financeiro:guia_estornar", "Estornar recolhimento", "arrow-counterclockwise",
         "financeiro.baixar_contapagar"),
    )
    can_create = can_edit = can_delete = False
    icon = "receipt-cutoff"
    menu = "Financeiro"
    menu_label = "Guias de tributos retidos"
    menu_order = 14
