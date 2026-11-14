"""Leitura de extrato OFX (o formato que os bancos brasileiros exportam). Aceita OFX 1 (SGML) e 2 (XML)."""

import datetime
import re
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError


def _tag(bloco, nome):
    m = re.search(rf"<{nome}>([^<\r\n]*)", bloco, re.I)
    return m.group(1).strip() if m else ""


def ler(conteudo):
    """bytes -> [{fitid, data, valor (com sinal), descricao}]"""
    if len(conteudo) > 5 * 1024 * 1024:
        raise ValidationError("Extrato maior que 5 MB. Exporte um período menor.")
    try:
        texto = conteudo.decode("utf-8")
    except UnicodeDecodeError:
        texto = conteudo.decode("latin-1")
    if "<OFX>" not in texto.upper():
        raise ValidationError("O arquivo não é um extrato OFX. No internet banking, exporte o extrato em "
                              "“OFX” ou “Money”.")
    lancamentos = []
    for bloco in re.findall(r"<STMTTRN>(.*?)</STMTTRN>", texto, re.S | re.I):
        bruto = _tag(bloco, "TRNAMT").replace(" ", "")
        if "," in bruto:
            bruto = bruto.replace(".", "").replace(",", ".")
        try:
            valor = Decimal(bruto).quantize(Decimal("0.01"))
            data = datetime.datetime.strptime(_tag(bloco, "DTPOSTED")[:8], "%Y%m%d").date()
        except (InvalidOperation, ValueError):
            continue
        if valor == 0:
            continue
        desc = _tag(bloco, "MEMO") or _tag(bloco, "NAME") or _tag(bloco, "TRNTYPE")
        fitid = _tag(bloco, "FITID") or f"{data:%Y%m%d}{valor}{desc}"
        lancamentos.append({"fitid": fitid[:120], "data": data, "valor": valor, "descricao": desc[:200]})
    if not lancamentos:
        raise ValidationError("Nenhum lançamento encontrado no extrato.")
    return lancamentos
