"""Validadores brasileiros (CPF, CNPJ) e utilitários de formatação."""
import re

from django.core.exceptions import ValidationError


def somente_digitos(valor):
    return re.sub(r"\D", "", valor or "")


def cpf_valido(cpf):
    cpf = somente_digitos(cpf)
    if len(cpf) != 11 or cpf == cpf[0] * 11:
        return False
    for i in (9, 10):
        soma = sum(int(cpf[j]) * ((i + 1) - j) for j in range(i))
        dig = (soma * 10) % 11 % 10
        if dig != int(cpf[i]):
            return False
    return True


def cnpj_valido(cnpj):
    cnpj = somente_digitos(cnpj)
    if len(cnpj) != 14 or cnpj == cnpj[0] * 14:
        return False
    pesos1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    pesos2 = [6] + pesos1
    for pesos, pos in ((pesos1, 12), (pesos2, 13)):
        soma = sum(int(cnpj[i]) * pesos[i] for i in range(len(pesos)))
        resto = soma % 11
        dig = 0 if resto < 2 else 11 - resto
        if dig != int(cnpj[pos]):
            return False
    return True


def validar_cpf(valor):
    if valor and not cpf_valido(valor):
        raise ValidationError("CPF inválido.")


def validar_cnpj(valor):
    if valor and not cnpj_valido(valor):
        raise ValidationError("CNPJ inválido.")


def validar_cpf_cnpj(valor):
    d = somente_digitos(valor)
    if not d:
        return
    if len(d) == 11:
        validar_cpf(d)
    elif len(d) == 14:
        validar_cnpj(d)
    else:
        raise ValidationError("Informe um CPF (11 dígitos) ou CNPJ (14 dígitos).")


def formatar_documento(valor):
    d = somente_digitos(valor)
    if len(d) == 11:
        return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"
    if len(d) == 14:
        return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"
    return valor or ""


# --------------------------------------------------------------------------- uploads
EXTENSOES_BLOQUEADAS = {
    ".exe", ".dll", ".com", ".bat", ".cmd", ".msi", ".msp", ".scr", ".ps1", ".psm1", ".vbs", ".vbe", ".js",
    ".jse", ".wsf", ".wsh", ".hta", ".jar", ".sh", ".php", ".phtml", ".asp", ".aspx", ".jsp", ".cgi", ".py",
    ".lnk", ".reg", ".cpl", ".html", ".htm", ".xhtml", ".svg",
}


def validar_arquivo_seguro(arquivo):
    """Bloqueia executáveis/scripts/HTML e arquivos acima do limite (ARQUIVO_MAX_MB, padrão 50)."""
    import os

    from django.conf import settings

    nome = (getattr(arquivo, "name", "") or "").lower()
    ext = os.path.splitext(nome)[1]
    if ext in EXTENSOES_BLOQUEADAS:
        raise ValidationError(f"Tipo de arquivo não permitido ({ext}). Compacte em .zip se for realmente necessário.")
    limite = getattr(settings, "ARQUIVO_MAX_MB", 50)
    if getattr(arquivo, "size", 0) > limite * 1024 * 1024:
        raise ValidationError(f"Arquivo maior que o limite de {limite} MB.")


def validar_xml(arquivo):
    import os

    validar_arquivo_seguro(arquivo)
    if os.path.splitext((arquivo.name or "").lower())[1] != ".xml":
        raise ValidationError("Envie um arquivo .xml.")
