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

Facturación desde la app clínica
--------------------------------

La lista móvil conserva el acceso directo al PDF y agrega el detalle con estado, fecha e historial. El detalle y los formularios móviles usan las mismas rutas `ver_factura`, `editar_factura`, `cambiar_fecha_factura` y `corregir_numero_factura` con `?app=1`. Los formularios conservan ese indicador al enviar; las respuestas regresan al detalle o a la lista móvil. El indicador selecciona únicamente la presentación y el destino de navegación: no concede permisos, altera modelos ni cambia las validaciones fiscales.

El manifest conserva su identificador y página inicial, y amplía `scope` al dashboard de la misma empresa para mantener estas pantallas dentro de la app instalada. La autorización del service worker y su registro usan ese mismo alcance; no se agregan cachés de datos clínicos ni fiscales.

La edición, el cambio de fecha, la anulación y la eliminación invocan las vistas existentes. La corrección fiscal histórica utiliza `puede_editar_facturas` junto con `ConfiguracionAvanzadaEmpresa.permite_gestion_fiscal_historica`; no existe un permiso móvil alternativo. La eliminación de borradores usa `puede_eliminar_borradores`; la histórica usa `puede_eliminar_facturas` y las restricciones existentes de pagos, recibos y notas de crédito. Las confirmaciones móviles solicitan motivo y, para eliminar, escribir `ELIMINAR`. Los registros se consultan en `RegistroAuditoria`, con el mismo aislamiento por empresa.

Ampliación progresiva de la interfaz clínica
------------------------------------------

La base `facturacion/mobile_invoice_base.html` proporciona encabezado, navegación de la app, tarjetas, formularios y hojas de acciones para nuevas pantallas. Cada adaptación debe seleccionar un template móvil desde la vista existente y conservar su formulario, permisos, servicios, transacciones y auditoría. La interfaz web mantiene su template original.

| Área | Estado móvil y puntos existentes que se deben reutilizar |
| --- | --- |
| Pacientes | Lista, perfil móvil y enlace de registro ya disponibles en `agenda_mobile`; creación/edición completa reutilizará `clinica_crear_paciente`, `clinica_editar_paciente` y sus formularios. |
| Agenda | Calendario, citas y disponibilidad ya adaptados; conservar vistas y servicios de `crm`, incluida la agenda central de Hospital Mía. |
| Facturación | Creación rápida, PDF, WhatsApp y nuevas acciones móviles sobre las vistas fiscales existentes. |
| Inventario y productos | Hospital Mía dispone de consulta por bodega/lote, FEFO y entrada rápida; ampliaciones deben usar las vistas de inventario/productos y sus movimientos actuales. |
| Transferencias | Pendiente de interfaz móvil; reutilizar `traslado_inventario_farmaceutico`, `traslado_rapido_farmaceutico` y las reglas de bodegas, lotes y `transferir_inventario`. |
| Expediente clínico | El perfil actual enlaza a la web. Próxima adaptación: `clinica_paciente_detalle`, `clinica_historias_especialidad`, `clinica_historial_clinico_consolidado` y los formularios existentes por especialidad. |
| Recetas | Pendiente de interfaz móvil completa; reutilizar `clinica_recetas_paciente`, `clinica_crear_receta_paciente`, PDF y envío existentes, con `ver_recetas`/`crear_recetas`. |
| Manuales PDF | Pendiente de interfaz móvil; reutilizar `clinica_manuales_pdf`, `clinica_enviar_manuales_pdf` y permisos de ver/enviar/administrar. |
| Tratamientos | Pendiente de interfaz móvil completa; reutilizar `clinica_planes_tratamiento_paciente`, tratamientos y controles actuales. |
| Configuración | Adaptar solo los campos ya autorizados en `clinica_configuracion`; usar `erp_access` y los permisos granulares del rol por empresa. |

Prioridad de próximas adaptaciones: expediente y recetas desde el perfil del paciente; luego manuales PDF y transferencias; finalmente tratamientos y configuración. Esta lista es una guía de implementación, no expone opciones todavía no adaptadas ni autoriza nuevas acciones al usuario.
