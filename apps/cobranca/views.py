from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.shortcuts import get_object_or_404, redirect, render

from apps.financeiro.models import ContaReceber

from .models import Cobranca, ReguaCobranca


@login_required
@permission_required("cobranca.add_cobranca", raise_exception=True)
def gerar_cobranca(request, pk):
    """Cria uma cobrança (PIX por padrão) a partir de uma conta a receber."""
    titulo = get_object_or_404(ContaReceber, pk=pk)
    if titulo.status in ("pago", "cancelado"):
        messages.error(request, "Título já está pago ou cancelado.")
        return redirect(titulo.get_absolute_url())
    if request.method != "POST":
        return render(request, "confirmar.html", {
            "titulo": "Gerar cobrança", "voltar": titulo.get_absolute_url(),
            "mensagem": f"Gerar cobrança PIX de {titulo.saldo} para “{titulo}”?",
        })
    tipo = request.POST.get("tipo", "pix") if request.POST.get("tipo") in dict(Cobranca.TIPOS) else "pix"
    cob = Cobranca.objects.create(conta_receber=titulo, tipo=tipo, valor=titulo.saldo, vencimento=titulo.vencimento)
    if tipo == "pix" and not cob.pix_copia_cola:
        messages.warning(request, "Cadastre a chave PIX da empresa para gerar o PIX copia-e-cola automaticamente.")
    messages.success(request, "Cobrança gerada.")
    return redirect(cob.get_absolute_url())


@login_required
@permission_required("cobranca.change_cobranca", raise_exception=True)
def enviar_cobranca(request, pk):
    cob = get_object_or_404(Cobranca, pk=pk)
    if request.method != "POST":
        return render(request, "confirmar.html", {
            "titulo": "Enviar cobrança", "voltar": cob.get_absolute_url(),
            "mensagem": f"Enviar a cobrança para {cob.email_destino or '(cliente sem e-mail)'}?",
        })
    if cob.enviar():
        messages.success(request, f"Cobrança enviada para {cob.email_destino}.")
    else:
        erro = cob.historico.first().erro
        messages.error(request, f"Falha no envio: {erro}")
    return redirect(cob.get_absolute_url())


@login_required
@permission_required("cobranca.change_cobranca", raise_exception=True)
def executar_regua(request):
    if request.method == "POST":
        n = ReguaCobranca.executar()
        messages.success(request, f"Régua executada: {n} cobrança(s) enviada(s).")
        return redirect("cobranca:historicocobranca_list")
    return render(request, "confirmar.html", {
        "titulo": "Executar régua de cobrança", "voltar": "/",
        "mensagem": "Enviar agora todas as cobranças previstas pela régua para hoje? "
                    "(Em produção, agende o comando `python manage.py executar_regua` diariamente.)",
    })
