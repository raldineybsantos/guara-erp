from decimal import Decimal

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render

from apps.core import acoes
from apps.core.crud import BootstrapMixin, restringir_por_empresa

from .models import ABERTOS, ESTAGIOS, Interacao, Oportunidade


@login_required
@permission_required("crm.view_oportunidade", raise_exception=True)
def funil(request):
    """Colunas por estágio com total e valor ponderado; o botão move a oportunidade de coluna."""
    if request.method == "POST":
        if not request.user.has_perm("crm.change_oportunidade"):
            messages.error(request, "Sem permissão para mover oportunidades.")
        else:
            op = get_object_or_404(Oportunidade, pk=request.POST.get("oportunidade"))
            try:
                op.mover(request.POST.get("estagio"))
            except ValidationError as e:
                messages.error(request, " ".join(e.messages))
        return redirect("crm:funil")
    qs = restringir_por_empresa(Oportunidade.objects.filter(estagio__in=ABERTOS), Oportunidade, request.user)
    qs = qs.select_related("cliente", "responsavel")
    if request.GET.get("minhas"):
        qs = qs.filter(responsavel=request.user)
    nomes = dict(ESTAGIOS)
    colunas = []
    for i, est in enumerate(ABERTOS):
        ops = [o for o in qs if o.estagio == est]
        colunas.append({"id": est, "nome": nomes[est], "ops": ops,
                        "total": sum((o.valor for o in ops), Decimal(0)),
                        "ponderado": sum((o.valor_ponderado for o in ops), Decimal(0)),
                        "anterior": ABERTOS[i - 1] if i else None,
                        "proximo": ABERTOS[i + 1] if i + 1 < len(ABERTOS) else None})
    return render(request, "crm/funil.html", {
        "titulo": "Funil de vendas", "icone": "funnel", "colunas": colunas,
        "total": sum((c["total"] for c in colunas), Decimal(0)),
        "ponderado": sum((c["ponderado"] for c in colunas), Decimal(0))})


def _op(pk):
    return get_object_or_404(Oportunidade, pk=pk)


@login_required
@permission_required("crm.change_oportunidade", raise_exception=True)
def ganhar(request, pk):
    op = _op(pk)
    return acoes.confirmar(
        request, op, f"Ganhar {op}", "Fechar como ganha? O prospect vira cliente (se ainda não for) e um pedido de "
        "venda em rascunho é aberto para você incluir os itens.", lambda: op.ganhar(request.user),
        lambda pv: f"Oportunidade ganha. {pv} aberto.", destino=lambda pv: pv.get_absolute_url())


class PerderForm(BootstrapMixin, forms.Form):
    motivo = forms.CharField(label="motivo da perda", max_length=200,
                             help_text="Ex.: preço, prazo, concorrente X, sem orçamento.")


@login_required
@permission_required("crm.change_oportunidade", raise_exception=True)
def perder(request, pk):
    op = _op(pk)
    return acoes.formulario(request, op, PerderForm, f"Perder {op}", lambda d: op.perder(d["motivo"]),
                            "Oportunidade marcada como perdida.", "x-circle")


class InteracaoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Interacao
        fields = ["tipo", "data", "resumo", "proximo_passo", "data_proximo_passo"]


@login_required
@permission_required("crm.change_oportunidade", raise_exception=True)
def interagir(request, pk):
    op = _op(pk)
    form = InteracaoForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        i = form.save(commit=False)
        i.oportunidade = op
        i.save()
        messages.success(request, "Interação registrada." + (" O próximo passo foi para o seu Kanban."
                                                             if i.proximo_passo and i.data_proximo_passo else ""))
        return redirect(op.get_absolute_url())
    return render(request, "form_simples.html", {"form": form, "titulo": f"Interação — {op}",
                                                 "icone": "chat-left-text"})
