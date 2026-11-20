import datetime
from decimal import Decimal

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.http import HttpResponse
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.core import acoes
from apps.core.crud import BootstrapMixin
from apps.core.models import Empresa
from apps.financeiro.models import CentroCusto

from . import servicos
from . import orcamento as orc_servico
from . import contratos, patrimonio
from . import ecd as ecd_servico
from . import encerramento as enc_servico
from .models import Bem, ContaContabil, Lancamento, Orcamento, Partida, PeriodoFechado
from apps.core.formatacao import moeda

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


def resultado(ini, fim, empresa=None, user=None, sem_encerramento=False):
    """Linhas da DRE e o resultado do intervalo."""
    mov = servicos.saldos(de=ini, ate=fim, empresa=empresa, user=user, sem_encerramento=sem_encerramento)
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
                                                      "fim": fim, **resultado(ini, fim, empresa, request.user, sem_encerramento=True)})


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


# --------------------------------------------------------------------------- ativo fixo
class DepreciarForm(BootstrapMixin, forms.Form):
    ano = forms.IntegerField(min_value=2000, max_value=2100)
    mes = forms.IntegerField(label="mês", min_value=1, max_value=12)
    acao = forms.ChoiceField(label="o que fazer", choices=[("depreciar", "Depreciar até este mês"),
                                                           ("estornar", "Estornar a depreciação deste mês")])


@login_required
@permission_required("contabilidade.add_depreciacao", raise_exception=True)
def depreciar(request):
    hoje = timezone.localdate()
    form = DepreciarForm(request.POST or None, initial={"ano": hoje.year, "mes": hoje.month, "acao": "depreciar"})
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        comp = datetime.date(d["ano"], d["mes"], 1)
        try:
            if d["acao"] == "depreciar":
                n = patrimonio.depreciar_ate(comp)
                messages.success(request, f"{n} depreciação(ões) lançada(s) até {comp:%m/%Y}." if n
                                 else "Nada a depreciar: os bens já estão em dia.")
            else:
                n = patrimonio.estornar_depreciacao(comp)
                messages.success(request, f"{n} depreciação(ões) de {comp:%m/%Y} estornada(s).")
            return redirect("contabilidade:bem_list")
        except ValidationError as e:
            messages.error(request, " ".join(e.messages))
    return render(request, "form_simples.html", {"form": form, "titulo": "Depreciação mensal", "icone": "calendar-check"})


class ApropriarForm(BootstrapMixin, forms.Form):
    ano = forms.IntegerField(min_value=2000, max_value=2100)
    mes = forms.IntegerField(label="mês", min_value=1, max_value=12)
    acao = forms.ChoiceField(label="o que fazer", choices=[("apropriar", "Apropriar até este mês"),
                                                           ("estornar", "Estornar a apropriação deste mês")])


@login_required
@permission_required("contabilidade.add_apropriacaocontrato", raise_exception=True)
def apropriar_contratos(request):
    hoje = timezone.localdate()
    form = ApropriarForm(request.POST or None, initial={"ano": hoje.year, "mes": hoje.month, "acao": "apropriar"})
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        comp = datetime.date(d["ano"], d["mes"], 1)
        try:
            if d["acao"] == "apropriar":
                n = contratos.apropriar_ate(comp)
                messages.success(request, f"{n} apropriação(ões) lançada(s) até {comp:%m/%Y}." if n
                                 else "Nada a apropriar: os contratos já estão em dia.")
            else:
                n = contratos.estornar_competencia(comp)
                messages.success(request, f"{n} apropriação(ões) de {comp:%m/%Y} estornada(s).")
            return redirect("contabilidade:contratocontabil_list")
        except ValidationError as e:
            messages.error(request, " ".join(e.messages))
    return render(request, "form_simples.html", {"form": form, "titulo": "Apropriação mensal de contratos",
                                                 "icone": "calendar-check"})


class BaixaBemForm(BootstrapMixin, forms.Form):
    data = forms.DateField(label="data da baixa", initial=timezone.localdate)
    valor_venda = forms.DecimalField(label="valor recebido (venda)", max_digits=14, decimal_places=2, min_value=0,
                                     initial=0, help_text="Zero para descarte/perda. Entra em Caixa e bancos.")
    motivo = forms.CharField(max_length=200, required=False)


@login_required
@permission_required("contabilidade.change_bem", raise_exception=True)
def bem_baixar(request, pk):
    bem = get_object_or_404(Bem, pk=pk)
    form = BaixaBemForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            r = patrimonio.baixar_bem(bem, **form.cleaned_data)
            messages.success(request, "Bem baixado. " + (f"Perda de {moeda(r)}." if r > 0 else
                                                          f"Ganho de {moeda(-r)}." if r < 0 else "Sem ganho nem perda."))
            return redirect(bem.get_absolute_url())
        except ValidationError as e:
            form.add_error(None, e)
    return render(request, "form_simples.html", {"form": form, "icone": "box-arrow-down",
                                                 "titulo": f"Baixar {bem} — valor contábil {moeda(bem.valor_contabil)}"})


# --------------------------------------------------------------------------- orçado × realizado
@login_required
@permission_required("contabilidade.view_orcamento", raise_exception=True)
def orcado_realizado(request, pk):
    orc = get_object_or_404(Orcamento, pk=pk)
    hoje = timezone.localdate()
    try:
        ate = int(request.GET.get("ate", hoje.month if hoje.year == orc.ano else 12))
    except ValueError:
        ate = 12
    ate = min(max(ate, 1), 12)
    linhas = orc_servico.quadro(orc, ate)
    return render(request, "contabilidade/orcado_realizado.html", {
        "titulo": f"Orçado × realizado — {orc}", "icone": "bar-chart-steps", "orc": orc, "linhas": linhas,
        "totais": orc_servico.totais(linhas), "ate": ate, "meses": list(range(1, 13))})


# --------------------------------------------------------------------------- encerramento do exercício e ECD
class EncerrarForm(BootstrapMixin, forms.Form):
    ano = forms.IntegerField(min_value=2000, max_value=2100)
    empresa = forms.ModelChoiceField(Empresa.objects.none(), label="empresa")
    acao = forms.ChoiceField(label="o que fazer", choices=[("encerrar", "Encerrar o exercício (apurar o resultado)"),
                                                           ("estornar", "Estornar o encerramento")])

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["empresa"].queryset = Empresa.acessiveis(user).order_by("-matriz", "razao_social")


@login_required
@permission_required("contabilidade.add_lancamento", raise_exception=True)
def encerrar_exercicio(request):
    hoje = timezone.localdate()
    form = EncerrarForm(request.POST or None, user=request.user,
                        initial={"ano": hoje.year - 1, "acao": "encerrar"})
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            if d["acao"] == "encerrar":
                lanc = enc_servico.encerrar_exercicio(d["ano"], d["empresa"])
                messages.success(request, f"Exercício {d['ano']} encerrado: lançamento {lanc.numero}.")
                return redirect(lanc.get_absolute_url())
            lanc = enc_servico.encerramento_do_ano(d["ano"], d["empresa"])
            if lanc is None:
                raise ValidationError(f"O exercício {d['ano']} não está encerrado.")
            lanc.estornar(motivo="Reabertura do exercício")
            messages.success(request, f"Encerramento de {d['ano']} estornado.")
            return redirect("contabilidade:encerrar_exercicio")
        except ValidationError as e:
            messages.error(request, " ".join(e.messages))
    return render(request, "form_simples.html", {
        "form": form, "titulo": "Encerramento do exercício", "icone": "calendar2-check",
        "aviso": "Zera as contas de receita, custo e despesa do ano contra 2.3.2 (lucros ou prejuízos acumulados), "
                 "em 31/12. Faça depois da última depreciação e de conferir o balancete. É necessário para a ECD."})


class EcdForm(EmpresaFiltroMixin, BootstrapMixin, forms.Form):
    ano = forms.IntegerField(min_value=2000, max_value=2100)
    contador_nome = forms.CharField(label="contador: nome", max_length=100)
    contador_cpf = forms.CharField(label="contador: CPF", max_length=18)
    contador_crc = forms.CharField(label="contador: nº do CRC", max_length=20, required=False)
    contador_uf_crc = forms.CharField(label="contador: UF do CRC", max_length=2, required=False)
    contador_email = forms.EmailField(label="contador: e-mail", required=False)
    resp_nome = forms.CharField(label="responsável legal: nome", max_length=100)
    resp_cpf = forms.CharField(label="responsável legal: CPF", max_length=18)
    resp_qualificacao = forms.CharField(label="responsável legal: qualificação", max_length=100, initial="Administrador")
    ordem = forms.IntegerField(label="nº de ordem do livro", min_value=1, initial=1)

    def clean_empresa(self):
        if not self.cleaned_data.get("empresa"):
            raise forms.ValidationError("Escolha a empresa.")
        return self.cleaned_data["empresa"]


@login_required
@permission_required("contabilidade.view_lancamento", raise_exception=True)
def gerar_ecd(request):
    hoje = timezone.localdate()
    form = EcdForm(request.POST or None, user=request.user, initial={"ano": hoje.year - 1})
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        sig = [{"nome": d["contador_nome"], "cpf": d["contador_cpf"], "qualificacao": "Contador", "codigo": "900",
                "crc": d["contador_crc"], "uf_crc": d["contador_uf_crc"], "email": d["contador_email"]},
               {"nome": d["resp_nome"], "cpf": d["resp_cpf"], "qualificacao": d["resp_qualificacao"], "codigo": "203",
                "responsavel_legal": True}]
        try:
            nome, conteudo = ecd_servico.gerar_ecd(d["empresa"], d["ano"], sig, d["ordem"])
        except ValidationError as e:
            for m in e.messages:
                messages.error(request, m)
        else:
            resp = HttpResponse(conteudo, content_type="text/plain; charset=iso-8859-1")
            resp["Content-Disposition"] = f'attachment; filename="{nome}"'
            return resp
    return render(request, "form_simples.html", {
        "form": form, "titulo": "SPED Contábil (ECD) — gerar arquivo", "icone": "file-earmark-text",
        "aviso": "Gera o arquivo do Livro Diário completo do ano. Antes de transmitir, valide no PVA da Receita e "
                 "assine com o certificado e-CNPJ. O contador deve conferir o resultado (plano referencial e "
                 "demais registros exigidos para o seu regime ficam por conta dele)."})
