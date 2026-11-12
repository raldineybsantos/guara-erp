import datetime
from collections import OrderedDict
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.db.models.functions import TruncMonth
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .forms import BaixaForm, TransferenciaForm
from .models import ContaBancaria, ContaPagar, ContaReceber, Movimentacao

MODELOS = {"pagar": ContaPagar, "receber": ContaReceber}


def _modelo(tipo):
    return MODELOS[tipo]


@login_required
def baixar(request, tipo, pk):
    modelo = _modelo(tipo)
    if not request.user.has_perm(f"financeiro.baixar_{modelo._meta.model_name}"):
        messages.error(request, "Você não tem permissão para baixar títulos.")
        return redirect(f"financeiro:{modelo._meta.model_name}_detail", pk)
    titulo = get_object_or_404(modelo, pk=pk)
    form = BaixaForm(request.POST or None, initial={
        "valor": titulo.saldo, "conta_bancaria": titulo.conta_bancaria,
        "forma_pagamento": titulo.forma_pagamento,
    })
    if request.method == "POST" and form.is_valid():
        try:
            titulo.baixar(**form.cleaned_data)
            messages.success(request, "Baixa registrada com sucesso.")
            return redirect(titulo.get_absolute_url())
        except ValidationError as e:
            form.add_error(None, e)
    return render(request, "financeiro/baixa.html", {
        "form": form, "titulo_obj": titulo, "tipo": tipo,
        "titulo": "Pagar título" if tipo == "pagar" else "Receber título",
    })


@login_required
def estornar(request, tipo, pk):
    modelo = _modelo(tipo)
    titulo = get_object_or_404(modelo, pk=pk)
    if not request.user.has_perm(f"financeiro.baixar_{modelo._meta.model_name}"):
        messages.error(request, "Sem permissão.")
    elif request.method == "POST":
        titulo.estornar()
        messages.success(request, "Pagamentos estornados; título voltou a ficar em aberto.")
    else:
        return render(request, "confirmar.html", {"titulo": "Estornar pagamentos",
                                                  "mensagem": f"Estornar todas as baixas de “{titulo}”?",
                                                  "voltar": titulo.get_absolute_url()})
    return redirect(titulo.get_absolute_url())


@login_required
def cancelar(request, tipo, pk):
    modelo = _modelo(tipo)
    titulo = get_object_or_404(modelo, pk=pk)
    if not request.user.has_perm(f"financeiro.change_{modelo._meta.model_name}"):
        messages.error(request, "Sem permissão.")
    elif request.method == "POST":
        try:
            titulo.cancelar()
            messages.success(request, "Título cancelado.")
        except ValidationError as e:
            messages.error(request, e.messages[0])
    else:
        return render(request, "confirmar.html", {"titulo": "Cancelar título",
                                                  "mensagem": f"Cancelar “{titulo}”?",
                                                  "voltar": titulo.get_absolute_url()})
    return redirect(titulo.get_absolute_url())


@login_required
@permission_required("financeiro.add_movimentacao", raise_exception=True)
def transferencia(request):
    form = TransferenciaForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        desc = d["descricao"] or f"Transferência {d['origem']} → {d['destino']}"
        with transaction.atomic():
            Movimentacao.objects.create(conta_bancaria=d["origem"], tipo="saida", valor=d["valor"],
                                        data=d["data"], descricao=desc, empresa=d["origem"].empresa)
            Movimentacao.objects.create(conta_bancaria=d["destino"], tipo="entrada", valor=d["valor"],
                                        data=d["data"], descricao=desc, empresa=d["destino"].empresa)
        messages.success(request, "Transferência registrada.")
        return redirect("financeiro:movimentacao_list")
    return render(request, "form_simples.html", {"form": form, "titulo": "Transferência entre contas",
                                                 "icone": "arrow-left-right"})


def _mes_label(d):
    nomes = "jan fev mar abr mai jun jul ago set out nov dez".split()
    return f"{nomes[d.month - 1]}/{d:%y}"


def fluxo_caixa_dados(meses_passados=6, meses_futuros=3):
    """Realizado (movimentações) e previsto (títulos em aberto) por mês."""
    hoje = timezone.localdate()
    m = hoje.month - 1 - (meses_passados - 1)
    inicio = datetime.date(hoje.year + m // 12, m % 12 + 1, 1)
    meses = OrderedDict()
    d = inicio
    for _ in range(meses_passados + meses_futuros):
        meses[d] = {"inicio": d, "label": _mes_label(d), "entradas": Decimal(0), "saidas": Decimal(0),
                    "a_receber": Decimal(0), "a_pagar": Decimal(0)}
        d = (d + datetime.timedelta(days=32)).replace(day=1)
    fim = d

    for row in (Movimentacao.objects.filter(data__gte=inicio, data__lt=fim)
                .annotate(m=TruncMonth("data")).values("m", "tipo").annotate(t=Sum("valor"))):
        chave = row["m"] if isinstance(row["m"], datetime.date) else row["m"].date()
        if chave in meses:
            meses[chave]["entradas" if row["tipo"] == "entrada" else "saidas"] += row["t"]

    for modelo, campo in ((ContaReceber, "a_receber"), (ContaPagar, "a_pagar")):
        for t in modelo.objects.em_aberto().filter(vencimento__lt=fim):
            chave = max(t.vencimento, hoje).replace(day=1)  # vencidos entram no mês atual
            if chave in meses:
                meses[chave][campo] += t.saldo
    return list(meses.values())


@login_required
@permission_required("financeiro.view_movimentacao", raise_exception=True)
def fluxo_caixa(request):
    dados = fluxo_caixa_dados()
    saldo_bancos = sum((c.saldo_atual for c in ContaBancaria.objects.filter(ativa=True)), Decimal(0))
    acumulado = saldo_bancos
    hoje_mes = timezone.localdate().replace(day=1)
    for m in dados:
        m["resultado"] = m["entradas"] - m["saidas"]
        m["previsto"] = m["a_receber"] - m["a_pagar"]
        # projeção de saldo: só do mês atual em diante (vencidos entram no mês atual)
        if m["inicio"] >= hoje_mes:
            acumulado += m["previsto"]
            m["saldo_projetado"] = acumulado
        else:
            m["saldo_projetado"] = None
    return render(request, "financeiro/fluxo_caixa.html", {
        "titulo": "Fluxo de caixa", "icone": "graph-up", "meses": dados, "saldo_bancos": saldo_bancos,
        "contas": ContaBancaria.objects.filter(ativa=True), "hoje_mes": hoje_mes,
    })
