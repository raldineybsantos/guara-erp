from datetime import timedelta
from decimal import ROUND_DOWN, Decimal

from django import forms
from django.utils import timezone

from apps.core.crud import BootstrapMixin, FormBase

from .models import (FORMAS_PAGAMENTO, CentroCusto, ContaBancaria, ContaPagar, ContaReceber, RegraRetencao)

CAMPOS_TITULO = ["descricao", "documento", "plano_conta", "centro_custo", "valor", "juros", "desconto",
                 "data_emissao", "competencia", "vencimento", "forma_pagamento", "conta_bancaria", "observacoes"]


class ParcelamentoMixin(forms.Form):
    parcelas = forms.IntegerField(min_value=1, max_value=120, initial=1, required=False,
                                  help_text="Na inclusão, divide o valor em N parcelas.")
    intervalo_dias = forms.IntegerField(label="Intervalo (dias)", min_value=1, max_value=365, initial=30, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields.pop("parcelas")
            self.fields.pop("intervalo_dias")
        else:
            self.fields["intervalo_dias"].label = "Intervalo entre parcelas (dias)"

    def save(self, commit=True):
        obj = super().save(commit=False)
        n = self.cleaned_data.get("parcelas") or 1
        self._parcelas_extra = []
        if not obj.pk and n > 1:
            intervalo = self.cleaned_data.get("intervalo_dias") or 30
            total = obj.valor
            base = (total / n).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
            primeira = total - base * (n - 1)
            obj.valor, obj.parcela, obj.total_parcelas = primeira, 1, n
            for i in range(2, n + 1):
                self._parcelas_extra.append(dict(valor=base, parcela=i, total_parcelas=n,
                                                 vencimento=obj.vencimento + timedelta(days=intervalo * (i - 1))))
        if commit:
            obj.save()
            self.save_m2m()
            self.salvar_parcelas(obj)
        return obj

    def salvar_parcelas(self, obj):
        for extra in getattr(self, "_parcelas_extra", []):
            copia = type(obj).objects.get(pk=obj.pk)
            copia.pk = None
            copia.id = None
            for k, v in extra.items():
                setattr(copia, k, v)
            copia.juros = copia.desconto = Decimal("0")
            copia.save()
        self._parcelas_extra = []


class ContaPagarForm(ParcelamentoMixin, FormBase):
    class Meta:
        model = ContaPagar
        fields = ["fornecedor"] + CAMPOS_TITULO[:-1] + ["codigo_barras", "aprovado", "observacoes"]

    def clean(self):
        dados = super().clean()
        # Orçamento com controle "bloquear": não deixa cadastrar título que estoure o orçado.
        if not self.errors and dados.get("valor") and dados.get("data_emissao") and dados.get("vencimento"):
            from apps.contabilidade import orcamento

            inst = self.instance
            for k in ("valor", "competencia", "data_emissao", "plano_conta", "centro_custo", "empresa"):
                if k in dados:
                    setattr(inst, k, dados[k])
            e = orcamento.estouro(inst)
            if e and e["orcamento"].controle == "bloquear":
                raise forms.ValidationError(
                    f"Estoura o orçamento “{e['orcamento']}” ({e['linha']}): orçado até o mês R$ {e['orcado']}, "
                    f"com este título R$ {e['realizado']}. Peça ajuste do orçamento ou ratifique com o responsável.")
        return dados


class ContaReceberForm(ParcelamentoMixin, FormBase):
    class Meta:
        model = ContaReceber
        fields = ["cliente"] + CAMPOS_TITULO


class BaixaForm(BootstrapMixin, forms.Form):
    valor = forms.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("0.01"))
    data = forms.DateField(initial=timezone.localdate)
    conta_bancaria = forms.ModelChoiceField(ContaBancaria.objects.filter(ativa=True), label="Conta bancária")
    forma_pagamento = forms.ChoiceField(choices=[("", "---------")] + FORMAS_PAGAMENTO, required=False,
                                        label="Forma de pagamento")
    observacao = forms.CharField(widget=forms.Textarea, required=False, label="Observação")


class TransferenciaForm(BootstrapMixin, forms.Form):
    origem = forms.ModelChoiceField(ContaBancaria.objects.filter(ativa=True), label="Conta de origem")
    destino = forms.ModelChoiceField(ContaBancaria.objects.filter(ativa=True), label="Conta de destino")
    valor = forms.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("0.01"))
    data = forms.DateField(initial=timezone.localdate)
    descricao = forms.CharField(max_length=150, required=False, label="Descrição")

    def clean(self):
        dados = super().clean()
        if dados.get("origem") and dados.get("origem") == dados.get("destino"):
            raise forms.ValidationError("Origem e destino devem ser contas diferentes.")
        return dados


class RetencaoForm(BootstrapMixin, forms.Form):
    base = forms.DecimalField(label="Base de cálculo", max_digits=14, decimal_places=2, min_value=Decimal("0.01"),
                              help_text="Em geral o valor do título; pode ser menor se parte dele não sofre retenção.")
    regras = forms.ModelMultipleChoiceField(
        RegraRetencao.objects.filter(ativa=True), label="Tributos a reter", required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text="Marque só o que se aplica a este fornecedor e a este serviço. Sem marcar nada, remove as retenções.")


class RateioLinhaForm(BootstrapMixin, forms.Form):
    centro_custo = forms.ModelChoiceField(CentroCusto.objects.filter(ativo=True), required=False,
                                          label="Centro de custo")
    percentual = forms.DecimalField(label="%", max_digits=6, decimal_places=2, required=False,
                                    min_value=Decimal("0"), max_value=Decimal("100"))


class RecolhimentoForm(BootstrapMixin, forms.Form):
    conta_bancaria = forms.ModelChoiceField(ContaBancaria.objects.filter(ativa=True), label="Conta bancária")
    data = forms.DateField(label="Data do recolhimento", initial=timezone.localdate)
