from django import forms

from apps.core.crud import Crud

from .models import (ConjuntoUniforme, EntregaUniforme, ItemUniforme, ModeloUniforme, PecaConjunto, TamanhoColaborador,
                     tamanhos_de)


class ModeloUniformeCrud(Crud):
    model = ModeloUniforme
    fields = ["codigo", "nome", "categoria", "genero", "numeracao", "descricao", "imagem", "vida_util_meses",
              "estoque_minimo", "ativo"]
    list_display = ("codigo", "nome", "categoria", "genero", "numeracao", "total_em_estoque", "situacao_geral", "ativo")
    search_fields = ("codigo", "nome", "descricao")
    filter_fields = ("categoria", "genero", "ativo")
    detail_template = "uniformes/modelo_detail.html"
    empresa_campo = "nada"
    icon = "bag"
    menu = "Uniformes"
    menu_label = "Peças (cadastro)"
    menu_order = 2

    @classmethod
    def after_save(cls, request, obj, created):
        obj.sincronizar_itens()

    @classmethod
    def extra_detalhe(cls, request, obj):
        return {"painel": obj.painel()}


class ConjuntoUniformeCrud(Crud):
    model = ConjuntoUniforme
    fields = ["nome", "descricao", "imagem", "departamento", "cargo", "ativo"]
    list_display = ("nome", "departamento", "cargo", "ativo")
    search_fields = ("nome", "descricao")
    inline = (PecaConjunto, ["modelo", "quantidade"], 3)
    detail_template = "uniformes/conjunto_detail.html"
    empresa_campo = "nada"
    actions = (("uniformes:entregar", "Entregar a um colaborador", "box-arrow-right", "uniformes.add_entregauniforme"),)
    icon = "collection"
    menu = "Uniformes"
    menu_label = "Conjuntos (kits)"
    menu_order = 3

    @classmethod
    def extra_detalhe(cls, request, obj):
        return {"cobertura": obj.cobertura()}


class TamanhoColaboradorForm(forms.ModelForm):
    class Meta:
        model = TamanhoColaborador
        fields = ["colaborador", "numeracao", "tamanho"]

    def clean(self):
        d = super().clean()
        if d.get("numeracao") and d.get("tamanho") and d["tamanho"] not in tamanhos_de(d["numeracao"]):
            self.add_error("tamanho", f"Tamanho inválido. Use: {', '.join(tamanhos_de(d['numeracao']))}.")
        return d


class TamanhoColaboradorCrud(Crud):
    model = TamanhoColaborador
    form_class = TamanhoColaboradorForm
    empresa_campo = "colaborador__empresa"
    list_display = ("colaborador", "numeracao", "tamanho")
    search_fields = ("colaborador__nome",)
    filter_fields = ("numeracao",)
    icon = "rulers"
    menu = "Uniformes"
    menu_label = "Tamanhos dos colaboradores"
    menu_order = 4


class EntregaUniformeCrud(Crud):
    model = EntregaUniforme
    empresa_campo = "colaborador__empresa"
    list_display = ("data", "colaborador", "conjunto", "status", "recebido_confirmado")
    search_fields = ("colaborador__nome", "conjunto__nome")
    filter_fields = ("status", "recebido_confirmado")
    related = (("itens", "Peças entregues", ["item", "quantidade", "trocar_ate"]),)
    detail_template = "uniformes/entrega_detail.html"
    can_create = False      # entrega só pela tela "Entregar" (baixa o estoque)
    can_edit = False
    can_delete = False      # é prova de entrega: corrige-se por estorno
    actions = (("uniformes:entrega_termo", "Imprimir termo", "printer", "uniformes.view_entregauniforme"),
               ("uniformes:entrega_assinada", "Marcar termo assinado", "pen", "uniformes.change_entregauniforme"),
               ("uniformes:entrega_estornar", "Estornar (devolver ao estoque)", "arrow-counterclockwise",
                "uniformes.change_entregauniforme"))
    acao_status = {"uniformes:entrega_assinada": ("entregue",), "uniformes:entrega_estornar": ("entregue",)}
    icon = "box-arrow-right"
    menu = "Uniformes"
    menu_label = "Entregas"
    menu_order = 5
