from django.core.exceptions import ValidationError

from apps.core.crud import Crud, FormBase

from .models import Bordero, Extrato


class ExtratoCrud(Crud):
    model = Extrato
    list_display = ("conta_bancaria", "inicio", "fim", "arquivo", "status", "criado_por")
    filter_fields = ("conta_bancaria",)
    detail_template = "bancario/extrato_detail.html"
    can_create = can_edit = False    # nasce na importação do OFX
    icon = "arrow-left-right"
    menu = "Financeiro"
    menu_label = "Conciliação bancária"
    menu_order = 7

    @classmethod
    def extra_detalhe(cls, request, obj):
        pend = list(obj.linhas.filter(movimentacao__isnull=True, ignorada=False))
        for l in pend:
            l.sugestoes = l.titulos_sugeridos()
        return {"pendentes": pend, "conciliadas": obj.linhas.filter(movimentacao__isnull=False)
                .select_related("movimentacao"), "ignoradas": obj.linhas.filter(ignorada=True),
                "so_no_erp": obj.so_no_erp()[:100]}


class BorderoForm(FormBase):
    class Meta:
        model = Bordero
        fields = ["conta_bancaria", "data_pagamento"]

    def clean(self):
        d = super().clean()
        if self.instance.pk and self.instance.status != "aberto":
            raise ValidationError("Só borderôs abertos podem ser alterados.")
        return d


class BorderoCrud(Crud):
    model = Bordero
    form_class = BorderoForm
    list_display = ("numero", "conta_bancaria", "data_pagamento", "total", "status")
    filter_fields = ("status", "conta_bancaria")
    detail_fields = ["numero", "conta_bancaria", "data_pagamento", "total", "status", "sequencial", "remetido_em",
                     "remetido_por"]
    related = (("itens", "Títulos", ["conta_pagar", "valor", "forma", "status", "ocorrencia"]),)
    actions = (
        ("bancario:bordero_titulos", "Adicionar títulos", "plus-circle", "bancario.change_bordero"),
        ("bancario:bordero_remessa", "Gerar arquivo de remessa", "download", "bancario.gerar_remessa_bordero"),
        ("bancario:bordero_cancelar", "Cancelar", "x-octagon", "bancario.change_bordero"),
    )
    can_delete = False
    icon = "bank"
    menu = "Financeiro"
    menu_order = 8
