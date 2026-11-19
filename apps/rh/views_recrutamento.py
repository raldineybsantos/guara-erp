from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.core.crud import BootstrapMixin

from .recrutamento import Candidatura, Vaga

PERM = "rh.gerir_recrutamento"


def _vaga_confirmar(request, pk, titulo, mensagem, func, ok):
    v = get_object_or_404(Vaga, pk=pk)
    if request.method != "POST":
        return render(request, "confirmar.html", {"titulo": titulo, "voltar": v.get_absolute_url(),
                                                  "mensagem": mensagem.format(v=v)})
    try:
        func(v)
        messages.success(request, ok)
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
    return redirect(v.get_absolute_url())


@login_required
@permission_required(PERM, raise_exception=True)
def vaga_abrir(request, pk):
    return _vaga_confirmar(request, pk, "Abrir vaga", "Abrir a vaga {v} para candidaturas?", lambda v: v.abrir(),
                           "Vaga aberta.")


@login_required
@permission_required(PERM, raise_exception=True)
def vaga_pausar(request, pk):
    return _vaga_confirmar(request, pk, "Pausar vaga", "Pausar a vaga {v}? O processo fica parado até reabrir.",
                           lambda v: v.pausar(), "Vaga pausada.")


@login_required
@permission_required(PERM, raise_exception=True)
def vaga_cancelar(request, pk):
    return _vaga_confirmar(request, pk, "Cancelar vaga",
                           "Cancelar a vaga {v}? Os candidatos em andamento serão reprovados com o motivo “Vaga cancelada”.",
                           lambda v: v.cancelar(), "Vaga cancelada.")


@login_required
@permission_required("rh.view_vaga", raise_exception=True)
def vaga_funil(request, pk):
    v = get_object_or_404(Vaga, pk=pk)
    cands = list(v.candidaturas.select_related("candidato"))
    colunas = [{"codigo": cod, "nome": nome, "itens": [c for c in cands if c.etapa == cod]}
               for cod, nome in Candidatura.ETAPAS]
    return render(request, "rh/vaga_funil.html", {"titulo": f"Funil — {v}", "vaga": v, "colunas": colunas})


# ---------------------------------------------------------------- candidaturas
def _cand(pk):
    return get_object_or_404(Candidatura.objects.select_related("vaga", "candidato"), pk=pk)


def _fluxo(request, c, func, ok):
    try:
        func()
        messages.success(request, ok)
        return redirect(c.get_absolute_url())
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
        return None


class MoverForm(BootstrapMixin, forms.Form):
    etapa = forms.ChoiceField(choices=[(c, n) for c, n in Candidatura.ETAPAS if c in Candidatura.FUNIL and c != "proposta"])
    observacao = forms.CharField(label="Observação", required=False, max_length=300)


class MotivoForm(BootstrapMixin, forms.Form):
    motivo = forms.CharField(max_length=300, widget=forms.Textarea(attrs={"rows": 3}))


class PropostaForm(BootstrapMixin, forms.Form):
    salario = forms.DecimalField(label="Salário proposto", max_digits=12, decimal_places=2, min_value=0)
    inicio = forms.DateField(label="Início previsto", widget=forms.DateInput(attrs={"type": "date"}))


class ContratarForm(BootstrapMixin, forms.Form):
    cpf = forms.CharField(label="CPF do candidato", max_length=14, required=False,
                          help_text="Obrigatório se o candidato não informou o CPF no cadastro.")


def _form_view(request, pk, form_cls, titulo, icone, func, ok, aviso=None, initial=None):
    c = _cand(pk)
    form = form_cls(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        r = _fluxo(request, c, lambda: func(c, form.cleaned_data), ok)
        if r:
            return r
    return render(request, "form_simples.html", {"form": form, "titulo": f"{titulo} — {c}", "icone": icone,
                                                 "aviso": aviso})


@login_required
@permission_required(PERM, raise_exception=True)
def candidatura_mover(request, pk):
    return _form_view(request, pk, MoverForm, "Mover de etapa", "arrow-right-circle",
                      lambda c, d: c.mover(d["etapa"], request.user, d["observacao"]), "Candidato movido.")


@login_required
@permission_required(PERM, raise_exception=True)
def candidatura_reprovar(request, pk):
    return _form_view(request, pk, MotivoForm, "Reprovar", "x-octagon",
                      lambda c, d: c.reprovar(d["motivo"], request.user), "Candidato reprovado.")


@login_required
@permission_required(PERM, raise_exception=True)
def candidatura_desistir(request, pk):
    return _form_view(request, pk, MotivoForm, "Desistência", "box-arrow-left",
                      lambda c, d: c.reprovar(d["motivo"], request.user, desistencia=True), "Desistência registrada.")


@login_required
@permission_required(PERM, raise_exception=True)
def candidatura_proposta(request, pk):
    c = _cand(pk)
    v = c.vaga
    faixa = f"Faixa da vaga: {v.faixa_min} a {v.faixa_max}." if v.faixa_max else None
    return _form_view(request, pk, PropostaForm, "Enviar proposta", "envelope-paper",
                      lambda c, d: c.enviar_proposta(d["salario"], d["inicio"], request.user), "Proposta registrada.",
                      aviso=faixa, initial={"salario": c.candidato.pretensao or None, "inicio": timezone.localdate()})


def _confirmar_cand(request, pk, titulo, mensagem, func, ok):
    c = _cand(pk)
    if request.method != "POST":
        return render(request, "confirmar.html", {"titulo": titulo, "voltar": c.get_absolute_url(), "mensagem": mensagem})
    r = _fluxo(request, c, lambda: func(c), ok)
    return r or redirect(c.get_absolute_url())


@login_required
@permission_required(PERM, raise_exception=True)
def candidatura_aceite(request, pk):
    return _confirmar_cand(request, pk, "Registrar aceite", "O candidato aceitou a proposta?",
                           lambda c: c.responder_proposta(True, request.user), "Aceite registrado.")


@login_required
@permission_required(PERM, raise_exception=True)
def candidatura_recusa(request, pk):
    return _confirmar_cand(request, pk, "Registrar recusa",
                           "O candidato recusou a proposta? A candidatura será encerrada como desistência.",
                           lambda c: c.responder_proposta(False, request.user), "Recusa registrada.")


@login_required
@permission_required(PERM, raise_exception=True)
def candidatura_contratar(request, pk):
    c = _cand(pk)
    form = ContratarForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        r = _fluxo(request, c, lambda: c.contratar(form.cleaned_data["cpf"], request.user),
                   "Candidato contratado: o colaborador foi criado no cadastro.")
        if r:
            return r
    return render(request, "form_simples.html", {
        "form": form, "titulo": f"Contratar — {c}", "icone": "person-check",
        "aviso": "Será criado o colaborador com cargo, departamento, gestor e tipo de contrato da vaga, e o salário e o "
                 "início da proposta aceita."})
