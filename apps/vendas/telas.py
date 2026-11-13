from django.core.exceptions import ValidationError

from apps.core.crud import Crud, FormBase

from .models import ItemPedidoVenda, PedidoVenda


class PedidoVendaForm(FormBase):
    class Meta:
        model = PedidoVenda
        fields = ["empresa", "cliente", "data_pedido", "previsao_entrega", "condicao_pagamento", "armazem",
                  "natureza_operacao", "valor_frete", "observacoes"]

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
                     "condicao_pagamento", "armazem", "valor_itens", "valor_frete", "valor_total", "vendedor",
                     "nota_fiscal", "natureza_operacao", "observacoes"]
    actions = (
        ("vendas:confirmar", "Confirmar (crédito e estoque)", "check2-circle", "vendas.change_pedidovenda"),
        ("vendas:liberar_credito", "Liberar crédito", "unlock", "vendas.liberar_credito_pedidovenda"),
        ("vendas:faturar", "Faturar", "receipt", "vendas.change_pedidovenda"),
        ("vendas:cancelar", "Cancelar", "x-octagon", "vendas.change_pedidovenda"),
    )
    icon = "bag-check"
    menu = "Vendas"
    menu_order = 1

    @classmethod
    def after_save(cls, request, obj, created):
        obj.recalcular()
