"""Conversão de valores vindos de planilhas e bancos (texto, número, data do Excel…) para os tipos dos campos."""
import datetime
import re
import unicodedata
from decimal import Decimal, InvalidOperation

NULOS = {"", "null", "none", "nan", "nat", "n/a", "#n/a", "-", "--"}
SIM = {"sim", "s", "yes", "y", "true", "verdadeiro", "v", "1", "x", "ativo", "ativa", "on"}
NAO = {"nao", "n", "no", "false", "falso", "f", "0", "inativo", "inativa", "off"}


def sem_acento(t):
    t = unicodedata.normalize("NFKD", str(t))
    return "".join(c for c in t if not unicodedata.combining(c))


def normalizar(t):
    """Minúsculas, sem acento, só letras e números: compara nomes de colunas e valores de escolha."""
    return re.sub(r"[^a-z0-9]+", "", sem_acento(t).lower())


def vazio(v):
    return v is None or (isinstance(v, str) and v.strip().lower() in NULOS)


def texto(v):
    if vazio(v):
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))          # 12345.0 do Excel vira "12345"
    if isinstance(v, (datetime.datetime, datetime.date)):
        return v.isoformat()
    return str(v).strip()


def so_digitos(v):
    return re.sub(r"\D", "", texto(v))


def cpf(v):
    d = so_digitos(v)
    return d.zfill(11) if d and len(d) < 11 else d      # o Excel come os zeros à esquerda


def cnpj_ou_cpf(v):
    d = so_digitos(v)
    if d and 11 < len(d) < 14:
        return d.zfill(14)
    if d and len(d) < 11:
        return d.zfill(11)
    return d


def inteiro(v):
    t = texto(v)
    if not t:
        return None
    if re.fullmatch(r"-?\d{1,3}(\.\d{3})+", t):      # 1.000 num campo inteiro é mil
        t = t.replace(".", "")
    try:
        d = decimal(t)
    except ValueError:
        raise ValueError(f"“{t}” não é um número inteiro.") from None
    if d != d.to_integral_value():
        raise ValueError(f"“{t}” não é um número inteiro.")
    return int(d)


def decimal(v):
    """Aceita 1.234,56 · 1234,56 · 1234.56 · 1,234.56 · R$ 1.234,56 · (50,00)."""
    if isinstance(v, bool):
        raise ValueError("Valor numérico esperado.")
    if isinstance(v, (int, float, Decimal)):
        return Decimal(str(v))
    t = texto(v)
    if not t:
        return None
    neg = t.startswith("(") and t.endswith(")") or t.startswith("-")
    t = re.sub(r"[^\d,.]", "", t)
    if not t:
        raise ValueError(f"“{texto(v)}” não é um número.")
    if "," in t and "." in t:
        dec = "," if t.rfind(",") > t.rfind(".") else "."
        t = t.replace("." if dec == "," else ",", "").replace(dec, ".")
    elif "," in t:
        t = t.replace(",", ".") if t.count(",") == 1 else t.replace(",", "")
    elif t.count(".") > 1:
        t = t.replace(".", "")
    try:
        d = Decimal(t)
    except InvalidOperation:
        raise ValueError(f"“{texto(v)}” não é um número.") from None
    return -d if neg else d


FORMATOS_DATA = ("%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y %H:%M:%S",
                 "%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%d%m%Y")


def data(v):
    if vazio(v):
        return None
    if isinstance(v, datetime.datetime):
        return v.date()
    if isinstance(v, datetime.date):
        return v
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if 1 <= v < 80000:      # número de série do Excel
            return datetime.date(1899, 12, 30) + datetime.timedelta(days=int(v))
        raise ValueError(f"“{v}” não parece uma data.")
    t = str(v).strip()
    if re.fullmatch(r"\d{4,5}(\.0+)?", t):      # número de série do Excel guardado como texto
        return datetime.date(1899, 12, 30) + datetime.timedelta(days=int(float(t)))
    for f in FORMATOS_DATA:
        try:
            return datetime.datetime.strptime(t, f).date()
        except ValueError:
            continue
    raise ValueError(f"“{t}” não é uma data reconhecida (use dd/mm/aaaa).")


def booleano(v):
    if isinstance(v, bool):
        return v
    t = normalizar(texto(v))
    if t in SIM:
        return True
    if t in NAO:
        return False
    raise ValueError(f"“{texto(v)}” não é sim/não.")


def escolha(v, choices, sinonimos=None):
    """Casa o valor com o código ou o rótulo da escolha, sem diferenciar maiúsculas e acentos."""
    t = normalizar(texto(v))
    if not t:
        return ""
    for cod, rot in choices:
        if t in (normalizar(cod), normalizar(rot)):
            return cod
    for k, cod in (sinonimos or {}).items():
        if t == normalizar(k):
            return cod
    validos = ", ".join(str(r) for _, r in choices)
    raise ValueError(f"“{texto(v)}” não é uma opção válida ({validos}).")
