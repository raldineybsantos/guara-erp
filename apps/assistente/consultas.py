"""Consultas pelo assistente. A IA só traduz a pergunta em (lista, busca, filtros); quem busca e mostra os dados
é o ERP, com a mesma lista que o usuário veria na tela (permissões, empresa e regras de visibilidade).
Nenhum dado do ERP é enviado à IA: ela recebe só a pergunta e os nomes dos filtros."""

from urllib.parse import urlencode

from django import forms
from django.db.models import Q
from django.urls import reverse

from apps.core.crud import REGISTRO, rotulo, valor_exibicao

from . import catalogo

MAX_LINHAS = 10


def disponiveis(user):
    catalogo._carregar_telas()
    return [(f"{c.app()}.{c.nome()}", str(c.meta().verbose_name_plural), c) for c in REGISTRO
            if user.has_perm(c.perm("view")) and not f"{c.app()}.".startswith(catalogo.IGNORAR)]


def _filtro(crud, params=None, queryset=None):
    fs = crud.filterset()
    return fs(params or {}, queryset=queryset if queryset is not None else crud.model._default_manager.none()) \
        if fs else None


def filtros(crud):
    """[{nome, rotulo, tipo, opcoes}] dos filtros que a lista aceita."""
    f = _filtro(crud)
    saida = []
    for nome, campo in (f.form.fields.items() if f else []):
        item = {"nome": nome, "rotulo": str(campo.label or nome)}
        if isinstance(campo, forms.ModelChoiceField):
            item["tipo"] = f"nome de {campo.queryset.model._meta.verbose_name}"
        elif getattr(campo, "choices", None):
            item["tipo"], item["opcoes"] = "opção", [str(v) for v, _ in campo.choices if v not in ("", None)][:30]
        elif isinstance(campo, forms.DateField):
            item["tipo"] = "data AAAA-MM-DD"
        elif isinstance(campo, forms.NullBooleanField):
            item["tipo"] = "true/false"
        else:
            item["tipo"] = "texto ou número"
        saida.append(item)
    return saida


def limpar(crud, busca, bruto):
    """Parâmetros seguros para a lista: só filtros que existem, com relações resolvidas por nome."""
    f = _filtro(crud)
    nomes = set(f.form.fields) if f else set()
    dados = {k: v for k, v in (bruto or {}).items() if k in nomes and isinstance(v, (str, int, float, bool))
             and v != ""}
    pend = catalogo.resolver_relacoes(f.form, dados) if f else []
    params = {k: ("true" if v is True else "false" if v is False else str(v)) for k, v in dados.items()}
    if busca and crud.search_fields:
        params["q"] = str(busca)[:80]
    return params, pend


def executar(crud, request, params):
    """Roda a consulta como a tela de lista faria. Devolve colunas, linhas (texto), total e o link da lista."""
    qs = crud.get_queryset(request)
    q = params.get("q", "").strip()
    if q and crud.search_fields:
        cond = Q()
        for campo in crud.search_fields:
            cond |= Q(**{f"{campo}__icontains": q})
        qs = qs.filter(cond).distinct()
    f = _filtro(crud, {k: v for k, v in params.items() if k != "q"}, qs)
    if f is not None and f.is_valid():
        qs = f.qs
    total = qs.count()
    cols = list(crud.list_display)[:7]
    linhas = [{"url": obj.get_absolute_url(), "valores": [str(valor_exibicao(obj, c, html=False) or "—") for c in cols]}
              for obj in qs[:MAX_LINHAS]]
    return {"colunas": [str(rotulo(crud.model, c)) for c in cols], "linhas": linhas, "total": total,
            "url": f"{reverse(crud.url_name('list'))}?{urlencode(params)}"}
