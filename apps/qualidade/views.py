from django import forms
from django.contrib.auth.decorators import login_required, permission_required
from django.shortcuts import get_object_or_404

from apps.core import acoes
from apps.core.crud import BootstrapMixin

from .models import Inspecao


class ResultadoForm(BootstrapMixin, forms.Form):
    quantidade_aprovada = forms.DecimalField(label="quantidade aprovada", min_value=0, decimal_places=4,
                                             help_text="O aprovado vai para o armazém principal. O resto fica na "
                                                       "quarentena e abre uma não conformidade.")
    resultado = forms.CharField(label="resultado / laudo", widget=forms.Textarea)


@login_required
@permission_required("qualidade.change_inspecao", raise_exception=True)
def concluir_inspecao(request, pk):
    ins = get_object_or_404(Inspecao, pk=pk)
    return acoes.formulario(
        request, ins, ResultadoForm, f"Resultado da {ins} — {ins.produto}",
        lambda d: ins.concluir(request.user, d["quantidade_aprovada"], d["resultado"]),
        lambda st: {"aprovada": "Inspeção aprovada: material liberado.",
                    "parcial": "Aprovação parcial: o aprovado foi liberado e a não conformidade foi aberta.",
                    "reprovada": "Inspeção reprovada: material retido na quarentena e não conformidade aberta."}[st],
        "clipboard-check", initial={"quantidade_aprovada": ins.quantidade})
