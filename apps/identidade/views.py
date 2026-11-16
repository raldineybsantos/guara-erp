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

from . import ldap_auth, oidc
from .forms import LDAPForm, OIDCForm
from .models import ConfiguracaoLDAP, IdentidadeExterna, ProvedorOIDC


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
        "vinculos": IdentidadeExterna.objects.count()})


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
    modelo = {"ldap": ConfiguracaoLDAP, "oidc": ProvedorOIDC}.get(tipo)
    if modelo is None:
        return redirect("identidade:painel")
    obj = get_object_or_404(modelo, pk=pk)
    obj.delete()
    messages.success(request, "Removido. Os usuários continuam existindo, mas deixam de entrar por esse caminho.")
    return redirect("identidade:painel")
