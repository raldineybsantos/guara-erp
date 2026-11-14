from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.core import acoes
from apps.core.crud import BootstrapMixin
from apps.financeiro.models import ContaBancaria, ContaPagar, ContaReceber, PlanoConta

from . import ofx
from .models import Bordero, Extrato, LinhaExtrato, processar_retorno


# --------------------------------------------------------------------------- conciliação
class ImportarForm(BootstrapMixin, forms.Form):
    conta_bancaria = forms.ModelChoiceField(label="conta bancária", queryset=ContaBancaria.objects.filter(ativa=True))
    arquivo = forms.FileField(label="extrato OFX", help_text="No internet banking, exporte o extrato em OFX (ou "
                                                             "“Money”). Pode importar períodos que se sobrepõem: "
                                                             "o que já entrou é ignorado.")


@login_required
@permission_required("bancario.add_extrato", raise_exception=True)
def importar_extrato(request):
    form = ImportarForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        arq = form.cleaned_data["arquivo"]
        try:
            ext = Extrato.importar(form.cleaned_data["conta_bancaria"], arq.name, ofx.ler(arq.read()))
        except ValidationError as e:
            form.add_error("arquivo", e)
        else:
            total, pend = ext.linhas.count(), ext.pendentes()
            messages.success(request, f"{total} lançamento(s) importado(s): {total - pend} conciliado(s) "
                                      f"automaticamente, {pend} para você resolver.")
            return redirect(ext.get_absolute_url())
    return render(request, "form_simples.html", {"form": form, "titulo": "Importar extrato (OFX)",
                                                 "icone": "upload", "multipart": True})


@login_required
@permission_required("bancario.change_extrato", raise_exception=True)
@require_POST
def resolver_linha(request, pk):
    linha = get_object_or_404(LinhaExtrato.objects.select_related("extrato"), pk=pk)
    acao = request.POST.get("acao")
    try:
        if acao == "titulo":
            tipo, _, tid = request.POST.get("titulo", "").partition(":")
            modelo = ContaReceber if linha.valor > 0 else ContaPagar
            if tipo != modelo._meta.model_name:
                raise ValidationError("Título incompatível com a linha.")
            linha.baixar_titulo(get_object_or_404(modelo, pk=tid))
            messages.success(request, "Título baixado e conciliado.")
        elif acao == "avulsa":
            cat = PlanoConta.objects.filter(pk=request.POST.get("plano_conta") or 0).first()
            linha.lancar_avulsa(request.POST.get("descricao", ""), cat)
            messages.success(request, "Movimentação lançada e conciliada.")
        elif acao == "ignorar":
            linha.ignorada = True
            linha.save(update_fields=["ignorada"])
        elif acao == "reconciliar":
            n = linha.extrato.conciliar()
            messages.success(request, f"{n} linha(s) conciliada(s).")
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
    return redirect(linha.extrato.get_absolute_url())


# --------------------------------------------------------------------------- borderô e CNAB
@login_required
@permission_required("bancario.change_bordero", raise_exception=True)
def bordero_titulos(request, pk):
    b = get_object_or_404(Bordero, pk=pk)
    if request.method == "POST":
        try:
            b.adicionar(list(ContaPagar.objects.filter(pk__in=request.POST.getlist("contas"))))
            messages.success(request, "Títulos adicionados.")
        except ValidationError as e:
            messages.error(request, " ".join(e.messages))
        return redirect(b.get_absolute_url())
    return render(request, "bancario/bordero_titulos.html", {
        "titulo": f"Adicionar títulos ao {b}", "icone": "plus-circle", "bordero": b,
        "contas": Bordero.elegiveis()[:300]})


@login_required
@permission_required("bancario.gerar_remessa_bordero", raise_exception=True)
def bordero_remessa(request, pk):
    b = get_object_or_404(Bordero, pk=pk)
    if request.method != "POST":
        return render(request, "confirmar.html", {
            "titulo": f"Gerar remessa do {b}", "voltar": b.get_absolute_url(),
            "mensagem": f"Gerar o arquivo CNAB 240 com {b.itens.count()} título(s), total de R$ {b.total}, para "
                        f"pagamento em {b.data_pagamento:%d/%m/%Y}? Envie o arquivo pelo internet banking da "
                        "empresa. Os títulos só são baixados quando o retorno do banco confirmar o pagamento."})
    try:
        texto = b.gerar_remessa(request.user)
    except ValidationError as e:
        for m in e.messages:
            messages.error(request, m)
        return redirect(b.get_absolute_url())
    resp = HttpResponse(texto.encode("ascii"), content_type="text/plain; charset=ascii")
    resp["Content-Disposition"] = f'attachment; filename="{b.numero}.rem"'
    return resp


@login_required
@permission_required("bancario.change_bordero", raise_exception=True)
def bordero_cancelar(request, pk):
    b = get_object_or_404(Bordero, pk=pk)
    return acoes.confirmar(request, b, f"Cancelar {b}", "Cancelar o borderô e liberar os títulos? Se a remessa já "
                           "foi enviada ao banco, cancele o lote lá também.", b.cancelar, "Borderô cancelado.")


class RetornoForm(BootstrapMixin, forms.Form):
    arquivo = forms.FileField(label="arquivo de retorno (.ret)")


@login_required
@permission_required("bancario.change_bordero", raise_exception=True)
def importar_retorno(request):
    form = RetornoForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        arq = form.cleaned_data["arquivo"]
        try:
            if arq.size > 5 * 1024 * 1024:
                raise ValidationError("Arquivo maior que 5 MB.")
            r = processar_retorno(arq.read().decode("latin-1"))
        except ValidationError as e:
            form.add_error("arquivo", e)
        else:
            messages.success(request, f"Retorno processado: {r['pagos']} pago(s), {r['agendados']} agendado(s), "
                                      f"{r['rejeitados']} rejeitado(s), {r['ignorados']} ignorado(s).")
            for d in r["detalhes"]:
                messages.warning(request, d)
            return redirect("bancario:bordero_list")
    return render(request, "form_simples.html", {"form": form, "titulo": "Importar retorno do banco (CNAB 240)",
                                                 "icone": "upload", "multipart": True})
