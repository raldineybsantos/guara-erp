from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ValidationError
from django.db.models import Avg
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.core import acoes
from apps.core.crud import BootstrapMixin

from .models import Area, ArtigoConhecimento, Chamado, Mudanca, sugerir_servicos


def _chamado(request, pk):
    c = Chamado.objects.visiveis_para(request.user).filter(pk=pk).select_related("area", "servico").first()
    if c is None:
        raise Http404
    return c


def _executar(request, c, func, ok):
    try:
        func()
        messages.success(request, ok)
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
    return redirect(c.get_absolute_url())


# --------------------------------------------------------------------------- portal do usuário
@login_required
@permission_required("servicedesk.add_chamado", raise_exception=True)
def catalogo(request):
    areas = Area.objects.filter(ativo=True).prefetch_related("servicos")
    meus = Chamado.objects.filter(solicitante=request.user).exclude(status__in=("fechado", "cancelado", "recusado"))
    from . import ia_triagem

    return render(request, "servicedesk/catalogo.html", {"titulo": "Abrir chamado", "icone": "plus-circle",
                                                         "areas": areas, "meus": meus[:10],
                                                         "triagem_ia": ia_triagem.ativa() is not None})


class AbrirComIAForm(BootstrapMixin, forms.Form):
    texto = forms.CharField(label="descreva o que você precisa", max_length=1500, widget=forms.Textarea,
                            help_text="O assistente escolhe o serviço e a equipe certos e tenta orientar você na "
                                      "hora. Se não resolver, o chamado segue para a equipe.")


@login_required
@permission_required("servicedesk.add_chamado", raise_exception=True)
def abrir_com_ia(request):
    from . import ia_triagem

    form = AbrirComIAForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        texto = form.cleaned_data["texto"]
        r = ia_triagem.classificar(texto)
        if r is None:
            messages.warning(request, "O assistente não conseguiu identificar o serviço. Escolha no catálogo.")
            return redirect("servicedesk:catalogo")
        servico, prioridade, titulo = r
        c = Chamado(servico=servico, titulo=titulo, descricao=texto, prioridade=prioridade, classificado_por_ia=True,
                    solicitante=request.user)
        c.save()
        ia_triagem.primeiro_atendimento(c)
        messages.success(request, f"Chamado {c.numero} aberto em {c.area} › {servico.nome}.")
        return redirect(c.get_absolute_url())
    return render(request, "form_simples.html", {"form": form, "titulo": "Abrir chamado com o assistente",
                                                 "icone": "stars"})


@login_required
@require_POST
def ia_resposta(request, pk):
    c = _chamado(request, pk)
    if request.POST.get("resolveu") == "sim":
        return _executar(request, c, lambda: c.ia_resolveu(request.user), "Que bom! Chamado encerrado.")
    if request.user != c.solicitante:
        messages.error(request, "Só quem abriu o chamado responde ao assistente.")
        return redirect(c.get_absolute_url())
    c.ia_encaminhar("O usuário informou que a orientação não resolveu.", autor=request.user)
    messages.info(request, f"Chamado encaminhado para a equipe de {c.area}.")
    return redirect(c.get_absolute_url())


# --------------------------------------------------------------------------- fila de atendimento
@login_required
def fila(request):
    areas = Area.objects.filter(grupo__in=request.user.groups.all()) if not request.user.is_superuser \
        else Area.objects.all()
    if not areas.exists():
        messages.info(request, "Você não faz parte de nenhuma equipe de atendimento. Estes são os seus chamados.")
        return redirect("servicedesk:chamado_list")
    base = Chamado.objects.filter(area__in=areas).select_related("area", "solicitante", "analista", "servico")
    andamento = base.filter(status__in=Chamado.ATIVOS)
    agora = timezone.now()
    visoes = {
        "nao_atribuidos": ("Não atribuídos", andamento.filter(analista__isnull=True)),
        "meus": ("Meus", andamento.filter(analista=request.user)),
        "vencidos": ("Vencidos", andamento.filter(prazo_solucao__lt=agora).exclude(status="aguardando_usuario")),
        "risco": ("Em risco", andamento.filter(alerta_risco=True, prazo_solucao__gte=agora)),
        "usuario": ("Aguardando usuário", andamento.filter(status="aguardando_usuario")),
        "todos": ("Todos em andamento", andamento),
    }
    atual = request.GET.get("v", "nao_atribuidos")
    atual = atual if atual in visoes else "nao_atribuidos"
    ordem = {"critica": 0, "alta": 1, "media": 2, "baixa": 3}
    chamados = sorted(visoes[atual][1], key=lambda c: (c.prazo_solucao is None, c.prazo_solucao or agora,
                                                       ordem.get(c.prioridade, 9)))
    mes = base.filter(resolvido_em__year=agora.year, resolvido_em__month=agora.month)
    no_prazo = sum(1 for c in mes if c.prazo_solucao and c.resolvido_em <= c.prazo_solucao)
    indicadores = {
        "andamento": andamento.count(),
        "vencidos": visoes["vencidos"][1].count(),
        "sla_mes": round(100 * no_prazo / mes.count()) if mes.count() else None,
        "satisfacao": base.filter(avaliacao__isnull=False).aggregate(m=Avg("avaliacao"))["m"],
        "aprovacao": base.filter(status="aguardando_aprovacao").count(),
        "ia_mes": base.filter(triagem_ia="resolvido", fechado_em__year=agora.year,
                              fechado_em__month=agora.month).count(),
    }
    abas = [{"id": k, "nome": v[0], "n": v[1].count()} for k, v in visoes.items()]
    return render(request, "servicedesk/fila.html", {
        "titulo": "Fila de atendimento", "icone": "inboxes", "abas": abas, "atual": atual, "chamados": chamados,
        "ind": indicadores, "areas": areas})


# --------------------------------------------------------------------------- chat
@login_required
def mensagens(request, pk):
    """GET: mensagens depois de ?depois=<id> (o chat consulta a cada poucos segundos). POST: envia."""
    c = _chamado(request, pk)
    equipe = c.area.atende(request.user)
    if request.method == "POST":
        try:
            m = c.enviar_mensagem(request.user, request.POST.get("texto", ""), request.POST.get("interna") == "1")
        except ValidationError as e:
            return JsonResponse({"erro": " ".join(e.messages)}, status=400)
        if request.user == c.solicitante and c.triagem_ia == "orientado":
            from . import ia_triagem

            ia_triagem.continuar(c, m.texto)
        return JsonResponse({"ok": True, "id": m.pk})
    depois = int(request.GET.get("depois") or 0)
    qs = c.mensagens.filter(pk__gt=depois).select_related("autor")
    if not equipe:
        qs = qs.filter(interna=False)
    dados = [{"id": m.pk, "ia": m.ia,
              "autor": "Assistente (IA)" if m.ia else (m.autor.get_full_name() or m.autor.username) if m.autor
              else "Sistema",
              "texto": m.texto, "interna": m.interna, "sistema": m.sistema, "minha": m.autor_id == request.user.pk,
              "data": timezone.localtime(m.data).strftime("%d/%m %H:%M")} for m in qs]
    return JsonResponse({"mensagens": dados, "status": c.get_status_display(), "sla": c.sla_situacao()})


# --------------------------------------------------------------------------- ações do chamado
@login_required
@require_POST
def assumir(request, pk):
    c = _chamado(request, pk)
    return _executar(request, c, lambda: c.assumir(request.user), "Chamado assumido.")


class TextoForm(BootstrapMixin, forms.Form):
    texto = forms.CharField(widget=forms.Textarea)


@login_required
def chamar_usuario(request, pk):
    c = _chamado(request, pk)
    form = TextoForm(request.POST or None)
    form.fields["texto"].label = "o que você precisa do usuário"
    form.fields["texto"].help_text = "O usuário recebe aviso na tela e por e-mail. O SLA fica pausado até ele responder."
    if request.method == "POST" and form.is_valid():
        return _executar(request, c, lambda: c.chamar_usuario(request.user, form.cleaned_data["texto"]),
                         "Usuário chamado. O SLA está pausado até a resposta.")
    return render(request, "form_simples.html", {"form": form, "titulo": f"Chamar o usuário — {c}",
                                                 "icone": "megaphone"})


class ResolverForm(BootstrapMixin, forms.Form):
    solucao = forms.CharField(label="solução aplicada", widget=forms.Textarea)
    artigo = forms.ModelChoiceField(label="artigo da biblioteca usado", required=False,
                                    queryset=ArtigoConhecimento.objects.filter(status="publicado"))
    criar_artigo = forms.BooleanField(label="transformar esta solução em artigo da biblioteca", required=False)


@login_required
def resolver(request, pk):
    c = _chamado(request, pk)
    form = ResolverForm(request.POST or None, initial={"artigo": c.artigo_id})
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            c.resolver(request.user, d["solucao"], d["artigo"])
        except ValidationError as e:
            form.add_error(None, e)
        else:
            if d["criar_artigo"] and request.user.has_perm("servicedesk.add_artigoconhecimento"):
                a = ArtigoConhecimento.objects.create(
                    titulo=c.titulo, area=c.area, servico=c.servico, sintoma=c.descricao, solucao=d["solucao"],
                    status="rascunho", chamado_origem=c)
                messages.success(request, "Chamado resolvido. Revise e publique o artigo criado.")
                return redirect("servicedesk:artigoconhecimento_update", a.pk)
            messages.success(request, "Chamado resolvido. O usuário vai confirmar e avaliar.")
            return redirect(c.get_absolute_url())
    return render(request, "form_simples.html", {"form": form, "titulo": f"Resolver {c}", "icone": "check2-circle"})


class FecharForm(BootstrapMixin, forms.Form):
    avaliacao = forms.TypedChoiceField(label="como foi o atendimento?", coerce=int,
                                       choices=[(5, "5 - Excelente"), (4, "4 - Bom"), (3, "3 - Regular"),
                                                (2, "2 - Ruim"), (1, "1 - Péssimo")])
    comentario = forms.CharField(label="comentário (opcional)", required=False, widget=forms.Textarea)


@login_required
def fechar(request, pk):
    c = _chamado(request, pk)
    return acoes.formulario(request, c, FecharForm, f"Confirmar solução — {c}",
                            lambda d: c.fechar(request.user, d["avaliacao"], d["comentario"]),
                            "Obrigado! Chamado fechado.", "star")


@login_required
def reabrir(request, pk):
    c = _chamado(request, pk)
    return acoes.formulario(request, c, TextoForm, f"Reabrir {c}", lambda d: c.reabrir(request.user, d["texto"]),
                            "Chamado reaberto.", "arrow-counterclockwise")


@login_required
def cancelar(request, pk):
    c = _chamado(request, pk)
    return acoes.formulario(request, c, TextoForm, f"Cancelar {c}", lambda d: c.cancelar(request.user, d["texto"]),
                            "Chamado cancelado.", "x-octagon")


class TransferirForm(BootstrapMixin, forms.Form):
    area = forms.ModelChoiceField(label="nova área", queryset=Area.objects.filter(ativo=True))
    motivo = forms.CharField(max_length=200)


@login_required
def transferir(request, pk):
    c = _chamado(request, pk)
    return acoes.formulario(request, c, TransferirForm, f"Transferir {c}",
                            lambda d: c.transferir(request.user, d["area"], d["motivo"]), "Chamado transferido.",
                            "arrow-left-right")


# --------------------------------------------------------------------------- assistente (chatbot)
@login_required
@permission_required("servicedesk.add_chamado", raise_exception=True)
def assistente(request):
    """Assistente que tenta resolver antes de abrir chamado: procura na biblioteca de soluções e indica o
    serviço certo do catálogo. Roda só com os dados do ERP, sem serviço externo."""
    conversa = request.session.get("assistente", [])
    if request.method == "POST":
        if request.POST.get("limpar"):
            request.session["assistente"] = []
            return redirect("servicedesk:assistente")
        texto = request.POST.get("texto", "").strip()[:500]
        if texto:
            artigos = ArtigoConhecimento.buscar(texto, request.user)
            servicos = sugerir_servicos(texto)
            if artigos:
                resposta = "Encontrei estas soluções na biblioteca. Veja se alguma resolve:"
            elif servicos:
                resposta = "Não achei uma solução pronta, mas este é o serviço certo para abrir o chamado:"
            else:
                resposta = ("Não encontrei nada parecido. Tente descrever com outras palavras (o que aparece na tela, "
                            "qual sistema ou equipamento) ou abra o chamado pelo catálogo.")
            conversa.append({"de": "usuario", "texto": texto})
            conversa.append({"de": "bot", "texto": resposta, "texto_original": texto,
                             "artigos": [{"id": a.pk, "titulo": a.titulo, "area": str(a.area or "")} for a in artigos],
                             "servicos": [{"id": s.pk, "nome": str(s)} for s in servicos]})
            request.session["assistente"] = conversa[-20:]
        return redirect("servicedesk:assistente")
    return render(request, "servicedesk/assistente.html", {"titulo": "Assistente do Service Desk",
                                                           "icone": "robot", "conversa": conversa})


@login_required
def artigo_ler(request, pk):
    a = get_object_or_404(ArtigoConhecimento.visiveis_para(request.user), pk=pk)
    ArtigoConhecimento.objects.filter(pk=a.pk).update(visualizacoes=a.visualizacoes + 1)
    if request.method == "POST":
        campo = "util_sim" if request.POST.get("util") == "sim" else "util_nao"
        votados = request.session.get("artigos_votados", [])
        if a.pk not in votados:
            ArtigoConhecimento.objects.filter(pk=a.pk).update(**{campo: getattr(a, campo) + 1})
            request.session["artigos_votados"] = votados + [a.pk]
        if campo == "util_sim":
            messages.success(request, "Que bom que resolveu!")
            return redirect("servicedesk:assistente")
        messages.info(request, "Vamos abrir um chamado para a equipe.")
        url = reverse("servicedesk:chamado_create")
        return redirect(f"{url}?servico={a.servico_id or ''}&titulo={a.titulo[:100]}")
    return render(request, "servicedesk/artigo_ler.html", {"titulo": a.titulo, "icone": "book", "artigo": a})


# --------------------------------------------------------------------------- mudanças
def _mudanca(pk):
    return get_object_or_404(Mudanca, pk=pk)


@login_required
@permission_required("servicedesk.change_mudanca", raise_exception=True)
def mudanca_enviar(request, pk):
    m = _mudanca(pk)
    conflito = ", ".join(x.numero for x in m.conflitos()) if m.janela_inicio else ""
    msg = "Enviar a mudança para aprovação (gestor da área e CAB)?" if m.tipo != "padrao" else \
        "Mudança padrão é pré-aprovada. Confirmar?"
    if conflito:
        msg += f" Atenção: a janela coincide com {conflito}."
    return acoes.confirmar(request, m, f"Enviar {m}", msg, lambda: m.enviar_para_aprovacao(request.user),
                           "Mudança enviada." if m.tipo != "padrao" else "Mudança aprovada.")


@login_required
@permission_required("servicedesk.change_mudanca", raise_exception=True)
def mudanca_executar(request, pk):
    m = _mudanca(pk)
    return acoes.confirmar(request, m, f"Executar {m}", "Registrar o início da execução agora?",
                           lambda: m.iniciar_execucao(request.user), "Execução iniciada.")


class ResultadoMudancaForm(BootstrapMixin, forms.Form):
    sucesso = forms.TypedChoiceField(label="resultado", coerce=lambda v: v == "1",
                                     choices=[("1", "Concluída com sucesso"), ("0", "Falhou: plano de retorno aplicado")])
    resultado = forms.CharField(label="o que foi feito / evidências", widget=forms.Textarea)


@login_required
@permission_required("servicedesk.change_mudanca", raise_exception=True)
def mudanca_concluir(request, pk):
    m = _mudanca(pk)
    return acoes.formulario(request, m, ResultadoMudancaForm, f"Resultado da {m}",
                            lambda d: m.concluir(request.user, d["sucesso"], d["resultado"]),
                            "Resultado registrado e chamados relacionados avisados.", "flag")

