from decimal import Decimal

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.core.crud import BootstrapMixin

from .models import Caucao, Contrato, Evento, Medicao, Paralisacao
from .processos import Andamento, Envolvido, Prazo, Processo


class RenovarForm(BootstrapMixin, forms.Form):
    meses = forms.IntegerField(label="Renovar por (meses)", min_value=1, max_value=120)
    percentual_reajuste = forms.DecimalField(label="Reajuste (%)", max_digits=6, decimal_places=2, initial=0,
                                             min_value=Decimal("-50"), max_value=Decimal("200"))
    descricao = forms.CharField(label="Observação", widget=forms.Textarea, required=False)


class EncerrarForm(BootstrapMixin, forms.Form):
    rescisao = forms.TypedChoiceField(label="Tipo", choices=[("0", "Encerramento (fim do contrato)"),
                                                             ("1", "Rescisão antecipada")], coerce=lambda v: v == "1")
    motivo = forms.CharField(label="Motivo", widget=forms.Textarea)


class AssinaturaManualForm(BootstrapMixin, forms.Form):
    data = forms.DateField(label="Data da assinatura", initial=timezone.localdate)
    observacao = forms.CharField(label="Como foi assinado", max_length=300,
                                 initial="Assinado em papel / plataforma externa")


def _contrato(pk):
    return get_object_or_404(Contrato, pk=pk)


def _executar(request, contrato, func, ok):
    try:
        func()
        messages.success(request, ok)
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
    return redirect(contrato.get_absolute_url())


@login_required
@permission_required("juridico.aprovar_contrato", raise_exception=True)
def enviar_aprovacao(request, pk):
    c = _contrato(pk)
    if request.method != "POST":
        return render(request, "confirmar.html", {"titulo": "Enviar para aprovação", "voltar": c.get_absolute_url(),
                                                  "mensagem": f"Enviar o contrato {c} para o fluxo de aprovação (jurídico → diretoria)?"})
    if not c.documento_id:
        messages.warning(request, "Atenção: o contrato foi enviado sem arquivo anexado.")
    return _executar(request, c, lambda: c.enviar_para_aprovacao(request.user), "Contrato enviado para aprovação.")


@login_required
@permission_required("juridico.aprovar_contrato", raise_exception=True)
def conferir_assinaturas(request, pk):
    c = _contrato(pk)
    if request.method != "POST":
        return redirect(c.get_absolute_url())
    if c.conferir_assinaturas(request.user):
        messages.success(request, "Assinaturas conferidas: o contrato agora está vigente.")
    else:
        n = len(c.assinaturas_validas())
        messages.info(request, f"O documento tem {n} assinatura(s) digital(is) válida(s); são necessárias "
                               f"{c.assinaturas_necessarias}. O contrato precisa estar aprovado.")
    return redirect(c.get_absolute_url())


@login_required
@permission_required("juridico.aprovar_contrato", raise_exception=True)
def assinatura_manual(request, pk):
    c = _contrato(pk)
    form = AssinaturaManualForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        return _executar(request, c, lambda: c.registrar_assinatura(request.user, d["data"], d["observacao"]),
                         "Assinatura registrada: contrato vigente.")
    return render(request, "form_simples.html", {"form": form, "titulo": f"Registrar assinatura — {c.numero}",
                                                 "icone": "pen"})


@login_required
@permission_required("juridico.change_contrato", raise_exception=True)
def renovar(request, pk):
    c = _contrato(pk)
    form = RenovarForm(request.POST or None, initial={"meses": c.prazo_renovacao_meses})
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        return _executar(request, c, lambda: c.renovar(request.user, d["meses"], d["percentual_reajuste"], d["descricao"]),
                         "Contrato renovado.")
    return render(request, "form_simples.html", {"form": form, "titulo": f"Renovar contrato {c.numero}",
                                                 "icone": "arrow-repeat"})


@login_required
@permission_required("juridico.change_contrato", raise_exception=True)
def encerrar(request, pk):
    c = _contrato(pk)
    form = EncerrarForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        return _executar(request, c, lambda: c.encerrar(request.user, d["motivo"], d["rescisao"]), "Contrato encerrado.")
    return render(request, "form_simples.html", {"form": form, "titulo": f"Encerrar contrato {c.numero}",
                                                 "icone": "x-octagon"})


class ReajusteForm(BootstrapMixin, forms.Form):
    percentual = forms.DecimalField(label="Reajuste (%)", max_digits=7, decimal_places=2,
                                    min_value=Decimal("-50"), max_value=Decimal("200"))
    descricao = forms.CharField(label="Observação", widget=forms.Textarea, required=False)


@login_required
@permission_required("juridico.change_contrato", raise_exception=True)
def reajustar(request, pk):
    c = _contrato(pk)
    inicial, ajuda = {}, ""
    try:
        inicial["percentual"] = c.sugestao_reajuste()
        ajuda = f"Sugestão: {inicial['percentual']}% ({c.get_indice_reajuste_display()} acumulado em 12 meses)."
    except ValidationError as e:
        ajuda = " ".join(e.messages)
    form = ReajusteForm(request.POST or None, initial=inicial)
    form.fields["percentual"].help_text = ajuda
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        return _executar(request, c, lambda: c.reajustar(request.user, d["percentual"], d["descricao"]), "Reajuste aplicado.")
    return render(request, "form_simples.html", {"form": form, "titulo": f"Reajustar contrato {c.numero}", "icone": "graph-up-arrow"})


def _acao_post(request, obj, titulo, mensagem, func, ok, voltar):
    if request.method != "POST":
        return render(request, "confirmar.html", {"titulo": titulo, "voltar": voltar, "mensagem": mensagem})
    try:
        func()
        messages.success(request, ok)
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
    return redirect(voltar)


@login_required
@permission_required("juridico.aprovar_contrato", raise_exception=True)
def medicao_aprovar(request, pk):
    m = get_object_or_404(Medicao, pk=pk)
    return _acao_post(request, m, "Aprovar medição", f"Aprovar a medição {m} (líquido {m.valor_liquido})?",
                      lambda: m.aprovar(request.user), "Medição aprovada.", m.get_absolute_url())


@login_required
@permission_required("juridico.change_contrato", raise_exception=True)
def medicao_faturar(request, pk):
    m = get_object_or_404(Medicao, pk=pk)
    return _acao_post(request, m, "Gerar título", f"Gerar o título financeiro da medição {m} (líquido {m.valor_liquido})?",
                      lambda: m.gerar_titulo(request.user), "Título gerado no financeiro.", m.get_absolute_url())


@login_required
@permission_required("juridico.change_contrato", raise_exception=True)
def medicao_cancelar(request, pk):
    m = get_object_or_404(Medicao, pk=pk)
    return _acao_post(request, m, "Cancelar medição", f"Cancelar a medição {m}? O título gerado também será cancelado.",
                      lambda: m.cancelar(request.user), "Medição cancelada.", m.get_absolute_url())


@login_required
@permission_required("juridico.change_contrato", raise_exception=True)
def caucao_devolver(request, pk):
    g = get_object_or_404(Caucao, pk=pk)
    return _acao_post(request, g, "Devolver garantia", f"Marcar a garantia {g} como devolvida/liberada?",
                      lambda: g.liberar(request.user), "Garantia devolvida.", g.get_absolute_url())


@login_required
@permission_required("juridico.change_contrato", raise_exception=True)
def caucao_executar(request, pk):
    g = get_object_or_404(Caucao, pk=pk)
    return _acao_post(request, g, "Executar garantia", f"Registrar a execução da garantia {g}?",
                      lambda: g.liberar(request.user, executar=True), "Garantia executada.", g.get_absolute_url())


class RetomadaForm(BootstrapMixin, forms.Form):
    fim = forms.DateField(label="Último dia parado", initial=timezone.localdate)


@login_required
@permission_required("juridico.change_contrato", raise_exception=True)
def paralisacao_retomar(request, pk):
    p = get_object_or_404(Paralisacao, pk=pk)
    form = RetomadaForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        return _executar(request, p.contrato, lambda: p.retomar(request.user, form.cleaned_data["fim"]), "Contrato retomado.")
    return render(request, "form_simples.html", {"form": form, "titulo": f"Retomar {p.contrato.numero}", "icone": "play-circle"})


@login_required
@permission_required("juridico.view_contrato", raise_exception=True)
def painel(request):
    hoje = timezone.localdate()
    vigentes = Contrato.objects.filter(status="vigente")
    a_vencer = [c for c in vigentes.filter(fim_vigencia__gte=hoje).select_related("fornecedor", "cliente")
                if c.dias_para_vencer is not None and c.dias_para_vencer <= c.alerta_dias]
    return render(request, "juridico/painel.html", {
        "titulo": "Painel jurídico", "icone": "bank2",
        "vigentes": vigentes.count(), "em_aprovacao": Contrato.objects.filter(status="em_aprovacao"),
        "aguardando": Contrato.objects.filter(status="aprovado"), "a_vencer": a_vencer,
        "vencidos": vigentes.filter(fim_vigencia__lt=hoje), "eventos": Evento.objects.select_related("contrato", "usuario")[:15],
    })


# --------------------------------------------------------------------------- contencioso
def _processo(pk):
    return get_object_or_404(Processo, pk=pk)


class _FilhoForm(BootstrapMixin, forms.ModelForm):
    pass


def _form_filho(modelo, campos):
    Meta = type("Meta", (), {"model": modelo, "fields": campos})
    return type(f"{modelo.__name__}Form", (_FilhoForm,), {"Meta": Meta})


def _criar_filho(request, pk, modelo, campos, titulo, icone, depois=None):
    proc = _processo(pk)
    Form = _form_filho(modelo, campos)
    form = Form(request.POST or None)
    if "data" in form.fields:
        form.fields["data"].widget = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
    if "data_fatal" in form.fields:
        form.fields["data_fatal"].widget = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        obj.processo = proc
        if hasattr(obj, "registrado_por"):
            obj.registrado_por = request.user
        obj.save()
        if depois:
            depois(proc, obj, request.user)
        messages.success(request, "Registrado.")
        return redirect(proc.get_absolute_url())
    return render(request, "form_simples.html", {"form": form, "titulo": f"{titulo} — {proc.numero}", "icone": icone})


@login_required
@permission_required("juridico.add_andamento", raise_exception=True)
def processo_andamento(request, pk):
    return _criar_filho(request, pk, Andamento, ["data", "tipo", "descricao"], "Registrar andamento", "journal-plus")


@login_required
@permission_required("juridico.add_prazo", raise_exception=True)
def processo_prazo(request, pk):
    return _criar_filho(request, pk, Prazo, ["descricao", "data_fatal", "alertar_dias", "responsavel", "observacoes"],
                        "Novo prazo", "alarm",
                        lambda proc, obj, u: proc.registrar(f"Prazo aberto: {obj}", "intimacao", None, u))


@login_required
@permission_required("juridico.add_envolvido", raise_exception=True)
def processo_envolvido(request, pk):
    return _criar_filho(request, pk, Envolvido, ["nome", "papel", "documento", "contato"], "Adicionar envolvido",
                        "person-plus")


class RiscoForm(BootstrapMixin, forms.Form):
    risco = forms.ChoiceField(choices=Processo.RISCOS, label="Risco de perda")
    valor_estimado = forms.DecimalField(label="Perda estimada (R$)", min_value=0, max_digits=14, decimal_places=2,
                                        help_text="Só o risco provável gera provisão no razão.")
    motivo = forms.CharField(required=False, max_length=300)


class EncerrarProcessoForm(BootstrapMixin, forms.Form):
    resultado = forms.ChoiceField(choices=Processo.RESULTADOS)
    data = forms.DateField(initial=timezone.localdate, widget=forms.DateInput(attrs={"type": "date"}))
    valor_condenacao = forms.DecimalField(label="Valor da condenação/acordo a pagar (R$)", required=False,
                                          min_value=0, max_digits=14, decimal_places=2,
                                          help_text="Vira conta a pagar (vence em 15 dias). Vazio se nada a pagar.")
    observacao = forms.CharField(required=False, max_length=300)


@login_required
@permission_required("juridico.change_processo", raise_exception=True)
def processo_risco(request, pk):
    proc = _processo(pk)
    form = RiscoForm(request.POST or None, initial={"risco": proc.risco, "valor_estimado": proc.valor_risco})
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        return _executar(request, proc, lambda: proc.avaliar_risco(d["risco"], d["valor_estimado"], request.user,
                                                                   motivo=d["motivo"]), "Risco reavaliado.")
    return render(request, "form_simples.html", {"form": form, "titulo": f"Reavaliar risco — {proc.numero}",
                                                 "icone": "speedometer"})


@login_required
@permission_required("juridico.change_processo", raise_exception=True)
def processo_encerrar(request, pk):
    proc = _processo(pk)
    form = EncerrarProcessoForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        return _executar(request, proc, lambda: proc.encerrar(d["resultado"], request.user, d["data"],
                                                              d["valor_condenacao"] or 0, d["observacao"]),
                         "Processo encerrado.")
    return render(request, "form_simples.html", {"form": form, "titulo": f"Encerrar processo {proc.numero}",
                                                 "icone": "check2-circle"})


@login_required
@permission_required("juridico.change_processo", raise_exception=True)
def processo_suspender(request, pk):
    proc = _processo(pk)
    if request.method != "POST":
        return render(request, "confirmar.html", {"titulo": "Suspender processo", "voltar": proc.get_absolute_url(),
                                                  "mensagem": f"Suspender o processo {proc.numero}?"})
    return _executar(request, proc, lambda: proc.suspender(request.user), "Processo suspenso.")


@login_required
@permission_required("juridico.change_processo", raise_exception=True)
def processo_retomar(request, pk):
    proc = _processo(pk)
    if request.method != "POST":
        return render(request, "confirmar.html", {"titulo": "Retomar processo", "voltar": proc.get_absolute_url(),
                                                  "mensagem": f"Retomar o processo {proc.numero}?"})
    return _executar(request, proc, lambda: proc.retomar(request.user), "Processo retomado.")


@login_required
@permission_required("juridico.change_prazo", raise_exception=True)
def prazo_acao(request, pk, acao):
    prazo = get_object_or_404(Prazo, pk=pk)
    if request.method != "POST":
        return render(request, "confirmar.html", {"titulo": "Confirmar", "voltar": prazo.get_absolute_url(),
                                                  "mensagem": f"Confirmar a ação sobre o prazo “{prazo}”?"})
    try:
        getattr(prazo, acao)(request.user)
        messages.success(request, "Prazo atualizado.")
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
    return redirect(prazo.processo.get_absolute_url())


@login_required
@permission_required("juridico.view_processo", raise_exception=True)
def contencioso(request):
    from django.db.models import Count, Sum

    ativos = Processo.objects.exclude(status="encerrado")
    por_risco = ativos.values("risco").annotate(n=Count("pk"), perda=Sum("valor_risco"), prov=Sum("provisao"))
    nome_risco = dict(Processo.RISCOS)
    riscos = [{"risco": nome_risco[r["risco"]], "n": r["n"], "perda": r["perda"] or 0, "prov": r["prov"] or 0}
              for r in por_risco]
    hoje = timezone.localdate()
    prazos = Prazo.objects.filter(status="pendente", processo__status="andamento").select_related("processo") \
        .order_by("data_fatal")[:15]
    return render(request, "juridico/contencioso.html", {
        "titulo": "Contencioso", "icone": "bank", "total": ativos.count(), "riscos": riscos, "prazos": prazos,
        "hoje": hoje, "provisao_total": ativos.aggregate(t=Sum("provisao"))["t"] or 0,
        "natureza": [{"nome": dict(Processo.NATUREZAS)[x["natureza"]], "n": x["n"]}
                     for x in ativos.values("natureza").annotate(n=Count("pk")).order_by("-n")]})
