# Guará ERP — gestão empresarial livre
# Copyright (C) 2026 Raldiney Santos e colaboradores
# SPDX-License-Identifier: GPL-3.0-or-later
"""Login único por OpenID Connect (fluxo de código + PKCE), sem biblioteca de "social login".

Validações do ID token: assinatura (JWKS do provedor), emissor, público (client id), validade, nonce; Microsoft: o tenant
do token tem de ser o configurado. O 'state' e o 'nonce' vivem na sessão e valem uma vez."""
import base64
import hashlib
import secrets
from urllib.parse import urlencode

import jwt
import requests
from django.conf import settings
from django.contrib.auth.models import User
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import IdentidadeExterna, ProvedorOIDC

TIMEOUT = 10
ALGORITMOS = ["RS256", "RS384", "RS512", "ES256", "ES384", "PS256"]


class OIDCErro(Exception):
    """Falha de login por SSO; a mensagem pode ser mostrada ao usuário."""


def _get(url):
    r = requests.get(url, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def _post(url, dados):
    r = requests.post(url, data=dados, timeout=TIMEOUT, headers={"Accept": "application/json"})
    try:
        corpo = r.json()
    except ValueError:
        corpo = {}
    if r.status_code != 200:
        raise OIDCErro(f"o provedor recusou a troca do código ({corpo.get('error', r.status_code)})")
    return corpo


def emissor_esperado(prov):
    if prov.tipo == "microsoft":
        return f"https://login.microsoftonline.com/{prov.tenant.strip()}/v2.0"
    if prov.tipo == "google":
        return "https://accounts.google.com"
    return prov.emissor.strip()


def descoberta(prov):
    """Metadados do provedor (endpoints e JWKS), com cache de 1 hora."""
    emissor = emissor_esperado(prov)
    chave = "oidc-desc-" + hashlib.sha256(emissor.encode()).hexdigest()
    doc = cache.get(chave)
    if doc is None:
        url = emissor.rstrip("/") + "/.well-known/openid-configuration"
        if not url.startswith("https://"):
            raise OIDCErro("o emissor precisa usar https")
        try:
            doc = _get(url)
        except (requests.RequestException, ValueError) as exc:
            raise OIDCErro(f"não foi possível consultar o provedor: {exc}") from exc
        if doc.get("issuer", "").rstrip("/") != emissor.rstrip("/"):
            raise OIDCErro("o emissor informado pelo provedor não confere com o configurado")
        for k in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            if not str(doc.get(k, "")).startswith("https://"):
                raise OIDCErro(f"metadado inválido do provedor: {k}")
        cache.set(chave, doc, 3600)
    return doc


def url_retorno(request, prov):
    base = getattr(settings, "ERP_URL_BASE", "") or request.build_absolute_uri("/").rstrip("/")
    return f"{base}/identidade/retorno/{prov.slug}/"


def _pkce():
    verificador = secrets.token_urlsafe(64)
    desafio = base64.urlsafe_b64encode(hashlib.sha256(verificador.encode()).digest()).rstrip(b"=").decode()
    return verificador, desafio


def iniciar(request, prov, proximo="/"):
    """Prepara state/nonce/PKCE na sessão e devolve a URL de autorização do provedor."""
    doc = descoberta(prov)
    state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    verificador, desafio = _pkce()
    request.session["oidc"] = {"slug": prov.slug, "state": state, "nonce": nonce, "verificador": verificador,
                               "proximo": proximo, "em": timezone.now().timestamp()}
    params = {"client_id": prov.client_id, "response_type": "code", "scope": "openid email profile",
              "redirect_uri": url_retorno(request, prov), "state": state, "nonce": nonce,
              "code_challenge": desafio, "code_challenge_method": "S256"}
    if prov.tipo == "google" and prov.dominios:
        params["hd"] = sorted(prov.dominios)[0]          # só uma dica de tela; a conferência é feita no token
    return doc["authorization_endpoint"] + "?" + urlencode(params)


def validar_retorno(request, prov, dados):
    """Confere state, troca o código e valida o ID token. Devolve (claims, proximo)."""
    guardado = request.session.pop("oidc", None)           # uso único
    if not guardado or guardado.get("slug") != prov.slug:
        raise OIDCErro("sessão de login expirada ou inválida; tente de novo")
    if timezone.now().timestamp() - guardado["em"] > 600:
        raise OIDCErro("o login demorou demais; tente de novo")
    if dados.get("error"):
        raise OIDCErro(f"o provedor recusou o login ({dados.get('error')})")
    if not secrets.compare_digest(str(dados.get("state", "")), guardado["state"]):
        raise OIDCErro("estado do login inválido")
    codigo = dados.get("code")
    if not codigo:
        raise OIDCErro("resposta do provedor sem código")
    doc = descoberta(prov)
    token = _post(doc["token_endpoint"], {
        "grant_type": "authorization_code", "code": codigo, "redirect_uri": url_retorno(request, prov),
        "client_id": prov.client_id, "client_secret": prov.obter_segredo(), "code_verifier": guardado["verificador"]})
    if not token.get("id_token"):
        raise OIDCErro("o provedor não devolveu o ID token")
    claims = validar_id_token(prov, doc, token["id_token"], guardado["nonce"])
    return claims, guardado.get("proximo") or "/"


def validar_id_token(prov, doc, id_token, nonce):
    try:
        cab = jwt.get_unverified_header(id_token)
        if cab.get("alg") not in ALGORITMOS:
            raise OIDCErro("algoritmo de assinatura não aceito")
        jwks = jwt.PyJWKSet.from_dict(_get(doc["jwks_uri"]))
        chave = next((k for k in jwks.keys if k.key_id == cab.get("kid")), None)
        if chave is None and len(jwks.keys) == 1 and not cab.get("kid"):
            chave = jwks.keys[0]
        if chave is None:
            raise OIDCErro("chave de assinatura do token desconhecida")
        claims = jwt.decode(id_token, chave.key, algorithms=[cab["alg"]], audience=prov.client_id,
                            issuer=emissor_esperado(prov), leeway=60,
                            options={"require": ["exp", "iat", "iss", "aud", "sub"]})
    except OIDCErro:
        raise
    except (jwt.PyJWTError, requests.RequestException, ValueError, KeyError) as exc:
        raise OIDCErro(f"ID token inválido: {exc}") from exc
    if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
        raise OIDCErro("nonce do token não confere")
    if prov.tipo == "microsoft":
        tid = str(claims.get("tid", "")).lower()
        t = prov.tenant.strip().lower()
        if len(t) == 36 and tid != t:
            raise OIDCErro("o token é de outro diretório (tenant)")
    return claims


def _email(prov, claims):
    email = str(claims.get("email") or "").strip().lower()
    if not email and prov.tipo == "microsoft":
        pref = str(claims.get("preferred_username") or "").strip().lower()
        email = pref if "@" in pref else ""
    return email


def _email_verificado(prov, claims):
    if prov.tipo == "microsoft":
        return True      # o tenant é fixo e administrado pela empresa; a vinculação por e-mail é opcional e desligada por padrão
    return claims.get("email_verified") in (True, "true")


def resolver_usuario(prov, claims):
    """Decide quem é o usuário do ERP para esta identidade. Levanta OIDCErro se não puder entrar."""
    origem, subject = f"oidc:{prov.slug}", str(claims["sub"])
    email = _email(prov, claims)
    dominio = email.rpartition("@")[2]
    if prov.dominios:
        if not email or dominio not in prov.dominios:
            raise OIDCErro("o domínio do seu e-mail não está autorizado neste sistema")
        if prov.tipo == "google" and str(claims.get("hd", "")).lower() not in prov.dominios:
            raise OIDCErro("a conta Google não pertence a um domínio autorizado")
    ident = IdentidadeExterna.objects.filter(origem=origem, subject=subject).select_related("usuario").first()
    if ident:
        user = ident.usuario
    else:
        if not email or not _email_verificado(prov, claims):
            raise OIDCErro("o provedor não confirmou o seu e-mail")
        candidatos = list(User.objects.filter(email__iexact=email)[:2])
        if len(candidatos) > 1:
            raise OIDCErro("há mais de um usuário com este e-mail; peça ajuda a um administrador")
        if candidatos:
            if not prov.vincular_por_email:
                raise OIDCErro("já existe um usuário com este e-mail que não está ligado a este provedor; "
                               "peça a um administrador para liberar")
            user = candidatos[0]
        elif prov.criar_usuarios:
            user = _criar(prov, email, claims)
        else:
            raise OIDCErro("você ainda não tem cadastro neste sistema; peça a um administrador")
        try:
            with transaction.atomic():
                ident = IdentidadeExterna.objects.create(usuario=user, origem=origem, subject=subject, email=email)
        except IntegrityError:
            ident = IdentidadeExterna.objects.get(origem=origem, subject=subject)
    if not user.is_active:
        raise OIDCErro("usuário desativado")
    ident.ultimo_login = timezone.now()
    ident.email = email or ident.email
    ident.save(update_fields=["ultimo_login", "email"])
    return user


def _criar(prov, email, claims):
    base, i = email[:150], 2
    username = base
    while User.objects.filter(username__iexact=username).exists():
        username, i = f"{base[:140]}-{i}", i + 1
    nome = str(claims.get("name") or "")
    user = User(username=username, email=email[:254], first_name=nome.partition(" ")[0][:150],
                last_name=nome.partition(" ")[2][:150])
    user.set_unusable_password()
    user.save()
    if prov.grupo_padrao_id:
        user.groups.add(prov.grupo_padrao)
    return user
