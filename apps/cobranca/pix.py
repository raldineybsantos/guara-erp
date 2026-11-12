"""Geração de PIX copia-e-cola (BR Code estático, padrão EMV do Banco Central)."""
import re
import unicodedata


def _campo(id_, valor):
    return f"{id_}{len(valor):02d}{valor}"


def _crc16(payload):
    crc = 0xFFFF
    for byte in payload.encode("utf-8"):
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else crc << 1
            crc &= 0xFFFF
    return f"{crc:04X}"


def _limpar(texto, tamanho):
    texto = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9 ]", "", texto).strip()[:tamanho] or "NA"


def gerar_pix(chave, nome, cidade, valor=None, txid="***", descricao=""):
    conta = _campo("00", "br.gov.bcb.pix") + _campo("01", chave.strip())
    if descricao:
        conta += _campo("02", _limpar(descricao, 40))
    payload = (
        _campo("00", "01")
        + _campo("26", conta)
        + _campo("52", "0000")
        + _campo("53", "986")
        + (_campo("54", f"{valor:.2f}") if valor else "")
        + _campo("58", "BR")
        + _campo("59", _limpar(nome, 25))
        + _campo("60", _limpar(cidade, 15))
        + _campo("62", _campo("05", re.sub(r"[^A-Za-z0-9]", "", txid)[:25] or "***"))
        + "6304"
    )
    return payload + _crc16(payload)
