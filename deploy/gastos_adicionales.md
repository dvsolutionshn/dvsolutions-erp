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
  y valida empresa, catálogo, importes finitos y límites, sin aceptar totales del
  navegador.
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

## Validación

```console
python manage.py test clinica.test_gastos_adicionales_models clinica.test_gastos_adicionales_views core.test_gastos_adicionales_permissions
```

Las pruebas de concurrencia requieren PostgreSQL y se omiten automáticamente con
SQLite. El PDF real se valida con WeasyPrint y extracción de texto. En Windows,
si el PATH de herramientas combina DLL de Poppler y GTK, ejecutar las pruebas de
renderizado en un proceso cuyo PATH de GTK no incluya las DLL de Poppler; la
mezcla provoca un fallo nativo de fuentes y no una excepción de Python. Esta
condición no modifica el motor PDF de producción.

Verificación realizada en la base local: 48 pruebas específicas, 46 correctas y
2 de concurrencia omitidas por SQLite. Se verificaron también búsquedas y edición
en navegador a 1440 y 390 px, y PDFs de una página y cuatro páginas con nombres
largos. La regresión de Facturación, permisos, Clínica y Recetas ejecutó 395
pruebas: 382 correctas y 13 fallos reproducidos también en el código anterior al
módulo, conservando los cambios previos del usuario.

El logo de `hospital_mia` está configurado en la base local; `medical_spa` no tiene
logo configurado y utiliza sus iniciales hasta que se cargue su identidad gráfica.
Los otros dos slugs están preparados en el código y en la activación selectiva,
pero no existen en esta base local. Deben verificarse en el entorno donde estén
registradas esas empresas antes de aplicar sus migraciones.
