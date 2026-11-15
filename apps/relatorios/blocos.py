"""Blocos dos dashboards: indicador (kpi), gráfico e tabela. Cada dashboard é uma lista de blocos."""
from decimal import Decimal


def num(v):
    return float(v or 0)


def brl(v):
    v = Decimal(str(v or 0))
    s = f"{abs(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"-R$ {s}" if v < 0 else f"R$ {s}"


def fmt_valor(v, f):
    if v is None or v == "":
        return "—"
    if f == "moeda":
        return brl(v)
    if f == "pct":
        return f"{num(v):.1f}%".replace(".", ",")
    if f == "int":
        return f"{int(round(num(v))):,}".replace(",", ".")
    if f == "num":
        return f"{num(v):,.1f}".replace(",", "X").replace(".", ",").replace("X", ".")
    if f == "dias":
        return f"{num(v):.1f} dias".replace(".", ",")
    if f == "horas":
        return f"{num(v):.1f} h".replace(".", ",")
    return str(v)


def kpi(rotulo, valor, fmt="moeda", delta=None, bom="alto", dica="", url=None, tom=""):
    """`delta` = variação % contra o período anterior; `bom` diz se subir é bom ("alto") ou ruim ("baixo")."""
    cor = ""
    if delta is not None and abs(delta) >= 0.05:
        melhorou = (delta > 0) == (bom == "alto")
        cor = "text-success" if melhorou else "text-danger"
    return {"tipo": "kpi", "rotulo": rotulo, "texto": fmt_valor(valor, fmt), "delta": delta,
            "delta_txt": None if delta is None else f"{delta:+.1f}%".replace(".", ","),
            "delta_cor": cor, "dica": dica, "url": url, "tom": tom}


def grafico(titulo, tipo, labels, series, fmt="moeda", largura=6, empilhado=False, nota=""):
    """tipo: bar | line | hbar | doughnut. `series` = [(nome, [valores])]."""
    return {"tipo": "grafico", "titulo": titulo, "grafico": tipo, "labels": list(labels), "fmt": fmt,
            "series": [{"nome": n, "dados": [num(x) for x in d]} for n, d in series],
            "largura": largura, "empilhado": empilhado, "nota": nota,
            "vazio": not any(num(x) for _, d in series for x in d)}


def tabela(titulo, colunas, linhas, fmts=None, largura=6, nota="", csv=True):
    """`linhas` com valores crus; `fmts` = formato de cada coluna (moeda, int, pct, dias, horas ou None)."""
    fmts = fmts or [None] * len(colunas)
    return {"tipo": "tabela", "titulo": titulo, "colunas": list(colunas), "linhas": [list(l) for l in linhas],
            "formatadas": [[fmt_valor(v, f) if f else ("—" if v in (None, "") else str(v))
                            for v, f in zip(l, fmts)] for l in linhas],
            "alinha": ["end" if f else "start" for f in fmts], "largura": largura, "nota": nota, "csv": csv}


def aviso(texto, tom="info"):
    return {"tipo": "aviso", "texto": texto, "tom": tom}
