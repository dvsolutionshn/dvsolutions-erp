# Acumulado de compras por cuentas

Primera etapa para Nordic de `dubon_asociados`, basada en la estructura de costos y gastos de `Acumulado NORDIC 2025.xlsx`. Se muestran enero a diciembre y el acumulado anual. No se importan sus valores de 2025 ni sus fórmulas.

## Acceso y uso

1. Libro Compras → Nordic → año 2026 → Ver acumulado.
2. La matriz muestra 66 cuentas iniciales: 26 de costos, 39 de operación/administración y una de gastos financieros. El catálogo conserva las cuentas de la referencia, incluidas las que todavía no tienen movimientos de compras. Ingresos y movimientos sin factura quedan para otra etapa; no se calcula utilidad ni flujo de efectivo.
3. Las compras importadas comienzan en **Por clasificar**. En Facturas del acumulado, filtrar por mes, proveedor y cuenta, seleccionar facturas y revisar la cuenta antes de confirmar. Nordic permite hasta 100 seleccionadas de la página o 1,000 del filtro. Las excepciones se pueden desmarcar durante la revisión.
4. Cada factura tiene una cuenta. Para cambiarla, seleccionar la cuenta actual o Todas en el filtro y volver a asignar; también se puede quitar la cuenta. Una factura mixta no se divide entre cuentas en esta etapa.
5. Pulsar un importe de la matriz abre el detalle del mes/cuenta. **Ver factura** abre el libro en otra pestaña y señala el registro original. El libro mensual también tiene el acceso **Clasificar compras**.
6. **Administrar cuentas** permite agregar cuentas, cambiar nombre/grupo/orden y desactivarlas. Las cuentas inactivas conservan sus importes históricos y no reciben nuevas asignaciones.

## Origen y conciliación

Los importes provienen de `RegistroCompraFiscal`: exento + base 15% + base 18% + exonerado, cuando existe. El ISV se muestra separado. El total sin ISV más ambos impuestos se compara con el total registrado en los libros; si hay diferencias, se muestran por mes para revisarlas. Los números usan coma de miles y punto decimal.

Se agrupa por `periodo_anio` y `periodo_mes`, independientemente de la fecha documental. Las anuladas no suman. Las ediciones y anulaciones se reflejan al cargar el acumulado sin copiar ni recalcular otra base de compras.

`CuentaAcumuladoCompra` es un catálogo de presentación por cliente, sin importes ni asientos. Las clasificaciones y cuentas contables existentes de la empresa administradora no están habilitadas para clientes en la arquitectura actual; se mantiene esa separación. Este informe no genera asientos contables ni modifica reportes fiscales de Dubón.

La cuenta se vincula al registro original, junto con usuario y fecha de la última clasificación. No cambia proveedor, fecha, número, importes, período, estado ni encabezado del libro. Puede clasificarse un libro finalizado según el permiso existente de editar compras. Nordic permite configurar una cuenta habitual por proveedor como sugerencia editable; consulta [Proveedores y clasificación](PROVEEDORES_ACUMULADO_NORDIC.md).

## Aislamiento y concurrencia

Solo se cargan cuentas de referencia para clientes cuyo nombre sea Nordic y cuya empresa sea `dubon_asociados`. Los demás clientes conservan su resumen mensual actual. No se crean empresas, clientes ni proveedores durante la migración.

Consulta y escritura respetan acceso a empresa, asignación a cliente y permisos existentes. Clasificar y administrar cuentas requieren editar compras y cliente activo. El modelo rechaza cuentas de otro cliente. La selección debe pertenecer al cliente y año actuales; una factura ajena, anulada o modificada invalida toda la operación. Se usa transacción, bloqueo de empresa y comprobación de versión antes de guardar el lote.

## Archivos y migraciones

- `acumulado_compras.py`: matriz, detalle, filtros, clasificación y catálogo.
- `models.py`: catálogo por cliente, relación y trazabilidad en la compra original.
- `captura_rapida.py`: la versión de la compra incluye la cuenta, para detectar cambios concurrentes.
- `urls.py`: conexión con Acumulado anual y rutas del catálogo.
- `templates/facturacion/acumulado_cuentas.html`, `cuentas_acumulado.html` y `captura_rapida.html`: pantallas y navegación.
- `migrations/0077_cuentas_acumulado_compras.py`: crea catálogo y campos opcionales; compras existentes quedan sin cuenta.
- `migrations/0078_cuentas_referencia_nordic.py`: nombres y orden iniciales solo para Nordic. Repetir su preparación no duplica cuentas. Revertir esta migración de datos no borra cuentas que puedan estar en uso.
- `test_acumulado_compras.py` y `browser_tests/acumulado_compras.cjs`: pruebas del período, importes, aislamiento, versiones, permisos, reclasificación, edición/anulación y navegación.

El Excel original se leyó sin modificarlo y no se incluye en el repositorio. Las pruebas usan compras sintéticas en bases temporales. La base de desarrollo no contiene las facturas que el usuario importó en el servidor; estas se verán al desplegar y abrir Nordic 2026.

## Servidor

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
