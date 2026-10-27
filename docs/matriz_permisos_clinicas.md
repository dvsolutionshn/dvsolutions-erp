# Matriz de permisos del piloto clínico

Alcance exclusivo: `hospital_mia`, `medical_spa`, `luque_aestetic` y
`serviciosmedicos`. Esta matriz fue levantada de `clinica/urls.py`,
`crm/agenda_urls.py`, `facturacion/urls.py`, sus vistas y los controles del
middleware. No autoriza extender el piloto a empresas ERP.

Leyenda del permiso actual: **granular** (acción separada), **general**
(comparte permiso con otras acciones) y **sin acción real** (la función
solicitada no existe hoy en las rutas revisadas y no debe inventarse).

| Módulo | Acción existente | Permiso actual | Nuevo permiso requerido |
|---|---|---|---|
| Dashboard | Entrar al inicio clínico | Derivado de cualquier acceso | Mantener derivado, sin permiso implícito adicional |
| Pacientes | Listar/buscar | Granular: `puede_ver_pacientes` | Mantener |
| Pacientes | Ver datos generales | Granular: `puede_ver_datos_generales_paciente` | Mantener |
| Pacientes | Crear | Granular: `puede_crear_pacientes` | Mantener |
| Pacientes | Editar | Granular: `puede_editar_pacientes` | Mantener |
| Pacientes | Eliminar | Granular: `puede_eliminar_pacientes` | Mantener |
| Pacientes | Crear recordatorios/seguimientos | Granular: `puede_crear_recordatorios_paciente` | Mantener |
| Pacientes | Marcar/quitar/ver motivo “No Agendar” | Sin acción real en URLs/modelos revisados | Implementar función primero; después crear tres permisos separados |
| Expediente | Ver historia completa | Granular por bloque y especialidad en roles v2 | Implementado con filtrado de datos y rutas |
| Expediente | Crear historia/diagnóstico | Granular por bloque y especialidad en roles v2 | Implementado |
| Expediente | Editar historia | Granular por bloque y especialidad en roles v2 | Implementado |
| Historia | Antecedentes y preconsulta | `puede_ver_antecedentes_clinicos` / `puede_editar_antecedentes_clinicos` | Implementado |
| Historia | Examen físico | `puede_ver_examen_fisico` / `puede_editar_examen_fisico` | Implementado |
| Historia | Diagnóstico | `puede_ver_diagnosticos` / `puede_editar_diagnosticos` | Implementado |
| Historia | Evaluación integral / medicina estética | Permisos de ver y editar separados | Implementado |
| Historia | Cirugía plástica | Permisos de ver y editar separados | Implementado |
| Historia | Capilar / tricopigmentación | Permisos de ver y editar separados | Implementado |
| Plan y tratamiento | Ver | Granular: `puede_ver_planes_tratamiento` | Mantener |
| Plan y tratamiento | Crear/editar | Granular: `puede_editar_planes_tratamiento` | Separar crear de editar cuando la UI lo requiera |
| Enfermería | Ver / registrar formulario | `puede_ver_enfermeria` / `puede_escribir_enfermeria` | Implementado |
| Terapias | Ver / registrar | Granular separado | Mantener |
| Cámara hiperbárica | Ver / registrar | Granular separado | Mantener |
| Postquirúrgicas | Ver / registrar | Granular separado | Mantener |
| Anexos | Ver anteriores | Granular: `puede_ver_anexos_clinicos` | Mantener |
| Anexos | Subir | Granular: `puede_subir_anexos_clinicos` | Mantener |
| Anexos | Descargar | `puede_descargar_anexos_clinicos` | Implementado en UI y descarga protegida |
| Anexos | Eliminar | Granular: `puede_eliminar_anexos_clinicos` | Mantener |
| Consentimientos/documentos/exámenes | Ver / subir | Comparte permisos de anexos | Mantener mientras sean anexos; documentar en UI |
| Recetas | Ver / crear / enviar o imprimir | Granular separado | Mantener |
| Manuales PDF | Ver / enviar / administrar | Granular separado | Mantener |
| Agenda | Ver calendario | Granular: `puede_ver_calendario` | Mantener |
| Agenda | Crear cita | Granular: `puede_crear_citas` | Mantener |
| Agenda | Editar/reagendar/cambiar estado | Editar cita, marcar pagada y marcar cortesía separados | Implementado |
| Agenda | Cancelar/eliminar | Granular: `puede_eliminar_citas` | Separar cancelar de eliminación definitiva si se añaden ambas acciones |
| Agenda | Crear/editar bloqueo de disponibilidad | `puede_crear_bloqueos_agenda` / `puede_editar_bloqueos_agenda` | Implementado |
| Agenda | Eliminar bloqueo | `puede_eliminar_bloqueos_agenda` | Implementado |
| Gastos adicionales | Ver/PDF, crear, editar, enviar, convertir | Cinco permisos granulares | Mantener |
| Facturación | Ver módulo | `puede_facturas` | Mantener explícito en editor clínico |
| Facturación | Crear factura | `puede_crear_facturas` | Mantener |
| Facturación | Ver facturas anteriores | `puede_ver_facturas` | Mantener |
| Facturación | Editar / cambiar fecha / anular | Permisos separados | Mantener |
| Facturación | Eliminar borrador / histórica | Permisos separados | Mantener |
| Facturación | Registrar pago | `puede_registrar_pagos_clientes` | Mantener |
| Facturación | Editar pago | Antes compartido; ahora `puede_editar_pagos_clientes` en piloto | Implementado |
| Facturación | Anular pago | Antes usaba anular factura; ahora `puede_anular_pagos_clientes` | Implementado |
| Facturación | Corrección fiscal histórica | Antes editar factura; ahora `puede_correccion_fiscal_facturas` | Implementado |
| Facturación | Imprimir/descargar factura | Antes ver factura; ahora `puede_imprimir_facturas` | Implementado |
| Facturación | Enviar factura | Antes ver factura; ahora `puede_enviar_facturas` | Implementado |
| Reportes | Ver reportes generales | `puede_reportes` | Mantener |
| Reportes | Ventas por producto | Antes reportes generales; ahora `puede_ver_reporte_ventas_producto` | Implementado |
| Reportes | Exportar Excel/ZIP | `puede_exportar_reportes` | Mantener |
| Inventario | Ver inventario/existencias | `puede_inventario` | Mantener |
| Inventario | Ver Kardex | Antes inventario; ahora `puede_ver_kardex` | Implementado |
| Inventario | Crear lote | Antes inventario; ahora `puede_crear_lotes` | Implementado |
| Inventario | Editar lote | Sin ruta independiente | Crear permiso cuando exista la acción |
| Bodegas | Ver bodegas y detalle | Antes inventario; ahora `puede_ver_bodegas` | Implementado |
| Bodegas | Crear/editar bodega | Sin rutas independientes en facturación | Crear permisos al implementar las acciones |
| Transferencias | Ejecutar transferencia rápida/traslado | `puede_transferir_inventario` | Mantener |
| Transferencias | Ver historial | Actualmente aparece dentro de inventario farmacéutico/Kardex | Usar `puede_ver_kardex`; separar si nace pantalla propia |
| Inventario | Salida rápida/regalía | Antes inventario; ahora `puede_registrar_salidas_inventario` | Implementado |
| Productos | Ver / crear / editar | Permisos separados | Mantener |
| Productos | Eliminar | Antes compartido; ahora `puede_eliminar_productos` | Implementado |
| Proveedores | Ver / crear / editar | Permisos separados | Mantener |
| Clientes | Ver / crear / editar | Permisos separados | Mantener |
| Compras | Ver / crear / editar / aplicar / anular / pagar | Permisos separados | Mantener visibles en editor clínico |
| Notas de crédito | Ver / crear / editar / anular | Permisos separados | Mantener visibles en editor clínico |
| Usuarios | Listar/asignar rol | Administrador clínico de empresa | Mantener; auditar empresa, usuario y rol |
| Roles | Crear/editar/activar/desactivar | Administrador clínico de empresa | Mantener |
| Roles | Duplicar | No existía | Implementado; copia inactiva |
| Configuración | Clínica / facturación / CAI / impuestos | Permisos separados | Mantener visibles en editor clínico |

## Hallazgos de seguridad

- La activación del motor granular debe comprobar el slug en el propio resolver,
  no sólo en la pantalla administrativa.
- Las rutas con acción propia deben comprobar el permiso de acción aunque el
  usuario pueda entrar al módulo.
- El rol seleccionado reemplaza el conjunto completo de permisos mostrados:
  las casillas no enviadas se guardan como `False`.
- La vista clínica consolidada filtra datos, formularios y notas por permiso;
  los POST y las rutas directas vuelven a validar la acción en backend.
- Los roles existentes continúan en compatibilidad v1 hasta que un administrador
  los revise y guarde. El guardado activa v2 y aplica exactamente las casillas
  seleccionadas, sin permisos heredados implícitos.
