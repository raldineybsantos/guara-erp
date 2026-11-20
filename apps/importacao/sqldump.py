"""Leitor de dumps SQL (mysqldump/MariaDB, pg_dump, SQL Server "gerar scripts"): só LÊ os comandos CREATE TABLE,
INSERT e COPY; nunca executa nada do arquivo."""
import re

SCAN = re.compile(r"""'(?:[^'\\]|\\.|'')*'|"(?:[^"\\]|\\.|"")*"|`[^`]*`|--[^\n]*|\#[^\n]*|/\*.*?\*/|;|[^'"`;\-#/]+|[-#/]""",
                  re.S | re.X)
PALAVRAS_RESTRICAO = {"primary", "key", "unique", "constraint", "foreign", "index", "check", "fulltext", "spatial",
                      "exclude", "like", "period"}


def _nome(t):
    t = t.strip()
    if "." in t and not t.startswith(("'", '"', "`", "[")) or re.match(r'^[\[`"]?\w+[\]`"]?\.', t):
        t = re.split(r"\.(?=[\[`\"]?[^.\[\]`\"]+[\]`\"]?$)", t)[-1]
    return t.strip("`\"[]' ")


def _declaracoes(texto):
    """Separa os comandos por ';' respeitando textos entre aspas e comentários."""
    atual, saida = [], []
    for m in SCAN.finditer(texto):
        tok = m.group(0)
        if tok == ";":
            cmd = "".join(atual).strip()
            if cmd:
                saida.append(cmd)
            atual = []
        elif tok.startswith(("--", "#")) or (tok.startswith("/*") and tok.endswith("*/")):
            continue
        else:
            atual.append(tok)
    cmd = "".join(atual).strip()
    if cmd:
        saida.append(cmd)
    return saida


def _colunas_create(corpo):
    itens, nivel, atual, aspas = [], 0, [], None
    for ch in corpo:
        if aspas:
            atual.append(ch)
            if ch == aspas:
                aspas = None
            continue
        if ch in "'\"`":
            aspas = ch
        elif ch == "(":
            nivel += 1
        elif ch == ")":
            nivel -= 1
        if ch == "," and nivel == 0:
            itens.append("".join(atual))
            atual = []
        else:
            atual.append(ch)
    itens.append("".join(atual))
    cols = []
    for it in itens:
        it = it.strip()
        if not it:
            continue
        m = re.match(r"""(`[^`]+`|"[^"]+"|\[[^\]]+\]|[\w$]+)""", it)
        if not m:
            continue
        nome = _nome(m.group(1))
        if nome.lower() in PALAVRAS_RESTRICAO and not m.group(1)[0] in "`\"[":
            continue
        cols.append(nome)
    return cols


ESC = {"n": "\n", "r": "\r", "t": "\t", "0": "\0", "b": "\b", "Z": "\x1a", "\\": "\\", "'": "'", '"': '"'}


def _valores(texto):
    """Lê tuplas '(1,'a'),(2,NULL)' e devolve listas de valores."""
    linhas, atual, i, n = [], None, 0, len(texto)
    while i < n:
        c = texto[i]
        if c == "(" and atual is None:
            atual, i = [], i + 1
            continue
        if atual is None:
            i += 1
            continue
        if c in " \t\r\n":
            i += 1
            continue
        if c == ")":
            linhas.append(atual)
            atual = None
            i += 1
            continue
        if c == ",":
            i += 1
            continue
        pref = ""
        if c in "NnEeBbXx" and i + 1 < n and texto[i + 1] == "'" and c.upper() in "NEBX":
            pref, i, c = c.upper(), i + 1, texto[i + 1]
        if c == "'":
            i += 1
            buf = []
            while i < n:
                ch = texto[i]
                if ch == "\\" and i + 1 < n:
                    buf.append(ESC.get(texto[i + 1], texto[i + 1]))
                    i += 2
                elif ch == "'":
                    if i + 1 < n and texto[i + 1] == "'":
                        buf.append("'")
                        i += 2
                    else:
                        i += 1
                        break
                else:
                    buf.append(ch)
                    i += 1
            atual.append("".join(buf))
            continue
        j = i
        nivel = 0
        while j < n:
            ch = texto[j]
            if ch == "(":
                nivel += 1
            elif ch == ")":
                if nivel == 0:
                    break
                nivel -= 1
            elif ch == "," and nivel == 0:
                break
            j += 1
        cru = texto[i:j].strip()
        i = j
        baixo = cru.lower()
        if baixo == "null":
            atual.append(None)
        elif baixo in ("true", "false"):
            atual.append(baixo == "true")
        elif re.fullmatch(r"-?\d+", cru):
            atual.append(int(cru))
        elif re.fullmatch(r"-?\d*\.\d+(e[-+]?\d+)?|-?\d+e[-+]?\d+", baixo):
            atual.append(float(cru))
        else:
            atual.append(cru)       # função (NOW()), hexadecimal… fica como texto
    return linhas


def _copy(bloco):
    linhas = []
    for ln in bloco.split("\n"):
        campos = []
        for c in ln.split("\t"):
            if c == "\\N":
                campos.append(None)
            else:
                campos.append(re.sub(r"\\(.)", lambda m: ESC.get(m.group(1), m.group(1)), c))
        linhas.append(campos)
    return linhas


def ler_dump(texto, limite):
    """{tabela: {"colunas": [...], "linhas": [[...]], "total": n}} — guarda só `limite` linhas por tabela."""
    tabelas = {}

    def tab(nome):
        return tabelas.setdefault(nome, {"colunas": [], "linhas": [], "total": 0})

    def adicionar(nome, cols, linhas):
        t = tab(nome)
        if cols and not t["colunas"]:
            t["colunas"] = cols
        for ln in linhas:
            t["total"] += 1
            if len(t["linhas"]) < limite:
                t["linhas"].append(ln)

    def copiar(m):
        nome = _nome(m.group(1))
        cols = [_nome(c) for c in m.group(2).split(",")]
        adicionar(nome, cols, _copy(m.group(3)))
        return ""

    texto = re.sub(r"(?im)^COPY\s+(\S+)\s*\(([^)]*)\)\s+FROM\s+stdin;\n(.*?)\n\\\.\s*$", copiar, texto, flags=re.S)
    for cmd in _declaracoes(texto):
        baixo = cmd[:20].lower()
        if baixo.startswith("create table") or re.match(r"(?is)create\s+(?:temporary\s+)?table", cmd):
            m = re.match(r"(?is)create\s+(?:temporary\s+)?table\s+(?:if\s+not\s+exists\s+)?(\S+?)\s*\((.*)\)[^)]*$", cmd)
            if m:
                tab(_nome(m.group(1)))["colunas"] = _colunas_create(m.group(2))
        elif re.match(r"(?is)insert\s+(?:ignore\s+)?(?:into\s+)?", cmd):
            m = re.match(r"(?is)insert\s+(?:ignore\s+)?(?:into\s+)?(\S+?)\s*(\(([^)]*)\))?\s*values\s*(.*)$", cmd)
            if m:
                cols = [_nome(c) for c in m.group(3).split(",")] if m.group(3) else None
                nome = _nome(m.group(1))
                linhas = _valores(m.group(4))
                adicionar(nome, cols or tab(nome)["colunas"], linhas)
    for t in tabelas.values():
        if not t["colunas"] and t["linhas"]:
            t["colunas"] = [f"coluna_{i + 1}" for i in range(len(t["linhas"][0]))]
    return {k: v for k, v in tabelas.items() if v["total"] or v["colunas"]}
