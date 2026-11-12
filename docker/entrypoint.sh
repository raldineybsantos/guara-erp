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

if [ -n "$DJANGO_SUPERUSER_USERNAME" ] && [ -n "$DJANGO_SUPERUSER_PASSWORD" ]; then
  python manage.py createsuperuser --noinput 2>/dev/null || true
fi

if [ "$CARREGAR_DEMO" = "1" ]; then
  python manage.py seed_demo
fi

exec "$@"
