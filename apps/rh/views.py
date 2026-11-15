import csv
import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied, ValidationError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from apps.core import acoes
from apps.core.crud import BootstrapMixin
from apps.core.models import Colaborador, Empresa, LogAuditoria
from django import forms

from . import ferias as ferias_svc
from . import folha as folha_svc
from . import ponto
from . import esocial
from .models import (EventoESocial, Folha, Holerite, PeriodoAquisitivo, Rescisao, SolicitacaoFerias, rotulo_competencia)


def _meu_colaborador(request):
    try:
        return request.user.colaborador
    except ObjectDoesNotExist:
        return None


def _ip(request):
    return request.META.get("REMOTE_ADDR")


# ------------------------------------------------------------------ ponto
@login_required
def bater_ponto(request):
    colab = _meu_colaborador(request)
    if colab is None:
        messages.error(request, "Seu usuário não está vinculado a um colaborador. Peça ao RH para vincular.")
        return redirect("dashboard")
    if request.method == "POST":
        try:
            m = ponto.bater_ponto(colab, _ip(request))
            messages.success(request, f"Ponto registrado às {timezone.localtime(m.momento):%H:%M:%S}.")
        except ValidationError as e:
            messages.warning(request, " ".join(e.messages))
        return redirect("rh:bater_ponto")
    hoje = timezone.localdate()
    um = datetime.timedelta(days=1)
    marcas = [timezone.localtime(m.momento) for m in colab.marcacoes.filter(
        momento__date__range=(hoje - um, hoje + um)).order_by("momento") if timezone.localtime(m.momento).date() == hoje]
    return render(request, "rh/bater_ponto.html", {"titulo": "Bater ponto", "colaborador": colab, "marcas": marcas,
                                                   "agora": timezone.localtime()})


def _competencia(request):
    hoje = timezone.localdate()
    try:
        ano, mes = (int(x) for x in request.GET.get("mes", f"{hoje.year}-{hoje.month}").split("-"))
        datetime.date(ano, mes, 1)
    except (ValueError, TypeError):
        ano, mes = hoje.year, hoje.month
    return ano, mes


@login_required
def espelho(request, pk=None):
    """Espelho de ponto do mês. Cada um vê o seu; o RH vê o de qualquer colaborador."""
    if pk is None:
        colab = _meu_colaborador(request)
        if colab is None:
            messages.error(request, "Seu usuário não está vinculado a um colaborador.")
            return redirect("dashboard")
    else:
        colab = get_object_or_404(Colaborador, pk=pk)
        if colab.usuario_id != request.user.pk and not request.user.has_perm("rh.gerir_ponto"):
            raise PermissionDenied
    ano, mes = _competencia(request)
    linhas, totais = ponto.espelho(colab, ano, mes)
    if request.GET.get("export") == "csv":
        LogAuditoria.registrar("exportar", colab, descricao=f"Exportou espelho de ponto {mes:02d}/{ano} de {colab}")
        resp = HttpResponse(content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = f'attachment; filename="ponto-{colab.pk}-{ano}-{mes:02d}.csv"'
        resp.write("﻿")
        w = csv.writer(resp, delimiter=";")
        w.writerow(["Data", "Marcações", "Trabalhado", "Saldo", "Observação"])
        for x in linhas:
            obs = x["feriado"] or (x["abono"].get_tipo_display() if x["abono"] else "") or (
                "FALTA" if x["apuracao"] and x["apuracao"].falta else "") or (
                "Marcações incompletas" if x["apuracao"] and x["apuracao"].inconsistente else "")
            w.writerow([x["data"].strftime("%d/%m/%Y"), " ".join(x["marcacoes"]), x["trabalhado"], x["saldo"], obs])
        return resp
    prev = datetime.date(ano, mes, 1) - datetime.timedelta(days=1)
    prox = datetime.date(ano + (mes == 12), mes % 12 + 1, 1)
    return render(request, "rh/espelho.html", {
        "titulo": "Espelho de ponto", "colaborador": colab, "linhas": linhas, "ano": ano, "mes": mes,
        "rotulo": rotulo_competencia(datetime.date(ano, mes, 1)), "mes_anterior": f"{prev.year}-{prev.month}",
        "mes_seguinte": f"{prox.year}-{prox.month}", "total_trabalhado": ponto.hhmm(totais["trabalhado"]),
        "total_saldo": ponto.hhmm(totais["saldo"]), "faltas": totais["faltas"],
        "saldo_banco": ponto.hhmm(ponto.saldo_banco(colab)), "pode_gerir": request.user.has_perm("rh.gerir_ponto")})


@login_required
@permission_required("rh.gerir_ponto", raise_exception=True)
def banco_horas(request):
    linhas = []
    for c in Colaborador.objects.exclude(status="desligado").select_related("dados_trabalhistas__jornada"):
        d = getattr(c, "dados_trabalhistas", None) if hasattr(c, "dados_trabalhistas") else None
        if d and d.jornada:
            linhas.append({"colaborador": c, "jornada": d.jornada, "saldo": ponto.hhmm(ponto.saldo_banco(c))})
    return render(request, "rh/banco_horas.html", {"titulo": "Banco de horas", "linhas": linhas})


# ------------------------------------------------------------------ férias
class SolicitarFeriasForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = SolicitacaoFerias
        fields = ["periodo", "inicio", "dias", "abono_dias", "adiantar_13"]


@login_required
def solicitar_ferias(request):
    colab = _meu_colaborador(request)
    if colab is None:
        messages.error(request, "Seu usuário não está vinculado a um colaborador.")
        return redirect("dashboard")
    ferias_svc.gerar_periodos(colab)
    form = SolicitarFeriasForm(request.POST or None)
    form.fields["periodo"].queryset = colab.periodos_ferias.all()
    if request.method == "POST" and form.is_valid():
        s = form.save(commit=False)
        s.colaborador = colab
        try:
            ferias_svc.solicitar(s, request.user)
        except ValidationError as e:
            form.add_error(None, e)
        else:
            messages.success(request, "Solicitação enviada ao seu gestor.")
            return redirect("rh:minhas_ferias")
    return render(request, "form_simples.html", {"form": form, "titulo": "Solicitar férias", "icone": "airplane"})


@login_required
def minhas_ferias(request):
    colab = _meu_colaborador(request)
    if colab is None:
        messages.error(request, "Seu usuário não está vinculado a um colaborador.")
        return redirect("dashboard")
    ferias_svc.gerar_periodos(colab)
    return render(request, "rh/minhas_ferias.html", {
        "titulo": "Minhas férias", "periodos": colab.periodos_ferias.order_by("-inicio"),
        "solicitacoes": colab.solicitacoes_ferias.all()})


class RecusaForm(BootstrapMixin, forms.Form):
    motivo = forms.CharField(max_length=200, label="Motivo da recusa")


@login_required
def ferias_aprovar(request, pk):
    s = get_object_or_404(SolicitacaoFerias, pk=pk)
    if not ferias_svc.pode_decidir(request.user, s):
        raise PermissionDenied
    return acoes.confirmar(request, s, "Aprovar férias", f"Aprovar as férias de {s.colaborador} a partir de "
                           f"{s.inicio:%d/%m/%Y} ({s.dias} dias)?",
                           lambda: ferias_svc.decidir(s, request.user, True), "Férias aprovadas.")


@login_required
def ferias_recusar(request, pk):
    s = get_object_or_404(SolicitacaoFerias, pk=pk)
    if not ferias_svc.pode_decidir(request.user, s):
        raise PermissionDenied
    return acoes.formulario(request, s, RecusaForm, "Recusar férias",
                            lambda d: ferias_svc.decidir(s, request.user, False, d["motivo"]), "Férias recusadas.",
                            icone="x-circle")


@login_required
@permission_required("rh.aprovar_solicitacaoferias", raise_exception=True)
def ferias_gozada(request, pk):
    s = get_object_or_404(SolicitacaoFerias, pk=pk)

    def func():
        if s.status != "aprovada":
            raise ValidationError("Só férias aprovadas podem ser marcadas como gozadas.")
        s.status = "gozada"
        s.save()

    return acoes.confirmar(request, s, "Marcar como gozada", "Confirmar que o colaborador gozou estas férias?",
                           func, "Férias marcadas como gozadas.")


@login_required
@permission_required("rh.add_periodoaquisitivo", raise_exception=True)
def gerar_periodos(request):
    if request.method == "POST":
        novos = sum(ferias_svc.gerar_periodos(c) for c in Colaborador.objects.exclude(status="desligado").filter(
            tipo_contrato__in=["clt", "temporario"]))
        messages.success(request, f"{novos} período(s) aquisitivo(s) criado(s).")
        return redirect("rh:periodoaquisitivo_list")
    return render(request, "confirmar.html", {"titulo": "Gerar períodos aquisitivos",
                                              "mensagem": "Criar os períodos aquisitivos que faltam para todos os "
                                                          "colaboradores CLT ativos?",
                                              "voltar": reverse("rh:periodoaquisitivo_list")})


# ------------------------------------------------------------------ folha
def _acao_folha(func_nome, titulo, msg, ok, perm="rh.fechar_folha"):
    @login_required
    @permission_required(perm, raise_exception=True)
    def view(request, pk):
        f = get_object_or_404(Folha, pk=pk)
        return acoes.confirmar(request, f, titulo, msg(f), lambda: getattr(folha_svc, func_nome)(*_args(func_nome, f, request)),
                               ok)
    return view


def _args(nome, f, request):
    return (f, request.user) if nome == "fechar" else (f,)


folha_calcular = _acao_folha("calcular", "Calcular folha", lambda f: f"Calcular {f}? Os holerites atuais serão refeitos.",
                             "Folha calculada.")
folha_importar_ponto = _acao_folha(
    "importar_ponto", "Importar ponto",
    lambda f: f"Importar horas extras e faltas do ponto de {rotulo_competencia(f.competencia)}? Os lançamentos "
              "anteriores vindos do ponto serão refeitos; depois calcule a folha.",
    lambda n: f"{n} lançamento(s) criado(s). Agora calcule a folha.")
folha_fechar = _acao_folha("fechar", "Fechar folha", lambda f: f"Fechar {f} e gerar as contas a pagar (salários, INSS, "
                           "FGTS e IRRF)? Depois disso a folha não pode mais ser recalculada.",
                           lambda ids: f"Folha fechada: {len(ids)} conta(s) a pagar gerada(s).")
folha_reabrir = _acao_folha("reabrir", "Reabrir folha", lambda f: f"Reabrir {f}? As contas a pagar abertas geradas por "
                            "ela serão canceladas.", "Folha reaberta.")


@login_required
def holerite_ver(request, pk):
    h = get_object_or_404(Holerite.objects.select_related("folha", "colaborador", "folha__empresa"), pk=pk)
    proprio = h.colaborador.usuario_id == request.user.pk and h.folha.status == "fechada"
    if not (proprio or request.user.has_perm("rh.view_folha")):
        raise PermissionDenied
    LogAuditoria.registrar("ler", h, descricao=f"Consultou holerite de {h.colaborador} ({h.folha})")
    return render(request, "rh/holerite.html", {
        "titulo": "Holerite", "h": h, "itens": h.itens.exclude(tipo="informativo"),
        "informativos": h.itens.filter(tipo="informativo"), "rotulo": rotulo_competencia(h.folha.competencia)})


@login_required
def meus_holerites(request):
    colab = _meu_colaborador(request)
    if colab is None:
        messages.error(request, "Seu usuário não está vinculado a um colaborador.")
        return redirect("dashboard")
    hs = colab.holerites.filter(folha__status="fechada").select_related("folha").order_by("-folha__competencia")
    return render(request, "rh/meus_holerites.html", {"titulo": "Meus holerites", "holerites": hs})


# ------------------------------------------------------------------ rescisão
@login_required
@permission_required("rh.change_rescisao", raise_exception=True)
def rescisao_calcular(request, pk):
    r = get_object_or_404(Rescisao, pk=pk)
    if r.status == "efetivada":
        messages.error(request, "Rescisão já efetivada.")
        return redirect(r.get_absolute_url())
    return acoes.confirmar(request, r, "Calcular rescisão", f"Calcular as verbas rescisórias de {r.colaborador}?",
                           lambda: folha_svc.calcular_rescisao(r), "Rescisão calculada. Confira antes de efetivar.")


@login_required
@permission_required("rh.efetivar_rescisao", raise_exception=True)
def rescisao_efetivar(request, pk):
    r = get_object_or_404(Rescisao, pk=pk)
    return acoes.confirmar(
        request, r, "Efetivar rescisão",
        f"Desligar {r.colaborador} em {r.data_desligamento:%d/%m/%Y} e gerar a conta a pagar de "
        f"R$ {r.total_liquido}? O colaborador passa a 'desligado'.",
        lambda: folha_svc.efetivar_rescisao(r), "Rescisão efetivada e conta a pagar gerada.")


# ------------------------------------------------------------------ eSocial
class GerarEventoForm(BootstrapMixin, forms.Form):
    TIPOS = [("S-1000", "S-1000 Informações do empregador"), ("S-1010", "S-1010 Tabela de rubricas"),
             ("S-2200", "S-2200 Admissão (escolha o colaborador)"),
             ("S-1299", "S-1299 Fechamento dos eventos periódicos (informe a competência)")]
    tipo = forms.ChoiceField(choices=TIPOS)
    empresa = forms.ModelChoiceField(queryset=Empresa.objects.all())
    colaborador = forms.ModelChoiceField(queryset=Colaborador.objects.all(), required=False)
    competencia = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))


@login_required
@permission_required("rh.add_eventoesocial", raise_exception=True)
def esocial_gerar(request):
    form = GerarEventoForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            if d["tipo"] == "S-1000":
                evs = [esocial.gerar_s1000(d["empresa"])]
            elif d["tipo"] == "S-1010":
                evs = esocial.gerar_s1010(d["empresa"])
            elif d["tipo"] == "S-2200":
                if not d["colaborador"]:
                    raise ValidationError("Escolha o colaborador.")
                evs = [esocial.gerar_s2200(d["colaborador"])]
            else:
                if not d["competencia"]:
                    raise ValidationError("Informe a competência.")
                evs = [esocial.gerar_s1299(d["empresa"], d["competencia"].replace(day=1))]
        except ValidationError as e:
            form.add_error(None, e)
        else:
            messages.success(request, f"{len(evs)} evento(s) gerado(s). Assine para montar o lote.")
            return redirect("rh:eventoesocial_list")
    return render(request, "form_simples.html", {"form": form, "titulo": "Gerar evento do eSocial",
                                                 "icone": "cloud-arrow-up"})


@login_required
@permission_required("rh.add_eventoesocial", raise_exception=True)
def esocial_da_folha(request, pk):
    f = get_object_or_404(Folha, pk=pk)

    def func():
        from apps.financeiro.models import ContaPagar

        if f.status == "aberta":
            raise ValidationError("Calcule a folha antes.")
        conta = ContaPagar.objects.filter(pk__in=f.titulos_ids, descricao__contains="líquidos").first() or \
            ContaPagar.objects.filter(pk__in=f.titulos_ids, descricao__contains="13º").first()
        data_pgto = conta.vencimento if conta else f.competencia
        n = 0
        for h in f.holerites.select_related("colaborador"):
            esocial.gerar_s1200(f, h)
            esocial.gerar_s1210(f, h, data_pgto)
            n += 2
        return n

    return acoes.confirmar(request, f, "Gerar eventos do eSocial",
                           f"Gerar os eventos S-1200 (remuneração) e S-1210 (pagamento) de {f}? As rubricas da "
                           "folha precisam estar cadastradas com a natureza do eSocial.", func,
                           lambda n: f"{n} evento(s) gerado(s).",
                           destino=lambda n: reverse("rh:eventoesocial_list"))


@login_required
@permission_required("rh.add_eventoesocial", raise_exception=True)
def esocial_da_rescisao(request, pk):
    r = get_object_or_404(Rescisao, pk=pk)
    return acoes.confirmar(request, r, "Gerar S-2299", f"Gerar o evento de desligamento de {r.colaborador}?",
                           lambda: esocial.gerar_s2299(r), "Evento S-2299 gerado.",
                           destino=lambda ev: ev.get_absolute_url())


@login_required
@permission_required("rh.change_eventoesocial", raise_exception=True)
def esocial_assinar(request, pk):
    ev = get_object_or_404(EventoESocial, pk=pk)
    return acoes.confirmar(request, ev, "Assinar evento", f"Assinar {ev} com o certificado A1 da empresa?",
                           lambda: esocial.assinar_evento(ev), "Evento assinado.")


def _download(conteudo, nome):
    resp = HttpResponse(conteudo, content_type="application/xml; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="{nome}"'
    return resp


@login_required
@permission_required("rh.view_eventoesocial", raise_exception=True)
def esocial_baixar(request, pk):
    ev = get_object_or_404(EventoESocial, pk=pk)
    LogAuditoria.registrar("exportar", ev, descricao=f"Baixou o XML do evento {ev}")
    return _download(ev.xml_assinado or ev.xml, f"{ev.tipo}-{ev.identificador}.xml")


@login_required
@permission_required("rh.view_eventoesocial", raise_exception=True)
def esocial_lote(request, pk):
    ev = get_object_or_404(EventoESocial, pk=pk)
    try:
        xml = esocial.lote_xml([ev])
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
        return redirect(ev.get_absolute_url())
    LogAuditoria.registrar("exportar", ev, descricao=f"Baixou lote com o evento {ev}")
    return _download(xml, f"lote-{ev.tipo}-{ev.pk}.xml")


@login_required
@permission_required("rh.view_eventoesocial", raise_exception=True)
def esocial_montar_lote(request):
    assinados = EventoESocial.objects.filter(status="assinado").select_related("colaborador", "empresa")
    if request.method == "POST":
        ids = request.POST.getlist("ev")
        evs = list(assinados.filter(pk__in=ids))
        try:
            xml = esocial.lote_xml(evs)
        except ValidationError as e:
            messages.error(request, " ".join(e.messages))
            return redirect("rh:esocial_montar_lote")
        for e in evs:
            LogAuditoria.registrar("exportar", e, descricao=f"Incluiu o evento {e} em um lote baixado")
        return _download(xml, f"lote-esocial-{timezone.localdate():%Y%m%d}.xml")
    return render(request, "rh/montar_lote.html", {"titulo": "Montar lote do eSocial", "eventos": assinados})


class RetornoForm(BootstrapMixin, forms.Form):
    status = forms.ChoiceField(choices=[("enviado", "Enviado"), ("aceito", "Aceito pelo governo"),
                                        ("erro", "Rejeitado/com erro")])
    recibo = forms.CharField(required=False, max_length=60, label="Nº do recibo")
    retorno = forms.CharField(required=False, widget=forms.Textarea, label="Retorno do governo")


@login_required
@permission_required("rh.change_eventoesocial", raise_exception=True)
def esocial_recibo(request, pk):
    ev = get_object_or_404(EventoESocial, pk=pk)

    def func(d):
        ev.status, ev.recibo, ev.retorno = d["status"], d["recibo"], d["retorno"]
        ev.save()

    return acoes.formulario(request, ev, RetornoForm, "Registrar retorno do governo", func, "Retorno registrado.",
                            initial={"status": ev.status, "recibo": ev.recibo, "retorno": ev.retorno}, icone="receipt")
