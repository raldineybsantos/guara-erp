#!/usr/bin/env bash
# Instalação do ERP Empresa em Linux (Debian/Ubuntu) sem Docker.
# Uso: sudo ./scripts/instalar_linux.sh
set -euo pipefail
cd "$(dirname "$0")/.."
DIR="$(pwd)"
USUARIO_APP="${SUDO_USER:-$USER}"

echo "==> Instalando dependências do sistema"
apt-get update -y
apt-get install -y python3 python3-venv python3-pip postgresql postgresql-client

if [ ! -f .env ]; then
  cp .env.example .env
  SENHA_DB="$(openssl rand -hex 12)"
  sed -i "s/^DJANGO_SECRET_KEY=.*/DJANGO_SECRET_KEY=$(openssl rand -hex 32)/" .env
  sed -i "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=${SENHA_DB}/" .env
  sed -i "s/^POSTGRES_HOST=.*/POSTGRES_HOST=localhost/" .env
  IP="$(hostname -I | awk '{print $1}')"
  sed -i "s/^DJANGO_ALLOWED_HOSTS=.*/DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,${IP},$(hostname)/" .env
  sed -i "s#^DJANGO_CSRF_TRUSTED_ORIGINS=.*#DJANGO_CSRF_TRUSTED_ORIGINS=http://localhost:8000,http://${IP}:8000#" .env
  chown "$USUARIO_APP" .env
  echo ".env criado (senha do banco gerada automaticamente)."
fi
set -a; . ./.env; set +a

echo "==> Criando banco e usuário PostgreSQL"
sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='${POSTGRES_USER}'" | grep -q 1 || \
  sudo -u postgres psql -c "CREATE ROLE ${POSTGRES_USER} LOGIN PASSWORD '${POSTGRES_PASSWORD}' CREATEDB;"
sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='${POSTGRES_DB}'" | grep -q 1 || \
  sudo -u postgres psql -c "CREATE DATABASE ${POSTGRES_DB} OWNER ${POSTGRES_USER};"

echo "==> Ambiente Python"
sudo -u "$USUARIO_APP" python3 -m venv .venv
sudo -u "$USUARIO_APP" .venv/bin/pip install -q -r requirements.txt
sudo -u "$USUARIO_APP" .venv/bin/python manage.py migrate --noinput
sudo -u "$USUARIO_APP" .venv/bin/python manage.py setup_erp
sudo -u "$USUARIO_APP" .venv/bin/python manage.py collectstatic --noinput -v0

echo "==> Serviços systemd (erp-web e erp-agendador)"
cat > /etc/systemd/system/erp-web.service <<UNIT
[Unit]
Description=ERP Empresa (web)
After=network.target postgresql.service

[Service]
User=${USUARIO_APP}
WorkingDirectory=${DIR}
ExecStart=${DIR}/.venv/bin/gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 3 --timeout 120
Restart=always

[Install]
WantedBy=multi-user.target
UNIT
cat > /etc/systemd/system/erp-agendador.service <<UNIT
[Unit]
Description=ERP Empresa (agendador da régua de cobrança)
After=erp-web.service

[Service]
User=${USUARIO_APP}
WorkingDirectory=${DIR}
ExecStart=${DIR}/.venv/bin/python manage.py agendador --hora 08:00
Restart=always

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now erp-web erp-agendador

echo
echo "Pronto! Crie o administrador com:"
echo "  sudo -u ${USUARIO_APP} ${DIR}/.venv/bin/python ${DIR}/manage.py createsuperuser"
echo "Acesse: http://$(hostname -I | awk '{print $1}'):8000"
