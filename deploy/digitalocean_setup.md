# Despliegue en DigitalOcean - DV Solutions ERP

Esta guia asume Ubuntu 24.04, dominio apuntando al Droplet y PostgreSQL en el mismo servidor.

## 1. Crear servidor

- Droplet Ubuntu 24.04 LTS.
- Plan inicial recomendado: 2 GB RAM / 1 vCPU.
- Activar backups del Droplet si el presupuesto lo permite.
- Crear un usuario no-root para operar el ERP.

```bash
adduser dvsolutions
usermod -aG sudo dvsolutions
```

## 2. Instalar paquetes del sistema

```bash
sudo apt update
sudo apt upgrade -y
sudo apt install -y python3-venv python3-pip python3-dev build-essential git nginx postgresql postgresql-contrib libpq-dev
sudo apt install -y libcairo2 libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 libffi-dev shared-mime-info
```

## 3. Crear base PostgreSQL

```bash
sudo -u postgres psql
```

Dentro de PostgreSQL:

```sql
CREATE DATABASE dvsolutions;
CREATE USER dvsolutions_user WITH PASSWORD 'CAMBIAR_PASSWORD_SEGURO';
ALTER ROLE dvsolutions_user SET client_encoding TO 'utf8';
ALTER ROLE dvsolutions_user SET default_transaction_isolation TO 'read committed';
ALTER ROLE dvsolutions_user SET timezone TO 'America/Tegucigalpa';
GRANT ALL PRIVILEGES ON DATABASE dvsolutions TO dvsolutions_user;
\q
```

## 4. Subir codigo desde GitHub

```bash
sudo mkdir -p /opt/dvsolutions
sudo chown dvsolutions:dvsolutions /opt/dvsolutions
cd /opt/dvsolutions
git clone TU_REPOSITORIO_GITHUB repo
cd repo
```

## 5. Crear entorno Python

```bash
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

## 6. Configurar variables de entorno

Crear `/opt/dvsolutions/repo/.env.production` usando el ejemplo:

```bash
cp .env.example .env.production
nano .env.production
```

Valores minimos:

```env
DEBUG=False
SECRET_KEY=CAMBIAR_SECRET_KEY_LARGO
ALLOWED_HOSTS=erp.tudominio.com
CSRF_TRUSTED_ORIGINS=https://erp.tudominio.com
DATABASE_URL=postgres://dvsolutions_user:CAMBIAR_PASSWORD_SEGURO@localhost:5432/dvsolutions
TIME_ZONE=America/Tegucigalpa
LANGUAGE_CODE=es-hn
SECURE_SSL_REDIRECT=true
SESSION_COOKIE_SECURE=true
CSRF_COOKIE_SECURE=true
```

## 7. Migraciones y archivos estaticos

```bash
source venv/bin/activate
set -a
source .env.production
set +a
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py createsuperuser
```

## 8. Gunicorn con systemd

Copiar `deploy/gunicorn.service`:

```bash
sudo cp deploy/gunicorn.service /etc/systemd/system/dvsolutions.service
sudo systemctl daemon-reload
sudo systemctl enable dvsolutions
sudo systemctl start dvsolutions
sudo systemctl status dvsolutions
```

## 9. Nginx

Copiar `deploy/nginx.conf` y editar dominio si aplica:

```bash
sudo cp deploy/nginx.conf /etc/nginx/sites-available/dvsolutions
sudo ln -s /etc/nginx/sites-available/dvsolutions /etc/nginx/sites-enabled/dvsolutions
sudo nginx -t
sudo systemctl reload nginx
```

## 10. SSL gratis con Certbot

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d erp.tudominio.com
```

## 11. Actualizar sistema desde GitHub

```bash
cd /opt/dvsolutions/repo
git pull
source venv/bin/activate
set -a
source .env.production
set +a
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
sudo systemctl restart dvsolutions
```

## 11.1 Reparar estaticos despues de subir a DigitalOcean

Si el sistema carga sin diseno, con imagenes rotas o se esta usando `:8000` en la URL,
el problema normalmente es que Nginx no esta sirviendo `/static/` y `/media/`.
Usa el script de reparacion con tu dominio o IP:

```bash
cd /opt/dvsolutions/repo
chmod +x deploy/repair_digitalocean_static.sh
./deploy/repair_digitalocean_static.sh 159.89.48.29
```

Despues entra sin `:8000`:

```text
http://159.89.48.29/digital_planning/dashboard/
```

## 12. Ruta de Hospital MIA

Cuando el dominio este activo, la empresa puede entrar por:

```text
https://erp.tudominio.com/hospital_mia/
```

El ERP se instala una sola vez. Los slugs son rutas internas por empresa.
