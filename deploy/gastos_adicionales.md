# Gastos Adicionales clínicos

El módulo captura documentos internos por paciente y los convierte en borradores
del motor existente de Facturación. El CAI y los efectos de inventario, cobro y
contabilidad se aplican únicamente al confirmar la emisión en Facturación.

## Empresas y activación

Los slugs canónicos del sistema son `hospital_mia`, `medical_spa`,
`serviciosmedicos` y `luque_aestetic`. `servicios_medicos` no es el slug usado por
la configuración clínica/fiscal existente. La base local revisada contiene solo
`hospital_mia` y `medical_spa`; las otras dos no se crearon.

La tarjeta independiente aparece en el panel principal y la navegación de estas
empresas cuando `gastos_adicionales` está activo y el usuario tiene acceso al módulo.
Su activación es independiente de Clínica y Facturación. La migración de
activación habilita este módulo únicamente en las cuatro empresas que ya existan
con esos slugs, sin cambiar la activación de otros módulos.
Las migraciones no crean empresas, roles, pacientes ni productos.

Aplicar el código y las migraciones en cada entorno:

```console
python manage.py migrate
python manage.py check
```

Los roles deben recibir explícitamente los permisos necesarios, desde la
administración de roles/usuarios. Los administradores mantienen su autorización
habitual. Los campos nuevos parten desactivados:

- `puede_ver_gastos_adicionales`: historial, detalle y PDF.
- `puede_crear_gastos_adicionales`: captura y búsqueda de pacientes/productos.
- `puede_editar_gastos_adicionales`: edición y búsqueda de pacientes/productos.
- `puede_enviar_gastos_adicionales`: envío por correo/WhatsApp mediante POST.
- `puede_convertir_gastos_adicionales_factura`: conversión mediante POST.

Convertir requiere además Facturación activa y sus permisos normales de crear,
editar y ver facturas, para poder revisar y continuar en sus pantallas existentes.
La autorización se resuelve por empresa y se comprueba en middleware y vistas;
el servicio de conversión también valida el usuario y los permisos fiscales.

## Comportamiento e historial

- Los números `GA-000001` se asignan por empresa, bajo bloqueo y restricciones
  únicas; no usan CAI ni numeración fiscal.
- Cantidad y precio admiten dos decimales. El backend recalcula subtotales/total
  y valida el catálogo permitido, importes finitos y límites, sin aceptar totales
  del navegador. El catálogo GA incluye productos de la empresa actual más los
  originales de `hospital_mia` y `medical_spa`. Esta excepción no replica ni cambia
  la empresa de los productos; los pacientes y documentos siguen siendo locales.
- Se conserva la descripción de cada producto al guardar. El GA registra el
  criterio de precios con impuesto incluido vigente al crearlo.
- La conversión usa `Factura`, `LineaFactura` y `calcular_totales()`. Consulta el
  impuesto real configurado en cada producto; si falta o está inactivo, revierte
  la operación y solicita revisar esa configuración.
- El paciente se enlaza con su cliente local existente. Si debe crearse su ficha
  comercial, el adaptador actual funciona con la sincronización entre empresas
  suspendida únicamente durante esa operación. No se modifica la sincronización
  habitual de otras operaciones.
- El GA se vincula a una sola factura, con usuario y fecha de conversión. Un
  intento repetido abre la factura existente y muestra una advertencia.
- Después de vincularse, el original permanece inmutable. La factura vinculada
  está protegida contra eliminación. Los datos fiscales se revisan en la factura.
- Incluso en clínicas de contado, guardar la revisión de un borrador originado
  en GA permite conservarlo como borrador. Seleccionar Emitida o Validar factura
  confirma la emisión utilizando el flujo fiscal existente.
- El estado GA permanece Pendiente mientras la factura esté en borrador y pasa
  a Facturado al emitir. Si la factura se anula, la relación permanece visible.

El historial ofrece filtros por paciente, fecha, número y estado. Los documentos
también aparecen en el expediente con el permiso de consulta correspondiente.
El PDF usa logo, nombre comercial y pie institucional de la empresa actual.
Correo utiliza el transporte existente de Django; WhatsApp utiliza la conexión
Cloud API de CRM de esa empresa. Las pruebas no envían mensajes externos.

## Captura y catálogo compartido

La pantalla de captura presenta Paciente, Productos y Resumen. En escritorio el
resumen permanece en la columna lateral; en móvil se ajusta a la altura real de
la navegación inferior. El subtotal, impuesto incluido y total se actualizan al
editar las líneas. Este desglose es una vista previa del motor de Facturación;
el importe histórico del GA se sigue guardando con su cálculo original.

Ambos buscadores abren su lista sin exigir texto y filtran desde el primer
carácter. Ofrecen selección con clic/tap y teclado, y páginas de 40 resultados
con «Ver más». Pacientes permite nombre, identidad, expediente y teléfonos
normalizados; productos permite nombre, código, descripción y empresa de origen.
La excepción del catálogo se comprueba en el selector, en POST y en el modelo.

La revisión de la factura conserva los IDs originales compartidos del GA, sin
ampliar el catálogo normal de Facturación. Los servicios externos sin inventario
se emiten con el motor actual. Para evitar descontar existencias de otra empresa,
un producto externo que controle inventario debe sustituirse por un producto
local antes de emitir; también se impide duplicar una factura GA con ese caso.
Guardar el GA, generar su PDF y preparar su borrador siguen disponibles.

## Cirugía, profesional e impuestos en el documento

La migración clínica `0035` agrega el tipo de cirugía y los nombres históricos
del procedimiento y del profesional. Los documentos anteriores conservan su
cirugía vacía; al crear o editar desde la pantalla se exige elegir una de las
31 cirugías del catálogo clínico existente antes de agregar productos.

El profesional visible es Dr. Luis González. La referencia se vincula únicamente
si existe un profesional activo e inequívoco con ese nombre en la empresa actual;
no se crean profesionales ni se utiliza un registro de otra empresa. El usuario
que registra el documento sigue conservado en la trazabilidad interna.

PDF y detalle muestran el ISV de cada producto y el resumen de subtotal sin ISV,
impuestos y total. El desglose consulta la configuración actual del catálogo y
utiliza `LineaFactura.calcular_importes()` en memoria, con las fórmulas originales
de Facturación. No modifica el total histórico ni crea una factura. Un impuesto
ausente o inactivo se muestra pendiente; una discrepancia con el importe original
se indica explícitamente. La conversión y la emisión mantienen su flujo existente.

## Validación

```console
python manage.py test clinica.test_gastos_adicionales_models clinica.test_gastos_adicionales_views clinica.test_gastos_adicionales_catalogo clinica.test_gastos_adicionales_cirugias clinica.test_gastos_adicionales_importes core.test_gastos_adicionales_permissions facturacion.test_gasto_adicional_catalogo
```

Las pruebas de concurrencia requieren PostgreSQL y se omiten automáticamente con
SQLite. El PDF real se valida con WeasyPrint y extracción de texto. En Windows,
si el PATH de herramientas combina DLL de Poppler y GTK, ejecutar las pruebas de
renderizado en un proceso cuyo PATH de GTK no incluya las DLL de Poppler; la
mezcla provoca un fallo nativo de fuentes y no una excepción de Python. Esta
condición no modifica el motor PDF de producción.

Verificación realizada en la base local: 86 pruebas específicas, 84 correctas y
2 de concurrencia omitidas por SQLite. Se verificaron también búsquedas y edición
en navegador a 1440 y 390 px, y PDFs de una página y cuatro páginas con nombres
largos. La regresión de Facturación, permisos, Clínica y Recetas ejecutó 395
pruebas en la implementación inicial: 382 correctas y 13 fallos reproducidos también en el código anterior al
módulo, conservando los cambios previos del usuario.

La prueba de navegador `clinica/browser_tests/gastos_adicionales.cjs` usa una
pantalla vacía renderizada indicada por `GA_TEST_URL` y respuestas de catálogo
simuladas; no modifica datos reales. Usa `PLAYWRIGHT_MODULE` para el paquete de
Playwright y opcionalmente `CHROME_EXECUTABLE` para el navegador. Verifica listas
sin texto, un carácter, teléfono, teclado, paginación, redondeo e impuestos,
contenido literal, agregar/eliminar líneas, profesional fijo, selección de cirugía
antes de abrir el catálogo y disposición móvil.

El logo de `hospital_mia` está configurado en la base local; `medical_spa` no tiene
logo configurado y utiliza sus iniciales hasta que se cargue su identidad gráfica.
Los otros dos slugs están preparados en el código y en la activación selectiva,
pero no existen en esta base local. Deben verificarse en el entorno donde estén
registradas esas empresas antes de aplicar sus migraciones.
