from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.core.crud import BootstrapMixin
from apps.core.models import Colaborador

from .treinamentos import Participacao, Turma, pendencias
from .views import _meu_colaborador


def _turma(pk):
    return get_object_or_404(Turma, pk=pk)


def _executar(request, turma, func, ok):
    try:
        func()
        messages.success(request, ok)
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
    return redirect(turma.get_absolute_url())


class InscreverForm(BootstrapMixin, forms.Form):
    colaboradores = forms.ModelMultipleChoiceField(
        queryset=Colaborador.objects.none(), label="Colaboradores",
        widget=forms.SelectMultiple(attrs={"size": 12}), help_text="Segure Ctrl para marcar vários.")

    def __init__(self, *a, turma, **kw):
        super().__init__(*a, **kw)
        ja = turma.ativas().values_list("colaborador_id", flat=True)
        qs = Colaborador.objects.exclude(status="desligado").exclude(pk__in=list(ja)).order_by("nome")
        if turma.empresa_id:
            qs = qs.filter(empresa_id=turma.empresa_id)
        self.fields["colaboradores"].queryset = qs


@login_required
@permission_required("rh.gerir_treinamento", raise_exception=True)
def turma_inscrever(request, pk):
    turma = _turma(pk)
    form = InscreverForm(request.POST or None, turma=turma)
    if request.method == "POST" and form.is_valid():
        try:
            n = turma.inscrever(list(form.cleaned_data["colaboradores"]))
            messages.success(request, f"{n} colaborador(es) inscrito(s).")
            return redirect(turma.get_absolute_url())
        except ValidationError as e:
            messages.error(request, " ".join(e.messages))
    vagas = turma.vagas_livres
    return render(request, "form_simples.html", {
        "form": form, "titulo": f"Inscrever em {turma}", "icone": "person-plus",
        "aviso": None if vagas is None else f"Vagas livres: {vagas}."})


def _confirmar(request, pk, titulo, mensagem, func, ok):
    turma = _turma(pk)
    if request.method != "POST":
        return render(request, "confirmar.html", {"titulo": titulo, "voltar": turma.get_absolute_url(),
                                                  "mensagem": mensagem.format(t=turma)})
    return _executar(request, turma, lambda: func(turma), ok)


@login_required
@permission_required("rh.gerir_treinamento", raise_exception=True)
def turma_iniciar(request, pk):
    return _confirmar(request, pk, "Iniciar turma", "Iniciar a turma {t}? Depois disso não há novas inscrições.",
                      lambda t: t.iniciar(), "Turma iniciada.")


@login_required
@permission_required("rh.gerir_treinamento", raise_exception=True)
def turma_concluir(request, pk):
    return _confirmar(request, pk, "Concluir turma",
                      "Concluir a turma {t}? O resultado de cada inscrito é apurado pela frequência e pela nota "
                      "mínimas do curso, e quem não teve frequência lançada fica como ausente.",
                      lambda t: t.concluir(), "Turma concluída e resultados apurados.")


@login_required
@permission_required("rh.gerir_treinamento", raise_exception=True)
def turma_cancelar(request, pk):
    return _confirmar(request, pk, "Cancelar turma", "Cancelar a turma {t} e todas as inscrições?",
                      lambda t: t.cancelar(), "Turma cancelada.")


@login_required
@permission_required("rh.gerir_treinamento", raise_exception=True)
def turma_resultados(request, pk):
    turma = _turma(pk)
    linhas = list(turma.participacoes.filter(resultado="inscrito").select_related("colaborador"))
    if request.method == "POST":
        erros, salvos = [], 0
        for p in linhas:
            freq = (request.POST.get(f"freq_{p.pk}") or "").strip()
            nota = (request.POST.get(f"nota_{p.pk}") or "").strip().replace(",", ".")
            if not freq and not nota:
                continue
            try:
                p.registrar(int(freq) if freq else None, forms.DecimalField().clean(nota) if nota else None)
                salvos += 1
            except (ValidationError, ValueError) as e:
                erros.append(f"{p.colaborador}: " + (" ".join(e.messages) if hasattr(e, "messages") else "valor inválido"))
        for e in erros:
            messages.error(request, e)
        if salvos:
            messages.success(request, f"Resultado de {salvos} participante(s) salvo.")
        if not erros:
            return redirect(turma.get_absolute_url())
        linhas = list(turma.participacoes.filter(resultado="inscrito").select_related("colaborador"))
    return render(request, "rh/turma_resultados.html", {"titulo": f"Frequência e nota — {turma}", "turma": turma,
                                                        "linhas": linhas})


@login_required
@permission_required("rh.view_turma", raise_exception=True)
def pendencias_treinamento(request):
    itens = pendencias(timezone.localdate(), alerta_dias=int(request.GET.get("dias") or 30))
    situacao = request.GET.get("situacao")
    if situacao:
        itens = [i for i in itens if i["situacao"] == situacao]
    return render(request, "rh/pendencias_treinamento.html", {"titulo": "Pendências de treinamento", "itens": itens,
                                                              "situacao": situacao or ""})


@login_required
def meus_treinamentos(request):
    colab = _meu_colaborador(request)
    if colab is None:
        messages.error(request, "Seu usuário não está vinculado a um colaborador. Peça ao RH para vincular.")
        return redirect("dashboard")
    hoje = timezone.localdate()
    participacoes = Participacao.objects.filter(colaborador=colab).exclude(resultado="cancelado").select_related(
        "turma", "turma__curso")
    minhas = [i for i in pendencias(hoje, 60) if i["colaborador"].pk == colab.pk]
    return render(request, "rh/meus_treinamentos.html", {"titulo": "Meus treinamentos", "participacoes": participacoes,
                                                         "pendencias": minhas})
