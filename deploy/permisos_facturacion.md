Permisos avanzados de facturación
================================

La migración `core.0050` agrega `puede_cambiar_fecha_factura` desactivado por defecto. Se reutilizan `puede_editar_facturas`, `puede_anular_facturas` y los roles individuales de `UsuarioEmpresaPermiso`. Los tres controles aparecen en Usuarios y Permisos y en la edición de roles.

Después de desplegar el código y aplicar las migraciones en la base de destino:

```sh
python manage.py migrate
python manage.py habilitar_permisos_facturacion --usuario-email dracandyluque@gmail.com --empresas hospital_mia serviciosmedicos luque_aestetic medical_spa
```

El comando exige que exista exactamente una cuenta con ese nombre de usuario, que existan las cuatro empresas y que la cuenta ya tenga acceso a ellas. La operación es atómica; no crea usuarios ni empresas ni concede acceso a otras empresas. Conserva los demás permisos efectivos y asigna roles individuales sin modificar el rol compartido. También acepta `--usuario-id`, `--usuario-email` y `--empresa-ids`. Los identificadores son parámetros de búsqueda, nunca condiciones de autorización.

La habilitación es una operación inicial: no ejecutar el comando periódicamente, pues volvería a activar permisos revocados posteriormente desde Usuarios y Permisos. Los administradores y superusuarios conservan el acceso global previsto por el sistema actual.

Editar no autoriza cambiar fecha: ese campo queda bloqueado sin el permiso independiente, y una fecha manipulada se rechaza. El formulario de cambio de fecha solo guarda la fecha de emisión y exige motivo. Conserva la validación fiscal/CAI del modelo y actualiza el asiento de emisión. La edición de facturas pagadas reutiliza la reconstrucción existente de inventario, contabilidad y pagos; rechaza un total inferior a los cobros. Se mantienen bloqueadas las ediciones de facturas con notas de crédito activas. Una factura anulada no se reabre mediante estas acciones.

La anulación conserva el documento y sus líneas, usando el estado interno existente `anulada` y la reversión actual. Los tres cambios registran empresa, factura, actor, fecha/hora, acción, motivo y valores anteriores/nuevos en `RegistroAuditoria`; un fallo al registrar el historial revierte la operación completa.

Verificación local: la base inspeccionada no contiene la cuenta solicitada ni las empresas `serviciosmedicos` y `luque_aestetic`. La asignación inicial debe ejecutarse en la base que contiene esos registros.
