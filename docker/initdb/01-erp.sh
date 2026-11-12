#!/bin/sh
# Executado pelo PostgreSQL só na PRIMEIRA criação do volume.
# Cria o usuário da aplicação SEM privilégios de superusuário (princípio do menor privilégio):
# mesmo que houvesse uma falha de injeção de SQL, ele não consegue executar comandos no
# sistema operacional (COPY ... PROGRAM), ler arquivos do servidor, criar extensões ou outros usuários.
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
  -v erp_user="$ERP_USER" -v erp_pass="$ERP_PASSWORD" -v erp_db="$ERP_DB" <<'SQL'
CREATE ROLE :"erp_user" LOGIN PASSWORD :'erp_pass' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
CREATE DATABASE :"erp_db" OWNER :"erp_user";
REVOKE ALL ON DATABASE :"erp_db" FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE :"erp_db" TO :"erp_user";
\connect :"erp_db"
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO :"erp_user";
SQL
