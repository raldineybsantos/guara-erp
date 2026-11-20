from django.core.exceptions import ValidationError

from apps.core.crud import Crud, FormBase

from .models import Comissao, ItemPedidoVenda, ItemTabelaPreco, PedidoVenda, RegraComissao, TabelaPreco


class PedidoVendaForm(FormBase):
    class Meta:
        model = PedidoVenda
        fields = ["empresa", "cliente", "data_pedido", "previsao_entrega", "condicao_pagamento", "tabela_preco",
                  "projeto", "armazem", "natureza_operacao", "valor_frete", "observacoes"]

    def clean(self):
        dados = super().clean()
        if self.instance.pk and self.instance.status not in PedidoVenda.EDITAVEIS:
            raise ValidationError(f"Pedido {self.instance.get_status_display().lower()} não pode ser alterado.")
        return dados


class PedidoVendaCrud(Crud):
    model = PedidoVenda
    form_class = PedidoVendaForm
    inline = (ItemPedidoVenda, ["produto", "quantidade", "valor_unitario", "desconto_pct"], 3)
    list_display = ("numero", "cliente", "data_pedido", "valor_total", "vendedor", "status")
    search_fields = ("numero", "cliente__nome", "cliente__nome_fantasia", "itens__descricao")
    filter_fields = ("status", "cliente", "vendedor")
    detail_fields = ["numero", "cliente", "status", "motivo_status", "data_pedido", "previsao_entrega",
                     "condicao_pagamento", "tabela_preco", "projeto", "armazem", "valor_itens", "valor_frete", "valor_total", "vendedor",
                     "nota_fiscal", "natureza_operacao", "observacoes"]
    actions = (
        ("vendas:confirmar", "Confirmar (crédito e estoque)", "check2-circle", "vendas.change_pedidovenda"),
        ("vendas:liberar_credito", "Liberar crédito", "unlock", "vendas.liberar_credito_pedidovenda"),
        ("vendas:faturar", "Faturar", "receipt", "vendas.change_pedidovenda"),
        ("vendas:cancelar", "Cancelar", "x-octagon", "vendas.change_pedidovenda"),
    )
    acao_status = {"vendas:confirmar": ("rascunho", "bloqueado"), "vendas:liberar_credito": ("bloqueado",),
                   "vendas:faturar": ("aprovado",), "vendas:cancelar": ("rascunho", "bloqueado", "aprovado")}
    icon = "bag-check"
    menu = "Vendas"
    menu_order = 1

    @classmethod
    def after_save(cls, request, obj, created):
        obj.recalcular()


class TabelaPrecoCrud(Crud):
    model = TabelaPreco
    fields = ["empresa", "nome", "inicio", "fim", "padrao", "clientes", "ativa"]
    inline = (ItemTabelaPreco, ["produto", "quantidade_minima", "preco"], 5)
    list_display = ("nome", "inicio", "fim", "padrao", "ativa")
    search_fields = ("nome", "itens__produto__descricao")
    filter_fields = ("padrao", "ativa")
    icon = "tags"
    menu = "Vendas"
    menu_order = 2


class RegraComissaoCrud(Crud):
    model = RegraComissao
    fields = ["vendedor", "percentual", "momento", "ativa"]
    list_display = ("vendedor", "percentual", "momento", "ativa")
    filter_fields = ("momento", "ativa")
    icon = "percent"
    menu = "Vendas"
    menu_label = "Regras de comissão"
    menu_order = 4


class ComissaoCrud(Crud):
    model = Comissao
    list_display = ("pedido", "vendedor", "base", "percentual", "valor", "valor_devido", "valor_lancado")
    search_fields = ("pedido__numero", "vendedor__username", "vendedor__first_name")
    filter_fields = ("vendedor", "momento")
    detail_fields = ["pedido", "vendedor", "base", "percentual", "valor", "momento", "valor_devido",
                     "valor_lancado", "a_lancar", "criado_em"]
    actions = (("vendas:lancar_comissao", "Lançar no contas a pagar", "cash-coin", "vendas.pagar_comissao"),)
    can_create = can_edit = can_delete = False
    icon = "coin"
    menu = "Vendas"
    menu_order = 3

    @classmethod
    def get_queryset(cls, request):
        qs = super().get_queryset(request)
        u = request.user
        if u.has_perm("vendas.pagar_comissao") or not u.has_perm("vendas.add_pedidovenda"):
            return qs               # Financeiro e auditoria veem todas
        return qs.filter(vendedor=u)    # o vendedor só enxerga as próprias comissões
