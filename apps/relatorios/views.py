import csv

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import render
from django.urls import reverse

from apps.core.crud import celula_csv
from apps.core.models import Empresa

from . import dashboards
from .filtros import PERIODOS, do_request


def _exportar(blocos, indice, nome):
    tabelas = [b for b in blocos if b["tipo"] == "tabela" and b["csv"]]
    if not 0 <= indice < len(tabelas):
        return None
    t = tabelas[indice]
    resp = HttpResponse(content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="{nome}-{indice + 1}.csv"'
    resp.write("﻿")
    w = csv.writer(resp, delimiter=";")
    w.writerow([celula_csv(c) for c in t["colunas"]])
    for linha in t["linhas"]:
        w.writerow([celula_csv(v) for v in linha])
    from apps.core.models import LogAuditoria

    LogAuditoria.objects.create(acao="exportar", modelo="relatorios.painel", objeto_id=nome[:50],
                                descricao=f"Exportou CSV do painel {nome} ({len(t['linhas'])} linha(s))")
    return resp


def _painel(request, chave):
    titulo, icone, funcao, perms = dashboards.DASHBOARDS[chave]
    if perms and not any(request.user.has_perm(p) for p in perms):
        raise PermissionDenied
    filtro = do_request(request)
    blocos = funcao(filtro)
    if "csv" in request.GET and request.GET["csv"].isdigit():
        resp = _exportar(blocos, int(request.GET["csv"]), chave)
        if resp:
            return resp
    indice = 0
    for i, b in enumerate(blocos):
        b["json_id"] = f"dados-g{i}"
        if b["tipo"] == "kpi" and b["url"]:
            b["url"] = reverse(b["url"]) + "?" + filtro.querystring()
        if b["tipo"] == "tabela" and b["csv"]:        # numeração das tabelas exportáveis
            b["indice"], indice = indice, indice + 1
    return render(request, "relatorios/painel.html", {
        "titulo": titulo, "icone": icone, "blocos": blocos, "filtro": filtro, "periodos": PERIODOS,
        "empresas": Empresa.acessiveis(request.user).order_by("-matriz", "razao_social"),
        "tem_graficos": any(b["tipo"] == "grafico" and not b["vazio"] for b in blocos),
        "qs": filtro.querystring(), "chave": chave,
        "abas": [(k, v[0], v[1], reverse(f"relatorios:{k}")) for k, v in dashboards.DASHBOARDS.items()
                 if not v[3] or any(request.user.has_perm(p) for p in v[3])]})


@login_required
def executivo(request):
    return _painel(request, "executivo")


@login_required
def financeiro(request):
    return _painel(request, "financeiro")


@login_required
def comercial(request):
    return _painel(request, "comercial")


@login_required
def suprimentos(request):
    return _painel(request, "suprimentos")


@login_required
def operacao(request):
    return _painel(request, "operacao")


@login_required
def servicedesk(request):
    return _painel(request, "servicedesk")
