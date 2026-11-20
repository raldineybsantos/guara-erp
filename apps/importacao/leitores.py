"""Leitura de arquivos e bancos de origem. Tudo aqui é SOMENTE LEITURA e limitado em linhas, para não esgotar a memória.

Formatos: CSV/TXT, Excel (.xlsx/.xlsm/.xls), JSON (inclusive exportações do Airtable, NocoDB e Baserow), SQLite,
dump SQL (MariaDB/MySQL, PostgreSQL, SQL Server) e Access (.mdb/.accdb, com o mdbtools instalado)."""
import csv
import datetime
import gzip
import io
import json
import os
import shutil
import sqlite3
import subprocess
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError

from . import sqldump

FORMATOS = {".csv": "texto", ".tsv": "texto", ".txt": "texto", ".xlsx": "excel", ".xlsm": "excel", ".xls": "xls",
            ".json": "json", ".sqlite": "sqlite", ".sqlite3": "sqlite", ".db": "sqlite", ".sql": "dump",
            ".mdb": "access", ".accdb": "access"}
ACEITOS = ",".join(FORMATOS)


def limite_linhas():
    return int(getattr(settings, "IMPORTACAO_MAX_LINHAS", 20000))


def formato_de(nome):
    ext = os.path.splitext((nome or "").lower())[1]
    if ext not in FORMATOS:
        raise ValidationError("Formato não suportado. Use: CSV, Excel (.xlsx/.xls), JSON, SQLite, dump .sql ou Access "
                              "(.mdb/.accdb). De outros sistemas (Airtable, NocoDB, Baserow, Notion…), exporte para CSV, "
                              "Excel ou JSON.")
    return FORMATOS[ext]


class Tabela:
    def __init__(self, nome, colunas, linhas, total=None):
        self.nome = nome
        self.colunas = _colunas_unicas(colunas)
        self.linhas = [_ajustar(l, len(self.colunas)) for l in linhas]
        self.total = total if total is not None else len(self.linhas)

    @property
    def truncada(self):
        return self.total > len(self.linhas)


def _colunas_unicas(cols):
    saida, vistos = [], {}
    for i, c in enumerate(cols):
        c = str(c).strip() if c is not None and str(c).strip() else f"coluna_{i + 1}"
        n = vistos.get(c, 0)
        vistos[c] = n + 1
        saida.append(c if n == 0 else f"{c}_{n + 1}")
    return saida


def _ajustar(linha, n):
    linha = [_simples(v) for v in list(linha)[:n]]
    return linha + [None] * (n - len(linha))


def _simples(v):
    """Valor pronto para ir a JSON: datas viram texto ISO; inteiros do Excel (1.0) viram int."""
    if v is None or isinstance(v, (str, bool, int)):
        return v
    if isinstance(v, float):
        return int(v) if v.is_integer() and abs(v) < 1e15 else v
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, datetime.datetime):
        return v.isoformat(sep=" ") if (v.hour or v.minute or v.second) else v.date().isoformat()
    if isinstance(v, (datetime.date, datetime.time)):
        return v.isoformat()
    if isinstance(v, (bytes, bytearray, memoryview)):
        return None             # binários (imagens, anexos) não são importados
    if isinstance(v, (list, tuple, set)):
        return ", ".join(str(_simples(x)) for x in v)
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False, default=str)
    return str(v)


# --------------------------------------------------------------------------- abrir/ler
def tabelas(caminho, nome_original):
    """Nomes das tabelas/planilhas do arquivo."""
    fmt = formato_de(nome_original)
    if fmt == "texto":
        return [os.path.splitext(os.path.basename(nome_original))[0]]
    if fmt == "excel":
        import openpyxl
        wb = _abrir_excel(caminho)
        try:
            return [ws.title for ws in wb.worksheets if ws.sheet_state == "visible"] or wb.sheetnames
        finally:
            wb.close()
    if fmt == "xls":
        return [s.name for s in _abrir_xls(caminho).sheets()]
    if fmt == "json":
        return list(_json(caminho))
    if fmt == "sqlite":
        return _sqlite_tabelas(caminho)
    if fmt == "dump":
        return list(_dump(caminho))
    return _access_tabelas(caminho)


def ler(caminho, nome_original, tabela):
    fmt = formato_de(nome_original)
    lim = limite_linhas()
    if fmt == "texto":
        return _texto(caminho, tabela, lim)
    if fmt == "excel":
        return _excel(caminho, tabela, lim)
    if fmt == "xls":
        return _xls(caminho, tabela, lim)
    if fmt == "json":
        d = _json(caminho)
        if tabela not in d:
            raise ValidationError("Tabela não encontrada no arquivo.")
        return d[tabela]
    if fmt == "sqlite":
        return _sqlite(caminho, tabela, lim)
    if fmt == "dump":
        d = _dump(caminho)
        if tabela not in d:
            raise ValidationError("Tabela não encontrada no dump.")
        return d[tabela]
    return _access(caminho, tabela, lim)


# --------------------------------------------------------------------------- texto
def _decodificar(bruto):
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return bruto.decode(enc)
        except UnicodeDecodeError:
            continue
    return bruto.decode("latin-1", errors="replace")


def _texto(caminho, nome, lim):
    with open(caminho, "rb") as f:
        txt = _decodificar(f.read())
    amostra = txt[:20000]
    try:
        dialeto = csv.Sniffer().sniff(amostra, delimiters=",;\t|")
    except csv.Error:
        dialeto = csv.excel
        dialeto.delimiter = ";" if amostra.count(";") > amostra.count(",") else ","
    csv.field_size_limit(1 << 20)
    leitor = csv.reader(io.StringIO(txt, newline=""), dialeto)
    linhas = [l for l in leitor if any((c or "").strip() for c in l)]
    if not linhas:
        raise ValidationError("O arquivo está vazio.")
    return Tabela(nome, linhas[0], linhas[1:lim + 1], total=len(linhas) - 1)


# --------------------------------------------------------------------------- Excel
def _abrir_excel(caminho):
    import openpyxl
    try:
        return openpyxl.load_workbook(caminho, read_only=True, data_only=True)
    except Exception:
        raise ValidationError("Não foi possível abrir a planilha. Confira se é um .xlsx válido e sem senha.") from None


def _excel(caminho, nome, lim):
    wb = _abrir_excel(caminho)
    try:
        if nome not in wb.sheetnames:
            raise ValidationError("Planilha não encontrada no arquivo.")
        linhas, total, cab = [], 0, None
        for row in wb[nome].iter_rows(values_only=True):
            if not any(c not in (None, "") for c in row):
                continue
            if cab is None:
                cab = list(row)
                continue
            total += 1
            if len(linhas) < lim:
                linhas.append(row)
        if cab is None:
            raise ValidationError("A planilha está vazia.")
        # corta colunas vazias no fim (o Excel costuma anunciar centenas de colunas)
        n = max((i + 1 for i, c in enumerate(cab) if c not in (None, "")), default=0)
        return Tabela(nome, cab[:n], [l[:n] for l in linhas], total=total)
    finally:
        wb.close()


def _abrir_xls(caminho):
    try:
        import xlrd
        return xlrd.open_workbook(caminho, on_demand=True)
    except ImportError:
        raise ValidationError("Para .xls antigo instale a biblioteca xlrd ou salve a planilha como .xlsx.") from None
    except Exception:
        raise ValidationError("Não foi possível abrir o .xls. Salve como .xlsx e tente de novo.") from None


def _xls(caminho, nome, lim):
    import xlrd
    wb = _abrir_xls(caminho)
    sh = wb.sheet_by_name(nome)
    linhas = []
    for r in range(sh.nrows):
        vals = []
        for c in range(sh.ncols):
            cel = sh.cell(r, c)
            if cel.ctype == xlrd.XL_CELL_DATE:
                vals.append(xlrd.xldate_as_datetime(cel.value, wb.datemode))
            elif cel.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
                vals.append(None)
            else:
                vals.append(cel.value)
        if any(v not in (None, "") for v in vals):
            linhas.append(vals)
    if not linhas:
        raise ValidationError("A planilha está vazia.")
    return Tabela(nome, linhas[0], linhas[1:lim + 1], total=len(linhas) - 1)


# --------------------------------------------------------------------------- JSON (inclui Airtable, NocoDB, Baserow)
CHAVES_LISTA = ("records", "results", "list", "rows", "data", "items")


def _registros(lista):
    """Lista de objetos → (colunas, linhas). Registros do Airtable ({id, fields:{…}}) são achatados."""
    achatados = []
    for it in lista:
        if not isinstance(it, dict):
            continue
        if isinstance(it.get("fields"), dict):
            it = {"id": it.get("id"), **it["fields"]}
        achatados.append(it)
    cols = []
    for it in achatados[:500]:
        for k in it:
            if k not in cols:
                cols.append(k)
    return cols, [[it.get(c) for c in cols] for it in achatados]


def _json(caminho):
    with open(caminho, "rb") as f:
        try:
            obj = json.loads(_decodificar(f.read()))
        except ValueError:
            raise ValidationError("O arquivo JSON é inválido.") from None
    lim = limite_linhas()
    listas = {}
    if isinstance(obj, list):
        listas["dados"] = obj
    elif isinstance(obj, dict):
        achou = False
        for k in CHAVES_LISTA:
            if isinstance(obj.get(k), list):
                listas[k] = obj[k]
                achou = True
        if not achou:
            for k, v in obj.items():
                if isinstance(v, list) and v and isinstance(v[0], dict):
                    listas[k] = v
    out = {}
    for nome, lista in listas.items():
        cols, linhas = _registros(lista)
        if cols:
            out[nome] = Tabela(nome, cols, linhas[:lim], total=len(linhas))
    if not out:
        raise ValidationError("Não encontrei uma lista de registros no JSON.")
    return out


# --------------------------------------------------------------------------- SQLite
def _sqlite_conectar(caminho):
    with open(caminho, "rb") as f:
        if f.read(16) != b"SQLite format 3\x00":
            raise ValidationError("O arquivo não é um banco SQLite.")
    return sqlite3.connect(f"file:{caminho}?mode=ro", uri=True)


def _sqlite_tabelas(caminho):
    con = _sqlite_conectar(caminho)
    try:
        return [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    finally:
        con.close()


def _sqlite(caminho, nome, lim):
    con = _sqlite_conectar(caminho)
    try:
        if nome not in _sqlite_tabelas(caminho):
            raise ValidationError("Tabela não encontrada no banco.")
        ident = '"' + nome.replace('"', '""') + '"'
        cur = con.execute(f"SELECT * FROM {ident} LIMIT ?", (lim,))
        cols = [d[0] for d in cur.description]
        linhas = cur.fetchall()
        total = con.execute(f"SELECT COUNT(*) FROM {ident}").fetchone()[0]
        return Tabela(nome, cols, linhas, total=total)
    finally:
        con.close()


# --------------------------------------------------------------------------- dump SQL
def _dump(caminho):
    with open(caminho, "rb") as f:
        txt = _decodificar(f.read())
    d = sqldump.ler_dump(txt, limite_linhas())
    if not d:
        raise ValidationError("Não encontrei tabelas com dados no arquivo .sql (esperado: CREATE TABLE, INSERT ou COPY).")
    return {k: Tabela(k, v["colunas"], v["linhas"], total=v["total"]) for k, v in d.items()}


# --------------------------------------------------------------------------- Access (mdbtools)
def _mdb(*args, entrada=None):
    exe = shutil.which(args[0])
    if not exe:
        raise ValidationError("Para ler arquivos do Access, o servidor precisa do pacote mdbtools (apt install mdbtools). "
                              "Alternativa: no Access, exporte cada tabela para Excel ou CSV.")
    try:
        r = subprocess.run([exe, *args[1:]], capture_output=True, timeout=120, check=True)   # lista fixa, sem shell
    except (subprocess.SubprocessError, OSError):
        raise ValidationError("Não foi possível ler o arquivo do Access (arquivo protegido por senha ou versão não "
                              "suportada). Exporte as tabelas para Excel ou CSV.") from None
    return r.stdout


def _access_tabelas(caminho):
    saida = _mdb("mdb-tables", "-1", caminho).decode("utf-8", "replace")
    nomes = [n.strip() for n in saida.splitlines() if n.strip() and not n.startswith("MSys")]
    if not nomes:
        raise ValidationError("Não encontrei tabelas no arquivo do Access.")
    return nomes


def _access(caminho, nome, lim):
    if nome not in _access_tabelas(caminho):
        raise ValidationError("Tabela não encontrada no arquivo.")
    bruto = _mdb("mdb-export", "-D", "%Y-%m-%d %H:%M:%S", caminho, nome)
    leitor = csv.reader(io.StringIO(bruto.decode("utf-8", "replace"), newline=""))
    linhas = [l for l in leitor if any(c.strip() for c in l)]
    if not linhas:
        raise ValidationError("A tabela está vazia.")
    return Tabela(nome, linhas[0], linhas[1:lim + 1], total=len(linhas) - 1)


# --------------------------------------------------------------------------- área de trabalho (compactada)
def salvar_tabela(tabela, caminho):
    """Grava a tabela lida em .json.gz; é isso que o resto do assistente usa (o arquivo original é descartado)."""
    with gzip.open(caminho, "wt", encoding="utf-8") as f:
        json.dump({"nome": tabela.nome, "colunas": tabela.colunas, "linhas": tabela.linhas, "total": tabela.total}, f,
                  ensure_ascii=False, default=str)


def carregar_tabela(caminho):
    with gzip.open(caminho, "rt", encoding="utf-8") as f:
        d = json.load(f)
    return Tabela(d["nome"], d["colunas"], d["linhas"], total=d["total"])
