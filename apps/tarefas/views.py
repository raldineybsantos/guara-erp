import datetime
import json
from collections import OrderedDict

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group
from django.db import transaction
from django.db.models import Count, Q
from django.http import Http404, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.workflow.models import Instancia

from .models import COLUNAS, Atividade, Comentario, e_gestor, equipe_de

VISOES = [("minhas", "Minhas"), ("grupos", "Meus grupos"), ("equipe", "Minha equipe"), ("todas", "Todas")]


@login_required
def quadro(request):
    u = request.user
    gestor = e_gestor(u)
    ver_todas = u.is_superuser or u.has_perm("tarefas.ver_todas_atividade")
    visoes = [v for v in VISOES if v[0] in ("minhas", "grupos") or (v[0] == "equipe" and gestor)
              or (v[0] == "todas" and ver_todas)]
    visao = request.GET.get("visao", "minhas")
    if visao not in dict(visoes):
        visao = "minhas"

    base = Atividade.objects.exclude(status="cancelada").select_related("responsavel", "grupo")
    qs = {"minhas": base.minhas(u), "grupos": base.do_grupo(u), "equipe": base.da_equipe(u),
          "todas": base.visiveis_para(u)}[visao].annotate(n_coment=Count("comentarios"))

    # filtros
    f_resp, f_grupo, f_prio, q = (request.GET.get(k, "") for k in ("responsavel", "grupo", "prioridade", "q"))
    if f_resp.isdigit():
        qs = qs.filter(responsavel_id=int(f_resp))
    elif f_resp == "sem":
        qs = qs.filter(responsavel__isnull=True)
    if f_grupo.isdigit():
        qs = qs.filter(grupo_id=int(f_grupo))
    if f_prio in dict(Atividade.PRIORIDADES):
        qs = qs.filter(prioridade=f_prio)
    if q:
        qs = qs.filter(Q(titulo__icontains=q) | Q(descricao__icontains=q) | Q(etiquetas__icontains=q))
    # concluídas: só as dos últimos 30 dias, para o quadro não crescer sem fim
    limite = timezone.now() - datetime.timedelta(days=30)
    qs = qs.filter(~Q(status="concluida") | Q(concluida_em__gte=limite) | Q(concluida_em__isnull=True)).distinct()

    colunas = OrderedDict((c, {"id": c, "nome": n, "cards": []}) for c, n in COLUNAS)
    for a in qs.order_by("ordem", "prazo", "pk"):
        a.editavel = a.pode_editar(u)
        colunas[a.status]["cards"].append(a)

    # resumo por pessoa (visão do gestor)
    resumo = []
    if visao in ("equipe", "todas"):
        User = get_user_model()
        ids = equipe_de(u) if visao == "equipe" else set(qs.values_list("responsavel_id", flat=True)) - {None}
        hoje = timezone.localdate()
        for pessoa in User.objects.filter(pk__in=ids).order_by("first_name", "username"):
            ativ = Atividade.objects.filter(responsavel=pessoa).exclude(status__in=["cancelada"])
            resumo.append({
                "pessoa": pessoa,
                "a_fazer": ativ.filter(status="a_fazer").count(),
                "fazendo": ativ.filter(status__in=["fazendo", "revisao"]).count(),
                "atrasadas": ativ.filter(prazo__lt=hoje).exclude(status="concluida").count(),
                "concluidas_30d": ativ.filter(status="concluida", concluida_em__gte=limite).count(),
            })

    User = get_user_model()
    pessoas = User.objects.filter(is_active=True).order_by("first_name", "username") if (gestor or ver_todas) \
        else User.objects.filter(pk=u.pk)
    return render(request, "tarefas/quadro.html", {
        "titulo": "Minhas tarefas", "icone": "kanban", "colunas": colunas.values(), "visoes": visoes, "visao": visao,
        "resumo": resumo, "pessoas": pessoas, "grupos": Group.objects.order_by("name") if (gestor or ver_todas) else u.groups.order_by("name"),
        "prioridades": Atividade.PRIORIDADES, "f": {"responsavel": f_resp, "grupo": f_grupo, "prioridade": f_prio, "q": q},
        "aprovacoes": Instancia.objects.pendentes_para(u).count(), "gestor": gestor,
        "pode_criar": u.has_perm("tarefas.add_atividade"),
    })


@login_required
@require_POST
def mover(request):
    """Recebe {id, status, ordem: [ids da coluna na nova ordem]} do arrastar-e-soltar."""
    try:
        dados = json.loads(request.body)
        pk, status = int(dados["id"]), dados["status"]
        ordem = [int(x) for x in dados.get("ordem", [])][:500]
    except (ValueError, KeyError, TypeError):
        return HttpResponseBadRequest("dados inválidos")
    if status not in dict(COLUNAS):
        return HttpResponseBadRequest("coluna inválida")
    atividade = Atividade.objects.visiveis_para(request.user).filter(pk=pk).first()
    if atividade is None:
        raise Http404
    if not atividade.pode_editar(request.user):
        return JsonResponse({"ok": False, "erro": "Você não pode mover esta atividade."}, status=403)
    with transaction.atomic():
        anterior = atividade.status
        atividade.mudar_status(status)
        if atividade.responsavel_id is None and status != "a_fazer":
            atividade.responsavel = request.user  # quem puxa a atividade do grupo assume
        atividade.save()
        if anterior != status:
            Comentario.objects.create(atividade=atividade, usuario=request.user,
                                      texto=f"Moveu de “{dict(COLUNAS).get(anterior, anterior)}” para “{dict(COLUNAS)[status]}”.")
        editaveis = {a.pk: a for a in Atividade.objects.visiveis_para(request.user).filter(pk__in=ordem)}
        for i, aid in enumerate(ordem):
            a = editaveis.get(aid)
            if a and a.pode_editar(request.user) and a.ordem != i:
                Atividade.objects.filter(pk=aid).update(ordem=i)
    return JsonResponse({"ok": True, "responsavel": str(atividade.responsavel or "")})


@login_required
@require_POST
def comentar(request, pk):
    atividade = get_object_or_404(Atividade.objects.visiveis_para(request.user), pk=pk)
    texto = (request.POST.get("texto") or "").strip()[:4000]
    if texto:
        Comentario.objects.create(atividade=atividade, usuario=request.user, texto=texto)
        messages.success(request, "Comentário adicionado.")
    return redirect(atividade.get_absolute_url())
