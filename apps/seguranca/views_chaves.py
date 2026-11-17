# Guará ERP — gestão empresarial livre
# Copyright (C) 2026 Raldiney Santos e colaboradores
# SPDX-License-Identifier: GPL-3.0-or-later
import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from . import chaves as fluxo
from . import mfa
from .models import ChaveSeguranca


def _json(request):
    try:
        d = json.loads(request.body or b"{}")
        return d if isinstance(d, dict) else {}
    except ValueError:
        return {}


def _pode_cadastrar(request) -> bool:
    """Quem já tem 2º fator só cadastra outro depois de passar por ele; quem não tem (cadastro obrigatório) pode."""
    return bool(request.session.get("mfa_ok")) or not mfa.tem_fator(request.user)


@never_cache
@login_required
@require_POST
def registrar_opcoes(request):
    if not mfa.ativo() or not _pode_cadastrar(request):
        return JsonResponse({"erro": "Não permitido."}, status=403)
    return JsonResponse(fluxo.opcoes_registro(request, request.user))


@never_cache
@login_required
@require_POST
def registrar_confirmar(request):
    if not mfa.ativo() or not _pode_cadastrar(request):
        return JsonResponse({"erro": "Não permitido."}, status=403)
    d = _json(request)
    sem_fator = not mfa.tem_fator(request.user)
    chave = fluxo.confirmar_registro(request, request.user, d.get("nome", ""), d.get("credencial") or {})
    if chave is None:
        return JsonResponse({"erro": "Não foi possível validar a chave. Tente de novo."}, status=400)
    destino = "/seguranca/2fa/"
    if sem_fator and mfa.codigos_restantes(request.user) == 0:
        request.session["codigos_novos"] = mfa.gerar_codigos_recuperacao(request.user)
        destino = "/seguranca/2fa/chaves/codigos/"
    if sem_fator:
        mfa.liberar(request)
    return JsonResponse({"ok": True, "destino": destino})


@never_cache
@login_required
def codigos_iniciais(request):
    """Mostra uma única vez os códigos de recuperação gerados no primeiro cadastro de chave."""
    codigos = request.session.pop("codigos_novos", None)
    if not codigos:
        return redirect("seguranca:mfa_painel")
    return render(request, "seguranca/codigos.html", {"titulo": "Códigos de recuperação", "icone": "key",
                                                      "codigos": codigos, "primeira": True})


@never_cache
@login_required
@require_POST
def entrar_opcoes(request):
    if not mfa.ativo() or request.session.get("mfa_ok"):
        return JsonResponse({"erro": "Não permitido."}, status=403)
    op = fluxo.opcoes_login(request, request.user)
    if op is None:
        return JsonResponse({"erro": "Nenhuma chave cadastrada."}, status=400)
    return JsonResponse(op)


@never_cache
@login_required
@require_POST
def entrar_verificar(request):
    if not mfa.ativo() or request.session.get("mfa_ok"):
        return JsonResponse({"erro": "Não permitido."}, status=403)
    if not fluxo.verificar_login(request, request.user, _json(request).get("credencial") or {}):
        return JsonResponse({"erro": "Chave não reconhecida (ou conta bloqueada por alguns minutos)."}, status=400)
    mfa.liberar(request)
    return JsonResponse({"ok": True})


@never_cache
@login_required
@require_POST
def remover(request, pk):
    """Exige a senha da conta. Perfil obrigado a ter 2FA não pode ficar sem nenhum fator."""
    chave = get_object_or_404(ChaveSeguranca, pk=pk, usuario=request.user)
    if not request.session.get("mfa_ok"):
        return redirect("seguranca:mfa_painel")
    if not request.user.check_password(request.POST.get("senha", "")):
        mfa.registrar(request.user, f"FALHA ao remover chave de segurança (senha incorreta): {request.user.username}", request)
        messages.error(request, "Senha incorreta. Nada foi alterado.")
        return redirect("seguranca:mfa_painel")
    restantes = mfa.chaves(request.user).exclude(pk=chave.pk).count() + (1 if mfa.dispositivo(request.user) else 0)
    if restantes == 0 and mfa.exigido(request.user):
        messages.error(request, "Seu perfil exige verificação em duas etapas; cadastre outro fator antes de remover este.")
        return redirect("seguranca:mfa_painel")
    nome = chave.nome
    chave.delete()
    mfa.registrar(request.user, f"Chave de segurança removida: {nome}", request)
    messages.success(request, "Chave removida.")
    return redirect("seguranca:mfa_painel")
