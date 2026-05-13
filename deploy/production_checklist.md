# Checklist de Produccion - DV Solutions ERP

## 1. Servidor recomendado

- Ubuntu Server 22.04 o 24.04.
- 1 vCPU y 2 GB RAM minimo para el primer cliente.
- 2 vCPU y 4 GB RAM recomendado cuando haya mas empresas o mas carga.
- PostgreSQL como base de datos.
- Nginx como proxy web.
- Gunicorn como servidor de aplicacion.
- Certbot para HTTPS.
- Repositorio GitHub privado para bajar actualizaciones.

## 1.1. Multiempresa por subdominio

- Recomendado: una URL por empresa usando subdominios.
- Ejemplos:
  - `hospitalmia.erp.tudominio.com`
  - `digitalplanning.erp.tudominio.com`
  - `amkt.erp.tudominio.com`
- Esto permite tener varias empresas abiertas al mismo tiempo en el mismo navegador sin pisar la sesion.
- Importante: no definas `SESSION_COOKIE_DOMAIN` al dominio padre si quieres mantener las sesiones separadas por empresa.

## 2. Variables de entorno obligatorias

Copiar `.env.example` a `.env` en el servidor y completar:

- `DEBUG=False`
- `SECRET_KEY`
- `ALLOWED_HOSTS`
- `CSRF_TRUSTED_ORIGINS`
- `DATABASE_URL`
- Variables SMTP si se enviaran correos reales.
- `LOGIN_THROTTLE_LIMIT`
- `LOGIN_THROTTLE_WINDOW_SECONDS`
- `SECURE_HSTS_SECONDS`
- `SESSION_COOKIE_SAMESITE`
- `CSRF_COOKIE_SAMESITE`

## 3. Base de datos

Produccion debe usar PostgreSQL. SQLite queda solo para desarrollo local.

Ejemplo de `DATABASE_URL`:

```env
DATABASE_URL=postgres://usuario:contrasena@localhost:5432/dvsolutions
```

## 4. Comandos de despliegue

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py createsuperuser
```

Guia completa: `deploy/digitalocean_setup.md`.

## 5. Archivos estaticos y media

- Servir `/static/` desde `staticfiles/`.
- Servir `/media/` desde `media/`.
- Respaldar `media/` junto con PostgreSQL.

## 6. Seguridad

- Confirmar `DEBUG=False`.
- Confirmar HTTPS activo.
- Confirmar `SECRET_KEY` unico y fuera del codigo.
- Confirmar tokens de WhatsApp en configuracion segura, nunca en GitHub.
- Confirmar que `/control/` sea solo para superadmin.
- Confirmar usuarios, roles y modulos por empresa.
- Confirmar que una empresa no pueda ver datos de otra.
- Confirmar cookies seguras y `SameSite=Lax`.
- Confirmar `HSTS`, `nosniff` y `Referrer-Policy`.
- Configurar rate limiting en Nginx para login y trafico general.
- Considerar Fail2ban para ataques repetidos por IP.
- Mantener `/control/` fuera de rutas publicas conocidas si es posible.
- Configurar backups automaticos.
- Probar restauracion de backup antes de entregar al cliente.

## 7. Prueba funcional antes de entregar

- Crear empresa.
- Activar licencia y modulos.
- Crear usuario operativo.
- Crear CAI, impuestos, cliente y producto.
- Emitir factura y PDF.
- Registrar pago y recibo.
- Crear compra, aplicar inventario y registrar pago.
- Revisar CXC, CXP, reportes y contabilidad.
- Crear empleado, generar planilla y descargar voucher.
- Crear campania CRM con plantilla aprobada de WhatsApp.
- Crear cierre de caja y revisar resumen diario.
- Probar inventario farmaceutico si la empresa lo tiene activo.
