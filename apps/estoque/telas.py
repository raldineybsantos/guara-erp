from django.core.exceptions import ValidationError

from apps.core.crud import Crud, FormBase

from .models import Armazem, Endereco, Inventario, ItemInventario, Lote, Movimento, Saldo, SaldoLote


class ArmazemCrud(Crud):
    model = Armazem
    fields = ["empresa", "codigo", "nome", "tipo", "padrao", "ativo"]
    list_display = ("codigo", "nome", "tipo", "padrao", "ativo")
    search_fields = ("codigo", "nome")
    icon = "building"
    menu = "Estoque"
    menu_order = 5


class SaldoCrud(Crud):
    model = Saldo
    empresa_campo = "armazem__empresa"
    list_display = ("produto", "armazem", "quantidade", "custo_medio", "valor", "atualizado_em")
    search_fields = ("produto__codigo", "produto__descricao")
    filter_fields = ("armazem",)
    detail_fields = ["produto", "armazem", "quantidade", "custo_medio", "valor", "atualizado_em"]
    can_create = can_edit = can_delete = False
    icon = "boxes"
    menu = "Estoque"
    menu_label = "Saldos"
    menu_order = 1


class MovimentoCrud(Crud):
    model = Movimento
    empresa_campo = "armazem__empresa"
    list_display = ("data", "tipo", "origem", "produto", "armazem", "quantidade", "custo_unitario", "saldo_apos",
                    "documento", "usuario")
    search_fields = ("produto__codigo", "produto__descricao", "documento", "observacao")
    filter_fields = ("tipo", "origem", "armazem", "produto")
    can_create = can_edit = can_delete = False   # movimento não se altera: corrige-se com outro
    icon = "arrow-down-up"
    menu = "Estoque"
    menu_label = "Movimentos (kardex)"
    menu_order = 2


class InventarioForm(FormBase):
    class Meta:
        model = Inventario
        fields = ["armazem", "data", "observacoes"]

    def clean(self):
        dados = super().clean()
        if self.instance.pk and self.instance.status != "aberto":
            raise ValidationError("Inventário encerrado não pode ser alterado.")
        return dados


class InventarioCrud(Crud):
    model = Inventario
    form_class = InventarioForm
    empresa_campo = "armazem__empresa"
    inline = (ItemInventario, ["produto", "quantidade_contada"], 0)
    inline_no_detalhe = False
    list_display = ("numero", "armazem", "data", "status")
    filter_fields = ("status", "armazem")
    related = (("itens", "Contagem", ["produto", "quantidade_sistema", "quantidade_contada", "diferenca"]),)
    actions = (("estoque:encerrar_inventario", "Encerrar e ajustar saldos", "check2-square",
                "estoque.change_inventario"),)
    icon = "clipboard-check"
    menu = "Estoque"
    menu_order = 4

    @classmethod
    def after_save(cls, request, obj, created):
        if created:
            obj.carregar_itens()


class EnderecoCrud(Crud):
    model = Endereco
    fields = ["armazem", "codigo", "descricao", "ativo"]
    list_display = ("armazem", "codigo", "descricao", "ativo")
    search_fields = ("codigo", "descricao")
    filter_fields = ("armazem", "ativo")
    empresa_campo = "armazem__empresa"
    icon = "geo-alt"
    menu = "Estoque"
    menu_order = 6


class LoteCrud(Crud):
    model = Lote
    fields = ["produto", "codigo", "fabricacao", "validade", "bloqueado", "motivo_bloqueio", "observacoes"]
    list_display = ("produto", "codigo", "fabricacao", "validade", "quantidade", "bloqueado")
    search_fields = ("codigo", "produto__codigo", "produto__descricao")
    filter_fields = ("bloqueado",)
    detail_fields = ["produto", "codigo", "fabricacao", "validade", "quantidade", "bloqueado", "motivo_bloqueio",
                     "observacoes"]
    related = (("saldos", "Onde está", ["armazem", "endereco", "quantidade"]),)
    can_create = False     # lotes nascem nas entradas
    can_delete = False
    icon = "tags"
    menu = "Estoque"
    menu_order = 7


class SaldoLoteCrud(Crud):
    model = SaldoLote
    empresa_campo = "armazem__empresa"
    list_display = ("produto", "lote", "armazem", "endereco", "quantidade")
    search_fields = ("produto__codigo", "produto__descricao", "lote__codigo")
    filter_fields = ("armazem", "endereco")
    can_create = can_edit = can_delete = False
    icon = "grid-3x3-gap"
    menu = "Estoque"
    menu_label = "Saldos por lote/endereço"
    menu_order = 3
