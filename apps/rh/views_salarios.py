from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render

from apps.core.crud import BootstrapMixin
from apps.core.models import Cargo, Departamento, Empresa

from .salarios import MovimentoSalarial, aprovar_lote, posicao_na_faixa, reajuste_coletivo


class ParecerForm(BootstrapMixin, forms.Form):
    parecer = forms.CharField(label="Parecer", max_length=300, required=False, widget=forms.Textarea(attrs={"rows": 3}))


def _decidir(request, pk, titulo, icone, func, ok, obrigatorio=False):
    m = get_object_or_404(MovimentoSalarial.objects.select_related("colaborador"), pk=pk)
    form = ParecerForm(request.POST or None)
    if obrigatorio:
        form.fields["parecer"].required = True
    if request.method == "POST" and form.is_valid():
        try:
            func(m, form.cleaned_data["parecer"])
            messages.success(request, ok)
            return redirect(m.get_absolute_url())
        except ValidationError as e:
            messages.error(request, " ".join(e.messages))
    aviso = f"{m.colaborador}: {m.colaborador.salario} → {m.salario_novo} (vigência {m.data_efetiva:%d/%m/%Y})."
    return render(request, "form_simples.html", {"form": form, "titulo": f"{titulo} — {m.colaborador}", "icone": icone,
                                                 "aviso": aviso})


@login_required
@permission_required("rh.aprovar_salarios", raise_exception=True)
def movimento_aprovar(request, pk):
    return _decidir(request, pk, "Aprovar movimentação", "check2-circle",
                    lambda m, p: m.aprovar(request.user, p), "Movimentação aprovada.")


@login_required
@permission_required("rh.aprovar_salarios", raise_exception=True)
def movimento_rejeitar(request, pk):
    return _decidir(request, pk, "Rejeitar movimentação", "x-circle",
                    lambda m, p: m.rejeitar(request.user, p), "Movimentação rejeitada.", obrigatorio=True)


@login_required
@permission_required("rh.gerir_salarios", raise_exception=True)
def movimento_cancelar(request, pk):
    m = get_object_or_404(MovimentoSalarial, pk=pk)
    if request.method != "POST":
        return render(request, "confirmar.html", {"titulo": "Cancelar movimentação", "voltar": m.get_absolute_url(),
                                                  "mensagem": f"Cancelar a movimentação de {m.colaborador}?"})
    try:
        m.cancelar()
        messages.success(request, "Movimentação cancelada.")
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
    return redirect(m.get_absolute_url())


class ReajusteForm(BootstrapMixin, forms.Form):
    percentual = forms.DecimalField(label="Percentual (%)", max_digits=6, decimal_places=2, localize=True)
    data_efetiva = forms.DateField(label="Vigência", widget=forms.DateInput(attrs={"type": "date"}))
    motivo = forms.CharField(label="Justificativa (convenção coletiva, política…)", max_length=300)
    empresa = forms.ModelChoiceField(Empresa.objects.all(), required=False)
    cargo = forms.ModelChoiceField(Cargo.objects.all(), required=False)
    departamento = forms.ModelChoiceField(Departamento.objects.all(), required=False)


@login_required
@permission_required("rh.gerir_salarios", raise_exception=True)
def reajuste_coletivo_view(request):
    form = ReajusteForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            lote, n = reajuste_coletivo(d["percentual"], d["data_efetiva"], d["motivo"], d["empresa"], d["cargo"],
                                        d["departamento"], request.user)
            messages.success(request, f"Lote {lote}: {n} movimentação(ões) criada(s) aguardando aprovação.")
            return redirect(f"{_lista_url()}?q={lote}")
        except ValidationError as e:
            messages.error(request, " ".join(e.messages))
    return render(request, "form_simples.html", {
        "form": form, "titulo": "Reajuste coletivo (dissídio)", "icone": "people",
        "aviso": "Cria uma movimentação pendente para cada colaborador ativo do recorte. Deixe empresa, cargo e departamento "
                 "em branco para reajustar todos. A aprovação é feita por outra pessoa."})


def _lista_url():
    from django.urls import reverse
    return reverse("rh:movimentosalarial_list")


class LoteForm(BootstrapMixin, forms.Form):
    lote = forms.CharField(label="Lote", max_length=40)
    parecer = forms.CharField(label="Parecer", max_length=300, required=False)


@login_required
@permission_required("rh.aprovar_salarios", raise_exception=True)
def aprovar_lote_view(request):
    form = LoteForm(request.POST or None, initial={"lote": request.GET.get("lote", "")})
    if request.method == "POST" and form.is_valid():
        ok, bad = aprovar_lote(form.cleaned_data["lote"], request.user, form.cleaned_data["parecer"])
        if ok:
            messages.success(request, f"{ok} movimentação(ões) aprovada(s).")
        if bad:
            messages.error(request, f"{bad} não puderam ser aprovadas (por exemplo, você foi quem as solicitou).")
        if not ok and not bad:
            messages.warning(request, "Nenhuma pendência encontrada nesse lote.")
        return redirect(_lista_url())
    return render(request, "form_simples.html", {"form": form, "titulo": "Aprovar lote de reajuste", "icone": "check2-all"})


@login_required
@permission_required("rh.view_faixasalarial", raise_exception=True)
def posicao_faixa(request):
    linhas = posicao_na_faixa()
    sit = request.GET.get("situacao")
    if sit:
        linhas = [x for x in linhas if x["situacao"] == sit]
    return render(request, "rh/posicao_faixa.html", {"titulo": "Posição na faixa salarial", "linhas": linhas,
                                                     "situacao": sit or ""})
