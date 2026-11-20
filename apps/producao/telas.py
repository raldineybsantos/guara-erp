from django.core.exceptions import ValidationError

from apps.core.crud import Crud, FormBase

from .models import ComponenteEstrutura, EstruturaProduto, OperacaoRoteiro, OrdemProducao


class EstruturaCrud(Crud):
    model = EstruturaProduto
    fields = ["produto", "quantidade_base", "observacoes", "ativo"]
    inline = (ComponenteEstrutura, ["componente", "quantidade", "perda_pct"], 3)
    list_display = ("produto", "quantidade_base", "ativo")
    search_fields = ("produto__codigo", "produto__descricao")
    related = (("operacoes", "Roteiro de operações", ["sequencia", "descricao", "recurso", "setup_min",
                                                      "tempo_unitario_min", "custo_hora"]),)
    icon = "diagram-3"
    menu = "Produção"
    menu_label = "Estruturas (lista de materiais)"
    menu_order = 2


class OrdemForm(FormBase):
    class Meta:
        model = OrdemProducao
        fields = ["empresa", "produto", "quantidade", "armazem", "data_prevista", "observacoes"]

    def clean(self):
        d = super().clean()
        if self.instance.pk and self.instance.status != "planejada":
            raise ValidationError("Só ordens planejadas podem ser alteradas.")
        return d


class OrdemProducaoCrud(Crud):
    model = OrdemProducao
    form_class = OrdemForm
    list_display = ("numero", "produto", "quantidade", "quantidade_produzida", "data_prevista", "status")
    search_fields = ("numero", "produto__codigo", "produto__descricao")
    filter_fields = ("status", "produto")
    detail_fields = ["numero", "produto", "quantidade", "quantidade_produzida", "saldo", "status", "armazem",
                     "data_prevista", "custo_total", "custo_unitario", "tempo_previsto_min", "custo_roteiro_previsto",
                     "observacoes"]
    related = (("apontamentos", "Apontamentos", ["data", "quantidade", "custo_unitario", "usuario", "observacao"]),)
    detail_template = "producao/ordem_detail.html"
    acao_status = {
        "producao:liberar": ('planejada',),
        "producao:apontar": ('liberada',),
        "producao:gerar_sc": ('planejada', 'liberada'),
        "producao:encerrar": ('liberada',),
        "producao:cancelar": ('planejada', 'liberada'),
    }
    actions = (
        ("producao:liberar", "Liberar", "play-circle", "producao.change_ordemproducao"),
        ("producao:apontar", "Apontar produção", "check2-square", "producao.change_ordemproducao"),
        ("producao:gerar_sc", "Comprar o que falta", "cart-plus", "compras.add_solicitacaocompra"),
        ("producao:encerrar", "Encerrar", "stop-circle", "producao.change_ordemproducao"),
        ("producao:cancelar", "Cancelar", "x-octagon", "producao.change_ordemproducao"),
    )
    icon = "gear-wide-connected"
    menu = "Produção"
    menu_order = 1


class OperacaoRoteiroCrud(Crud):
    model = OperacaoRoteiro
    fields = ["estrutura", "sequencia", "descricao", "recurso", "setup_min", "tempo_unitario_min", "custo_hora"]
    list_display = ("estrutura", "sequencia", "descricao", "recurso", "setup_min", "tempo_unitario_min", "custo_hora")
    search_fields = ("descricao", "recurso", "estrutura__produto__descricao")
    filter_fields = ("estrutura",)
    icon = "stopwatch"
    menu = "Produção"
    menu_label = "Roteiro de operações"
    menu_order = 3
    can_delete = True
