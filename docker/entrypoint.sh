#!/bin/sh
set -e

echo "Aguardando o PostgreSQL em ${POSTGRES_HOST}:${POSTGRES_PORT:-5432}..."
until pg_isready -h "$POSTGRES_HOST" -p "${POSTGRES_PORT:-5432}" -U "$POSTGRES_USER" >/dev/null 2>&1; do
  sleep 1
done

if [ "$SOMENTE_AGUARDAR_BANCO" = "1" ]; then
  sleep 15  # deixa o serviço web aplicar as migrações
  exec "$@"
fi

python manage.py migrate --noinput
python manage.py setup_erp

# Instalação nova sem administrador: cria GuaraSuperAdmin (senha provisória, troca obrigatória no 1º login)
python manage.py criar_admin_inicial

if [ "$CARREGAR_DEMO" = "1" ]; then
  python manage.py seed_demo
fi

exec "$@"
