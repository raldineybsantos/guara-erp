import datetime

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.core.crud import BootstrapMixin

from .models import Comissao, PedidoVenda, TabelaPreco
from apps.core.formatacao import moeda


def _confirmar_e_executar(request, pk, titulo, mensagem, func, ok):
    p = get_object_or_404(PedidoVenda, pk=pk)
    if request.method != "POST":
        return render(request, "confirmar.html", {"titulo": titulo, "mensagem": mensagem.format(p=p, total=moeda(p.valor_total)),
                                                  "voltar": p.get_absolute_url()})
    try:
        resultado = func(p)
        messages.success(request, ok(p, resultado) if callable(ok) else ok)
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
    return redirect(p.get_absolute_url())


@login_required
@permission_required("vendas.change_pedidovenda", raise_exception=True)
def confirmar(request, pk):
    return _confirmar_e_executar(
        request, pk, "Confirmar pedido", "Conferir estoque e crédito do cliente para o pedido {p} ({total})?",
        lambda p: p.confirmar(),
        lambda p, ok: "Pedido aprovado." if ok else f"Pedido bloqueado por crédito. {p.motivo_status}")


@login_required
@permission_required("vendas.change_pedidovenda", raise_exception=True)
def faturar(request, pk):
    return _confirmar_e_executar(
        request, pk, "Faturar pedido",
        "Emitir a nota fiscal do pedido {p}, gerar as contas a receber e baixar o estoque?",
        lambda p: p.faturar(), lambda p, nota: f"Faturado: {nota}. Transmita a NF-e pelo seu emissor.")


class MotivoForm(BootstrapMixin, forms.Form):
    motivo = forms.CharField(label="justificativa", widget=forms.Textarea(attrs={"rows": 3}))


def _com_motivo(request, pk, titulo, func, ok):
    p = get_object_or_404(PedidoVenda, pk=pk)
    form = MotivoForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            func(p, form.cleaned_data["motivo"])
            messages.success(request, ok)
        except ValidationError as e:
            messages.error(request, " ".join(e.messages))
        return redirect(p.get_absolute_url())
    return render(request, "form_simples.html", {"form": form, "titulo": f"{titulo} — {p}", "icone": "pencil"})


@login_required
@permission_required("vendas.liberar_credito_pedidovenda", raise_exception=True)
def liberar_credito(request, pk):
    return _com_motivo(request, pk, "Liberar crédito", lambda p, m: p.liberar_credito(request.user, m),
                       "Crédito liberado: o pedido está aprovado.")


@login_required
@permission_required("vendas.change_pedidovenda", raise_exception=True)
def cancelar(request, pk):
    return _com_motivo(request, pk, "Cancelar pedido", lambda p, m: p.cancelar(m), "Pedido cancelado.")


class VencimentoForm(BootstrapMixin, forms.Form):
    vencimento = forms.DateField(label="vencimento da conta a pagar")


@login_required
@permission_required("vendas.pagar_comissao", raise_exception=True)
def lancar_comissao(request, pk):
    c = get_object_or_404(Comissao, pk=pk)
    form = VencimentoForm(request.POST or None,
                          initial={"vencimento": timezone.localdate() + datetime.timedelta(days=30)})
    if request.method == "POST" and form.is_valid():
        try:
            titulo = c.lancar_conta_pagar(form.cleaned_data["vencimento"])
            messages.success(request, f"Conta a pagar criada: {moeda(titulo.valor)}.")
            return redirect(c.get_absolute_url())
        except ValidationError as e:
            messages.error(request, " ".join(e.messages))
            return redirect(c.get_absolute_url())
    return render(request, "form_simples.html", {
        "form": form, "titulo": f"Lançar comissão — a lançar agora: {moeda(c.a_lancar)}", "icone": "cash-coin"})


@login_required
def preco_item(request):
    """GET ?produto=&cliente=&quantidade=&data= → preço sugerido (tabela do cliente ou cadastro do produto)."""
    from decimal import Decimal, InvalidOperation

    from django.http import JsonResponse

    from apps.core.models import Cliente
    from apps.fiscal.models import Produto

    produto = Produto.objects.filter(pk=request.GET.get("produto") or 0).first()
    if not produto:
        return JsonResponse({"erro": "Produto não encontrado."}, status=404)
    try:
        qtd = Decimal(request.GET.get("quantidade") or "1")
    except InvalidOperation:
        qtd = Decimal("1")
    try:
        data = datetime.date.fromisoformat(request.GET.get("data") or "")
    except ValueError:
        data = timezone.localdate()
    cliente = Cliente.objects.filter(pk=request.GET.get("cliente") or 0).first()
    preco, origem = produto.preco, "cadastro do produto"
    if cliente:
        tabela = TabelaPreco.para_cliente(cliente, data)
        p = tabela.preco_para(produto, qtd, data) if tabela else None
        if p is not None:
            preco, origem = p, f"tabela “{tabela}”"
    return JsonResponse({"preco": str(preco), "origem": origem})
