#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="/opt/dvsolutions/repo"
BACKUP_DIR="/opt/dvsolutions/backups"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Este despliegue debe ejecutarse como root." >&2
  exit 1
fi

cd "${APP_DIR}"

if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  echo "El repositorio tiene cambios locales. No se modifico nada." >&2
  git status --short
  exit 1
fi

git fetch origin main
git pull --ff-only origin main

set -a
source .env.production
set +a

mkdir -p "${BACKUP_DIR}"
chmod 700 "${BACKUP_DIR}"
pg_dump "${DATABASE_URL}" > "${BACKUP_DIR}/pre-crm-whatsapp-$(date +%Y%m%d-%H%M%S).sql"

source venv/bin/activate
pip install -r requirements.txt
python manage.py check --deploy
python manage.py migrate --noinput
python manage.py collectstatic --noinput

install -m 0644 deploy/crm-automatizaciones.service /etc/systemd/system/dvsolutions-crm-automatizaciones.service
install -m 0644 deploy/crm-automatizaciones.timer /etc/systemd/system/dvsolutions-crm-automatizaciones.timer
systemctl daemon-reload
systemctl enable --now dvsolutions-crm-automatizaciones.timer
systemctl restart dvsolutions.service

systemctl is-active --quiet dvsolutions.service
systemctl is-active --quiet dvsolutions-crm-automatizaciones.timer

echo "DESPLIEGUE_OK"
systemctl --no-pager --full status dvsolutions.service | head -n 20
systemctl --no-pager --full status dvsolutions-crm-automatizaciones.timer | head -n 20
