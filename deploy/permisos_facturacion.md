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

Base de módulos y Gastos Adicionales móviles
------------------------------------------

El inicio agrega únicamente `Más funciones` después de las tarjetas existentes. No cambia su orden, acciones, navegación inferior ni flujos entrenados. El catálogo `crm/mobile_modules.py` reutiliza `core.context_processors.erp_access` con la empresa de la URL y está limitado a Hospital Mía, Medical Spa, Luque Aestetic y Servicios Médicos. Las opciones pendientes son informativas, sin enlaces a formularios web. Las vistas de destino conservan sus controles de backend: aparecer en el catálogo no concede permisos.

`agenda_mobile_modules` publica un catálogo autenticado y sin caché de presentación (`version`, empresa, módulos y destinos), sin datos de pacientes ni documentos. Es una base de descubrimiento para futuros clientes, no una API alternativa de operaciones ni un sistema de autenticación nuevo.

Gastos Adicionales usa sus rutas existentes con `?app=1`: lista, filtros, creación, edición, PDF, correo, WhatsApp y conversión. Las tarjetas móviles y el adaptador visual reutilizan el formulario y `gastos_adicionales.js` actuales, incluida la búsqueda paginada, cirugía obligatoria, profesional fijado, importes y conservación de líneas tras errores. La conversión mantiene el servicio transaccional e idempotente original y abre un borrador en el editor móvil de facturas; no emite el documento ni consume CAI o inventario. La web conserva sus templates y destinos originales.

Orden autorizado de ampliación: base/accesos y Gastos Adicionales; pacientes y expediente; recetas y atención clínica; inventario y transferencias para las cuatro empresas; cobros, documentos y reportes de facturación pendientes; configuración y administración autorizadas. Cada bloque debe adaptarse sobre la lógica existente y verificarse antes de publicar. No cambiar roles ni conceder permisos para facilitar una adaptación.

Una futura aplicación iOS distribuida por App Store requiere una etapa propia: cliente móvil empaquetado, contratos autenticados de operaciones sobre los servicios actuales, funciones apropiadas del dispositivo, pruebas iOS, cuenta/firma y revisión de Apple. El catálogo móvil y los servicios reutilizados facilitan esa transición, pero esta implementación no incluye un binario nativo ni garantiza aprobación. No basta con apuntar un contenedor a la web. Referencias: https://capacitorjs.com/docs y https://developer.apple.com/app-store/review/guidelines/ (mínima funcionalidad).

Pacientes, consulta del expediente e inventario para las cuatro empresas
---------------------------------------------------------------------

El acceso adicional a Pacientes usa las rutas clínicas existentes con `app=1`: listado paginado/búsqueda, datos generales, creación/edición con el formulario general básico actual, fotografías, consulta de historias por área/consolidado y registro de un evento mediante el formulario existente. No reemplaza la tarjeta ni el perfil entrenado de Inicio. Los POST del consolidado móvil se rechazan: la consulta no concede edición de historias ni controles especializados. Las pantallas de consulta no crean preconsultas ni inicializan profesionales. La edición básica conserva los antecedentes y datos clínicos existentes y la sincronización actual con clientes. Los demás flujos clínicos completos, incluido editar historias/recetas/controles, continúan pendientes de adaptación.

`agenda_mobile_inventory` agrega consulta de productos/stock por empresa para las cuatro clínicas. `crm/mobile_inventory.py` extrae literalmente la presentación de existencias del panel original de Hospital Mía para compartir cálculos, bodegas, lotes, vencimientos y FEFO sin una versión paralela. El panel original consume el mismo helper y conserva su payload y su entrada rápida. La pantalla nueva solo admite GET; no genera movimientos ni modifica existencias. La entrada rápida original solo se enlaza para Hospital Mía cuando inventario, ajuste y acceso al inicio actual están autorizados. Entradas/transferencias móviles para las otras empresas siguen pendientes.

Las rutas nuevas de consulta de inventario requieren `puede_inventario`, no permiso de calendario. El catálogo y el hub independiente `agenda_mobile_modules_screen` comprueban acceso a empresa y módulos autorizados desde sus propias vistas; únicamente muestran metadatos. Son un destino móvil seguro para roles granulares sin calendario. No se flexibiliza el permiso del inicio/calendario entrenado; las nuevas pantallas usan navegación por destinos realmente accesibles y no conceden permisos.

El trabajo automático de continuación debe revisar esta guía y el catálogo antes de elegir el siguiente bloque. Priorizar recetas/manuales y escritura clínica autorizada, después transferencias/compras/entradas para las cuatro empresas, cobros y restantes documentos/reportes, y configuración por rol. La app instalada actual sigue siendo PWA; no existe todavía un binario para App Store.
