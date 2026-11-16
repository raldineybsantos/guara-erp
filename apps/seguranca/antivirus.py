# Guará ERP — gestão empresarial livre
# Copyright (C) 2026 Raldiney Santos e colaboradores
# SPDX-License-Identifier: GPL-3.0-or-later
"""Antivírus (ClamAV) pelo protocolo do daemon clamd (INSTREAM), sem dependências: só socket.

Regras:
- Arquivo infectado: recusado e registrado na auditoria.
- ClamAV fora do ar, com erro ou sem resposta: o arquivo é RECUSADO (falha fechada) e os administradores são avisados.
- Arquivo acima do limite que o clamd aceita (ANTIVIRUS_LIMITE_MB, padrão 100): recusado. O limite geral de envio do ERP
  (ARQUIVO_MAX_MB, padrão 50) é menor de propósito. Para aceitar arquivos maiores, aumente os DOIS e o StreamMaxLength do clamd."""
import hashlib
import socket
import struct
from dataclasses import dataclass

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError

CHUNK = 64 * 1024


class AntivirusErro(Exception):
    """ClamAV indisponível, fora de contrato ou com erro."""


@dataclass
class Resultado:
    situacao: str            # "limpo", "infectado"
    assinatura: str = ""


def ativo() -> bool:
    return getattr(settings, "ANTIVIRUS_ATIVO", True)


def limite_bytes() -> int:
    return int(getattr(settings, "ANTIVIRUS_LIMITE_MB", 100)) * 1024 * 1024


def _conectar():
    timeout = getattr(settings, "ANTIVIRUS_TIMEOUT", 120)
    caminho = getattr(settings, "CLAMAV_SOCKET", "")
    try:
        if caminho and hasattr(socket, "AF_UNIX"):
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(timeout)
            s.connect(caminho)
        else:
            s = socket.create_connection((settings.CLAMAV_HOST, int(settings.CLAMAV_PORT)), timeout=min(timeout, 10))
            s.settimeout(timeout)
        return s
    except OSError as exc:
        raise AntivirusErro(f"não foi possível conectar ao ClamAV: {exc}") from exc


def _resposta(s) -> str:
    partes = []
    try:
        while True:
            b = s.recv(4096)
            if not b:
                break
            partes.append(b)
            if b.endswith(b"\0") or b.endswith(b"\n"):
                break
    except OSError as exc:
        raise AntivirusErro(f"sem resposta do ClamAV: {exc}") from exc
    return b"".join(partes).decode("utf-8", "replace").strip("\0\n ")


def _comando(cmd: bytes) -> str:
    s = _conectar()
    try:
        s.sendall(b"z" + cmd + b"\0")
        return _resposta(s)
    finally:
        s.close()


def ping() -> bool:
    try:
        return _comando(b"PING") == "PONG"
    except AntivirusErro:
        return False


def versao() -> str:
    return _comando(b"VERSION")


def escanear(blocos) -> Resultado:
    """Envia os blocos (iterável de bytes) ao clamd. Levanta AntivirusErro se não houver um veredito confiável."""
    s = _conectar()
    try:
        try:
            s.sendall(b"zINSTREAM\0")
            for b in blocos:
                for i in range(0, len(b), CHUNK):
                    parte = b[i:i + CHUNK]
                    s.sendall(struct.pack("!I", len(parte)) + parte)
            s.sendall(struct.pack("!I", 0))
        except (BrokenPipeError, ConnectionResetError):
            pass      # o clamd pode fechar antes (limite estourado): a resposta abaixo diz o motivo
        except OSError as exc:
            raise AntivirusErro(f"falha ao enviar o arquivo ao ClamAV: {exc}") from exc
        resp = _resposta(s)
    finally:
        s.close()
    if resp.endswith("OK"):
        return Resultado("limpo")
    if resp.endswith("FOUND"):
        nome = resp.split(":", 1)[-1].strip()[:-len("FOUND")].strip()
        return Resultado("infectado", nome)
    raise AntivirusErro(f"resposta do ClamAV: {resp or 'vazia'}")


def _blocos(arquivo):
    if hasattr(arquivo, "chunks"):
        arquivo.seek(0)
        yield from arquivo.chunks(CHUNK)
        arquivo.seek(0)
    else:
        pos = arquivo.tell() if hasattr(arquivo, "tell") else 0
        arquivo.seek(0)
        for b in iter(lambda: arquivo.read(CHUNK), b""):
            yield b
        arquivo.seek(pos)


def _tamanho(arquivo):
    if getattr(arquivo, "size", None) is not None:
        return arquivo.size
    pos = arquivo.tell()
    arquivo.seek(0, 2)
    n = arquivo.tell()
    arquivo.seek(pos)
    return n


def _registrar(descricao):
    from apps.core.models import LogAuditoria

    LogAuditoria.objects.create(usuario=None, acao="seguranca", modelo="seguranca.antivirus", objeto_id="",
                                descricao=descricao[:300])


def _avisar_indisponivel(motivo):
    """Um aviso por hora aos superusuários, para não inundar quando cada envio falhar."""
    if cache.add("antivirus-aviso-indisponivel", 1, 3600):
        from django.contrib.auth.models import User

        from apps.core.models import notificar

        notificar(User.objects.filter(is_superuser=True, is_active=True),
                  f"Antivírus indisponível: uploads estão sendo recusados. {motivo}"[:240], "/seguranca/antivirus/", email=True)


def garantir(arquivo) -> str:
    """Examina o arquivo antes de aceitá-lo. Devolve "limpo" (ou "nao_verificado" se o antivírus estiver desligado)
    e levanta ValidationError se houver vírus ou se não for possível verificar. Não examina duas vezes o mesmo objeto."""
    if not ativo():
        return "nao_verificado"
    if getattr(arquivo, "_antivirus_ok", False):
        return "limpo"
    nome = (getattr(arquivo, "name", "") or "arquivo")[:120]
    tam = _tamanho(arquivo)
    if tam > limite_bytes():
        _registrar(f"Arquivo recusado (maior que o limite do antivírus de {limite_bytes() // 1048576} MB): {nome}")
        raise ValidationError(
            f"O arquivo tem {tam / 1048576:.0f} MB e passa do limite de {limite_bytes() // 1048576} MB que o antivírus "
            "consegue verificar. Divida ou compacte o arquivo, ou peça a um administrador para ampliar o limite.")
    try:
        r = escanear(_blocos(arquivo))
    except AntivirusErro as exc:
        _registrar(f"Arquivo recusado, antivírus indisponível ({exc}): {nome}")
        _avisar_indisponivel(str(exc))
        raise ValidationError("Não foi possível verificar o arquivo com o antivírus, então o envio foi recusado por "
                              "segurança. Tente de novo em alguns minutos; se continuar, avise o administrador.") from None
    if r.situacao == "infectado":
        _registrar(f"Arquivo recusado: vírus {r.assinatura} em {nome}")
        raise ValidationError(f"O antivírus detectou uma ameaça no arquivo ({r.assinatura}). O envio foi recusado.")
    arquivo._antivirus_ok = True
    return "limpo"


def validador(arquivo):
    """Para usar em FileField(validators=[...])."""
    garantir(arquivo)


def eicar() -> bytes:
    """Arquivo de teste padrão da indústria (inofensivo). Montado em pedaços para o próprio repositório não ser acusado."""
    return (b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$" + b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE!" + b"$H+H*")


def sha256(blocos) -> str:
    h = hashlib.sha256()
    for b in blocos:
        h.update(b)
    return h.hexdigest()
