# Guará ERP — gestão empresarial livre
# Copyright (C) 2026 Raldiney Santos e colaboradores
# SPDX-License-Identifier: GPL-3.0-or-later
"""Chaves de segurança FIDO2/WebAuthn como segundo fator (biblioteca py_webauthn, BSD-3).

O desafio é gerado no servidor, guardado na sessão, vale uma única vez e expira em 5 minutos. O domínio (rp_id) e a
origem são conferidos pela biblioteca — por isso a chave só funciona no endereço real do ERP (anti-phishing).
"""
import base64
import json
import time
from urllib.parse import urlsplit

import webauthn
from django.conf import settings
from django.utils import timezone
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.structs import (AuthenticatorSelectionCriteria, PublicKeyCredentialDescriptor,
                                      ResidentKeyRequirement, UserVerificationRequirement)

from . import mfa
from .models import ChaveSeguranca

VALIDADE_DESAFIO = 300
CHAVE_SESSAO = "webauthn_desafio"


def rp_id(request) -> str:
    if getattr(settings, "WEBAUTHN_RP_ID", ""):
        return settings.WEBAUTHN_RP_ID
    if settings.ERP_URL_BASE:
        return urlsplit(settings.ERP_URL_BASE).hostname
    return request.get_host().split(":")[0]


def origens(request) -> list[str]:
    if getattr(settings, "WEBAUTHN_ORIGENS", ()):
        return list(settings.WEBAUTHN_ORIGENS)
    if settings.ERP_URL_BASE:
        p = urlsplit(settings.ERP_URL_BASE)
        return [f"{p.scheme}://{p.netloc}"]
    return [f"{'https' if request.is_secure() else 'http'}://{request.get_host()}"]


def _guardar(request, tipo: str, desafio: bytes):
    request.session[CHAVE_SESSAO] = {"t": tipo, "c": bytes_to_base64url(desafio), "ate": time.time() + VALIDADE_DESAFIO}


def _consumir(request, tipo: str) -> bytes | None:
    """Uso único: o desafio sai da sessão ao ser lido, valendo ou não."""
    d = request.session.pop(CHAVE_SESSAO, None)
    if not d or d.get("t") != tipo or d.get("ate", 0) < time.time():
        return None
    return base64url_to_bytes(d["c"])


def _descritores(user):
    return [PublicKeyCredentialDescriptor(id=bytes(c.credencial_id)) for c in mfa.chaves(user)]


def opcoes_registro(request, user) -> dict:
    op = webauthn.generate_registration_options(
        rp_id=rp_id(request), rp_name=getattr(settings, "MFA_EMISSOR", "Guará ERP"),
        user_id=str(user.pk).encode(), user_name=user.username,
        user_display_name=user.get_full_name() or user.username,
        exclude_credentials=_descritores(user),                    # não cadastra a mesma chave duas vezes
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.DISCOURAGED, user_verification=UserVerificationRequirement.PREFERRED))
    _guardar(request, "reg", op.challenge)
    return json.loads(webauthn.options_to_json(op))


def confirmar_registro(request, user, nome: str, credencial) -> ChaveSeguranca | None:
    desafio = _consumir(request, "reg")
    if desafio is None:
        return None
    try:
        v = webauthn.verify_registration_response(
            credential=credencial, expected_challenge=desafio, expected_rp_id=rp_id(request),
            expected_origin=origens(request))
    except Exception:
        mfa.registrar(user, f"FALHA ao cadastrar chave de segurança: {user.username}", request)
        return None
    if ChaveSeguranca.objects.filter(credencial_id=v.credential_id).exists():
        return None
    transportes = ""
    try:
        transportes = ",".join(t for t in (credencial.get("response", {}).get("transports") or []) if isinstance(t, str))[:80]
    except AttributeError:
        pass
    chave = ChaveSeguranca.objects.create(
        usuario=user, nome=(nome or "Chave de segurança").strip()[:60] or "Chave de segurança",
        credencial_id=v.credential_id, chave_publica=v.credential_public_key, contador=v.sign_count,
        transportes=transportes)
    mfa.registrar(user, f"Chave de segurança cadastrada: {chave.nome}", request)
    return chave


def opcoes_login(request, user) -> dict | None:
    descritores = _descritores(user)
    if not descritores:
        return None
    op = webauthn.generate_authentication_options(
        rp_id=rp_id(request), allow_credentials=descritores,
        user_verification=UserVerificationRequirement.PREFERRED)
    _guardar(request, "auth", op.challenge)
    return json.loads(webauthn.options_to_json(op))


def verificar_login(request, user, credencial) -> bool:
    """Confere a assinatura da chave. Contador que não avança (chave clonada) é recusado. Conta para o bloqueio."""
    if mfa.bloqueado(user):
        mfa.registrar(user, "FALHA de 2FA: usuário bloqueado temporariamente por excesso de tentativas", request)
        return False
    desafio = _consumir(request, "auth")
    try:
        cid = base64url_to_bytes(credencial["id"])
        chave = ChaveSeguranca.objects.get(usuario=user, credencial_id=cid)
        if desafio is None:
            raise ValueError("desafio ausente ou expirado")
        v = webauthn.verify_authentication_response(
            credential=credencial, expected_challenge=desafio, expected_rp_id=rp_id(request),
            expected_origin=origens(request), credential_public_key=bytes(chave.chave_publica),
            credential_current_sign_count=chave.contador)
    except Exception:
        mfa.registrar(user, f"FALHA de 2FA (chave de segurança): {user.username}", request)
        return False
    chave.contador, chave.ultimo_uso = v.new_sign_count, timezone.now()
    chave.save(update_fields=["contador", "ultimo_uso"])
    mfa.registrar(user, f"2FA verificado com chave de segurança ({chave.nome})", request)
    return True
