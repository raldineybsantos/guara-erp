import mimetypes

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ValidationError
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.core.crud import restringir_por_empresa
from apps.core.models import Colaborador

from .models import (ConjuntoUniforme, EntregaUniforme, ItemUniforme, ModeloUniforme, TamanhoColaborador)


@login_required
def imagem(request, tipo, pk):
    """Imagem servida só a quem está logado (a pasta de mídia não é pública)."""
    obj = get_object_or_404(ModeloUniforme if tipo == "modelo" else ConjuntoUniforme, pk=pk)
    if not obj.imagem:
        raise Http404
    resposta = FileResponse(obj.imagem.open("rb"), content_type=mimetypes.guess_type(obj.imagem.name)[0] or "image/jpeg")
    resposta["X-Content-Type-Options"] = "nosniff"
    resposta["Content-Disposition"] = "inline"
    return resposta


@login_required
@permission_required("uniformes.view_modelouniforme", raise_exception=True)
def catalogo(request):
    q = request.GET.get("q", "").strip()
    categoria = request.GET.get("categoria", "")
    situacao = request.GET.get("situacao", "")
    qs = ModeloUniforme.objects.filter(ativo=True)
    if q:
        qs = qs.filter(nome__icontains=q) | qs.filter(codigo__icontains=q)
    if categoria:
        qs = qs.filter(categoria=categoria)
    cartoes = []
    for m in qs.distinct().order_by("nome"):
        painel = m.painel()
        geral = m.situacao_geral
        if situacao and geral != situacao:
            continue
        cartoes.append({"modelo": m, "painel": painel, "situacao": geral,
                        "total": sum(p["disponivel"] for p in painel)})
    return render(request, "uniformes/catalogo.html", {
        "cartoes": cartoes, "q": q, "categoria": categoria, "situacao": situacao,
        "categorias": ModeloUniforme.CATEGORIAS, "titulo": "Catálogo de uniformes", "icone": "grid"})


class EntregarForm(forms.Form):
    colaborador = forms.ModelChoiceField(Colaborador.objects.none(), label="Colaborador")
    data = forms.DateField(label="Data da entrega", initial=timezone.localdate, widget=forms.DateInput(attrs={"type": "date"}))
    observacoes = forms.CharField(label="Observações", max_length=300, required=False)

    def __init__(self, *a, user=None, **kw):
        super().__init__(*a, **kw)
        qs = Colaborador.objects.exclude(status="desligado").order_by("nome")
        self.fields["colaborador"].queryset = restringir_por_empresa(qs, Colaborador, user) if user else qs.none()


def _entrega_visivel(request, pk, select=()):
    qs = restringir_por_empresa(EntregaUniforme.objects.all(), EntregaUniforme, request.user, "colaborador__empresa")
    return get_object_or_404(qs.select_related(*select), pk=pk)


def _sugerir(colab, modelo, itens):
    """Item do tamanho cadastrado para o colaborador (se houver)."""
    if modelo.numeracao == "unico":
        return next(iter(itens), None)
    t = TamanhoColaborador.objects.filter(colaborador=colab, numeracao=modelo.numeracao).first()
    return next((i for i in itens if t and i.tamanho == t.tamanho), None)


@login_required
@permission_required("uniformes.add_entregauniforme", raise_exception=True)
def entregar(request, pk=None):
    conjunto = get_object_or_404(ConjuntoUniforme, pk=pk) if pk else None
    pecas = list(conjunto.pecas.select_related("modelo")) if conjunto else []
    if conjunto is None:
        modelos = ModeloUniforme.objects.filter(ativo=True).order_by("nome")
        pecas = [type("P", (), {"modelo": m, "quantidade": 0, "pk": m.pk})() for m in modelos]
    form = EntregarForm(request.POST or None, user=request.user, initial={"colaborador": request.GET.get("colaborador")})
    escolhas = []
    for p in pecas:
        itens = p.modelo.itens_ordenados
        disp = {r["item"].pk: r["disponivel"] for r in p.modelo.painel()}
        sugerido = None
        colab_id = request.POST.get("colaborador") or request.GET.get("colaborador")
        if colab_id and str(colab_id).isdigit():
            colab = form.fields["colaborador"].queryset.filter(pk=colab_id).first()
            sugerido = _sugerir(colab, p.modelo, itens) if colab else None
        escolhas.append({"peca": p, "itens": [(i, disp.get(i.pk, 0)) for i in itens], "sugerido": sugerido,
                         "campo": f"item_{p.modelo.pk}", "qtd": f"qtd_{p.modelo.pk}"})
    if request.method == "POST" and form.is_valid():
        linhas = []
        for e in escolhas:
            item_id, qtd = request.POST.get(e["campo"]), request.POST.get(e["qtd"], "0")
            if item_id and qtd.isdigit() and int(qtd) > 0:
                item = ItemUniforme.objects.filter(pk=item_id, modelo=e["peca"].modelo).first()
                if item:
                    linhas.append((item, int(qtd)))
        try:
            entrega = EntregaUniforme.registrar(form.cleaned_data["colaborador"], linhas, conjunto=conjunto,
                                                data=form.cleaned_data["data"], observacoes=form.cleaned_data["observacoes"])
            entrega.criado_por = request.user
            entrega.save(update_fields=["criado_por"])
        except ValidationError as err:
            form.add_error(None, err)
        else:
            messages.success(request, "Entrega registrada e estoque baixado.")
            return redirect("uniformes:entregauniforme_detail", pk=entrega.pk)
    return render(request, "uniformes/entregar.html", {
        "form": form, "conjunto": conjunto, "escolhas": escolhas, "titulo": "Entregar uniforme", "icone": "box-arrow-right",
        "post": request.POST})


@login_required
@permission_required("uniformes.view_entregauniforme", raise_exception=True)
def entrega_termo(request, pk):
    entrega = _entrega_visivel(request, pk, ("colaborador",))
    return render(request, "uniformes/termo.html", {"entrega": entrega, "itens": entrega.itens.select_related("item__modelo"),
                                                    "hoje": timezone.localdate()})


@login_required
@permission_required("uniformes.change_entregauniforme", raise_exception=True)
def entrega_assinada(request, pk):
    entrega = _entrega_visivel(request, pk)
    if request.method == "POST" and entrega.status == "entregue":
        entrega.recebido_confirmado = True
        entrega.save(update_fields=["recebido_confirmado", "atualizado_em"])
        messages.success(request, "Termo marcado como assinado.")
    return redirect("uniformes:entregauniforme_detail", pk=pk)


@login_required
@permission_required("uniformes.change_entregauniforme", raise_exception=True)
def entrega_estornar(request, pk):
    entrega = _entrega_visivel(request, pk)
    if request.method == "POST":
        try:
            entrega.estornar()
            messages.success(request, "Entrega estornada: as peças voltaram ao estoque.")
        except ValidationError as err:
            messages.error(request, "; ".join(err.messages))
        return redirect("uniformes:entregauniforme_detail", pk=pk)
    return render(request, "uniformes/confirmar_estorno.html", {"entrega": entrega, "titulo": "Estornar entrega"})
