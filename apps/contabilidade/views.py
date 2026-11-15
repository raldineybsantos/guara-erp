import datetime
from decimal import Decimal

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.core import acoes
from apps.core.crud import BootstrapMixin
from apps.core.models import Empresa
from apps.financeiro.models import CentroCusto

from . import servicos
from .models import ContaContabil, Lancamento, Partida, PeriodoFechado

ZERO = Decimal("0")
VER = "contabilidade.view_lancamento"


class EmpresaFiltroMixin:
    """Campo opcional "empresa": vazio soma as empresas a que o usuário tem acesso."""

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["empresa"] = forms.ModelChoiceField(
            label="empresa / filial", required=False, empty_label="Todas que eu acesso",
            queryset=Empresa.acessiveis(user).order_by("-matriz", "razao_social"))
        self.order_fields(["empresa"] + [f for f in self.fields if f != "empresa"])

    def empresa(self):
        return self.cleaned_data.get("empresa") if self.is_bound and self.is_valid() else None


class PeriodoForm(EmpresaFiltroMixin, BootstrapMixin, forms.Form):
    inicio = forms.DateField(label="de")
    fim = forms.DateField(label="até")


def _periodo(request):
    """(formulário, início, fim, empresa escolhida ou None)."""
    hoje = timezone.localdate()
    form = PeriodoForm(request.GET or None, initial={"inicio": hoje.replace(day=1), "fim": hoje}, user=request.user)
    if form.is_bound and form.is_valid():
        return form, form.cleaned_data["inicio"], form.cleaned_data["fim"], form.empresa()
    return form, hoje.replace(day=1), hoje, None


def _arvore(valores):
    """Soma os valores das contas analíticas nas sintéticas acima delas. valores: {conta_id: [n1, n2, ...]}."""
    contas = list(ContaContabil.objects.all())
    por_id = {c.pk: c for c in contas}
    total = {c.pk: list(valores.get(c.pk, [])) or None for c in contas}
    for c in contas:
        v = valores.get(c.pk)
        pai = por_id.get(c.superior_id)
        while v and pai:
            total[pai.pk] = [a + b for a, b in zip(total[pai.pk] or [ZERO] * len(v), v)]
            pai = por_id.get(pai.superior_id)
    return [(c, total[c.pk], c.codigo.count(".")) for c in contas if total[c.pk] and any(total[c.pk])]


@login_required
@permission_required(VER, raise_exception=True)
def balancete(request):
    form, ini, fim, empresa = _periodo(request)
    antes = servicos.saldos(ate=ini - datetime.timedelta(days=1), empresa=empresa, user=request.user)
    mov = servicos.saldos(de=ini, ate=fim, empresa=empresa, user=request.user)
    contas = {c.pk: c for c in ContaContabil.objects.all()}
    valores = {}
    for pk in set(antes) | set(mov):
        ad, ac = antes.get(pk, (ZERO, ZERO))
        d, cr = mov.get(pk, (ZERO, ZERO))
        ant = ad - ac  # sempre devedor positivo para somar na árvore
        valores[pk] = [ant, d, cr, ant + d - cr]
    linhas = [{"conta": c, "nivel": n, "anterior": v[0], "debito": v[1], "credito": v[2], "saldo": v[3]}
              for c, v, n in _arvore(valores)]
    tot_d = sum((v[1] for v in valores.values()), ZERO)
    tot_c = sum((v[2] for v in valores.values()), ZERO)
    return render(request, "contabilidade/balancete.html", {
        "titulo": "Balancete de verificação", "icone": "table", "form": form, "linhas": linhas, "ini": ini,
        "fim": fim, "tot_d": tot_d, "tot_c": tot_c, "fecha": tot_d == tot_c})


def resultado(ini, fim, empresa=None, user=None):
    """Linhas da DRE e o resultado do intervalo."""
    mov = servicos.saldos(de=ini, ate=fim, empresa=empresa, user=user)
    grupos = {n: [] for n in ("receita", "deducao", "custo", "despesa")}
    for c in ContaContabil.objects.filter(natureza__in=grupos, sintetica=False):
        d, cr = mov.get(c.pk, (ZERO, ZERO))
        v = servicos.saldo_natural(c, d, cr)
        if v:
            grupos[c.natureza].append((c, v))
    t = {n: sum((v for _, v in itens), ZERO) for n, itens in grupos.items()}
    liquida = t["receita"] - t["deducao"]
    bruto = liquida - t["custo"]
    return {"grupos": grupos, "t": t, "liquida": liquida, "bruto": bruto, "resultado": bruto - t["despesa"]}


@login_required
@permission_required(VER, raise_exception=True)
def dre(request):
    form, ini, fim, empresa = _periodo(request)
    return render(request, "contabilidade/dre.html", {"titulo": "DRE — demonstração do resultado",
                                                      "icone": "graph-up-arrow", "form": form, "ini": ini,
                                                      "fim": fim, **resultado(ini, fim, empresa, request.user)})


@login_required
@permission_required(VER, raise_exception=True)
def balanco(request):
    try:
        data = datetime.date.fromisoformat(request.GET.get("data", ""))
    except ValueError:
        data = timezone.localdate()
    empresa = Empresa.acessiveis(request.user).filter(pk=int(request.GET["empresa"])).first() \
        if request.GET.get("empresa", "").isdigit() else None
    sal = servicos.saldos(ate=data, empresa=empresa, user=request.user)
    lados = {"ativo": [], "passivo": [], "pl": []}
    for c in ContaContabil.objects.filter(natureza__in=lados, sintetica=False):
        d, cr = sal.get(c.pk, (ZERO, ZERO))
        v = servicos.saldo_natural(c, d, cr)
        if v:
            lados[c.natureza].append((c, v))
    res = resultado(None, data, empresa, request.user)["resultado"]
    t = {n: sum((v for _, v in itens), ZERO) for n, itens in lados.items()}
    return render(request, "contabilidade/balanco.html", {
        "titulo": "Balanço patrimonial", "icone": "bank", "data": data, "empresa": empresa,
        "empresas": Empresa.acessiveis(request.user).order_by("-matriz", "razao_social"), "lados": lados, "t": t, "resultado": res,
        "total_passivo": t["passivo"] + t["pl"] + res, "fecha": t["ativo"] == t["passivo"] + t["pl"] + res})


class RazaoForm(EmpresaFiltroMixin, BootstrapMixin, forms.Form):
    conta = forms.ModelChoiceField(queryset=ContaContabil.objects.filter(sintetica=False))
    inicio = forms.DateField(label="de")
    fim = forms.DateField(label="até")


@login_required
@permission_required(VER, raise_exception=True)
def razao(request):
    hoje = timezone.localdate()
    form = RazaoForm(request.GET or None, initial={"inicio": hoje.replace(day=1), "fim": hoje}, user=request.user)
    ctx = {"titulo": "Razão", "icone": "journal-bookmark", "form": form}
    if form.is_bound and form.is_valid():
        d = form.cleaned_data
        c = d["conta"]
        empresa = form.empresa()
        ad, ac = servicos.saldos(ate=d["inicio"] - datetime.timedelta(days=1), empresa=empresa,
                                 user=request.user).get(c.pk, (ZERO, ZERO))
        saldo = servicos.saldo_natural(c, ad, ac)
        linhas = []
        partidas = servicos.escopo_lancamentos(
            Partida.objects.filter(conta=c, lancamento__data__range=(d["inicio"], d["fim"])), empresa, request.user)
        for p in partidas.select_related("lancamento").order_by("lancamento__data", "lancamento_id", "id"):
            saldo += p.valor if (p.tipo == "D") == c.devedora else -p.valor
            linhas.append({"p": p, "saldo": saldo})
        ctx.update(conta=c, anterior=servicos.saldo_natural(c, ad, ac), linhas=linhas, saldo=saldo)
    return render(request, "contabilidade/razao.html", ctx)


class LancamentoManualForm(BootstrapMixin, forms.Form):
    data = forms.DateField(initial=timezone.localdate)
    historico = forms.CharField(label="histórico", max_length=250)
    debito = forms.ModelChoiceField(label="conta a debitar", queryset=ContaContabil.objects.filter(sintetica=False, ativo=True))
    credito = forms.ModelChoiceField(label="conta a creditar", queryset=ContaContabil.objects.filter(sintetica=False, ativo=True))
    valor = forms.DecimalField(min_value=Decimal("0.01"), decimal_places=2)
    centro_custo = forms.ModelChoiceField(label="centro de custo", required=False, queryset=CentroCusto.objects.all())

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["empresa"] = forms.ModelChoiceField(
            label="empresa", queryset=Empresa.acessiveis(user).order_by("-matriz", "razao_social"))


@login_required
@permission_required("contabilidade.add_lancamento", raise_exception=True)
def lancar_manual(request):
    form = LancamentoManualForm(request.POST or None, user=request.user,
                                initial={"empresa": getattr(request, "empresa", None)})
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            lanc = servicos.gravar(d["data"], d["historico"], d["debito"], d["credito"], d["valor"],
                                   centro_custo=d["centro_custo"], manual=True, empresa=d["empresa"])
        except ValidationError as e:
            form.add_error(None, e)
        else:
            messages.success(request, f"Lançamento {lanc.numero} gravado.")
            return redirect(lanc.get_absolute_url())
    return render(request, "form_simples.html", {"form": form, "titulo": "Novo lançamento manual",
                                                 "icone": "journal-plus"})


@login_required
@permission_required("contabilidade.add_lancamento", raise_exception=True)
def estornar(request, pk):
    lanc = get_object_or_404(Lancamento, pk=pk)
    return acoes.confirmar(request, lanc, f"Estornar {lanc.numero}",
                           "Gravar um lançamento inverso? O original continua no diário, marcado como estornado.",
                           lambda: lanc.estornar(motivo=f"Por {request.user.get_full_name() or request.user}."),
                           lambda est: f"Estorno {est.numero} gravado.")


class FecharForm(BootstrapMixin, forms.Form):
    ano = forms.IntegerField(min_value=2000, max_value=2100)
    mes = forms.IntegerField(label="mês", min_value=1, max_value=12)


@login_required
@permission_required("contabilidade.add_periodofechado", raise_exception=True)
def periodos(request):
    hoje = timezone.localdate()
    anterior = hoje.replace(day=1) - datetime.timedelta(days=1)
    form = FecharForm(request.POST or None, initial={"ano": anterior.year, "mes": anterior.month})
    if request.method == "POST":
        if request.POST.get("reabrir"):
            if not request.user.has_perm("contabilidade.delete_periodofechado"):
                messages.error(request, "Sem permissão para reabrir períodos.")
            else:
                PeriodoFechado.objects.filter(pk=request.POST["reabrir"]).delete()
                messages.success(request, "Período reaberto.")
            return redirect("contabilidade:periodos")
        if form.is_valid():
            d = form.cleaned_data
            if (d["ano"], d["mes"]) >= (hoje.year, hoje.month):
                form.add_error(None, "Só meses já encerrados podem ser fechados.")
            else:
                PeriodoFechado.objects.get_or_create(ano=d["ano"], mes=d["mes"],
                                                     defaults={"fechado_por": request.user})
                messages.success(request, f"Período {d['mes']:02d}/{d['ano']} fechado.")
                return redirect("contabilidade:periodos")
    return render(request, "contabilidade/periodos.html", {"titulo": "Fechamento de períodos", "icone": "lock",
                                                           "form": form, "periodos": PeriodoFechado.objects.all()})
