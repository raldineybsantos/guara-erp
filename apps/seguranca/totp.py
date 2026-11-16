# Guará ERP — gestão empresarial livre
# Copyright (C) 2026 Raldiney Santos e colaboradores
# SPDX-License-Identifier: GPL-3.0-or-later
"""TOTP (RFC 6238) e HOTP (RFC 4226) só com a biblioteca padrão: SHA-1, 6 dígitos, passo de 30 s,
que é o que Google Authenticator, Microsoft Authenticator, Authy, 1Password e Bitwarden esperam."""
import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

PASSO = 30
DIGITOS = 6


def novo_segredo() -> str:
    """160 bits aleatórios em base32 (sem preenchimento), como recomenda a RFC 4226."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _bytes(segredo: str) -> bytes:
    s = segredo.strip().replace(" ", "").upper()
    return base64.b32decode(s + "=" * (-len(s) % 8))


def hotp(segredo: str, contador: int) -> str:
    h = hmac.new(_bytes(segredo), struct.pack(">Q", contador), hashlib.sha1).digest()
    i = h[-1] & 0x0F
    n = (struct.unpack(">I", h[i:i + 4])[0] & 0x7FFFFFFF) % (10 ** DIGITOS)
    return str(n).zfill(DIGITOS)


def passo_atual(agora: float | None = None) -> int:
    return int((time.time() if agora is None else agora) // PASSO)


def codigo(segredo: str, agora: float | None = None) -> str:
    return hotp(segredo, passo_atual(agora))


def verificar(segredo: str, informado: str, ultimo_passo: int = 0, janela: int = 1, agora: float | None = None):
    """Devolve o passo aceito (int) ou None. Tolera ±janela passos de relógio e recusa passo já usado
    (um código vale uma vez só, mesmo dentro da janela de 30 s)."""
    informado = "".join(c for c in (informado or "") if c.isdigit())
    if len(informado) != DIGITOS:
        return None
    atual = passo_atual(agora)
    aceito = None
    for p in range(atual - janela, atual + janela + 1):
        if hmac.compare_digest(hotp(segredo, p), informado) and p > ultimo_passo:
            aceito = p
    return aceito


def uri(segredo: str, conta: str, emissor: str) -> str:
    return (f"otpauth://totp/{quote(emissor)}:{quote(conta)}?secret={segredo}&issuer={quote(emissor)}"
            f"&algorithm=SHA1&digits={DIGITOS}&period={PASSO}")
