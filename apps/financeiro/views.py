import datetime
from collections import OrderedDict
from decimal import Decimal

from django.contrib import messages
from django.forms import formset_factory
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q as models_q, Sum
from django.db.models.functions import TruncMonth
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.core.crud import restringir_por_empresa

from . import aging
from .forms import BaixaForm, CompensacaoForm, RenegociacaoForm, RateioLinhaForm, RecolhimentoForm, RetencaoForm, TransferenciaForm
from .models import (ApuracaoPDD, Compensacao, ContaBancaria, ContaPagar, Renegociacao, ContaReceber, GuiaTributo, Movimentacao, Retencao,
                     definir_rateio, partes_por_centro)

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
        "retencao_prevista": (titulo.saldo - titulo.saldo_liquido) if tipo == "pagar" else 0,
        "titulo": "Pagar título" if tipo == "pagar" else "Receber título",
    })


@login_required
def estornar(request, tipo, pk):
    modelo = _modelo(tipo)
    titulo = get_object_or_404(modelo, pk=pk)
    if not request.user.has_perm(f"financeiro.baixar_{modelo._meta.model_name}"):
        messages.error(request, "Sem permissão.")
    elif request.method == "POST":
        try:
            titulo.estornar()
            messages.success(request, "Pagamentos estornados; título voltou a ficar em aberto.")
        except ValidationError as e:
            messages.error(request, e.messages[0])
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
                                        data=d["data"], descricao=desc, empresa=d["origem"].empresa,
                                        transferencia=True)
            Movimentacao.objects.create(conta_bancaria=d["destino"], tipo="entrada", valor=d["valor"],
                                        data=d["data"], descricao=desc, empresa=d["destino"].empresa,
                                        transferencia=True)
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


# --------------------------------------------------------------------------- tributos retidos
@login_required
def calcular_retencoes(request, pk):
    titulo = get_object_or_404(ContaPagar, pk=pk)
    url = titulo.get_absolute_url()
    if not request.user.has_perm("financeiro.change_contapagar"):
        messages.error(request, "Você não tem permissão para alterar este título.")
        return redirect(url)
    if titulo.valor_pago > 0 or titulo.status in ("pago", "cancelado"):
        messages.error(request, "As retenções só podem ser definidas antes do primeiro pagamento.")
        return redirect(url)
    atuais = {r.regra_id: r for r in titulo.retencoes.all()}
    form = RetencaoForm(request.POST or None, initial={"base": titulo.valor_total, "regras": list(atuais)})
    if request.method == "POST" and form.is_valid():
        base, regras = form.cleaned_data["base"], form.cleaned_data["regras"]
        if base > titulo.valor_total:
            form.add_error("base", "A base não pode ser maior que o valor do título.")
        else:
            with transaction.atomic():
                titulo.retencoes.all().delete()
                for regra in regras:
                    if base < regra.base_minima:
                        continue
                    valor = (base * regra.aliquota / 100).quantize(Decimal("0.01"))
                    if valor > 0:
                        Retencao.objects.create(conta_pagar=titulo, regra=regra, base=base,
                                                aliquota=regra.aliquota, valor=valor)
            n = titulo.retencoes.count()
            messages.success(request, f"{n} retenção(ões) registrada(s): total {titulo.retencoes_previstas}." if n
                             else "Nenhuma retenção aplicada a este título.")
            return redirect(url)
    return render(request, "financeiro/retencoes.html", {
        "form": form, "titulo_obj": titulo, "titulo": "Tributos retidos na fonte", "icone": "percent",
        "regras": form.fields["regras"].queryset})


@login_required
def recolher_guia(request, pk):
    guia = get_object_or_404(GuiaTributo, pk=pk)
    if not request.user.has_perm("financeiro.baixar_contapagar"):
        messages.error(request, "Você não tem permissão para pagar guias.")
        return redirect(guia.get_absolute_url())
    form = RecolhimentoForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            guia.recolher(**form.cleaned_data)
            messages.success(request, "Recolhimento registrado.")
            return redirect(guia.get_absolute_url())
        except ValidationError as e:
            form.add_error(None, e)
    return render(request, "form_simples.html", {"form": form, "titulo": f"Recolher {guia} — {guia.valor}",
                                                 "icone": "bank"})


@login_required
def estornar_guia(request, pk):
    guia = get_object_or_404(GuiaTributo, pk=pk)
    if not request.user.has_perm("financeiro.baixar_contapagar"):
        messages.error(request, "Sem permissão.")
    elif request.method == "POST":
        try:
            guia.estornar_recolhimento()
            messages.success(request, "Recolhimento estornado; a guia voltou a ficar a recolher.")
        except ValidationError as e:
            messages.error(request, e.messages[0])
    else:
        return render(request, "confirmar.html", {"titulo": "Estornar recolhimento",
                                                  "mensagem": f"Estornar o recolhimento de “{guia}”?",
                                                  "voltar": guia.get_absolute_url()})
    return redirect(guia.get_absolute_url())


# --------------------------------------------------------------------------- rateio por centro de custo
@login_required
def ratear(request, tipo, pk):
    modelo = _modelo(tipo)
    titulo = get_object_or_404(modelo, pk=pk)
    url = titulo.get_absolute_url()
    if not request.user.has_perm(f"financeiro.change_{modelo._meta.model_name}"):
        messages.error(request, "Você não tem permissão para alterar este título.")
        return redirect(url)
    FormSet = formset_factory(RateioLinhaForm, extra=0)
    existentes = [{"centro_custo": r.centro_custo, "percentual": r.percentual} for r in titulo.rateios.all()]
    if not existentes:
        existentes = [{"percentual": Decimal("100"), "centro_custo": titulo.centro_custo}]
    fs = FormSet(request.POST or None, initial=existentes + [{}, {}] if request.method != "POST" else None)
    if request.method == "POST" and fs.is_valid():
        linhas = [(f.cleaned_data.get("centro_custo"), f.cleaned_data.get("percentual"))
                  for f in fs if f.cleaned_data.get("centro_custo") and f.cleaned_data.get("percentual")]
        try:
            definir_rateio(titulo, linhas)
            messages.success(request, "Rateio salvo." if linhas else "Rateio removido.")
            return redirect(url)
        except ValidationError as e:
            messages.error(request, e.messages[0])
    return render(request, "financeiro/rateio.html", {"fs": fs, "titulo_obj": titulo,
                                                      "titulo": "Rateio por centro de custo", "icone": "diagram-3"})


@login_required
@permission_required("financeiro.view_centrocusto", raise_exception=True)
def relatorio_centro_custo(request):
    hoje = timezone.localdate()
    try:
        de = datetime.date.fromisoformat(request.GET.get("de", ""))
    except ValueError:
        de = hoje.replace(day=1)
    try:
        ate = datetime.date.fromisoformat(request.GET.get("ate", ""))
    except ValueError:
        ate = hoje
    tipo = request.GET.get("tipo") if request.GET.get("tipo") in MODELOS else "pagar"
    modelo = _modelo(tipo)
    qs = restringir_por_empresa(modelo.objects.exclude(status="cancelado"), modelo, request.user)
    qs = qs.filter(vencimento__gte=de, vencimento__lte=ate).select_related("centro_custo").prefetch_related(
        "rateios__centro_custo")
    linhas, total = OrderedDict(), Decimal("0")
    for t in qs:
        for cc, valor in partes_por_centro(t):
            chave = cc.pk if cc else 0
            item = linhas.setdefault(chave, {"centro": cc, "total": Decimal("0"), "titulos": 0})
            item["total"] += valor
            item["titulos"] += 1
            total += valor
    ordenadas = sorted(linhas.values(), key=lambda x: -x["total"])
    for x in ordenadas:
        x["pct"] = (x["total"] / total * 100).quantize(Decimal("0.1")) if total else Decimal("0")
    return render(request, "financeiro/relatorio_centro_custo.html", {
        "titulo": "Resultado por centro de custo", "icone": "bullseye", "linhas": ordenadas, "total": total,
        "de": de, "ate": ate, "tipo": tipo})


# --------------------------------------------------------------------------- compensação e renegociação
@login_required
def compensar(request, tipo, pk):
    modelo = _modelo(tipo)
    titulo = get_object_or_404(modelo, pk=pk)
    if not request.user.has_perm("financeiro.add_compensacao"):
        messages.error(request, "Você não tem permissão para compensar títulos.")
        return redirect(titulo.get_absolute_url())
    if titulo.status not in ("aberto", "parcial"):
        messages.error(request, "O título precisa estar em aberto.")
        return redirect(titulo.get_absolute_url())
    outro = ContaReceber if tipo == "pagar" else ContaPagar
    parceiro = titulo.fornecedor if tipo == "pagar" else titulo.cliente
    candidatos = outro.objects.none()
    if parceiro is not None:
        campo = "cliente" if tipo == "pagar" else "fornecedor"
        filtro = models_q(**{f"{campo}__parceironegocio_id": parceiro.parceironegocio_id})
        if parceiro.documento:
            filtro |= models_q(**{f"{campo}__documento": parceiro.documento})
        candidatos = restringir_por_empresa(outro.objects.em_aberto().filter(filtro, empresa_id=titulo.empresa_id),
                                            outro, request.user)
    form = CompensacaoForm(request.POST or None, titulo=titulo, contrapartes=candidatos)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        contra = d["contraparte"]
        pagar, receber = (titulo, contra) if tipo == "pagar" else (contra, titulo)
        try:
            comp = Compensacao.compensar(pagar, receber, d["valor"], d["data"], d["observacoes"])
            messages.success(request, f"Compensação nº {comp.pk} registrada.")
            return redirect(titulo.get_absolute_url())
        except ValidationError as e:
            form.add_error(None, e)
    elif request.method == "GET" and candidatos.exists():
        form.fields["valor"].initial = titulo.saldo
    return render(request, "financeiro/compensar.html", {
        "form": form, "titulo_obj": titulo, "tipo": tipo, "titulo": "Compensar com título do mesmo parceiro",
        "icone": "arrow-left-right", "sem_candidatos": not candidatos.exists()})


@login_required
@permission_required("financeiro.change_compensacao", raise_exception=True)
def estornar_compensacao(request, pk):
    comp = get_object_or_404(Compensacao, pk=pk)
    if request.method != "POST":
        return render(request, "confirmar.html", {"titulo": "Estornar compensação", "voltar": comp.get_absolute_url(),
                                                  "mensagem": f"Estornar a compensação nº {comp.pk}? Os dois títulos "
                                                              f"voltam a ter o valor compensado em aberto."})
    try:
        comp.estornar()
        messages.success(request, "Compensação estornada.")
    except ValidationError as e:
        messages.error(request, e.messages[0])
    return redirect(comp.get_absolute_url())


@login_required
def renegociar(request, pk):
    origem = get_object_or_404(ContaReceber, pk=pk)
    if not request.user.has_perm("financeiro.add_renegociacao"):
        messages.error(request, "Você não tem permissão para renegociar títulos.")
        return redirect(origem.get_absolute_url())
    if origem.cliente_id is None or origem.status not in ("aberto", "parcial"):
        messages.error(request, "O título precisa estar em aberto e ter cliente.")
        return redirect(origem.get_absolute_url())
    abertos = restringir_por_empresa(
        ContaReceber.objects.em_aberto().filter(cliente_id=origem.cliente_id, empresa_id=origem.empresa_id),
        ContaReceber, request.user)
    selecionados = [t.pk for t in abertos if t.vencido or t.pk == origem.pk]
    form = RenegociacaoForm(request.POST or None, abertos=abertos, initial={
        "titulos": selecionados, "primeiro_vencimento": timezone.localdate() + datetime.timedelta(days=30)})
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            reneg = Renegociacao.efetivar(
                list(d["titulos"]), d["primeiro_vencimento"], d["parcelas"], d["intervalo_dias"],
                d["acrescimo"] or 0, d["desconto"] or 0, observacoes=d["observacoes"])
            messages.success(request, f"Renegociação nº {reneg.pk} registrada: {reneg.parcelas} nova(s) parcela(s).")
            return redirect(reneg.get_absolute_url())
        except ValidationError as e:
            form.add_error(None, e)
    return render(request, "form_simples.html", {
        "form": form, "titulo": f"Renegociar títulos de {origem.cliente}", "icone": "arrow-repeat"})


@login_required
@permission_required("financeiro.change_renegociacao", raise_exception=True)
def desfazer_renegociacao(request, pk):
    reneg = get_object_or_404(Renegociacao, pk=pk)
    if request.method != "POST":
        return render(request, "confirmar.html", {
            "titulo": "Desfazer renegociação", "voltar": reneg.get_absolute_url(),
            "mensagem": f"Desfazer a renegociação nº {reneg.pk}? As novas parcelas serão canceladas e os títulos "
                        f"originais voltam a ficar em aberto."})
    try:
        reneg.desfazer()
        messages.success(request, "Renegociação desfeita.")
    except ValidationError as e:
        messages.error(request, e.messages[0])
    return redirect(reneg.get_absolute_url())


# --------------------------------------------------------------------------- aging e PDD
@login_required
def aging_view(request):
    tipo = request.GET.get("tipo") if request.GET.get("tipo") in MODELOS else "receber"
    modelo = _modelo(tipo)
    if not request.user.has_perm(f"financeiro.view_{modelo._meta.model_name}"):
        raise PermissionDenied
    try:
        data = datetime.date.fromisoformat(request.GET.get("data", ""))
    except ValueError:
        data = timezone.localdate()
    qs = restringir_por_empresa(modelo.objects.em_aberto(), modelo, request.user)
    qs = qs.select_related("cliente" if tipo == "receber" else "fornecedor")
    dados = aging.gerar(qs, data)
    return render(request, "financeiro/aging.html", {"titulo": "Aging de títulos em aberto", "icone": "hourglass-split",
                                                     "tipo": tipo, "dados": dados, "data": data})


@login_required
@permission_required("financeiro.add_apuracaopdd", raise_exception=True)
def apurar_pdd(request):
    try:
        data = datetime.date.fromisoformat(request.POST.get("data") or request.GET.get("data", ""))
    except ValueError:
        data = timezone.localdate()
    if request.method == "POST":
        try:
            ap = ApuracaoPDD.apurar(data, observacoes=request.POST.get("observacoes", ""))
            messages.success(request, "Apuração registrada." if ap.ajuste else
                             "Apuração registrada: a provisão já estava correta, sem lançamento.")
            return redirect(ap.get_absolute_url())
        except ValidationError as e:
            messages.error(request, e.messages[0])
    return render(request, "financeiro/apurar_pdd.html", {"titulo": "Apurar provisão para devedores duvidosos",
                                                          "icone": "shield-exclamation",
                                                          "calc": ApuracaoPDD.calcular(data), "data": data})


@login_required
@permission_required("financeiro.change_apuracaopdd", raise_exception=True)
def estornar_pdd(request, pk):
    ap = get_object_or_404(ApuracaoPDD, pk=pk)
    if request.method != "POST":
        return render(request, "confirmar.html", {"titulo": "Estornar apuração de PDD", "voltar": ap.get_absolute_url(),
                                                  "mensagem": f"Estornar a apuração de {ap.data:%d/%m/%Y}?"})
    try:
        ap.estornar()
        messages.success(request, "Apuração estornada.")
    except ValidationError as e:
        messages.error(request, e.messages[0])
    return redirect(ap.get_absolute_url())
