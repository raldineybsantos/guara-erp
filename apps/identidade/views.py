# Guará ERP — gestão empresarial livre
# Copyright (C) 2026 Raldiney Santos e colaboradores
# SPDX-License-Identifier: GPL-3.0-or-later
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from apps.core.models import LogAuditoria
from apps.core.signals import ip_da_requisicao

from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt

from . import ldap_auth, oidc, saml
from .forms import LDAPForm, OIDCForm, SAMLForm, TokenSCIMForm
from .models import ConfiguracaoLDAP, IdentidadeExterna, ProvedorOIDC, ProvedorSAML, TokenSCIM


def _falha(request, descricao, usuario=""):
    LogAuditoria.objects.create(usuario=None, acao="acao", modelo="auth.login", objeto_id=(usuario or "sso")[:50],
                                descricao=f"FALHA de login SSO: {descricao}"[:300], ip=ip_da_requisicao(request))


def _seguro(destino):
    return destino if destino and url_has_allowed_host_and_scheme(destino, None) and destino.startswith("/") else "/"


@never_cache
def entrar(request, slug):
    """Começa o login no provedor (público)."""
    prov = get_object_or_404(ProvedorOIDC, slug=slug, ativo=True)
    try:
        url = oidc.iniciar(request, prov, _seguro(request.GET.get("next")))
    except oidc.OIDCErro as exc:
        messages.error(request, f"Não foi possível entrar com {prov.nome}: {exc}")
        return redirect("login")
    return redirect(url)


@never_cache
def retorno(request, slug):
    """Volta do provedor: valida tudo e abre a sessão."""
    prov = get_object_or_404(ProvedorOIDC, slug=slug, ativo=True)
    try:
        claims, proximo = oidc.validar_retorno(request, prov, request.GET)
        user = oidc.resolver_usuario(prov, claims)
    except oidc.OIDCErro as exc:
        _falha(request, f"{prov.nome}: {exc}", str(request.GET.get("login_hint", "")))
        messages.error(request, f"Não foi possível entrar com {prov.nome}: {exc}.")
        return redirect("login")
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    from apps.seguranca import mfa

    # O provedor já fez a verificação forte. O 2FA do ERP só é exigido se o administrador marcou isso no provedor.
    request.session["mfa_ok"] = not (prov.exigir_2fa_erp and mfa.precisa_verificar(user))
    LogAuditoria.objects.create(usuario=user, acao="acao", modelo="auth.login", objeto_id=user.username[:50],
                                descricao=f"Login por SSO ({prov.nome}): {user.username}", ip=ip_da_requisicao(request))
    return redirect(_seguro(proximo))


def _admin(u):
    return u.is_superuser


@login_required
@user_passes_test(_admin)
def painel(request):
    return render(request, "identidade/painel.html", {
        "titulo": "Login corporativo (LDAP e SSO)", "icone": "diagram-3",
        "ldaps": ConfiguracaoLDAP.objects.all(), "provedores": ProvedorOIDC.objects.all(),
        "samls": [{"p": p, **saml.urls(request, p)} for p in ProvedorSAML.objects.all()],
        "tokens": TokenSCIM.objects.all(), "scim_url": f"{saml.base_url(request)}/scim/v2",
        "vinculos": IdentidadeExterna.objects.count(), "token_novo": request.session.pop("scim_token_novo", None)})


def _formulario(request, modelo, formulario, pk, titulo, voltar="identidade:painel"):
    obj = get_object_or_404(modelo, pk=pk) if pk else None
    form = formulario(request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        salvo = form.save()
        messages.success(request, f"{salvo} salvo.")
        return redirect(voltar)
    return render(request, "form_simples.html", {"titulo": titulo, "icone": "diagram-3", "form": form, "uma_coluna": False})


@login_required
@user_passes_test(_admin)
def ldap_form(request, pk=None):
    return _formulario(request, ConfiguracaoLDAP, LDAPForm, pk, "Diretório LDAP / Active Directory")


@login_required
@user_passes_test(_admin)
def oidc_form(request, pk=None):
    return _formulario(request, ProvedorOIDC, OIDCForm, pk, "Provedor de login único (OIDC)")


@login_required
@user_passes_test(_admin)
@require_POST
def ldap_testar(request, pk):
    cfg = get_object_or_404(ConfiguracaoLDAP, pk=pk)
    resultados = ldap_auth.testar(cfg, request.POST.get("login", "").strip(), request.POST.get("senha", ""))
    return render(request, "identidade/teste.html", {"titulo": f"Teste: {cfg.nome}", "icone": "diagram-3", "resultados": resultados})


@login_required
@user_passes_test(_admin)
@require_POST
def excluir(request, tipo, pk):
    modelo = {"ldap": ConfiguracaoLDAP, "oidc": ProvedorOIDC, "saml": ProvedorSAML}.get(tipo)
    if modelo is None:
        return redirect("identidade:painel")
    obj = get_object_or_404(modelo, pk=pk)
    obj.delete()
    messages.success(request, "Removido. Os usuários continuam existindo, mas deixam de entrar por esse caminho.")
    return redirect("identidade:painel")


# ---------------------------------------------------------------- SAML 2.0
@never_cache
def saml_entrar(request, slug):
    prov = get_object_or_404(ProvedorSAML, slug=slug, ativo=True)
    url, vinculo = saml.iniciar(request, prov, _seguro(request.GET.get("next")))
    r = redirect(url)
    r.set_cookie(saml.COOKIE, vinculo, max_age=saml.VALIDADE_PEDIDO, httponly=True, secure=request.is_secure(),
                 samesite="None" if request.is_secure() else "Lax", path=f"/identidade/saml/{prov.slug}/")
    return r


@csrf_exempt                    # quem posta aqui é o provedor; a defesa é o RelayState de uso único + assinatura
@never_cache
@require_POST
def saml_acs(request, slug):
    prov = get_object_or_404(ProvedorSAML, slug=slug, ativo=True)
    try:
        info, proximo = saml.validar_retorno(request, prov, request.POST)
        user = saml.resolver_usuario(prov, info)
    except saml.SAMLErro as exc:
        _falha(request, f"{prov.nome} (SAML): {exc}")
        messages.error(request, f"Não foi possível entrar com {prov.nome}: {exc}.")
        return redirect("login")
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    from apps.seguranca import mfa

    request.session["mfa_ok"] = not (prov.exigir_2fa_erp and mfa.precisa_verificar(user))
    LogAuditoria.objects.create(usuario=user, acao="acao", modelo="auth.login", objeto_id=user.username[:50],
                                descricao=f"Login por SSO SAML ({prov.nome}): {user.username}", ip=ip_da_requisicao(request))
    r = redirect(_seguro(proximo))
    r.delete_cookie(saml.COOKIE, path=f"/identidade/saml/{prov.slug}/")
    return r


def saml_metadata(request, slug):
    prov = get_object_or_404(ProvedorSAML, slug=slug)
    return HttpResponse(saml.metadados(request, prov), content_type="application/samlmetadata+xml")


@login_required
@user_passes_test(_admin)
def saml_form(request, pk=None):
    return _formulario(request, ProvedorSAML, SAMLForm, pk, "Provedor SAML 2.0")


# ---------------------------------------------------------------- SCIM
@login_required
@user_passes_test(_admin)
def token_novo(request):
    form = TokenSCIMForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        obj, token = TokenSCIM.novo(form.cleaned_data["nome"], form.cleaned_data["grupo_padrao"])
        LogAuditoria.objects.create(usuario=request.user, acao="criar", modelo="scim", objeto_id=obj.nome[:50],
                                    descricao=f"Token SCIM '{obj.nome}' criado", ip=ip_da_requisicao(request))
        request.session["scim_token_novo"] = {"nome": obj.nome, "token": token}
        return redirect("identidade:painel")
    return render(request, "form_simples.html", {"titulo": "Novo token SCIM", "icone": "diagram-3", "form": form,
                                                 "uma_coluna": False})


@login_required
@user_passes_test(_admin)
@require_POST
def token_revogar(request, pk):
    t = get_object_or_404(TokenSCIM, pk=pk)
    t.ativo = False
    t.save(update_fields=["ativo"])
    LogAuditoria.objects.create(usuario=request.user, acao="acao", modelo="scim", objeto_id=t.nome[:50],
                                descricao=f"Token SCIM '{t.nome}' revogado", ip=ip_da_requisicao(request))
    messages.success(request, "Token revogado. O provedor deixa de conseguir provisionar usuários.")
    return redirect("identidade:painel")
