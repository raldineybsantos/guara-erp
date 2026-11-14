"""Cofre do certificado A1: o .pfx e a senha ficam cifrados no banco (Fernet/AES) e só são abertos em
memória na hora de assinar. A chave do cofre vem de FISCAL_CHAVE_COFRE; sem ela, deriva-se da SECRET_KEY."""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ValidationError


def _fernet():
    chave = getattr(settings, "FISCAL_CHAVE_COFRE", "") or settings.SECRET_KEY
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(chave.encode()).digest()))


def cifrar(dados: bytes) -> bytes:
    return _fernet().encrypt(dados)


def decifrar(dados) -> bytes:
    try:
        return _fernet().decrypt(bytes(dados))
    except InvalidToken:
        raise ValidationError("Não foi possível abrir o certificado guardado: a chave do cofre mudou "
                              "(FISCAL_CHAVE_COFRE ou SECRET_KEY). Envie o certificado de novo.") from None
