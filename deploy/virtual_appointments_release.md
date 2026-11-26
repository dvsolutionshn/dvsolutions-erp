# Citas virtuales en web y app

Empresas: Hospital Mía, Medical Spa, Luque Aestetic y Servicios Médicos.

Recepción puede marcar **Cita virtual** en el formulario actual de creación o
edición, con los mismos permisos de agenda. Es independiente del cobro:
virtual + pagada y virtual + cortesía son válidas. Pagada + cortesía siguen
siendo incompatibles. Sin marcar virtual, la cita sigue siendo presencial.

La etiqueta con computadora aparece en calendarios y detalles web/app y en
la búsqueda móvil. No reemplaza colores de consulta/profesional ni concede
permisos. No crea videollamadas, enlaces externos ni mensajes nuevos.

## Actualizar el servidor

Dentro de una sesión SSH Linux/root:

```bash
cd /var/www/dvsolutions-erp
git pull --ff-only origin main && .venv/bin/python deploy/deploy_virtual_appointments_release.py
systemctl show dvsolutions.service --no-pager -p ActiveState -p WorkingDirectory
```

El actualizador copia la **release realmente activa**, no el checkout completo,
y conserva los ajustes de facturación, historial clínico, navegación y arranque.
Rechaza bloques/plantillas desconocidos en lugar de sobrescribirlos. Sólo agrega
los bloques, templates y migraciones revisados, con respaldo de código, control
del proceso, overrides de systemd y restauración de código si falla la activación.
Comprueba ocho entradas anónimas de app/web; esto no sustituye probar con recepción.

## Dos cambios de esquema, sin migrar módulos ajenos

Se agregan dos campos booleanos: `CitaCliente.virtual` y `CitaClinica.virtual`.
Cada migración contiene únicamente un `AddField`, sin `RunPython` ni cambios
de permisos/cuentas. Se verifica el plan antes de escribir: si requiere otras
migraciones, reversas, reparaciones de historial o un esquema inesperado, se detiene.

Ambos campos usan `default=False` **y `db_default=False` permanente**. Esto
conserva las citas anteriores como presenciales y permite que la versión anterior
siga insertando citas durante la transición o una restauración. Los espejos,
series y múltiples sesiones reutilizan su guardado existente y copian la marca.

El cambio requiere DDL transaccional. En PostgreSQL se limita la espera por
bloqueos a 5 segundos y cada sentencia a 30 segundos, sólo en el proceso de
migración. Si falla o cambia la release activa, no se activa la copia preparada.
Una primera columna aplicada correctamente puede quedar antes de fallar la
segunda: es aditiva y compatible; el siguiente intento valida ese estado parcial.

**El rollback restaura código, nunca borra columnas ni revierte migraciones.**
No se ejecuta `migrate` general, `collectstatic`, borrado de caché/sesión o limpieza
de datos. Los nuevos iconos y estilos están dentro de los templates.

Éxito: `DEPLOY_OK` o `ALREADY_ACTIVE`. Si aparece `DEPLOY_STOPPED`, no forzar
migraciones, no borrar overrides ni copiar el repositorio entero encima del activo.
Los respaldos quedan en `/var/backups/dvsolutions-erp/app-virtual-*`.

## Validación

Pruebas con base en memoria y transportes bloqueados: pago independiente,
creación/edición/desmarcado, series, espejos, reagendado, permisos, aislamiento,
inserciones del código antiguo y guardas de migración. La revisión responsive usa
WebKit y Chromium con datos sintéticos, no una cuenta real ni un iPhone físico.

Hallazgo ajeno registrado: el script de historial web del administrador ya
referenciaba `appointmentForm` fuera de su ámbito en el commit base. La revisión
lo separó de las pruebas de esta función; no se amplió ese flujo en este bloque.
