from django.core.exceptions import ValidationError

from apps.core.crud import Crud, FormBase

from .models import (AlcadaCompra, Cotacao, ItemCotacao, ItemPedido, ItemSolicitacao, PedidoCompra,
                     RecebimentoCompra, SolicitacaoCompra)


class SolicitacaoForm(FormBase):
    class Meta:
        model = SolicitacaoCompra
        fields = ["empresa", "departamento", "data_necessidade", "justificativa"]

    def clean(self):
        dados = super().clean()
        if self.instance.pk and self.instance.status != "aberta":
            raise ValidationError("A solicitação já foi para cotação e não pode mais ser alterada.")
        return dados


class SolicitacaoCompraCrud(Crud):
    model = SolicitacaoCompra
    form_class = SolicitacaoForm
    inline = (ItemSolicitacao, ["produto", "descricao", "quantidade", "unidade"], 3)
    list_display = ("numero", "solicitante", "departamento", "data_necessidade", "status")
    search_fields = ("numero", "justificativa", "itens__descricao")
    filter_fields = ("status", "departamento")
    detail_fields = ["numero", "solicitante", "departamento", "data_necessidade", "justificativa", "status",
                     "empresa", "criado_em"]
    related = (("cotacoes", "Cotações", ["numero", "status"]), ("pedidos", "Pedidos", ["numero", "fornecedor",
                                                                                     "valor_total", "status"]))
    actions = (("compras:gerar_cotacao", "Gerar cotação", "clipboard-data", "compras.add_cotacao"),)
    icon = "cart-plus"
    menu = "Compras"
    menu_order = 1

    @classmethod
    def get_queryset(cls, request):
        qs = super().get_queryset(request)
        # Quem não é do setor de compras vê só as próprias solicitações.
        if not request.user.has_perm("compras.view_cotacao"):
            qs = qs.filter(solicitante=request.user)
        return qs


class CotacaoCrud(Crud):
    model = Cotacao
    fields = ["empresa", "descricao", "solicitacao", "prazo_resposta"]
    inline = (ItemCotacao, ["fornecedor", "produto", "descricao", "quantidade", "valor_unitario",
                            "prazo_entrega_dias"], 1)
    list_display = ("numero", "descricao", "solicitacao", "prazo_resposta", "status")
    search_fields = ("numero", "descricao")
    filter_fields = ("status",)
    actions = (("compras:mapa_cotacao", "Mapa de cotação", "bar-chart-steps", "compras.add_pedidocompra"),)
    related = (("pedidos", "Pedido gerado", ["numero", "fornecedor", "valor_total", "status"]),)
    icon = "clipboard-data"
    menu = "Compras"
    menu_order = 2


class PedidoForm(FormBase):
    class Meta:
        model = PedidoCompra
        fields = ["empresa", "fornecedor", "departamento", "solicitacao", "cotacao", "data_pedido",
                  "previsao_entrega", "condicao_pagamento", "projeto", "centro_custo", "plano_conta", "valor_frete",
                  "observacoes"]

    def clean(self):
        dados = super().clean()
        if self.instance.pk and self.instance.status not in PedidoCompra.EDITAVEIS:
            raise ValidationError(
                f"Pedido {self.instance.get_status_display().lower()} não pode ser alterado. "
                "Cancele e gere outro, se precisar mudar itens ou valores.")
        return dados


class PedidoCompraCrud(Crud):
    model = PedidoCompra
    form_class = PedidoForm
    inline = (ItemPedido, ["produto", "descricao", "quantidade", "valor_unitario"], 3)
    list_display = ("numero", "fornecedor", "data_pedido", "previsao_entrega", "valor_total", "status")
    search_fields = ("numero", "fornecedor__nome", "fornecedor__nome_fantasia", "itens__descricao")
    filter_fields = ("status", "fornecedor")
    detail_fields = ["numero", "fornecedor", "status", "motivo_status", "departamento", "aprovador_coordenador",
                     "aprovador_gerente", "aprovador_diretor", "data_pedido", "previsao_entrega",
                     "condicao_pagamento", "valor_itens", "valor_frete", "valor_total", "projeto", "centro_custo",
                     "plano_conta", "solicitacao", "cotacao", "comprador", "observacoes"]
    related = (("recebimentos", "Recebimentos", ["data", "nota_fiscal", "conferencia", "divergencias"]),)
    actions = (
        ("compras:enviar_pedido", "Enviar para aprovação", "send-check", "compras.change_pedidocompra"),
        ("compras:receber_pedido", "Registrar recebimento", "box-arrow-in-down", "compras.add_recebimentocompra"),
        ("compras:pdf_pedido", "PDF para o fornecedor", "filetype-pdf", "compras.view_pedidocompra"),
        ("compras:cancelar_pedido", "Cancelar", "x-octagon", "compras.change_pedidocompra"),
    )
    icon = "cart-check"
    menu = "Compras"
    menu_order = 3

    @classmethod
    def after_save(cls, request, obj, created):
        obj.recalcular()


class RecebimentoForm(FormBase):
    """Na edição só se vincula a nota e se anota; quantidades vêm do recebimento físico."""

    class Meta:
        model = RecebimentoCompra
        fields = ["nota_fiscal", "observacoes"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["nota_fiscal"].queryset = self.fields["nota_fiscal"].queryset.filter(
                fornecedor_id=self.instance.pedido.fornecedor_id)


class RecebimentoCompraCrud(Crud):
    model = RecebimentoCompra
    form_class = RecebimentoForm
    list_display = ("data", "pedido", "nota_fiscal", "status", "conta_pagar")
    search_fields = ("pedido__numero", "pedido__fornecedor__nome")
    filter_fields = ("conferencia",)
    detail_fields = ["pedido", "data", "nota_fiscal", "status", "divergencias", "conta_pagar", "recebido_por",
                     "observacoes"]
    related = (("itens", "Itens recebidos", ["item_pedido", "quantidade"]),)
    actions = (("compras:conferir_recebimento", "Conferir de novo", "check2-all", "compras.change_recebimentocompra"),)
    can_create = False   # nasce no botão "Registrar recebimento" do pedido
    can_delete = False
    icon = "box-arrow-in-down"
    menu = "Compras"
    menu_label = "Recebimentos"
    menu_order = 4

    @classmethod
    def after_save(cls, request, obj, created):
        obj.conferir()


class AlcadaForm(FormBase):
    class Meta:
        model = AlcadaCompra
        fields = ["departamento", "coordenador", "limite_coordenador", "gerente", "limite_gerente", "diretor"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from django.contrib.auth import get_user_model

        ativos = get_user_model().objects.filter(is_active=True).order_by("first_name", "username")
        for nome in ("coordenador", "gerente", "diretor"):
            self.fields[nome].queryset = ativos
            self.fields[nome].label_from_instance = lambda u: u.get_full_name() or u.username
        self.fields["departamento"].empty_label = "— Padrão da empresa —"


class AlcadaCompraCrud(Crud):
    """Tela de atualização das alçadas: quem aprova e até quanto, por departamento."""

    model = AlcadaCompra
    form_class = AlcadaForm
    list_display = ("escopo", "coordenador", "limite_coordenador", "gerente", "limite_gerente", "diretor")
    search_fields = ("departamento__nome", "coordenador__first_name", "gerente__first_name", "diretor__first_name")
    detail_fields = ["departamento", "coordenador", "limite_coordenador", "gerente", "limite_gerente", "diretor",
                     "criado_por", "criado_em", "atualizado_em"]
    icon = "diagram-2"
    menu = "Compras"
    menu_label = "Alçadas de aprovação"
    menu_order = 9
