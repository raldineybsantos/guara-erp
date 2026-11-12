import django_filters
from django.utils import timezone

from .models import ContaPagar, ContaReceber

SITUACOES = [("abertos", "Em aberto"), ("vencidos", "Vencidos"), ("pagos", "Pagos"),
             ("cancelados", "Cancelados"), ("todos", "Todos")]


class TituloFilterBase(django_filters.FilterSet):
    situacao = django_filters.ChoiceFilter(label="Situação", choices=SITUACOES, method="filtrar_situacao",
                                           empty_label=None)
    vencimento__gte = django_filters.DateFilter(field_name="vencimento", lookup_expr="gte", label="Vencimento de")
    vencimento__lte = django_filters.DateFilter(field_name="vencimento", lookup_expr="lte", label="até")

    def __init__(self, data=None, *args, **kwargs):
        if data is not None and not data.get("situacao"):
            data = data.copy()
            data["situacao"] = "abertos"  # padrão: o que ainda precisa de ação
        super().__init__(data, *args, **kwargs)

    def filtrar_situacao(self, qs, name, valor):
        hoje = timezone.localdate()
        return {
            "abertos": qs.filter(status__in=["aberto", "parcial"]),
            "vencidos": qs.filter(status__in=["aberto", "parcial"], vencimento__lt=hoje),
            "pagos": qs.filter(status="pago"),
            "cancelados": qs.filter(status="cancelado"),
        }.get(valor, qs)


class ContaPagarFilter(TituloFilterBase):
    class Meta:
        model = ContaPagar
        fields = ["situacao", "fornecedor", "plano_conta", "centro_custo", "aprovado"]


class ContaReceberFilter(TituloFilterBase):
    class Meta:
        model = ContaReceber
        fields = ["situacao", "cliente", "plano_conta"]
