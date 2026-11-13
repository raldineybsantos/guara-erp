from django import forms
from django.contrib.auth.decorators import login_required, permission_required
from django.shortcuts import get_object_or_404

from apps.core import acoes
from apps.core.crud import BootstrapMixin

from .models import OrdemServico


@login_required
@permission_required("manutencao.change_ordemservico", raise_exception=True)
def iniciar(request, pk):
    os_ = get_object_or_404(OrdemServico, pk=pk)
    return acoes.confirmar(request, os_, f"Iniciar {os_}", "Registrar o início do atendimento agora?",
                           os_.iniciar, "Atendimento iniciado.")


@login_required
@permission_required("manutencao.change_ordemservico", raise_exception=True)
def cancelar(request, pk):
    os_ = get_object_or_404(OrdemServico, pk=pk)
    return acoes.confirmar(request, os_, f"Cancelar {os_}", "Cancelar a ordem de serviço?", os_.cancelar,
                           "Ordem cancelada.")


class ConcluirForm(BootstrapMixin, forms.Form):
    servico_executado = forms.CharField(label="serviço executado", widget=forms.Textarea)
    horas = forms.DecimalField(label="horas trabalhadas", min_value=0, decimal_places=2, required=False)
    custo_mao_obra = forms.DecimalField(label="custo de mão de obra / terceiros (R$)", min_value=0,
                                        decimal_places=2, required=False)


@login_required
@permission_required("manutencao.change_ordemservico", raise_exception=True)
def concluir(request, pk):
    os_ = get_object_or_404(OrdemServico, pk=pk)
    return acoes.formulario(
        request, os_, ConcluirForm, f"Concluir {os_} — {os_.equipamento}",
        lambda d: os_.concluir(d["servico_executado"], d["horas"], d["custo_mao_obra"]),
        "Ordem concluída. As peças foram baixadas do estoque.", "check2-circle")
