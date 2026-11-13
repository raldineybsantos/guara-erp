from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from apps.core.crud import BootstrapMixin
from apps.core.models import Fornecedor
from apps.core.pdf_simples import gerar_pdf
from apps.fiscal.models import NotaFiscal

from .models import Cotacao, PedidoCompra, RecebimentoCompra, SolicitacaoCompra


def _executar(request, obj, func, ok, destino=None):
    try:
        resultado = func()
        messages.success(request, ok)
        return redirect(destino(resultado) if destino and resultado else obj.get_absolute_url())
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
        return redirect(obj.get_absolute_url())


# --------------------------------------------------------------------------- solicitação → cotação
class GerarCotacaoForm(BootstrapMixin, forms.Form):
    fornecedores = forms.ModelMultipleChoiceField(
        queryset=Fornecedor.objects.filter(ativo=True), widget=forms.SelectMultiple(attrs={"size": 8}),
        help_text="Segure Ctrl para escolher vários. Cada um recebe uma linha por item para informar preço e prazo.")
    prazo_resposta = forms.DateField(label="prazo de resposta", required=False,
                                     widget=forms.DateInput(attrs={"type": "date"}))


@login_required
@permission_required("compras.add_cotacao", raise_exception=True)
def gerar_cotacao(request, pk):
    sc = get_object_or_404(SolicitacaoCompra, pk=pk)
    form = GerarCotacaoForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            cot = sc.gerar_cotacao(list(d["fornecedores"]), d["prazo_resposta"])
        except ValidationError as e:
            messages.error(request, " ".join(e.messages))
            return redirect(sc.get_absolute_url())
        messages.success(request, f"{cot} criada. Preencha os preços e prazos de cada fornecedor.")
        return redirect("compras:cotacao_update", cot.pk)
    return render(request, "form_simples.html", {"form": form, "titulo": f"Gerar cotação da {sc}",
                                                 "icone": "clipboard-data"})


@login_required
@permission_required("compras.add_pedidocompra", raise_exception=True)
def mapa_cotacao(request, pk):
    cot = get_object_or_404(Cotacao, pk=pk)
    if request.method == "POST":
        forn = get_object_or_404(Fornecedor, pk=request.POST.get("fornecedor"))
        return _executar(request, cot, lambda: cot.escolher(forn, request.user),
                         f"Pedido gerado para {forn}. Revise e envie para aprovação.",
                         destino=lambda pedido: pedido.get_absolute_url())
    return render(request, "compras/mapa.html", {"titulo": f"Mapa de cotação {cot}", "cotacao": cot,
                                                 "mapa": cot.mapa()})


# --------------------------------------------------------------------------- pedido
def _pedido(pk):
    return get_object_or_404(PedidoCompra.objects.select_related("fornecedor"), pk=pk)


@login_required
@permission_required("compras.change_pedidocompra", raise_exception=True)
def enviar_pedido(request, pk):
    p = _pedido(pk)
    if request.method != "POST":
        return render(request, "confirmar.html", {
            "titulo": "Enviar para aprovação", "voltar": p.get_absolute_url(),
            "mensagem": f"Enviar o pedido {p} (R$ {p.valor_total}) para aprovação conforme a alçada de compras?"})
    return _executar(request, p, lambda: p.enviar_para_aprovacao(request.user),
                     "Pedido enviado. Pedidos dentro da alçada automática já ficam aprovados.")


class CancelarForm(BootstrapMixin, forms.Form):
    motivo = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}))


@login_required
@permission_required("compras.change_pedidocompra", raise_exception=True)
def cancelar_pedido(request, pk):
    p = _pedido(pk)
    form = CancelarForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        return _executar(request, p, lambda: p.cancelar(form.cleaned_data["motivo"]), "Pedido cancelado.")
    return render(request, "form_simples.html", {"form": form, "titulo": f"Cancelar pedido {p}", "icone": "x-octagon"})


class ReceberForm(BootstrapMixin, forms.Form):
    data = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    nota_fiscal = forms.ModelChoiceField(
        queryset=NotaFiscal.objects.none(), required=False, label="nota fiscal de entrada",
        help_text="Importe o XML em Fiscal → Importar XML NF-e. Sem nota, a conferência fica pendente.")
    observacoes = forms.CharField(label="observações", required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, pedido, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.pedido = pedido
        self.fields["nota_fiscal"].queryset = NotaFiscal.objects.filter(
            tipo="entrada", fornecedor_id=pedido.fornecedor_id).order_by("-data_emissao")
        for item in pedido.itens.all():
            if item.saldo > 0:
                self.fields[f"item_{item.pk}"] = forms.DecimalField(
                    label=f"{item} (pedido {item.quantidade:g}, saldo {item.saldo:g})", min_value=0,
                    max_value=item.saldo, initial=item.saldo, decimal_places=4,
                    widget=forms.NumberInput(attrs={"class": "form-control", "step": "any"}))

    def quantidades(self):
        return {k[5:]: v for k, v in self.cleaned_data.items() if k.startswith("item_") and v}


@login_required
@permission_required("compras.add_recebimentocompra", raise_exception=True)
def receber_pedido(request, pk):
    p = _pedido(pk)
    if p.status not in ("aprovado", "parcial"):
        messages.error(request, "Só pedidos aprovados podem ser recebidos.")
        return redirect(p.get_absolute_url())
    from django.utils import timezone

    form = ReceberForm(p, request.POST or None, initial={"data": timezone.localdate()})
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            rec = p.receber(form.quantidades(), d["nota_fiscal"], d["data"], d["observacoes"], request.user)
        except ValidationError as e:
            messages.error(request, " ".join(e.messages))
        else:
            _avisar_conferencia(request, rec)
            return redirect(rec.get_absolute_url())
    return render(request, "form_simples.html", {"form": form, "titulo": f"Receber pedido {p} — {p.fornecedor}",
                                                 "icone": "box-arrow-in-down"})


def _avisar_conferencia(request, rec):
    if rec.conferencia == "ok":
        messages.success(request, "Conferência em três vias OK: a conta a pagar foi liberada para pagamento.")
    elif rec.conferencia == "divergente":
        messages.warning(request, "Divergência entre pedido, recebimento e nota. A conta a pagar ficou bloqueada "
                                  "e uma tarefa foi aberta para o setor de Compras.")
    else:
        messages.info(request, "Recebimento registrado. Vincule a nota fiscal para conferir e liberar o pagamento.")


@login_required
@permission_required("compras.change_recebimentocompra", raise_exception=True)
def conferir_recebimento(request, pk):
    rec = get_object_or_404(RecebimentoCompra, pk=pk)
    if request.method != "POST":
        return render(request, "confirmar.html", {
            "titulo": "Conferir em três vias", "voltar": rec.get_absolute_url(),
            "mensagem": "Comparar de novo pedido, recebimento e nota fiscal? Use depois de corrigir a nota ou o pedido."})
    rec.conferir()
    _avisar_conferencia(request, rec)
    return redirect(rec.get_absolute_url())


@login_required
@permission_required("compras.view_pedidocompra", raise_exception=True)
def pdf_pedido(request, pk):
    p = _pedido(pk)
    f = p.fornecedor
    linhas = [
        f"Fornecedor: {f.nome}  CNPJ/CPF: {f.documento_formatado}",
        f"Data: {p.data_pedido:%d/%m/%Y}   Entrega prevista: "
        f"{p.previsao_entrega.strftime('%d/%m/%Y') if p.previsao_entrega else 'a combinar'}",
        f"Pagamento: {p.condicao_pagamento} dias   Situação: {p.get_status_display()}",
        "",
        "Itens:",
    ]
    for i, item in enumerate(p.itens.all(), start=1):
        linhas.append(f"{i:>2}. {item} - {item.quantidade:g} x R$ {item.valor_unitario:.2f} = R$ {item.total:.2f}")
    linhas += ["", f"Total dos itens: R$ {p.valor_itens:.2f}", f"Frete: R$ {p.valor_frete:.2f}",
               f"VALOR TOTAL: R$ {p.valor_total:.2f}"]
    if p.observacoes:
        linhas += ["", "Observações:", *p.observacoes.splitlines()]
    if p.status not in ("aprovado", "parcial", "recebido"):
        linhas += ["", "ATENÇÃO: pedido ainda não aprovado - não vale como ordem de compra."]
    resp = HttpResponse(gerar_pdf(f"PEDIDO DE COMPRA {p.numero}", linhas), content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="{p.numero}.pdf"'
    return resp

