# Importación de libros de compras por cliente

Disponible en `dubon_asociados`, dentro de los libros de clientes contables. Usa los proveedores, RegistroCompraFiscal y LibroCompraMensual existentes. No crea una segunda tabla de compras.

## Uso

1. **Libro Compras → cliente → año → mes → Importar Excel**.
2. Seleccionar el `.xlsx` y pulsar **Revisar archivo**. Se lee COMPRAS o, si no existe, la primera hoja. No se guarda ninguna compra durante la vista previa.
3. Verificar el encabezado del archivo frente al cliente/sucursal y el período de destino. La importación no identifica ni crea sucursales automáticamente.
4. Corregir fechas, proveedor, RTN o referencia cuando corresponda. Desmarcar las filas que no se deban importar. Pulsar **Actualizar vista previa** para revisar nuevamente los resultados y totales.
5. Revisar las advertencias y marcar la confirmación; pulsar **Confirmar importación**. Las filas seleccionadas con errores impiden guardar el lote completo. Las duplicadas se omiten.
6. Las compras aparecen en el cuadro mensual y alimentan el resumen anual existente. El acumulado definitivo por cuentas sigue pendiente del formato que proporcionará el usuario.

La vista previa caduca tras una hora sin renovarse. Límite: 5 MB, 1000 filas de compras, 40 columnas y 5000 filas físicas de hoja. Para documentos mayores, dividirlos en archivos.

## Formato reconocido

Se reutiliza la detección de encabezados del importador tradicional. Campos mínimos: Fecha, Beneficiario/Proveedor y Subtotal. Campos adicionales: Nº de factura, RTN, ISV 15%, ISV 18% (también encabezado numérico 0.18) y Total.

- Una única tasa identifica la base a partir del subtotal; sin impuestos el subtotal se trata como exento. Si hay dos tasas, el archivo debe proporcionar Base 15%, Base 18% y los importes exentos cuando correspondan: no se inventa su distribución.
- Las fórmulas se leen desde sus resultados guardados. No se ejecutan fórmulas ni vínculos externos. Si falta el resultado de una fórmula, se solicita abrir, recalcular y guardar el Excel antes de cargarlo otra vez.
- Se omiten filas vacías y la fila TOTAL; las filas incompletas con montos se muestran con errores, sin desaparecer silenciosamente.
- Las fechas anteriores permanecen en el período seleccionado. Una fecha posterior al período se señala para revisar el año; nunca se corrige automáticamente. El usuario confirma expresamente las fechas y las advertencias antes de guardar. Esto no constituye una validación de elegibilidad fiscal.

## Proveedores y números

Se buscan proveedores exclusivamente dentro de empresa + cliente, por RTN si se informa o por nombre sin distinguir mayúsculas si falta. Los nuevos proveedores se crean al confirmar, una sola vez por identidad dentro del lote, respetando el permiso de crear proveedores.

Los históricos pueden importarse sin RTN. Completar el RTN posteriormente en la ficha conserva el proveedor y sus compras. Si un nombre ya existe con un RTN diferente o vacío, corregir su ficha antes de importar con un RTN nuevo evita crear otro proveedor accidentalmente. Los nombres distintos no se fusionan por similitud.

Las referencias cortas se conservan; los números largos sin guiones se formatean con 3-3-2-resto. Un número vacío se guarda vacío, sin correlativo inventado, con observación «Sin número / número ilegible». El cuadro lo identifica con esa etiqueta y permite editarlo.

La detección de duplicados con número consulta todo el historial del cliente, incluyendo anuladas, y compara por proveedor/RTN y número normalizado. Sin número se avisa de coincidencias de proveedor, fecha e importe, sin declararlas duplicados definitivos.

Cada fila recibe una clave única derivada de empresa, cliente, hash del archivo, hoja y número de fila. Reenviar el mismo archivo no duplica sus filas aunque cambie el nombre del archivo o el período elegido. Para corregir una compra ya importada, editarla desde el libro. Para archivos distintos, las filas sin número requieren revisar las coincidencias aproximadas.

## Importes y trazabilidad

El cálculo reutiliza Decimal y el redondeo a centavos de la captura rápida. Se muestran por fila los importes calculados, el total guardado en Excel y la diferencia, además del total general del Excel y la suma de filas válidas seleccionadas. Una diferencia global puede incluir exclusiones, errores, duplicados y redondeo. No se distribuye ninguna diferencia entre facturas ni se fuerza el resultado para cuadrar el total.

Se conservan usuario y fecha de registro, nombre del archivo, hoja, fila, fecha original informada en Excel y diferencia de importes en la observación de cada compra. Las compras existentes no se reescriben.

La confirmación se firma para usuario, empresa, cliente y período. Al guardar se vuelven a revisar permisos, asignación, cliente activo, estado del libro, proveedores y duplicados dentro de una transacción con bloqueo de empresa. Un error revierte todas las compras y proveedores creados por ese intento.

## Archivos y migración

- `facturacion/importacion_clientes.py`: lectura, revisión y confirmación.
- `facturacion/templates/facturacion/importar_cliente.html`: carga y vista previa editable.
- `facturacion/urls.py` y `templates/facturacion/captura_rapida.html`: ruta y botón de acceso.
- `facturacion/importadores.py`: detección compartida de encabezados.
- `facturacion/models.py` y `migrations/0076_importacion_historica_clientes.py`: clave única de importación y números vacíos para documentos históricos. No se modifican datos existentes.
- `facturacion/captura_rapida.py`, `static/facturacion/captura_rapida.js` y `forms.py`: edición de históricos sin número, manteniendo obligatorio el número en la captura y formulario tradicionales.
- `facturacion/test_importacion_clientes.py` y `browser_tests/importacion_clientes.cjs`: pruebas de lectura, permisos, integridad, reenvíos y navegador.

Ruta: `/dubon_asociados/dashboard/facturacion/libro-compras/clientes/<cliente_id>/libros/<anio>/<mes>/importar/`.

Pruebas: importación sin escritura previa, correcciones, exclusiones, RTN pendiente, referencias cortas, documentos sin número y su edición, duplicados históricos, aislamiento entre clientes, permisos revocados, CSRF, firma y vencimiento, redondeo, fórmulas sin caché, formatos incorrectos, reversión completa ante error y regresión de compras tradicionales. El Excel privado de Nordic se prueba opcionalmente con `IMPORT_EXCEL_SAMPLE`, siempre en una base temporal; no se incluye en el repositorio.

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
