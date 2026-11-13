from django import forms
from django.contrib.auth.decorators import login_required, permission_required
from django.shortcuts import get_object_or_404

from apps.core import acoes
from apps.core.crud import BootstrapMixin

from .models import OrdemProducao
from apps.core.formatos import qtd as fq


def _op(pk):
    return get_object_or_404(OrdemProducao, pk=pk)


@login_required
@permission_required("producao.change_ordemproducao", raise_exception=True)
def liberar(request, pk):
    op = _op(pk)
    return acoes.confirmar(request, op, f"Liberar {op}", "Liberar a ordem para a fábrica?", op.liberar,
                           "Ordem liberada.")


@login_required
@permission_required("producao.change_ordemproducao", raise_exception=True)
def encerrar(request, pk):
    op = _op(pk)
    return acoes.confirmar(request, op, f"Encerrar {op}",
                           f"Encerrar com {fq(op.quantidade_produzida)} de {fq(op.quantidade)} produzidos?",
                           op.encerrar, "Ordem encerrada.")


@login_required
@permission_required("producao.change_ordemproducao", raise_exception=True)
def cancelar(request, pk):
    op = _op(pk)
    return acoes.confirmar(request, op, f"Cancelar {op}", "Cancelar a ordem de produção?", op.cancelar,
                           "Ordem cancelada.")


class ApontarForm(BootstrapMixin, forms.Form):
    quantidade = forms.DecimalField(label="quantidade produzida", min_value=0, decimal_places=4)
    observacao = forms.CharField(label="observação", required=False, max_length=200)


@login_required
@permission_required("producao.change_ordemproducao", raise_exception=True)
def apontar(request, pk):
    op = _op(pk)
    return acoes.formulario(
        request, op, ApontarForm, f"Apontar produção — {op} ({op.produto})",
        lambda d: op.apontar(d["quantidade"], request.user, d["observacao"]),
        lambda unit: f"Produção apontada. Custo unitário apurado: R$ {unit:.4f}.", "check2-square",
        initial={"quantidade": op.saldo})


@login_required
@permission_required("compras.add_solicitacaocompra", raise_exception=True)
def gerar_sc(request, pk):
    op = _op(pk)
    return acoes.confirmar(request, op, f"Comprar componentes da {op}",
                           "Abrir uma solicitação de compra com os componentes que faltam no estoque?",
                           lambda: op.gerar_solicitacao_compra(request.user),
                           lambda sc: f"{sc} aberta.", destino=lambda sc: sc.get_absolute_url())
