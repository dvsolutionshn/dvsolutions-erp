# Proveedores y clasificación de Nordic

Disponible inicialmente para el cliente Nordic de `dubon_asociados`. Reutiliza Proveedor, RegistroCompraFiscal y CuentaAcumuladoCompra. No genera otra base de compras ni asientos contables paralelos.

## Flujo

1. Las nuevas importaciones crean o reutilizan proveedores del cliente. La identificación usa primero RTN; sin RTN, nombre normalizado sin diferencias de mayúsculas, acentos, espacios o puntuación. No se fusionan coincidencias ambiguas ni nombres con RTN contradictorios.
2. En cada libro existente, usar **Crear / Vincular proveedores desde este libro**, revisar los grupos y confirmar. Solo se procesan facturas sin proveedor vinculado; los vínculos existentes se conservan. Se crean únicamente proveedores faltantes. Un RTN faltante en el catálogo puede completarse desde el documento si el usuario tiene permiso de editar proveedores. Los nombres y RTN originales de las facturas, importes, fechas, estados y períodos se conservan.
3. En **Proveedores del cliente → Editar**, configurar **Cuenta contable habitual** con el catálogo existente de ese cliente. La sugerencia aparece al elegir proveedor en captura y al clasificar; no asigna automáticamente una cuenta a todas sus facturas.
4. En **Acumulado anual**, filtrar por proveedor, mes y cuenta. El resumen por proveedor muestra cantidad, subtotal sin ISV, impuestos, total y sugerencia. El filtro inicial muestra compras por clasificar; elegir Todas para incluir las ya clasificadas.
5. Seleccionar hasta 100 compras de la página o hasta 1,000 del filtro. Elegir una cuenta o **Aplicar cuenta sugerida a todas**. La revisión muestra cuentas, cantidad e importes y permite desmarcar excepciones. Solo **Confirmar asignación** modifica las cuentas.

## Integridad

Las consultas y escrituras se limitan a empresa y cliente, con los permisos existentes. Las revisiones firmadas vencen en una hora; cambios concurrentes invalidan la confirmación. Se valida todo el lote antes de guardar dentro de una transacción con bloqueo de empresa. Se registra usuario y fecha de la última vinculación, configuración habitual y clasificación. Una sugerencia ausente o modificada obliga a revisar nuevamente.

## Archivos y migración

- `proveedores_compras.py`: identificación compartida por importación, captura y vinculación.
- `vincular_proveedores.py` y su plantilla: revisión y vinculación histórica.
- `clasificacion_masiva.py` y `revisar_clasificacion.html`: revisión y confirmación de cuentas.
- `acumulado_compras.py` y su plantilla: filtros, agrupación y acciones masivas.
- `clientes_contables.py`, `importacion_clientes.py`, `captura_rapida.py`, sus plantillas y JavaScript: integración en pantallas existentes.
- `models.py`, `urls.py`, migración `0079_proveedores_cuenta_habitual.py`: relaciones opcionales, trazabilidad y ruta. La migración no clasifica ni vincula datos históricos automáticamente.
- `test_proveedores_acumulado.py` y `browser_tests/proveedores_acumulado.cjs`: normalización, aislamiento, permisos, conservación de datos, revisiones y recorrido completo en navegador. Las pruebas existentes de acumulado también usan la confirmación nueva.

Validación: suite de compras, clientes, importación y acumulado con 60 pruebas, 59 aprobadas y una opcional omitida. Las pruebas usan datos sintéticos. Los libros del servidor se procesan desde su botón después del despliegue.
