# Captura Rápida de Compras — demo_1

## Acceso y alcance

En `demo_1`, abrir **Libro de Compras**, seleccionar el año y pulsar **Abrir** en uno de los doce meses. El acceso a Captura Rápida desde Compras también lleva a esta selección.

Selector: `/demo_1/dashboard/facturacion/libro-compras/`. Libro concreto: `/demo_1/dashboard/facturacion/libro-compras/captura-rapida/2026/8/` (Agosto 2026). La ruta de captura sin período muestra el selector y rechaza guardar facturas hasta elegir año y mes.

La consulta requiere acceso a la empresa, licencia, módulo de facturación habilitado y `puede_compras`. Capturar requiere `puede_crear_compras`; editar, finalizar y reabrir requieren `puede_editar_compras`; anular requiere `puede_anular_compras`. Se mantienen las excepciones existentes para administradores. Para cualquier otro slug, la funcionalidad devuelve 404, también por POST y en las consultas auxiliares.

Flujo: Fecha → TAB → Proveedor → TAB → Factura → TAB → Exenta → TAB → Base 15% → TAB → Base 18% → ENTER. Las flechas seleccionan proveedores; TAB o ENTER acepta. SHIFT+TAB retrocede. ESC o el botón Omitir descarta una duplicada y devuelve el foco a Fecha.

Si el proveedor no está creado, escribir su nombre y elegir **Crear «nombre» · ingresar RTN**. El nombre se toma de la búsqueda; solo se pide el RTN. ENTER lo guarda, lo selecciona y devuelve el foco a Nº Factura sin borrar los datos de la fila. ESC cancela el alta. Requiere además el permiso existente `puede_crear_proveedores`. Si el RTN ya existe en la empresa, se selecciona ese proveedor sin duplicarlo ni cambiar su nombre. Los proveedores inactivos o RTN con varios proveedores activos requieren revisión en el catálogo. Esta ampliación no necesita migraciones.

Las fechas compactas usan DDMMAA, con año 20AA. También se admite día/mes/año con uno o dos dígitos de día y mes y año de dos o cuatro dígitos: `5/8/26` equivale a `05/08/2026`. Se admiten guiones y el formato AAAA-MM-DD. Los años de dos dígitos siempre corresponden a 20AA. Se conserva el mes explícito y se rechazan fechas inexistentes. Se ejecuta `RegistroCompraFiscal.save()` y su validación existente; el registro fiscal actual no impone una restricción adicional de mes corriente ni cierre contable.

## Libros mensuales

El período de incorporación es independiente de la fecha de factura. Una factura fechada 25/07/2026 ingresada en Agosto 2026 conserva `fecha_documento=2026-07-25`, `periodo_anio=2026` y `periodo_mes=8`, incluso al editar su fecha. El servidor toma el período de la URL seleccionada, nunca de un campo manipulable ni de la fecha.

Se reutiliza la relación compuesta existente `empresa + periodo_anio + periodo_mes` de RegistroCompraFiscal. La única tabla nueva, LibroCompraMensual, conserva la cabecera y estado por empresa/año/mes, con restricción única, usuario y fecha de actualización. No contiene otra base de compras ni copias de importes. Su propiedad `registros` consulta los documentos del período. Los libros históricos sin cabecera se muestran En proceso; la cabecera se persiste al guardar o cambiar el estado, incluso si el libro está vacío.

El selector presenta los doce meses con cantidades y totales. Dentro del libro, todas sus filas se cargan debajo de la fila activa; al guardar aparecen inmediatamente y se recalculan los totales en el servidor con Decimal. La tabla se puede desplazar para consultar grandes volúmenes. Actualizar cuadro incorpora cambios de otras sesiones.

Editar carga la factura en la fila activa. Se conservan su período y campos históricos ajenos a la captura, incluidos importes exonerados. Una versión del contenido evita sobreescribir cambios de otra sesión. Anular reutiliza el estado existente: mantiene la fila para trazabilidad, pero la excluye de los totales y reportes. No se borran compras físicamente.

Finalizado conserva el cuadro y permite editar o anular con permisos; para capturar nuevas facturas se puede Reabrir libro. No hay un cierre irreversible.

## Fuente única e integración

Se guarda **un RegistroCompraFiscal**, enlazado al proveedor, con el usuario, fecha de registro, número formateado y número normalizado. No se crea una compra de inventario adicional.

El Libro de Compras, sus acumulados mensuales y el reporte de impuestos de Contabilidad leen ese mismo registro automáticamente. En demo_1, el reporte de impuestos filtra las compras por meses del libro (incluye completos los meses del rango); las ventas siguen por fecha de emisión. En otras empresas se conserva el filtro anterior por fecha del documento. La arquitectura fiscal actual no genera asientos ni alimenta directamente el Estado de Resultados; esta función conserva esa limitación y no asigna cuentas, gastos ni pagos supuestos. Las clasificaciones y la contabilidad existentes permanecen disponibles.

Los duplicados se buscan en todo el historial fiscal y de compras de inventario de la empresa, incluidas anulaciones. La identidad utiliza proveedor o RTN; el nombre solo sirve como respaldo para documentos históricos sin proveedor vinculado ni RTN. No se rellenan ni recortan los correlativos. Los registros históricos se comparan en la consulta, sin modificar sus valores.

Los cálculos usan Decimal en servidor y centavos BigInt en navegador, con el mismo redondeo HALF_EVEN. Campos vacíos equivalen a cero; entradas inválidas, negativas, no finitas o fuera de capacidad se rechazan. El total debe ser positivo.

El guardado usa transacción, bloqueo por empresa y restricción única. Los reenvíos se rechazan con la información del documento existente. SQLite puede responder 503 cuando otro guardado mantiene un bloqueo; la fila se conserva para reintentar.

## Archivos modificados o nuevos

- `core/access.py`: acceso de consulta; las acciones comprueban sus permisos en el servidor.
- `facturacion/models.py`: modelo fiscal existente y cabecera LibroCompraMensual.
- `facturacion/captura_rapida.py`: formularios, búsqueda, cálculo compartido y endpoint del piloto.
- `facturacion/views.py`: cálculo compartido y entrada al selector mensual solo en demo_1.
- `contabilidad/views.py`: filtro por período de incorporación para compras de demo_1.
- `contabilidad/templates/contabilidad/reporte_impuestos.html`: explicación del filtro mensual.
- `facturacion/urls.py`: ruta adicional.
- `facturacion/templates/facturacion/captura_rapida.html`: cuadro mensual, estados y totales.
- `facturacion/templates/facturacion/libros_captura_mensual.html`: selector anual de doce meses.
- `facturacion/templates/facturacion/compras_cxp_premium.html`: acceso desde Compras, solo demo_1.
- `facturacion/templates/facturacion/libro_compras_meses_premium.html`: acceso desde Libro de Compras, solo demo_1.
- `facturacion/static/facturacion/captura_rapida.js`: teclado, autocompletado, cálculo exacto y guardado asíncrono.
- `facturacion/migrations/0072_captura_rapida_compras.py`: migración inicial existente.
- `facturacion/migrations/0073_libro_compra_mensual.py`: nueva cabecera mensual, sin migración de documentos.
- `facturacion/test_captura_rapida.py`: pruebas del servidor y navegador.
- `facturacion/test_libros_mensuales.py`: período independiente, edición, anulación, permisos, estados, reportes y aislamiento.
- `facturacion/browser_tests/captura_rapida.cjs`: flujo real de teclado y solicitudes concurrentes.
- `facturacion/browser_tests/settings.py`: base temporal en disco para pruebas concurrentes SQLite.
- `facturacion/CAPTURA_RAPIDA.md`: documentación.

## Migraciones y verificación local

Se creó y aplicó localmente `facturacion.0073_libro_compra_mensual`. Depende de la migración inicial `0072`, ya existente. Solo agrega la cabecera mensual; no cambia el período ni los importes de documentos existentes. Los registros anteriores permanecen en sus períodos fiscales actuales.

Respaldo previo: `tmp/libros-mensuales-pre-migracion.sqlite3`. Se compararon los valores completos de compras fiscales, compras de inventario y proveedores antes y después de la migración para comprobar que permanecen intactos.

Para instalar en otro entorno del mismo proyecto:

```powershell
python manage.py migrate facturacion 0073
```

En despliegues que sirven archivos estáticos recopilados, ejecutar además el `collectstatic` habitual. No se realizó despliegue remoto.

## Pruebas

La verificación incluye el flujo completo en Chrome: captura con teclado, proveedor nuevo, fecha corta, factura de un mes anterior incorporada al libro elegido, filas persistentes, totales inmediatos, edición, anulación, finalización, reapertura y doce meses en el selector. También cubre concurrencia, conflictos de edición, aislamiento entre empresas y meses, lectura sin permisos de escritura, campos históricos, duplicados e integración con impuestos. Se ejecutaron las regresiones existentes de Libro de Compras y reportes de impuestos.

Pruebas de servidor:

```powershell
python manage.py test facturacion.test_captura_rapida facturacion.test_libros_mensuales --noinput
```

Para incluir navegador, configurar `PLAYWRIGHT_MODULE` con la ruta del paquete Node Playwright y `CHROME_EXECUTABLE` con la del ejecutable Chrome, y ejecutar:

```powershell
python manage.py test facturacion.test_captura_rapida facturacion.test_libros_mensuales --settings=facturacion.browser_tests.settings --noinput
```

El navegador se omite explícitamente si `PLAYWRIGHT_MODULE` no está configurado. Las pruebas usan bases temporales; no insertan facturas de prueba en la base operativa. También se verificó `manage.py check`, `makemigrations --check --dry-run`, sintaxis JavaScript y `git diff --check`.
