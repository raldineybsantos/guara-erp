from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render

from apps.core.crud import BootstrapMixin

from .desempenho import AcaoDesenvolvimento, Avaliacao, CicloAvaliacao, Competencia
from .models import Curso
from .views import _meu_colaborador

PERM = "rh.gerir_avaliacao"


def _ciclo(pk):
    return get_object_or_404(CicloAvaliacao, pk=pk)


def _confirmar(request, pk, titulo, mensagem, func, ok):
    ciclo = _ciclo(pk)
    if request.method != "POST":
        return render(request, "confirmar.html", {"titulo": titulo, "voltar": ciclo.get_absolute_url(),
                                                  "mensagem": mensagem.format(c=ciclo)})
    try:
        r = func(ciclo)
        messages.success(request, ok.format(r=r))
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
    return redirect(ciclo.get_absolute_url())


@login_required
@permission_required(PERM, raise_exception=True)
def ciclo_abrir(request, pk):
    return _confirmar(request, pk, "Abrir ciclo",
                      "Abrir o ciclo {c}? Será criada uma avaliação para cada colaborador ativo admitido até o início.",
                      lambda c: c.abrir(), "Ciclo aberto com {r} avaliação(ões).")


@login_required
@permission_required(PERM, raise_exception=True)
def ciclo_calibrar(request, pk):
    """Inicia a calibração (fecha as respostas) e, com o ciclo em calibração, ajusta as notas finais."""
    ciclo = _ciclo(pk)
    if ciclo.status == "aberto":
        return _confirmar(request, pk, "Iniciar calibração",
                          "Iniciar a calibração do ciclo {c}? As respostas deixam de ser aceitas.",
                          lambda c: c.iniciar_calibracao(), "Ciclo em calibração.")
    if ciclo.status != "calibracao":
        messages.error(request, "Só se calibra um ciclo aberto ou em calibração.")
        return redirect(ciclo.get_absolute_url())
    avs = list(ciclo.avaliacoes.filter(status="concluida").select_related("colaborador"))
    if request.method == "POST":
        erros, salvos = [], 0
        for a in avs:
            nota = (request.POST.get(f"nota_{a.pk}") or "").strip().replace(",", ".")
            if not nota:
                continue
            try:
                n = forms.DecimalField().clean(nota)
                if a.nota_efetiva is not None and n == a.nota_efetiva:
                    continue
                a.calibrar(n, request.POST.get(f"motivo_{a.pk}", ""), request.user)
                salvos += 1
            except ValidationError as e:
                erros.append(f"{a.colaborador}: " + " ".join(e.messages))
        for e in erros:
            messages.error(request, e)
        if salvos:
            messages.success(request, f"{salvos} nota(s) calibrada(s).")
        if not erros:
            return redirect(ciclo.get_absolute_url())
        avs = list(ciclo.avaliacoes.filter(status="concluida").select_related("colaborador"))
    return render(request, "rh/ciclo_calibrar.html", {"titulo": f"Calibração — {ciclo}", "ciclo": ciclo, "avaliacoes": avs})


@login_required
@permission_required(PERM, raise_exception=True)
def ciclo_encerrar(request, pk):
    return _confirmar(request, pk, "Encerrar ciclo",
                      "Encerrar o ciclo {c}? Avaliações sem as duas partes ficam incompletas e não geram nota.",
                      lambda c: c.encerrar(), "Ciclo encerrado.")


def _responder(request, pk, tipo):
    av = get_object_or_404(Avaliacao.objects.select_related("ciclo", "ciclo__modelo", "colaborador"), pk=pk)
    if not av.pode_responder(request.user, tipo):
        raise PermissionDenied
    itens = list(av.ciclo.modelo.itens.select_related("competencia"))
    escala = av.ciclo.modelo.escala_max
    if request.method == "POST":
        try:
            notas = {it.pk: int(request.POST.get(f"nota_{it.pk}")) for it in itens if request.POST.get(f"nota_{it.pk}")}
        except ValueError:
            notas = {}
        coment = {it.pk: request.POST.get(f"obs_{it.pk}", "") for it in itens}
        try:
            av.responder(tipo, notas, request.POST.get("comentario", ""), request.user, coment)
            messages.success(request, "Avaliação enviada.")
            return redirect("rh:minhas_avaliacoes")
        except ValidationError as e:
            messages.error(request, " ".join(e.messages))
    titulo = "Autoavaliação" if tipo == "auto" else f"Avaliação de {av.colaborador}"
    return render(request, "rh/avaliacao_responder.html", {
        "titulo": f"{titulo} — {av.ciclo}", "av": av, "itens": itens, "escala": range(1, escala + 1), "tipo": tipo,
        "post": request.POST})


@login_required
def avaliacao_responder_auto(request, pk):
    return _responder(request, pk, "auto")


@login_required
def avaliacao_responder_gestor(request, pk):
    return _responder(request, pk, "gestor")


@login_required
def minhas_avaliacoes(request):
    colab = _meu_colaborador(request)
    if colab is None:
        messages.error(request, "Seu usuário não está vinculado a um colaborador. Peça ao RH para vincular.")
        return redirect("dashboard")
    abertas = Avaliacao.objects.filter(ciclo__status="aberto").select_related("ciclo", "colaborador")
    minhas = abertas.filter(colaborador=colab, auto_enviada_em__isnull=True)
    a_fazer = abertas.filter(gestor=colab)
    concluidas = (Avaliacao.objects.filter(colaborador=colab, ciclo__status="encerrado", status="concluida")
                  .select_related("ciclo", "ciclo__modelo").prefetch_related("acoes"))
    return render(request, "rh/minhas_avaliacoes.html", {
        "titulo": "Minhas avaliações", "minhas": minhas.filter(ciclo__inclui_auto=True),
        "a_fazer": a_fazer.filter(gestor_enviada_em__isnull=True),
        "concluidas": concluidas})


class AcaoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = AcaoDesenvolvimento
        fields = ["descricao", "competencia", "curso", "prazo"]
        widgets = {"prazo": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")}


@login_required
@permission_required(PERM, raise_exception=True)
def avaliacao_pdi(request, pk):
    av = get_object_or_404(Avaliacao, pk=pk)
    form = AcaoForm(request.POST or None)
    form.fields["competencia"].queryset = Competencia.objects.filter(ativo=True)
    form.fields["curso"].queryset = Curso.objects.filter(ativo=True)
    if request.method == "POST" and form.is_valid():
        a = form.save(commit=False)
        a.avaliacao = av
        a.save()
        messages.success(request, "Ação adicionada ao PDI.")
        return redirect(av.get_absolute_url())
    return render(request, "form_simples.html", {"form": form, "titulo": f"PDI — {av.colaborador}", "icone": "plus-circle"})


def _acao(request, pk, func, ok):
    a = get_object_or_404(AcaoDesenvolvimento, pk=pk)
    if request.method != "POST":
        return render(request, "confirmar.html", {"titulo": "Confirmar", "voltar": a.avaliacao.get_absolute_url(),
                                                  "mensagem": f"{ok} “{a.descricao}”?"})
    try:
        func(a)
        messages.success(request, "Ação atualizada.")
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
    return redirect(a.avaliacao.get_absolute_url())


@login_required
@permission_required(PERM, raise_exception=True)
def pdi_concluir(request, pk):
    return _acao(request, pk, lambda a: a.concluir(), "Concluir a ação")


@login_required
@permission_required(PERM, raise_exception=True)
def pdi_cancelar(request, pk):
    return _acao(request, pk, lambda a: a.cancelar(), "Cancelar a ação")
