from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render

from apps.core.crud import BootstrapMixin
from apps.fiscal.models import Produto

from . import models as est
from .models import Armazem, Inventario


class MovimentarForm(BootstrapMixin, forms.Form):
    OPERACOES = [("entrada", "Entrada (ajuste para mais)"), ("saida", "Saída / consumo interno"),
                 ("transferencia", "Transferência entre armazéns")]
    operacao = forms.ChoiceField(label="operação", choices=OPERACOES)
    produto = forms.ModelChoiceField(queryset=Produto.objects.filter(tipo="produto", ativo=True))
    armazem = forms.ModelChoiceField(label="armazém", queryset=Armazem.objects.filter(ativo=True))
    destino = forms.ModelChoiceField(label="armazém de destino (transferência)", required=False,
                                     queryset=Armazem.objects.filter(ativo=True))
    quantidade = forms.DecimalField(min_value=0, decimal_places=4)
    custo_unitario = forms.DecimalField(label="custo unitário (só entrada)", required=False, min_value=0,
                                        decimal_places=4)
    documento = forms.CharField(required=False, max_length=60)
    motivo = forms.CharField(max_length=200)

    def clean(self):
        d = super().clean()
        if d.get("operacao") == "transferencia" and not d.get("destino"):
            self.add_error("destino", "Informe o armazém de destino.")
        return d


@login_required
@permission_required("estoque.add_movimento", raise_exception=True)
def movimentar(request):
    form = MovimentarForm(request.POST or None, initial={"armazem": Armazem.principal()})
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            if d["operacao"] == "entrada":
                custo = d["custo_unitario"] if d["custo_unitario"] is not None else est.custo_medio(d["produto"],
                                                                                                   d["armazem"])
                est.entrada(d["produto"], d["quantidade"], custo, d["armazem"], "ajuste", d["documento"], d["motivo"])
            elif d["operacao"] == "saida":
                est.saida(d["produto"], d["quantidade"], d["armazem"], "consumo", d["documento"], d["motivo"])
            else:
                est.transferir(d["produto"], d["quantidade"], d["armazem"], d["destino"], d["documento"], d["motivo"])
        except ValidationError as e:
            form.add_error(None, e)
        else:
            messages.success(request, "Movimento registrado.")
            return redirect("estoque:movimento_list")
    return render(request, "form_simples.html", {"form": form, "titulo": "Movimentação manual",
                                                 "icone": "arrow-down-up"})


@login_required
@permission_required("estoque.change_inventario", raise_exception=True)
def encerrar_inventario(request, pk):
    inv = get_object_or_404(Inventario, pk=pk)
    if request.method != "POST":
        return render(request, "confirmar.html", {
            "titulo": f"Encerrar {inv}", "voltar": inv.get_absolute_url(),
            "mensagem": "Os saldos do armazém serão ajustados para as quantidades contadas. Os ajustes ficam no "
                        "kardex com origem Inventário. Esta ação não pode ser desfeita."})
    try:
        n = inv.encerrar()
        messages.success(request, f"Inventário encerrado: {n} ajuste(s) de saldo.")
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
    return redirect(inv.get_absolute_url())


@login_required
@permission_required("estoque.view_saldo", raise_exception=True)
def reposicao(request):
    """Produtos abaixo do estoque mínimo; gera uma solicitação de compra com as faltas."""
    abaixo = []
    for p in Produto.objects.filter(tipo="produto", ativo=True, estoque_minimo__gt=0):
        disp = est.saldo_disponivel(p)
        if disp < p.estoque_minimo:
            abaixo.append({"produto": p, "saldo": disp, "minimo": p.estoque_minimo,
                           "comprar": p.estoque_minimo * 2 - disp})
    if request.method == "POST" and abaixo:
        if not request.user.has_perm("compras.add_solicitacaocompra"):
            messages.error(request, "Sem permissão para abrir solicitação de compra.")
            return redirect("estoque:reposicao")
        from apps.compras.models import ItemSolicitacao, SolicitacaoCompra

        sc = SolicitacaoCompra.objects.create(justificativa="Reposição: produtos abaixo do estoque mínimo.",
                                              solicitante=request.user)
        for a in abaixo:
            ItemSolicitacao.objects.create(solicitacao=sc, produto=a["produto"], quantidade=a["comprar"],
                                           unidade=a["produto"].unidade)
        messages.success(request, f"{sc} aberta com {len(abaixo)} item(ns). Revise as quantidades.")
        return redirect(sc.get_absolute_url())
    return render(request, "estoque/reposicao.html", {"titulo": "Reposição de estoque", "icone": "bell",
                                                      "abaixo": abaixo})
