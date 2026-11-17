# Guará ERP — gestão empresarial livre
# Copyright (C) 2026 Raldiney Santos e colaboradores
# SPDX-License-Identifier: GPL-3.0-or-later
import segno
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from . import mfa, totp


def _destino(request):
    nxt = request.POST.get("next") or request.GET.get("next") or "/"
    return nxt if url_has_allowed_host_and_scheme(nxt, None) and nxt.startswith("/") else "/"


@never_cache
@login_required
def verificar(request):
    """Segunda etapa do login: código do aplicativo ou código de recuperação."""
    if not mfa.precisa_verificar(request.user):
        return redirect(_destino(request))
    if not mfa.tem_fator(request.user):
        return redirect("seguranca:mfa_ativar")
    if request.session.get("mfa_ok"):
        return redirect(_destino(request))
    erro = bloqueado = False
    if request.method == "POST":
        if mfa.verificar(request, request.user, request.POST.get("codigo", "")):
            mfa.liberar(request)
            return redirect(_destino(request))
        bloqueado = mfa.bloqueado(request.user)
        erro = not bloqueado
    return render(request, "seguranca/verificar.html", {"titulo": "Verificação em duas etapas", "icone": "shield-lock",
                                                         "erro": erro, "bloqueado": bloqueado,
                                                         "next": _destino(request),
                                                         "tem_totp": mfa.dispositivo(request.user) is not None,
                                                         "tem_chave": mfa.chaves(request.user).exists()},
                  status=429 if bloqueado else 200)


@never_cache
@login_required
def painel(request):
    disp = mfa.dispositivo(request.user)
    ativo = mfa.tem_fator(request.user)
    return render(request, "seguranca/painel.html", {
        "titulo": "Segurança da conta", "icone": "shield-lock", "ativo": ativo, "tem_totp": disp is not None,
        "chaves": mfa.chaves(request.user), "obrigatorio": mfa.exigido(request.user),
        "restantes": mfa.codigos_restantes(request.user) if ativo else 0,
        "desde": disp.confirmado_em if disp else None, "mfa_ligado": mfa.ativo()})


def _qr(segredo, user):
    uri = totp.uri(segredo, user.username, settings.MFA_EMISSOR)
    return segno.make(uri, error="m").svg_inline(scale=5, border=2, light="white")


@never_cache
@login_required
def ativar(request):
    """Cadastra o aplicativo autenticador: QR/chave + um código para provar que funcionou."""
    if mfa.dispositivo(request.user) is not None:
        return redirect("seguranca:mfa_painel")
    disp = mfa.iniciar_cadastro(request.user)
    erro = False
    if request.method == "POST":
        codigos = mfa.confirmar_cadastro(request, request.user, request.POST.get("codigo", ""))
        if codigos:
            mfa.liberar(request)
            return render(request, "seguranca/codigos.html", {"titulo": "Códigos de recuperação", "icone": "key",
                                                              "codigos": codigos, "primeira": True})
        erro = True
    segredo = disp.obter_segredo()
    return render(request, "seguranca/ativar.html", {
        "titulo": "Ativar verificação em duas etapas", "icone": "shield-lock", "qr": _qr(segredo, request.user),
        "chave": " ".join(segredo[i:i + 4] for i in range(0, len(segredo), 4)), "erro": erro,
        "obrigatorio": mfa.exigido(request.user), "next": _destino(request)},
        status=400 if erro else 200)


def _exigir_codigo(request):
    """Ações sensíveis pedem um código válido agora (mesmo com a sessão já liberada)."""
    return mfa.verificar(request, request.user, request.POST.get("codigo", ""))


@never_cache
@login_required
@require_POST
def novos_codigos(request):
    if not mfa.tem_fator(request.user):
        return redirect("seguranca:mfa_painel")
    if not _exigir_codigo(request):
        messages.error(request, "Código inválido (ou conta bloqueada por alguns minutos). Nada foi alterado.")
        return redirect("seguranca:mfa_painel")
    codigos = mfa.gerar_codigos_recuperacao(request.user)
    mfa.registrar(request.user, "Códigos de recuperação do 2FA regenerados", request)
    return render(request, "seguranca/codigos.html", {"titulo": "Códigos de recuperação", "icone": "key",
                                                      "codigos": codigos, "primeira": False})


@never_cache
@login_required
@require_POST
def desativar(request):
    if mfa.exigido(request.user):
        messages.error(request, "Seu perfil exige verificação em duas etapas; ela não pode ser desativada.")
        return redirect("seguranca:mfa_painel")
    if not mfa.tem_fator(request.user) or not _exigir_codigo(request):
        messages.error(request, "Código inválido (ou conta bloqueada por alguns minutos). Nada foi alterado.")
        return redirect("seguranca:mfa_painel")
    mfa.redefinir(request.user, request.user, request)
    messages.success(request, "Verificação em duas etapas desativada.")
    return redirect("seguranca:mfa_painel")


def _admin(u):
    return u.is_superuser


@login_required
@user_passes_test(_admin)
def usuarios(request):
    """Superusuário vê quem usa 2FA e redefine para quem perdeu o celular e os códigos."""
    lista = []
    for u in User.objects.filter(is_active=True).order_by("username"):
        d = mfa.dispositivo(u)
        ativo = mfa.tem_fator(u)
        lista.append({"u": u, "ativo": ativo, "desde": d.confirmado_em if d else None,
                      "exigido": mfa.exigido(u), "restantes": mfa.codigos_restantes(u) if ativo else 0,
                      "chaves": mfa.chaves(u).count()})
    return render(request, "seguranca/usuarios.html", {"titulo": "Dois fatores por usuário", "icone": "people",
                                                       "lista": lista})


@login_required
@user_passes_test(_admin)
@require_POST
def redefinir(request, pk):
    alvo = get_object_or_404(User, pk=pk)
    if alvo.pk == request.user.pk:
        messages.error(request, "Para si mesmo, use a página Segurança da conta.")
    else:
        mfa.redefinir(alvo, request.user, request)
        messages.success(request, f"2FA de {alvo.username} redefinido. No próximo login ele cadastrará outro aparelho.")
    return redirect("seguranca:mfa_usuarios")
