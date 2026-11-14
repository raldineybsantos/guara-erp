"""Chave de acesso de 44 dígitos da NF-e/NFC-e, com o dígito verificador (módulo 11)."""

import secrets

from apps.core.validators import somente_digitos

CODIGO_UF = {"RO": 11, "AC": 12, "AM": 13, "RR": 14, "PA": 15, "AP": 16, "TO": 17, "MA": 21, "PI": 22, "CE": 23,
             "RN": 24, "PB": 25, "PE": 26, "AL": 27, "SE": 28, "BA": 29, "MG": 31, "ES": 32, "RJ": 33, "SP": 35,
             "PR": 41, "SC": 42, "RS": 43, "MS": 50, "MT": 51, "GO": 52, "DF": 53}


def digito(chave43):
    pesos = [2, 3, 4, 5, 6, 7, 8, 9]
    soma = sum(int(d) * pesos[i % 8] for i, d in enumerate(reversed(chave43)))
    resto = soma % 11
    return 0 if resto in (0, 1) else 11 - resto


def gerar(uf, data, cnpj, modelo, serie, numero, tipo_emissao=1, codigo=None):
    codigo = secrets.randbelow(10 ** 8) if codigo is None else codigo
    corpo = (f"{CODIGO_UF[uf]:02d}{data:%y%m}{somente_digitos(cnpj):0>14}{int(modelo):02d}{int(serie):03d}"
             f"{int(numero):09d}{tipo_emissao}{codigo:08d}")
    return corpo + str(digito(corpo))


def valida(chave):
    d = somente_digitos(chave)
    return len(d) == 44 and str(digito(d[:43])) == d[43]
