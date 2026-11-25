# Mantenimiento preventivo del inicio móvil

Alcance: `hospital_mia`, `medical_spa`, `luque_aestetic`, `serviciosmedicos`.
No cambia permisos, cuentas, formularios fiscales, versión web ni modelos.
No confirma la causa de la pantalla blanca reportada en un iPhone físico.

## Qué se refuerza

- Aviso manual si la navegación o las funciones de la pantalla no terminan de iniciar.
  El encabezado y la navegación nunca se ocultan para esperar JavaScript.
- Preferencias opcionales tolerantes a almacenamiento bloqueado o contenido corrupto.
  No se guardan borradores clínicos ni de facturas.
- Fallos opcionales de notificaciones y registro del service worker aislados del resto.
- Aviso de incompatibilidad al abrir nuevo paciente sin soporte para su diálogo.
- Service worker **sin caché**: sólo intercepta navegaciones GET del inicio canónico
  de su propia empresa. Un fallo de red, respuesta 5xx/204/205 o espera superior a
  20 segundos devuelve una página estática de recuperación con reintento manual.
  No usa CacheStorage y pide `cache: no-store` para no leer/escribir caché HTTP.
  No intercepta POST, API, PDF, otras pantallas ni otras empresas. Los accesos
  denegados y la autenticación mantienen sus respuestas originales.

La recuperación del service worker requiere que el navegador haya cargado y
activado esta versión al menos una vez. No repara por sí sola un primer acceso
sin conexión, un icono que apunte fuera del inicio canónico ni un fallo del
proceso de Safari. No hay borrado de cookies, caché, sesión ni datos del usuario,
recarga automática o repetición de operaciones desde la aplicación. El motor
del navegador puede reintentar una conexión GET fallida por su cuenta.

## Actualización del servidor

Ejecutar dentro de una sesión SSH **Linux/root**, no en PowerShell local:

```bash
cd /var/www/dvsolutions-erp
git pull --ff-only origin main && .venv/bin/python deploy/deploy_app_startup_release.py
systemctl show dvsolutions.service --no-pager -p ActiveState -p WorkingDirectory
```

El servicio usa una carpeta de releases. El script copia **la versión realmente
activa**, conserva sus parches y reemplaza únicamente la función del service
worker y cuatro templates móviles revisados. Rechaza código activo desconocido,
rutas inseguras y cambios concurrentes. No activa el checkout antiguo completo.
No ejecuta migraciones ni collectstatic; los cambios no requieren nuevos estáticos.

La publicación usa el orden real de los overrides de systemd, verifica el proceso
y las cuatro rutas y conserva el temporizador CRM y la ruta de WhatsApp. Si falla
la activación restaura sólo su propio override. Conserva la versión anterior y un
respaldo bajo `/var/backups/dvsolutions-erp/app-startup-*`.

Éxito: `DEPLOY_OK` o `ALREADY_ACTIVE`. Ante `DEPLOY_STOPPED`, no forzar el cambio,
no borrar overrides ni actualizar la carpeta activa manualmente: revisar la
incompatibilidad indicada. La salida no incluye secretos ni datos clínicos.

Después de actualizar, abrir una vez el inicio de la app con conexión y volver
a abrir el icono. Si el icono ya permanece blanco, abrir el mismo inicio desde
Safari para permitir obtener la versión nueva. No es necesario borrar datos ni
reinstalar como primer paso. La prueba final en el iPhone afectado queda pendiente.

## Verificación

Las pruebas locales usan datos sintéticos, base de datos en memoria, transportes
externos bloqueados y fallos de red simulados. WebKit de Playwright en Windows no
equivale a Safari de iOS ni a una prueba en teléfono físico. No se accede a la cuenta
real del doctor ni se cambian permisos durante las pruebas.
