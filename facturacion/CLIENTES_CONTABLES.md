# Libros de Compras por cliente contable

Disponible únicamente en `dubon_asociados`. Conserva la captura mensual de `demo_1` y las compras tradicionales de las demás empresas.

## Acceso y uso

1. Entrar a **Dubón → Facturación → Libro Compras**.
2. Hacer clic en el nombre del cliente o en **Entrar al cliente**. En su panel, seleccionar el año y **Abrir libros**; luego **Abrir** en el mes correspondiente. Desde el mismo panel se accede al resumen acumulado anual y a proveedores.
3. Capturar con TAB / SHIFT+TAB y ENTER al terminar Base 18%. La fila guardada aparece en el mismo cuadro, sin recargar.
4. Usar **Acumulado anual** para consultar los doce meses y TOTAL AÑO. Se calcula directamente desde las facturas; las anuladas no suman.

La fecha `25/7/26` o `250726` en el libro Agosto 2026 permanece en agosto. Se conservan formato de correlativo variable, importes Decimal, búsqueda de proveedores, duplicados históricos, edición, anulación y estados de libro.

**Crear clientes y asignar usuarios:** un administrador autorizado abre **Crear cliente contable** o **Editar / asignar usuarios**, completa nombre, razón social, RTN y estado, marca los usuarios y guarda. Puede asignar el mismo usuario a varios clientes. Solo aparecen usuarios activos con acceso a Dubón. La asignación no otorga permisos nuevos: siguen aplicándose los permisos de compras y proveedores del rol. Los administradores con acceso a la empresa pueden consultar todos los clientes.

El botón **Editar / asignar usuarios** está en la fila de cada cliente y en la navegación de sus libros, acumulados y proveedores. La lista muestra las asignaciones actuales al administrador. Al editar se muestran marcados los usuarios existentes; desmarcar uno retira su acceso sin alterar las facturas. Se confirma el guardado con un mensaje. Si el botón no aparece, revisar que la cuenta tenga `es_administrador_empresa` o sea superusuario, además del acceso a Dubón.

**Proveedores:** desde el cliente, abrir **Proveedores del cliente** para crear o editar nombre, RTN y estado. También pueden crearse durante la captura. Un RTN puede existir en distintos clientes; no se reutiliza automáticamente el proveedor de otro cliente. Se conservan los datos históricos de la factura al editar el catálogo.

**Inactivos:** los usuarios normales dejan de ver el cliente; los administradores pueden consultarlo, sin guardar cambios en sus libros ni proveedores hasta reactivarlo.

## Rutas

Prefijo: `/dubon_asociados/dashboard/facturacion/libro-compras/`.

| Ruta relativa | Pantalla |
| --- | --- |
| `clientes/` | Selección de cliente |
| `clientes/<cliente_id>/?anio=2026` | Panel del cliente: libros, resumen acumulado y proveedores |
| `clientes/crear/` | Alta y asignaciones |
| `clientes/<cliente_id>/editar/` | Datos, estado y usuarios |
| `clientes/<cliente_id>/proveedores/` | Catálogo y alta de proveedores |
| `clientes/<cliente_id>/proveedores/<proveedor_id>/editar/` | Edición del proveedor |
| `clientes/<cliente_id>/libros/?anio=2026` | Doce libros mensuales |
| `clientes/<cliente_id>/libros/2026/8/` | Captura de Agosto 2026 |
| `clientes/<cliente_id>/acumulado/?anio=2026` | Acumulado anual |
| `propias/` | Compras propias anteriores de Dubón, sin cliente contable |

Todas las operaciones de cliente resuelven empresa y asignación en backend. Manipular un cliente, proveedor o factura en la URL/POST no permite acceder a otros clientes.

## Modelos y migraciones

- `0074_clientes_contables`: agrega ClienteContable, asignaciones de usuarios y relaciones opcionales en Proveedor, LibroCompraMensual y RegistroCompraFiscal. Ajusta restricciones únicas de libros y facturas para incluir el cliente, conservando las restricciones de registros sin cliente.
- `0075_clientes_iniciales_dubon`: crea Nordic, Molac y Gecko si existe `dubon_asociados`. No crea empresas ERP, no inventa razones sociales/RTN ni asigna usuarios automáticamente. Completar estos datos desde la pantalla de administración. Si Dubón se crea después de ejecutar la migración, los clientes se pueden crear desde esa misma pantalla.

Los registros anteriores conservan `cliente_contable=NULL`: no se reasignan ni se modifican sus importes, fechas o proveedores. Existe una sola factura en RegistroCompraFiscal; LibroCompraMensual es una cabecera y el acumulado es una consulta. El guardado bloquea la empresa y mantiene la restricción única en base de datos para impedir reenvíos duplicados.

Las cuentas y clasificaciones contables actuales pertenecen a la empresa ERP, sin dimensión cliente. Por eso no se comparten con los clientes ni se suman sus compras al reporte de impuestos propio de Dubón. El reporte por cliente es su acumulado anual. No se introducen asientos ni una contabilidad paralela.

El acumulado definitivo por cuentas queda pendiente del modelo que proporcionará el usuario. El panel conserva el resumen mensual actual; no se crean clasificaciones ni reglas contables provisionales.

## Archivos

- `facturacion/models.py`: relaciones, validaciones y restricciones.
- `facturacion/clientes_contables.py`: asignaciones, administración y catálogo aislado.
- `facturacion/captura_rapida.py`: contexto por cliente, acumulados y guardado reutilizado.
- `facturacion/urls.py`, `facturacion/views.py`: rutas, navegación y separación de consultas tradicionales.
- `facturacion/forms.py`, `facturacion/importadores.py`, `contabilidad/views.py`: aislamiento de proveedores y compras propias en los procesos existentes.
- `facturacion/templates/facturacion/`: `captura_rapida.html`, `libros_captura_mensual.html`, `_cliente_contable_nav.html`, `clientes_contables.html`, `cliente_contable_form.html`, `proveedores_cliente_contable.html`.
- Las dos migraciones anteriores.
- `facturacion/test_clientes_contables.py`, `facturacion/browser_tests/clientes_contables.cjs`: pruebas nuevas.
- Este documento.

## Validación

- Backend: usuarios con varios clientes, administradores, permisos del rol, asignaciones revocadas, clientes inactivos y solicitudes manipuladas.
- Proveedores: búsqueda aislada, alta/edición, RTN obligatorio y repetido en otro cliente.
- Facturas: fecha distinta del período, correlativo normalizado, duplicado entre períodos, mismo número/RTN válido en clientes distintos, restricciones de base de datos, edición/anulación y acumulados.
- Compatibilidad: regresión de captura y libros de demo_1, compras tradicionales, importación y reportes de impuestos.
- Chrome: flujo real con teclado en demo_1 y Dubón; navegación entre Nordic/Molac, acumulado, duplicados, edición/anulación y acceso rechazado para un usuario no asignado. Bases temporales, separadas de los datos operativos.
- Migración local aplicada sobre respaldo: los 79 registros fiscales, 69 compras de inventario y 7 proveedores anteriores conservaron exactamente todos sus campos originales.

Pruebas nuevas de backend:

```bash
python manage.py test facturacion.test_clientes_contables.ClientesContablesTests --noinput
```

Para los navegadores se requiere `PLAYWRIGHT_MODULE` con la ruta del paquete Playwright y `CHROME_EXECUTABLE` con la ruta de Chrome. Ejecutar los dos `*BrowserTests` con `--settings=facturacion.browser_tests.settings`.

## Actualización del servidor

```bash
(
set -e
cd /var/www/dvsolutions-erp
source .venv/bin/activate
set -a
source /etc/dvsolutions-erp.env
set +a
git pull --ff-only origin main
python manage.py check
python manage.py migrate --noinput
python manage.py collectstatic --noinput
sudo systemctl restart dvsolutions
sleep 2
sudo systemctl status dvsolutions --no-pager -l
)
```
