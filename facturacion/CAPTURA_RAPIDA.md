# Captura Rápida de Compras — demo_1

## Acceso y alcance

En `demo_1`, abrir **Compras** o **Libro de Compras** y seleccionar **Captura Rápida de Compras**.

Ruta: `/demo_1/dashboard/facturacion/libro-compras/captura-rapida/`.

Requiere acceso a la empresa, licencia y módulo de facturación habilitados, y los permisos existentes `puede_compras` y `puede_crear_compras`. Se mantienen las excepciones existentes para administradores. Para cualquier otro slug, la funcionalidad devuelve 404, también por POST y en las consultas auxiliares.

Flujo: Fecha → TAB → Proveedor → TAB → Factura → TAB → Exenta → TAB → Base 15% → TAB → Base 18% → ENTER. Las flechas seleccionan proveedores; TAB o ENTER acepta. SHIFT+TAB retrocede. ESC o el botón Omitir descarta una duplicada y devuelve el foco a Fecha.

Si el proveedor no está creado, escribir su nombre y elegir **Crear «nombre» · ingresar RTN**. El nombre se toma de la búsqueda; solo se pide el RTN. ENTER lo guarda, lo selecciona y devuelve el foco a Nº Factura sin borrar los datos de la fila. ESC cancela el alta. Requiere además el permiso existente `puede_crear_proveedores`. Si el RTN ya existe en la empresa, se selecciona ese proveedor sin duplicarlo ni cambiar su nombre. Los proveedores inactivos o RTN con varios proveedores activos requieren revisión en el catálogo. Esta ampliación no necesita migraciones.

Las fechas compactas usan DDMMAA, con año 20AA. También se admite día/mes/año con uno o dos dígitos de día y mes y año de dos o cuatro dígitos: `5/8/26` equivale a `05/08/2026`. Se admiten guiones y el formato AAAA-MM-DD. Los años de dos dígitos siempre corresponden a 20AA. Se conserva el mes explícito y se rechazan fechas inexistentes. Se ejecuta `RegistroCompraFiscal.save()` y su validación existente; el registro fiscal actual no impone una restricción adicional de mes corriente ni cierre contable.

## Fuente única e integración

Se guarda **un RegistroCompraFiscal**, enlazado al proveedor, con el usuario, fecha de registro, número formateado y número normalizado. No se crea una compra de inventario adicional.

El Libro de Compras, sus acumulados mensuales y el reporte de impuestos de Contabilidad leen ese mismo registro automáticamente. La arquitectura fiscal actual no genera asientos ni alimenta directamente el Estado de Resultados; esta función conserva esa limitación y no asigna cuentas, gastos ni pagos supuestos. Las clasificaciones y la contabilidad existentes permanecen disponibles.

Los duplicados se buscan en todo el historial fiscal y de compras de inventario de la empresa, incluidas anulaciones. La identidad utiliza proveedor o RTN; el nombre solo sirve como respaldo para documentos históricos sin proveedor vinculado ni RTN. No se rellenan ni recortan los correlativos. Los registros históricos se comparan en la consulta, sin modificar sus valores.

Los cálculos usan Decimal en servidor y centavos BigInt en navegador, con el mismo redondeo HALF_EVEN. Campos vacíos equivalen a cero; entradas inválidas, negativas, no finitas o fuera de capacidad se rechazan. El total debe ser positivo.

El guardado usa transacción, bloqueo por empresa y restricción única. Los reenvíos se rechazan con la información del documento existente. SQLite puede responder 503 cuando otro guardado mantiene un bloqueo; la fila se conserva para reintentar.

## Archivos modificados o nuevos

- `core/access.py`: asignación del permiso existente a la ruta nueva.
- `facturacion/models.py`: trazabilidad, normalización y restricción de unicidad en el modelo fiscal actual.
- `facturacion/captura_rapida.py`: formularios, búsqueda, cálculo compartido y endpoint del piloto.
- `facturacion/views.py`: reutilización del cálculo Decimal en la captura fiscal tradicional.
- `facturacion/urls.py`: ruta adicional.
- `facturacion/templates/facturacion/captura_rapida.html`: hoja de captura.
- `facturacion/templates/facturacion/compras_cxp_premium.html`: acceso desde Compras, solo demo_1.
- `facturacion/templates/facturacion/libro_compras_meses_premium.html`: acceso desde Libro de Compras, solo demo_1.
- `facturacion/static/facturacion/captura_rapida.js`: teclado, autocompletado, cálculo exacto y guardado asíncrono.
- `facturacion/migrations/0072_captura_rapida_compras.py`: migración aditiva.
- `facturacion/test_captura_rapida.py`: pruebas del servidor y navegador.
- `facturacion/browser_tests/captura_rapida.cjs`: flujo real de teclado y solicitudes concurrentes.
- `facturacion/browser_tests/settings.py`: base temporal en disco para pruebas concurrentes SQLite.
- `facturacion/CAPTURA_RAPIDA.md`: documentación.

## Migraciones y verificación local

Se creó y aplicó localmente `facturacion.0072_captura_rapida_compras`. Su dependencia `core.0050_permiso_cambiar_fecha_factura`, que ya existía y estaba pendiente, también se aplicó; no se modificó ese archivo.

La migración agrega campos opcionales y una restricción única, sin migración de datos ni actualización de compras históricas. Se creó un respaldo en `tmp/captura-rapida-pre-migracion.sqlite3` y se compararon todos los valores previos de las tablas de compras fiscales, compras de inventario y proveedores antes y después: 79, 69 y 7 registros, respectivamente, intactos.

Para instalar en otro entorno del mismo proyecto:

```powershell
python manage.py migrate facturacion 0072
```

En despliegues que sirven archivos estáticos recopilados, ejecutar además el `collectstatic` habitual. No se realizó despliegue remoto.

## Pruebas

19 pruebas: 13 del servidor, un flujo en Chrome real y cinco regresiones existentes de Libro de Compras/importación/clasificación/compras de inventario. Verifican fechas de distintos meses, correlativos variables, duplicados históricos, proveedores distintos, RTN, aislamiento multiempresa, permisos operativos, CSRF, precisión y límites monetarios, reenvíos, restricción de BD, acumulados e impuestos. El navegador comprueba foco, TAB/SHIFT+TAB/ENTER, omisión con ESC, errores recuperables y dos envíos concurrentes que producen una sola factura.

Pruebas de servidor:

```powershell
python manage.py test facturacion.test_captura_rapida --noinput
```

Para incluir navegador, configurar `PLAYWRIGHT_MODULE` con la ruta del paquete Node Playwright y `CHROME_EXECUTABLE` con la del ejecutable Chrome, y ejecutar:

```powershell
python manage.py test facturacion.test_captura_rapida --settings=facturacion.browser_tests.settings --noinput
```

El navegador se omite explícitamente si `PLAYWRIGHT_MODULE` no está configurado. Las pruebas usan bases temporales; no insertan facturas de prueba en la base operativa. También se verificó `manage.py check`, `makemigrations --check --dry-run`, sintaxis JavaScript y `git diff --check`.
