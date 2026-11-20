"""Adiciona o papel de cliente a um fornecedor (ou o contrário) sem duplicar o cadastro."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from .models import Cliente, Fornecedor


def _tornar(request, origem, papel, rotulo):
    destino = Cliente if papel == "cliente" else Fornecedor
    existente = destino.objects.filter(parceironegocio_id=origem.parceironegocio_id).first()
    if existente:
        messages.info(request, f"{origem} já é {rotulo}.")
        return redirect(existente.get_absolute_url())
    if request.method == "POST":
        novo = origem.adicionar_papel(papel)
        messages.success(request, f"{origem} agora também é {rotulo}. Complete os dados do papel, se precisar.")
        return redirect("core:" + papel + "_update", novo.pk)
    return render(request, "confirmar.html", {
        "titulo": f"Tornar {rotulo}",
        "mensagem": (f"{origem} ({origem.documento_formatado or 'sem CPF/CNPJ'}) passará a ser também {rotulo}, "
                     "usando o mesmo cadastro de nome, documento, endereço e contato."),
        "voltar": origem.get_absolute_url(),
    })


@login_required
@permission_required("core.add_cliente", raise_exception=True)
def tornar_cliente(request, pk):
    return _tornar(request, get_object_or_404(Fornecedor, pk=pk), "cliente", "cliente")


@login_required
@permission_required("core.add_fornecedor", raise_exception=True)
def tornar_fornecedor(request, pk):
    return _tornar(request, get_object_or_404(Cliente, pk=pk), "fornecedor", "fornecedor")


@login_required
def consulta_cnpj(request):
    """GET ?cnpj=… → JSON com os dados públicos do CNPJ, para preencher o cadastro."""
    from .consulta import consultar_cnpj
    try:
        return JsonResponse(consultar_cnpj(request.GET.get("cnpj", "")))
    except ValidationError as e:
        return JsonResponse({"erro": " ".join(e.messages)}, status=400)


@login_required
def consulta_cep(request):
    """GET ?cep=… → JSON com o endereço, para preencher o cadastro."""
    from .consulta import consultar_cep
    try:
        return JsonResponse(consultar_cep(request.GET.get("cep", "")))
    except ValidationError as e:
        return JsonResponse({"erro": " ".join(e.messages)}, status=400)
