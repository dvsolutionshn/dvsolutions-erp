#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/dvsolutions/repo}"
SERVICE_NAME="${SERVICE_NAME:-dvsolutions}"
NGINX_SITE_NAME="${NGINX_SITE_NAME:-dvsolutions}"
SERVER_NAME="${1:-${SERVER_NAME:-_}}"

if [[ ! -d "$APP_DIR" ]]; then
    echo "No existe APP_DIR: $APP_DIR" >&2
    exit 1
fi

cd "$APP_DIR"

if [[ ! -f ".env.production" ]]; then
    echo "Falta $APP_DIR/.env.production" >&2
    exit 1
fi

if [[ ! -x "venv/bin/python" ]]; then
    echo "Falta el entorno virtual en $APP_DIR/venv" >&2
    exit 1
fi

set -a
source .env.production
set +a

git pull --ff-only

venv/bin/pip install -r requirements.txt
venv/bin/python manage.py migrate
venv/bin/python manage.py collectstatic --noinput

sudo cp deploy/nginx.conf "/etc/nginx/sites-available/$NGINX_SITE_NAME"
sudo sed -i "s/server_name .*/server_name $SERVER_NAME;/" "/etc/nginx/sites-available/$NGINX_SITE_NAME"
sudo ln -sf "/etc/nginx/sites-available/$NGINX_SITE_NAME" "/etc/nginx/sites-enabled/$NGINX_SITE_NAME"

if [[ -e /etc/nginx/sites-enabled/default ]]; then
    sudo rm -f /etc/nginx/sites-enabled/default
fi

sudo nginx -t
sudo systemctl restart "$SERVICE_NAME"
sudo systemctl reload nginx

echo "Reparacion completada."
echo "Entra por http://$SERVER_NAME/ si usaste una IP o por https://$SERVER_NAME/ si ya tienes SSL."
