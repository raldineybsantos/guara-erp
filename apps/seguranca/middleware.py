# Guará ERP — gestão empresarial livre
# Copyright (C) 2026 Raldiney Santos e colaboradores
# SPDX-License-Identifier: GPL-3.0-or-later
from urllib.parse import quote

from django.shortcuts import redirect
from django.urls import reverse

from . import mfa
from .models import TrocaSenhaObrigatoria

ISENTOS = ("/login/", "/logout/", "/seguranca/2fa/", "/static/", "/admin/login/")
ISENTOS_SENHA = ("/senha/", "/logout/", "/static/")


class MFAMiddleware:
    """Quem passou pela senha mas não pelo segundo fator só alcança a tela do 2FA (nenhuma outra)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if (user is not None and user.is_authenticated and not request.session.get("mfa_ok")
                and not request.path.startswith(ISENTOS) and mfa.precisa_verificar(user)):
            destino = reverse("seguranca:mfa_verificar" if mfa.tem_fator(user) else "seguranca:mfa_ativar")
            if request.method == "GET" and not request.headers.get("x-requested-with"):
                destino += f"?next={quote(request.get_full_path(), safe='/')}"
            return redirect(destino)
        return self.get_response(request)


class TrocaSenhaMiddleware:
    """Quem tem senha provisória (administrador inicial, senha redefinida) só alcança a tela de trocar a senha."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if (user is not None and user.is_authenticated and not request.path.startswith(ISENTOS_SENHA)
                and TrocaSenhaObrigatoria.pendente(user)):
            return redirect(reverse("password_change"))
        return self.get_response(request)
