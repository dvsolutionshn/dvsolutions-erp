#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
BACKUP_DIR="${DV_BACKUP_DIR:-/var/backups/dvsolutions-erp}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Este despliegue debe ejecutarse como root." >&2
  exit 1
fi

cd "${APP_DIR}"

if [[ ! -d ".git" || ! -f "manage.py" ]]; then
  echo "La carpeta ${APP_DIR} no parece ser una instalacion de DV Solutions." >&2
  exit 1
fi

if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  echo "El repositorio tiene cambios locales. No se modifico nada." >&2
  git status --short
  exit 1
fi

git fetch origin main
git pull --ff-only origin main

if [[ -f "${APP_DIR}/.env.production" ]]; then
  ENV_FILE="${APP_DIR}/.env.production"
elif [[ -f "/etc/dvsolutions-erp.env" ]]; then
  ENV_FILE="/etc/dvsolutions-erp.env"
else
  echo "No se encontro la configuracion de produccion." >&2
  exit 1
fi

if [[ -x "${APP_DIR}/.venv/bin/python" ]]; then
  VENV_DIR="${APP_DIR}/.venv"
elif [[ -x "${APP_DIR}/venv/bin/python" ]]; then
  VENV_DIR="${APP_DIR}/venv"
else
  echo "No se encontro el entorno virtual de produccion." >&2
  exit 1
fi

set -a
source "${ENV_FILE}"
set +a

mkdir -p "${BACKUP_DIR}"
chmod 700 "${BACKUP_DIR}"
pg_dump "${DATABASE_URL}" > "${BACKUP_DIR}/pre-crm-whatsapp-$(date +%Y%m%d-%H%M%S).sql"

source "${VENV_DIR}/bin/activate"
pip install -r requirements.txt
python manage.py check --deploy
python manage.py migrate --noinput
python manage.py collectstatic --noinput

sed \
  -e "s|__APP_DIR__|${APP_DIR}|g" \
  -e "s|__ENV_FILE__|${ENV_FILE}|g" \
  -e "s|__PYTHON_BIN__|${VENV_DIR}/bin/python|g" \
  deploy/crm-automatizaciones.service \
  > /etc/systemd/system/dvsolutions-crm-automatizaciones.service
chmod 0644 /etc/systemd/system/dvsolutions-crm-automatizaciones.service
install -m 0644 deploy/crm-automatizaciones.timer /etc/systemd/system/dvsolutions-crm-automatizaciones.timer
systemctl daemon-reload
systemctl enable --now dvsolutions-crm-automatizaciones.timer
systemctl restart dvsolutions.service

systemctl is-active --quiet dvsolutions.service
systemctl is-active --quiet dvsolutions-crm-automatizaciones.timer

echo "DESPLIEGUE_OK"
systemctl --no-pager --full status dvsolutions.service | head -n 20
systemctl --no-pager --full status dvsolutions-crm-automatizaciones.timer | head -n 20
