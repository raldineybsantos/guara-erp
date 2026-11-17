# Guará ERP — gestão empresarial livre
# Copyright (C) 2026 Raldiney Santos e colaboradores
# SPDX-License-Identifier: GPL-3.0-or-later
"""SCIM 2.0 (RFC 7643/7644) — recurso Users. O provedor de identidade cria, atualiza e desativa usuários aqui.

Regras de segurança: autenticação por token (só o SHA-256 é guardado); o SCIM só enxerga e altera usuários que ELE mesmo
criou (marcados por IdentidadeExterna 'scim:'); nunca toca em superusuário/equipe; nunca apaga (DELETE = desativar, a
auditoria precisa do histórico); nunca define senha nem grupos além do grupo padrão do token."""
import json
import re

from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from apps.core.models import LogAuditoria
from apps.core.signals import ip_da_requisicao

from .models import IdentidadeExterna, TokenSCIM

USER = "urn:ietf:params:scim:schemas:core:2.0:User"
LISTA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
ERRO = "urn:ietf:params:scim:api:messages:2.0:Error"
PATCH = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
CT = "application/scim+json"
MAX_CORPO = 200_000
POR_PAGINA = 100


def _resp(dados, status=200):
    r = JsonResponse(dados, status=status, content_type=CT)
    r["Cache-Control"] = "no-store"
    return r


def _erro(status, detalhe, tipo=None):
    d = {"schemas": [ERRO], "status": str(status), "detail": detalhe}
    if tipo:
        d["scimType"] = tipo
    return _resp(d, status)


def _auditar(request, token, texto):
    LogAuditoria.objects.create(usuario=None, acao="acao", modelo="scim", objeto_id=(token.nome if token else "?")[:50],
                                descricao=texto[:300], ip=ip_da_requisicao(request))


def _autenticar(request):
    cab = request.headers.get("Authorization", "")
    if not cab.lower().startswith("bearer "):
        return None
    token = TokenSCIM.objects.filter(impressao=TokenSCIM.impressao_de(cab[7:].strip()), ativo=True).first()
    if token:
        TokenSCIM.objects.filter(pk=token.pk).update(ultimo_uso=timezone.now())
    return token


def scim(view):
    """Autentica, limita o corpo e converte exceções em erro SCIM."""
    @csrf_exempt
    def interna(request, *args, **kwargs):
        token = _autenticar(request)
        if token is None:
            LogAuditoria.objects.create(usuario=None, acao="acao", modelo="scim", objeto_id="?",
                                        descricao="FALHA de SCIM: token ausente ou inválido", ip=ip_da_requisicao(request))
            r = _erro(401, "Token ausente ou inválido.")
            r["WWW-Authenticate"] = 'Bearer realm="scim"'
            return r
        if len(request.body) > MAX_CORPO:
            return _erro(413, "Corpo grande demais.")
        corpo = {}
        if request.method in ("POST", "PUT", "PATCH"):
            try:
                corpo = json.loads(request.body or b"{}")
            except ValueError:
                return _erro(400, "JSON inválido.", "invalidSyntax")
            if not isinstance(corpo, dict):
                return _erro(400, "Corpo inválido.", "invalidSyntax")
        return view(request, token, corpo, *args, **kwargs)
    return interna


def _gerenciavel(token, pk):
    """O usuário só é visível ao SCIM se foi criado pelo SCIM e não é privilegiado."""
    u = User.objects.filter(pk=pk, is_superuser=False, is_staff=False,
                            identidades__origem__startswith="scim:").distinct().first()
    return u


def _representar(request, u):
    ident = u.identidades.filter(origem__startswith="scim:").first()
    d = {"schemas": [USER], "id": str(u.pk), "userName": u.username, "active": u.is_active,
         "name": {"givenName": u.first_name, "familyName": u.last_name,
                  "formatted": (u.get_full_name() or u.username)},
         "displayName": u.get_full_name() or u.username,
         "emails": [{"value": u.email, "primary": True, "type": "work"}] if u.email else [],
         "meta": {"resourceType": "User", "created": u.date_joined.isoformat(), "location": r_location(request, u)}}
    if ident and ident.subject:
        d["externalId"] = ident.subject
    return d


def _email(corpo):
    for e in corpo.get("emails") or []:
        if isinstance(e, dict) and e.get("primary") and e.get("value"):
            return str(e["value"]).strip().lower()
    for e in corpo.get("emails") or []:
        if isinstance(e, dict) and e.get("value"):
            return str(e["value"]).strip().lower()
    return ""


def _booleano(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, str) and v.strip().lower() in ("true", "false"):      # o Entra ID envia "True"/"False" como texto
        return v.strip().lower() == "true"
    raise ValueError("active deve ser verdadeiro/falso")


def _aplicar(u, corpo):
    """Aplica os atributos suportados do SCIM no usuário (sem salvar)."""
    if "userName" in corpo:
        nome = str(corpo["userName"]).strip()
        if not nome or len(nome) > 150:
            raise ValueError("userName inválido")
        u.username = nome
    n = corpo.get("name")
    if isinstance(n, dict):
        if "givenName" in n:
            u.first_name = str(n["givenName"] or "")[:150]
        if "familyName" in n:
            u.last_name = str(n["familyName"] or "")[:150]
    if "emails" in corpo:
        u.email = _email(corpo)[:254]
    if "active" in corpo:
        u.is_active = _booleano(corpo["active"])


def _normalizar_patch(op):
    """Converte uma operação PATCH em um dicionário de atributos (add/replace). Remove -> vazio."""
    nome = str(op.get("op", "")).lower()
    caminho = op.get("path")
    valor = op.get("value")
    if nome not in ("add", "replace", "remove"):
        raise ValueError("operação não suportada")
    if nome == "remove":
        if caminho in ("emails", "name.givenName", "name.familyName"):
            return {"emails": []} if caminho == "emails" else {"name": {caminho.split(".")[1]: ""}}
        raise ValueError("não é possível remover este atributo")
    if not caminho:
        if not isinstance(valor, dict):
            raise ValueError("valor inválido")
        return valor
    c = re.sub(r"^urn:ietf:params:scim:schemas:core:2\.0:User:", "", str(caminho))
    if c == "active" or c == "userName":
        return {c: valor}
    if c in ("name.givenName", "name.familyName"):
        return {"name": {c.split(".")[1]: valor}}
    if c == "emails" and isinstance(valor, list):
        return {"emails": valor}
    if c.startswith("emails"):                                           # emails[type eq "work"].value
        return {"emails": [{"value": valor, "primary": True}]}
    if c == "displayName":
        return {}
    raise ValueError(f"atributo não suportado: {caminho}")


def _tem_conflito(username, email, ignorar=None):
    q = User.objects.filter(username__iexact=username)
    if ignorar:
        q = q.exclude(pk=ignorar)
    if q.exists():
        return "userName já existe"
    if email:
        q = User.objects.filter(email__iexact=email)
        if ignorar:
            q = q.exclude(pk=ignorar)
        if q.exists():
            return "e-mail já existe"
    return ""


@scim
def usuarios(request, token, corpo):
    if request.method == "GET":
        qs = User.objects.filter(is_superuser=False, is_staff=False, identidades__origem__startswith="scim:").distinct()
        filtro = request.GET.get("filter", "")
        if filtro:
            m = re.fullmatch(r'\s*(userName|externalId)\s+eq\s+"([^"]{1,300})"\s*', filtro, re.I)
            if not m:
                return _erro(400, "Filtro não suportado (use userName eq \"...\" ou externalId eq \"...\").", "invalidFilter")
            if m.group(1).lower() == "username":
                qs = qs.filter(username__iexact=m.group(2))
            else:
                qs = qs.filter(identidades__origem__startswith="scim:", identidades__subject=m.group(2))
        qs = qs.order_by("pk")
        try:
            inicio = max(int(request.GET.get("startIndex", 1)), 1)
            qtd = min(max(int(request.GET.get("count", POR_PAGINA)), 0), POR_PAGINA)
        except ValueError:
            return _erro(400, "Paginação inválida.", "invalidValue")
        total = qs.count()
        pagina = list(qs[inicio - 1: inicio - 1 + qtd])
        return _resp({"schemas": [LISTA], "totalResults": total, "startIndex": inicio, "itemsPerPage": len(pagina),
                      "Resources": [_representar(request, u) for u in pagina]})
    if request.method != "POST":
        return _erro(405, "Método não permitido.")
    u = User()
    try:
        if not str(corpo.get("userName", "")).strip():
            raise ValueError("userName é obrigatório")
        _aplicar(u, corpo)
    except ValueError as exc:
        return _erro(400, str(exc), "invalidValue")
    conflito = _tem_conflito(u.username, u.email)
    if conflito:
        return _erro(409, f"{conflito} (usuário local ou de outra origem não é alterado pelo SCIM).", "uniqueness")
    u.set_unusable_password()
    try:
        with transaction.atomic():
            u.save()
            IdentidadeExterna.objects.create(usuario=u, origem=f"scim:{token.pk}", email=u.email[:254],
                                             subject=str(corpo.get("externalId") or u.pk)[:300])
            if token.grupo_padrao_id:
                u.groups.add(token.grupo_padrao)
    except IntegrityError:
        return _erro(409, "Usuário já provisionado.", "uniqueness")
    _auditar(request, token, f"SCIM: usuário {u.username} criado (token {token.nome})")
    r = _resp(_representar(request, u), 201)
    r["Location"] = r_location(request, u)
    return r


def r_location(request, u):
    return request.build_absolute_uri(f"/scim/v2/Users/{u.pk}")


@scim
def usuario(request, token, corpo, pk):
    u = _gerenciavel(token, pk)
    if u is None:
        return _erro(404, "Usuário não encontrado.")
    if request.method == "GET":
        return _resp(_representar(request, u))
    if request.method == "DELETE":
        if u.is_active:
            u.is_active = False
            u.save(update_fields=["is_active"])
        _auditar(request, token, f"SCIM: usuário {u.username} desativado")
        return HttpResponse(status=204)
    if request.method not in ("PUT", "PATCH"):
        return _erro(405, "Método não permitido.")
    try:
        mudancas = {}
        if request.method == "PUT":
            mudancas = corpo
        else:
            ops = corpo.get("Operations")
            if not isinstance(ops, list):
                raise ValueError("Operations ausente")
            for op in ops:
                parcial = _normalizar_patch(op if isinstance(op, dict) else {})
                for k, v in parcial.items():
                    if isinstance(v, dict) and isinstance(mudancas.get(k), dict):
                        mudancas[k].update(v)
                    else:
                        mudancas[k] = v
        antigo_nome, antigo_email = u.username, u.email
        _aplicar(u, mudancas)
    except ValueError as exc:
        return _erro(400, str(exc), "invalidValue")
    conflito = _tem_conflito(u.username, u.email, ignorar=u.pk) if (u.username != antigo_nome or u.email != antigo_email) else ""
    if conflito:
        return _erro(409, conflito, "uniqueness")
    u.save()
    _auditar(request, token, f"SCIM: usuário {u.username} atualizado ({'ativo' if u.is_active else 'desativado'})")
    return _resp(_representar(request, u))


@scim
def grupos(request, token, corpo):
    if request.method == "GET":
        return _resp({"schemas": [LISTA], "totalResults": 0, "startIndex": 1, "itemsPerPage": 0, "Resources": []})
    return _erro(501, "Grupos não são sincronizados: o acesso aos módulos é definido dentro do ERP.")


@scim
def config(request, token, corpo):
    return _resp({"schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
                  "patch": {"supported": True}, "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
                  "filter": {"supported": True, "maxResults": POR_PAGINA}, "changePassword": {"supported": False},
                  "sort": {"supported": False}, "etag": {"supported": False},
                  "authenticationSchemes": [{"type": "oauthbearertoken", "name": "Token", "description": "Bearer token"}]})


@scim
def tipos(request, token, corpo):
    return _resp({"schemas": [LISTA], "totalResults": 1, "Resources": [{
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ResourceType"], "id": "User", "name": "User",
        "endpoint": "/Users", "schema": USER}]})
