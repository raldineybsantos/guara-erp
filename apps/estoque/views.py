from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render

from apps.core.crud import BootstrapMixin, restringir_por_empresa
from apps.fiscal.models import Produto

from . import models as est
from .models import Armazem, Endereco, Inventario


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
    lote = forms.CharField(required=False, max_length=40,
                           help_text="Produto com controle de lote: entrada cria/usa o lote; saída sem lote usa o que vence primeiro.")
    validade = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}),
                               help_text="Só na entrada de lote novo.")
    endereco = forms.ModelChoiceField(label="endereço", required=False, queryset=Endereco.objects.filter(ativo=True))
    motivo = forms.CharField(max_length=200)

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user is not None:
            qs = restringir_por_empresa(Armazem.objects.filter(ativo=True), Armazem, user)
            self.fields["armazem"].queryset = qs
            self.fields["destino"].queryset = qs

    def clean(self):
        d = super().clean()
        if d.get("operacao") == "transferencia" and not d.get("destino"):
            self.add_error("destino", "Informe o armazém de destino.")
        return d


@login_required
@permission_required("estoque.add_movimento", raise_exception=True)
def movimentar(request):
    form = MovimentarForm(request.POST or None, user=request.user,
                         initial={"armazem": Armazem.principal(getattr(request, "empresa", None))})
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            if d["operacao"] == "entrada":
                custo = d["custo_unitario"] if d["custo_unitario"] is not None else est.custo_medio(d["produto"],
                                                                                                   d["armazem"])
                est.entrada(d["produto"], d["quantidade"], custo, d["armazem"], "ajuste", d["documento"], d["motivo"],
                           lote=d["lote"] or None, validade=d["validade"], endereco=d["endereco"])
            elif d["operacao"] == "saida":
                est.saida(d["produto"], d["quantidade"], d["armazem"], "consumo", d["documento"], d["motivo"],
                         lote=d["lote"] or None, endereco=d["endereco"])
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


class EnderecarForm(BootstrapMixin, forms.Form):
    produto = forms.ModelChoiceField(queryset=Produto.objects.filter(tipo="produto", ativo=True))
    armazem = forms.ModelChoiceField(label="armazém", queryset=Armazem.objects.filter(ativo=True))
    lote = forms.CharField(required=False, max_length=40)
    de = forms.ModelChoiceField(label="endereço de origem", required=False, queryset=Endereco.objects.all(),
                                help_text="Vazio = saldo ainda sem endereço.")
    para = forms.ModelChoiceField(label="endereço de destino", required=False, queryset=Endereco.objects.filter(ativo=True),
                                  help_text="Vazio = tirar do endereço.")
    quantidade = forms.DecimalField(min_value=0, decimal_places=4)


@login_required
@permission_required("estoque.add_movimento", raise_exception=True)
def enderecar(request):
    form = EnderecarForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            est.enderecar(d["produto"], d["quantidade"], d["armazem"], d["lote"] or None, d["de"], d["para"])
        except ValidationError as e:
            form.add_error(None, e)
        else:
            messages.success(request, "Endereço atualizado.")
            return redirect("estoque:saldolote_list")
    return render(request, "form_simples.html", {"form": form, "titulo": "Mudar endereço de estoque",
                                                 "icone": "geo-alt"})


@login_required
@permission_required("estoque.view_saldolote", raise_exception=True)
def lotes_a_vencer(request):
    try:
        dias = max(0, min(int(request.GET.get("dias", 30)), 3650))
    except ValueError:
        dias = 30
    qs = restringir_por_empresa(est.lotes_a_vencer(dias), est.SaldoLote, request.user, "armazem__empresa")
    return render(request, "estoque/lotes_a_vencer.html", {
        "titulo": "Lotes a vencer", "icone": "calendar-x", "linhas": qs, "dias": dias})
