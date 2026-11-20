"""Conexão direta a bancos de origem (MariaDB/MySQL e PostgreSQL), sempre em modo somente leitura.

A senha é usada só durante a leitura: o ERP não a grava (fica cifrada na sessão do usuário, por poucos minutos,
até a tabela ser escolhida)."""
import ipaddress
import socket

from django.conf import settings
from django.core.exceptions import ValidationError

from .leitores import Tabela, limite_linhas

TIPOS = [("mariadb", "MariaDB / MySQL", 3306), ("postgres", "PostgreSQL", 5432)]
PORTA_PADRAO = {k: p for k, _, p in TIPOS}


def _conferir_destino(host, porta):
    """Recusa o endereço de metadados de nuvem (169.254.x.x) e o próprio banco do ERP."""
    try:
        infos = socket.getaddrinfo(host, porta, proto=socket.IPPROTO_TCP)
    except OSError:
        raise ValidationError("Não foi possível encontrar o servidor informado.") from None
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_link_local or ip.is_multicast or ip.is_unspecified:
            raise ValidationError("Endereço de servidor não permitido.")
    d = settings.DATABASES["default"]
    if str(d.get("PORT") or 5432) == str(porta) and host.lower() in {str(d.get("HOST", "")).lower()} - {""}:
        raise ValidationError("Este é o banco do próprio ERP; escolha o banco de origem de outro sistema.")


def abrir(tipo, host, porta, banco, usuario, senha):
    if tipo not in PORTA_PADRAO:
        raise ValidationError("Tipo de banco não suportado. Para outros bancos, exporte para CSV, Excel ou .sql.")
    porta = int(porta or PORTA_PADRAO[tipo])
    host = (host or "").strip()
    if not host or not banco:
        raise ValidationError("Informe o servidor e o nome do banco.")
    _conferir_destino(host, porta)
    try:
        if tipo == "mariadb":
            import pymysql
            con = pymysql.connect(host=host, port=porta, user=usuario, password=senha, database=banco,
                                  connect_timeout=10, read_timeout=120, charset="utf8mb4")
            with con.cursor() as cur:
                cur.execute("SET SESSION TRANSACTION READ ONLY")
            return con
        import psycopg
        con = psycopg.connect(host=host, port=porta, user=usuario, password=senha, dbname=banco, connect_timeout=10,
                              options="-c default_transaction_read_only=on -c statement_timeout=120000")
        return con
    except ValidationError:
        raise
    except Exception:
        raise ValidationError("Não foi possível conectar. Confira servidor, porta, banco, usuário e senha, e se o "
                              "servidor aceita conexões deste computador.") from None


def listar_tabelas(con, tipo, banco):
    cur = con.cursor()
    if tipo == "mariadb":
        cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = %s ORDER BY table_name",
                    (banco,))
    else:
        cur.execute("SELECT table_schema || '.' || table_name FROM information_schema.tables "
                    "WHERE table_schema NOT IN ('pg_catalog','information_schema') ORDER BY 1")
    nomes = [r[0] for r in cur.fetchall()]
    cur.close()
    if not nomes:
        raise ValidationError("O banco não tem tabelas visíveis para este usuário.")
    return nomes


def ler_tabela(con, tipo, banco, nome):
    if nome not in listar_tabelas(con, tipo, banco):         # só tabelas que o próprio banco listou
        raise ValidationError("Tabela não encontrada.")
    lim = limite_linhas()
    cur = con.cursor()
    if tipo == "mariadb":
        ident = "`" + nome.replace("`", "``") + "`"
        cur.execute(f"SELECT COUNT(*) FROM {ident}")
        total = cur.fetchone()[0]
        cur.execute(f"SELECT * FROM {ident} LIMIT {int(lim)}")
    else:
        from psycopg import sql
        esquema, _, tab = nome.partition(".")
        ident = sql.Identifier(esquema, tab)
        cur.execute(sql.SQL("SELECT COUNT(*) FROM {}").format(ident))
        total = cur.fetchone()[0]
        cur.execute(sql.SQL("SELECT * FROM {} LIMIT {}").format(ident, sql.Literal(int(lim))))
    cols = [d[0] for d in cur.description]
    linhas = cur.fetchall()
    cur.close()
    return Tabela(nome, cols, linhas, total=total)
