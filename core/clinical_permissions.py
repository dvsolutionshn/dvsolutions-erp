"""Catálogo y resolución central de permisos clínicos granulares.

Los nombres de roles nunca participan en la autorización. Las rutas se traducen
a una acción concreta y el middleware comprueba esa acción contra el rol
asignado al usuario para la empresa de la URL.
"""

EMPRESAS_CLINICAS_CON_ROLES = {
    "hospital_mia",
    "medical_spa",
    "luque_aestetic",
    "serviciosmedicos",
}

PERMISOS_CLINICOS = (
    ("Sólo lectura por módulo", (
        ("solo_lectura_facturacion", "Sólo ver Facturación"),
        ("solo_lectura_inventario", "Sólo ver Inventario y Compras"),
        ("solo_lectura_contabilidad", "Sólo ver Contabilidad"),
        ("solo_lectura_rrhh", "Sólo ver Recursos Humanos"),
        ("solo_lectura_crm", "Sólo ver CRM y Marketing"),
        ("solo_lectura_agenda", "Sólo ver Agenda y Citas"),
        ("solo_lectura_clinica", "Sólo ver Clínica y Expedientes"),
        ("solo_lectura_tecnicentro", "Sólo ver Tecnicentro"),
    )),
    ("Calendario", (
        ("puede_ver_calendario", "Ver calendario y citas"),
        ("puede_crear_citas", "Crear citas"),
        ("puede_editar_citas", "Editar datos de citas"),
        ("puede_reagendar_citas", "Reagendar citas"),
        ("puede_cambiar_estado_citas", "Cambiar estado de citas"),
        ("puede_cancelar_citas", "Cancelar citas y notificar"),
        ("puede_eliminar_citas", "Eliminar citas"),
        ("puede_marcar_citas_pagadas", "Marcar o desmarcar citas pagadas"),
        ("puede_marcar_citas_cortesia", "Marcar o desmarcar citas de cortesía"),
        ("puede_crear_bloqueos_agenda", "Crear bloqueos de disponibilidad"),
        ("puede_editar_bloqueos_agenda", "Editar bloqueos de disponibilidad"),
        ("puede_eliminar_bloqueos_agenda", "Eliminar bloqueos de disponibilidad"),
    )),
    ("Pacientes", (
        ("puede_ver_pacientes", "Ver listado y buscar pacientes"),
        ("puede_ver_datos_generales_paciente", "Ver datos generales del paciente"),
        ("puede_crear_pacientes", "Crear pacientes"),
        ("puede_editar_pacientes", "Editar datos generales"),
        ("puede_eliminar_pacientes", "Eliminar pacientes"),
        ("puede_crear_recordatorios_paciente", "Agendar recordatorios"),
        ("puede_restringir_citas_paciente", "Puede marcar paciente como No Agendar"),
        ("puede_habilitar_citas_paciente", "Puede quitar restricción"),
        ("puede_ver_motivo_restriccion_paciente", "Puede ver motivo de restricción"),
    )),
    ("Historia clínica", (
        ("puede_ver_historia_clinica", "Ver historia clínica y diagnósticos"),
        ("puede_crear_historia_clinica", "Crear historia clínica y diagnósticos"),
        ("puede_editar_historia_clinica", "Editar historia clínica"),
        ("puede_ver_antecedentes_clinicos", "Ver antecedentes"),
        ("puede_editar_antecedentes_clinicos", "Editar antecedentes"),
        ("puede_ver_examen_fisico", "Ver examen físico"),
        ("puede_editar_examen_fisico", "Editar examen físico"),
        ("puede_ver_diagnosticos", "Ver diagnósticos"),
        ("puede_editar_diagnosticos", "Editar diagnósticos"),
        ("puede_ver_evaluacion_integral", "Ver evaluación médica integral"),
        ("puede_crear_evaluacion_integral", "Crear evaluación médica integral"),
        ("puede_editar_evaluacion_integral", "Editar evaluación médica integral"),
    )),
    ("Especialidades clínicas", (
        ("puede_ver_capilar", "Ver historias capilares"),
        ("puede_crear_capilar", "Crear historias capilares"),
        ("puede_editar_capilar", "Editar historias capilares"),
        ("puede_ver_tricopigmentacion", "Ver tricopigmentación"),
        ("puede_crear_tricopigmentacion", "Crear tricopigmentación"),
        ("puede_editar_tricopigmentacion", "Editar tricopigmentación"),
        ("puede_ver_cirugia_clinica", "Ver cirugía plástica y reconstructiva"),
        ("puede_crear_cirugia_clinica", "Crear cirugía plástica"),
        ("puede_editar_cirugia_clinica", "Editar cirugía plástica"),
        ("puede_ver_medicina_estetica", "Ver medicina estética"),
        ("puede_crear_medicina_estetica", "Crear medicina estética"),
        ("puede_editar_medicina_estetica", "Editar medicina estética"),
    )),
    ("Anexos", (
        ("puede_ver_anexos_clinicos", "Ver anexos anteriores"),
        ("puede_subir_anexos_clinicos", "Subir anexos nuevos"),
        ("puede_eliminar_anexos_clinicos", "Eliminar anexos"),
        ("puede_descargar_anexos_clinicos", "Descargar anexos"),
    )),
    ("Recetas", (
        ("puede_ver_recetas", "Ver recetas"),
        ("puede_crear_recetas", "Crear recetas"),
        ("puede_enviar_recetas", "Imprimir o enviar recetas"),
    )),
    ("Gastos Adicionales", (
        ("puede_ver_gastos_adicionales", "Ver Gastos Adicionales y generar PDF"),
        ("puede_crear_gastos_adicionales", "Crear Gastos Adicionales"),
        ("puede_editar_gastos_adicionales", "Editar Gastos Adicionales"),
        ("puede_enviar_gastos_adicionales", "Enviar Gastos Adicionales"),
        ("puede_convertir_gastos_adicionales_factura", "Convertir Gasto Adicional a Factura"),
    )),
    ("Plan y tratamiento", (
        ("puede_ver_planes_tratamiento", "Ver plan y tratamiento"),
        ("puede_crear_planes_tratamiento", "Crear plan y tratamiento"),
        ("puede_editar_planes_tratamiento", "Editar plan y tratamiento"),
        ("puede_eliminar_planes_tratamiento", "Eliminar plan y tratamiento"),
    )),
    ("Enfermería", (
        ("puede_ver_enfermeria", "Ver notas de Enfermería"),
        ("puede_escribir_enfermeria", "Registrar formulario de Enfermería"),
    )),
    ("Terapias", (
        ("puede_ver_terapias", "Ver terapias"),
        ("puede_escribir_terapias", "Registrar terapias"),
    )),
    ("Cámara hiperbárica", (
        ("puede_ver_camara_hiperbarica", "Ver controles de cámara hiperbárica"),
        ("puede_escribir_camara_hiperbarica", "Registrar controles de cámara hiperbárica"),
    )),
    ("Postquirúrgicas", (
        ("puede_ver_postquirurgicas", "Ver terapias postquirúrgicas"),
        ("puede_escribir_postquirurgicas", "Registrar terapias postquirúrgicas"),
    )),
    ("Manuales PDF", (
        ("puede_ver_manuales_pdf", "Ver y descargar manuales PDF"),
        ("puede_enviar_manuales_pdf", "Enviar manuales PDF"),
        ("puede_administrar_manuales_pdf", "Crear y editar manuales PDF"),
    )),
    ("Facturación", (
        ("puede_punto_venta", "Usar punto de venta"),
        ("puede_facturas", "Ver módulo de facturación"),
        ("puede_crear_cotizaciones", "Crear cotizaciones"),
        ("puede_editar_cotizaciones", "Editar cotizaciones"),
        ("puede_aprobar_cotizaciones", "Aprobar cotizaciones"),
        ("puede_convertir_cotizaciones", "Convertir cotizaciones en facturas"),
        ("puede_crear_facturas", "Crear facturas"),
        ("puede_ver_facturas", "Ver facturas anteriores"),
        ("puede_editar_facturas", "Editar facturas"),
        ("puede_cambiar_fecha_factura", "Cambiar fecha de factura"),
        ("puede_anular_facturas", "Anular facturas"),
        ("puede_eliminar_borradores", "Eliminar borradores de factura"),
        ("puede_eliminar_facturas", "Eliminar facturas históricas"),
        ("puede_registrar_pagos_clientes", "Registrar pagos de clientes"),
        ("puede_editar_pagos_clientes", "Editar pagos de clientes"),
        ("puede_anular_pagos_clientes", "Anular pagos de clientes"),
        ("puede_correccion_fiscal_facturas", "Realizar corrección fiscal histórica"),
        ("puede_imprimir_facturas", "Imprimir o descargar facturas"),
        ("puede_enviar_facturas", "Enviar facturas"),
        ("puede_reportes", "Ver reportes de facturación"),
        ("puede_ver_reporte_ventas_producto", "Ver reporte de ventas por producto"),
        ("puede_exportar_reportes", "Exportar reportes"),
        ("puede_cierres_caja", "Ver cierres de caja"),
        ("puede_operar_cierres_caja", "Abrir, cerrar o modificar cierres de caja"),
        ("puede_recibos", "Ver recibos"),
        ("puede_editar_recibos", "Editar recibos"),
        ("puede_egresos", "Ver egresos"),
        ("puede_cxc", "Ver cuentas por cobrar"),
        ("puede_cxp", "Ver cuentas por pagar"),
    )),
    ("Inventario y productos", (
        ("puede_inventario", "Ver inventario"),
        ("puede_productos", "Ver productos"),
        ("puede_crear_productos", "Crear productos"),
        ("puede_editar_productos", "Editar productos"),
        ("puede_eliminar_productos", "Eliminar productos"),
        ("puede_ver_kardex", "Ver Kardex"),
        ("puede_crear_lotes", "Crear lotes"),
        ("puede_ver_bodegas", "Ver bodegas y existencias"),
        ("puede_ajustar_inventario", "Ajustar inventario"),
        ("puede_transferir_inventario", "Realizar transferencias rápidas"),
        ("puede_registrar_salidas_inventario", "Registrar salidas rápidas o regalías"),
    )),
    ("Compras", (
        ("puede_compras", "Ver compras"),
        ("puede_crear_compras", "Crear compras"),
        ("puede_editar_compras", "Editar compras"),
        ("puede_aplicar_compras", "Aplicar compras al inventario"),
        ("puede_anular_compras", "Anular compras"),
        ("puede_registrar_pagos_proveedores", "Registrar pagos a proveedores"),
        ("puede_revertir_pagos_proveedores", "Revertir pagos a proveedores"),
        ("puede_eliminar_compras_anuladas", "Eliminar compras anuladas"),
        ("puede_importar_libro_compras", "Importar libro de compras"),
        ("puede_crear_registros_libro_compras", "Crear registros del libro de compras"),
        ("puede_anular_registros_libro_compras", "Anular registros del libro de compras"),
        ("puede_crear_entradas_inventario", "Crear entradas documentales de inventario"),
    )),
    ("Notas de crédito", (
        ("puede_notas_credito", "Ver notas de crédito"),
        ("puede_crear_notas_credito", "Crear notas de crédito"),
        ("puede_editar_notas_credito", "Editar notas de crédito"),
        ("puede_anular_notas_credito", "Anular notas de crédito"),
    )),
    ("Proveedores", (
        ("puede_proveedores", "Ver proveedores"),
        ("puede_crear_proveedores", "Crear proveedores"),
        ("puede_editar_proveedores", "Editar proveedores"),
    )),
    ("Clientes", (
        ("puede_clientes", "Ver clientes"),
        ("puede_crear_clientes", "Crear clientes"),
        ("puede_editar_clientes", "Editar clientes"),
        ("puede_eliminar_clientes", "Eliminar clientes"),
    )),
    ("Configuración", (
        ("puede_configuracion_clinica", "Administrar configuración clínica"),
        ("puede_configuracion_facturacion", "Administrar configuración de facturación"),
        ("puede_cai", "Administrar CAI"),
        ("puede_impuestos", "Administrar impuestos"),
        ("puede_crear_profesionales_clinicos", "Crear profesionales clínicos"),
        ("puede_editar_profesionales_clinicos", "Editar profesionales clínicos"),
        ("puede_crear_servicios_clinicos", "Crear servicios clínicos"),
        ("puede_editar_servicios_clinicos", "Editar servicios clínicos"),
        ("puede_eliminar_servicios_clinicos", "Eliminar servicios clínicos"),
    )),
    ("Contabilidad", (
        ("puede_contabilidad", "Ver resumen contable"),
        ("puede_configurar_contabilidad", "Modificar configuración contable"),
        ("puede_catalogo_cuentas", "Ver catálogo de cuentas"),
        ("puede_crear_cuentas_contables", "Crear cuentas contables"),
        ("puede_editar_cuentas_contables", "Editar cuentas contables"),
        ("puede_importar_catalogo_cuentas", "Importar o sustituir catálogo contable"),
        ("puede_crear_periodos_contables", "Crear períodos contables"),
        ("puede_editar_periodos_contables", "Editar períodos contables"),
        ("puede_cerrar_periodos_contables", "Cerrar períodos contables"),
        ("puede_reabrir_periodos_contables", "Reabrir períodos contables"),
        ("puede_administrar_clasificaciones_contables", "Administrar clasificaciones contables"),
        ("puede_ver_bancos_contables", "Ver bancos y movimientos"),
        ("puede_administrar_cuentas_financieras", "Administrar cuentas financieras"),
        ("puede_importar_movimientos_bancarios", "Importar movimientos bancarios"),
        ("puede_administrar_reglas_bancarias", "Administrar reglas bancarias"),
        ("puede_clasificar_movimientos_bancarios", "Clasificar movimientos bancarios"),
        ("puede_enlazar_movimientos_bancarios", "Enlazar movimientos con documentos"),
        ("puede_conciliar_movimientos_bancarios", "Conciliar y desconciliar movimientos"),
        ("puede_crear_asientos", "Crear asientos contables"),
        ("puede_editar_asientos", "Editar asientos contables"),
        ("puede_duplicar_asientos", "Duplicar asientos contables"),
        ("puede_contabilizar_asientos", "Contabilizar asientos"),
        ("puede_reversar_asientos", "Reversar asientos"),
        ("puede_regresar_asientos_borrador", "Regresar asientos a borrador"),
        ("puede_regularizar_asientos", "Regularizar moneda y conversiones"),
        ("puede_reportes_contables", "Ver reportes contables"),
        ("puede_exportar_reportes_contables", "Exportar reportes contables"),
    )),
    ("Recursos Humanos", (
        ("puede_rrhh", "Ver resumen de Recursos Humanos"),
        ("puede_empleados", "Ver empleados"),
        ("puede_crear_empleados", "Crear empleados"),
        ("puede_editar_empleados", "Editar empleados"),
        ("puede_eliminar_empleados", "Eliminar empleados"),
        ("puede_planillas", "Ver planillas"),
        ("puede_crear_planillas", "Crear planillas"),
        ("puede_editar_planillas", "Editar detalle de planillas"),
        ("puede_eliminar_planillas", "Eliminar planillas"),
        ("puede_generar_planillas", "Generar planillas"),
        ("puede_cerrar_planillas", "Cerrar planillas"),
        ("puede_reabrir_planillas", "Reabrir planillas"),
        ("puede_pagar_planillas", "Pagar planillas"),
        ("puede_configurar_editores_planilla", "Configurar editores de planilla"),
        ("puede_gestionar_movimientos_planilla", "Registrar bonos y deducciones"),
        ("puede_vacaciones", "Ver vacaciones"),
        ("puede_gestionar_vacaciones", "Registrar vacaciones"),
        ("puede_enviar_vouchers_planilla", "Enviar comprobantes de planilla"),
        ("puede_configuracion_rrhh", "Modificar configuración de RR. HH."),
    )),
    ("CRM y Marketing", (
        ("puede_crm", "Ver resumen de CRM"),
        ("puede_campanias", "Ver campañas"),
        ("puede_administrar_plantillas_crm", "Crear o editar plantillas"),
        ("puede_crear_campanias", "Crear campañas"),
        ("puede_preparar_campanias", "Preparar destinatarios de campañas"),
        ("puede_enviar_pruebas_campanias", "Enviar pruebas de campañas"),
        ("puede_enviar_campanias", "Ejecutar envíos de campañas"),
        ("puede_configuracion_crm", "Ver configuración de CRM"),
        ("puede_conectar_whatsapp_crm", "Conectar WhatsApp Meta"),
        ("puede_probar_whatsapp_crm", "Enviar pruebas de WhatsApp"),
    )),
    ("Tecnicentro", (
        ("puede_tecnicentro", "Ver resumen del taller"),
        ("puede_recepcion_taller", "Ver recepción y agenda del taller"),
        ("puede_crear_recepciones_taller", "Crear recepciones y órdenes"),
        ("puede_crear_citas_taller", "Crear citas del taller"),
        ("puede_cambiar_estado_citas_taller", "Cambiar estado de citas del taller"),
        ("puede_registrar_inspecciones_taller", "Registrar inspecciones de recepción"),
        ("puede_operacion_taller", "Ver órdenes del taller"),
        ("puede_cambiar_estado_ordenes_taller", "Cambiar estado de órdenes"),
        ("puede_asignar_ordenes_taller", "Asignar técnicos y bahías"),
        ("puede_diagnostico_taller", "Ver diagnósticos"),
        ("puede_editar_diagnosticos_taller", "Crear o editar diagnósticos"),
        ("puede_agregar_evidencias_taller", "Agregar evidencias"),
        ("puede_configuracion_taller", "Ver configuración del taller"),
        ("puede_editar_configuracion_taller", "Editar configuración del taller"),
        ("puede_crear_bahias_taller", "Crear bahías de servicio"),
    )),
)

TODOS_LOS_PERMISOS_CLINICOS = tuple(
    campo for _, permisos in PERMISOS_CLINICOS for campo, _ in permisos
)

PERMISOS_CLINICOS_LEGADOS = (
    "puede_citas", "puede_clinica", "puede_pacientes",
    "puede_expediente_clinico", "puede_tratamientos_clinicos",
)

PERMISOS_CONSULTA_RESTRICCION_CITAS = (
    "puede_restringir_citas_paciente",
    "puede_habilitar_citas_paciente",
    "puede_ver_motivo_restriccion_paciente",
)

PERMISOS_HISTORIA_POR_TIPO = {
    "evaluacion_integral": ("puede_ver_evaluacion_integral", "puede_crear_evaluacion_integral", "puede_editar_evaluacion_integral"),
    "capilar": ("puede_ver_capilar", "puede_crear_capilar", "puede_editar_capilar"),
    "tricopigmentacion": ("puede_ver_tricopigmentacion", "puede_crear_tricopigmentacion", "puede_editar_tricopigmentacion"),
    "cirugia_plastica": ("puede_ver_cirugia_clinica", "puede_crear_cirugia_clinica", "puede_editar_cirugia_clinica"),
    "medicina_estetica": ("puede_ver_medicina_estetica", "puede_crear_medicina_estetica", "puede_editar_medicina_estetica"),
    "enfermeria": ("puede_ver_enfermeria", "puede_escribir_enfermeria", "puede_escribir_enfermeria"),
    "terapias": ("puede_ver_terapias", "puede_escribir_terapias", "puede_escribir_terapias"),
    "camara_hiperbarica": ("puede_ver_camara_hiperbarica", "puede_escribir_camara_hiperbarica", "puede_escribir_camara_hiperbarica"),
    "terapias_postquirurgicas": ("puede_ver_postquirurgicas", "puede_escribir_postquirurgicas", "puede_escribir_postquirurgicas"),
}
PERMISOS_ACCESO_HISTORIAL = tuple(dict.fromkeys(
    (
        "puede_ver_historia_clinica", "puede_crear_historia_clinica", "puede_editar_historia_clinica",
        "puede_ver_antecedentes_clinicos", "puede_editar_antecedentes_clinicos",
        "puede_ver_examen_fisico", "puede_editar_examen_fisico",
        "puede_ver_diagnosticos", "puede_editar_diagnosticos",
    ) + tuple(permiso for permisos in PERMISOS_HISTORIA_POR_TIPO.values() for permiso in permisos)
))
PERMISOS_EDICION_HISTORIAS = tuple(dict.fromkeys(
    ("puede_editar_historia_clinica",)
    + tuple(permisos[2] for permisos in PERMISOS_HISTORIA_POR_TIPO.values())
))


def permiso_historia_por_tipo(tipo, *, crear=False, editar=False):
    permisos = PERMISOS_HISTORIA_POR_TIPO.get(tipo)
    return permisos[2 if editar else 1 if crear else 0] if permisos else None


def rol_clinico_v2(usuario, empresa):
    rol = rol_clinico_granular(usuario, empresa)
    return rol if rol and rol.usa_permisos_clinicos_v2 else None


def rol_clinico_v3(usuario, empresa):
    rol = rol_clinico_v2(usuario, empresa)
    return rol if rol and rol.usa_permisos_acciones_v3 else None


def rol_clinico_granular(usuario, empresa):
    if not empresa or empresa.slug not in EMPRESAS_CLINICAS_CON_ROLES:
        return None
    rol = usuario.rol_para_empresa(empresa)
    return rol if rol and rol.activo and rol.usa_permisos_clinicos_granulares else None


def permiso_facturacion_clinico_granular(path_suffix, method="GET", *, v3=False):
    """Resuelve acciones sólo después de validar el piloto de cuatro clínicas."""
    from .access import permiso_facturacion_accion

    parts = [part for part in (path_suffix or "").strip("/").split("/") if part]
    if not parts:
        return None
    first = parts[0]
    unsafe = method.upper() not in {"GET", "HEAD", "OPTIONS"}
    if not v3:
        if first == "reportes" and len(parts) > 1:
            if parts[1] == "ventas-producto": return "puede_ver_reporte_ventas_producto"
            if parts[1] in {"excel", "facturas-pdf"}: return "puede_exportar_reportes"
        if first == "inventario":
            if len(parts) > 1 and parts[1] == "kardex": return "puede_ver_kardex"
            if len(parts) > 2 and parts[1:3] == ["farmaceutico", "lotes"]: return "puede_crear_lotes"
            if len(parts) > 1 and parts[1] == "bodegas": return "puede_ver_bodegas"
            if len(parts) > 1 and parts[1] == "regalias": return "puede_registrar_salidas_inventario"
            if len(parts) > 2 and parts[1:3] in (["farmaceutico", "traslados"], ["farmaceutico", "traslado-rapido"]):
                return "puede_transferir_inventario"
        if first == "productos" and len(parts) > 2 and parts[2] == "eliminar": return "puede_eliminar_productos"
        if first.isdigit() and len(parts) > 1:
            action = parts[1]
            if action == "corregir-numero": return "puede_correccion_fiscal_facturas"
            if action == "enviar-whatsapp": return "puede_enviar_facturas"
            if action == "pdf": return "puede_imprimir_facturas"
            if action == "pago":
                if len(parts) > 3 and parts[3] == "editar": return "puede_editar_pagos_clientes"
                if len(parts) > 3 and parts[3] == "anular": return "puede_anular_pagos_clientes"
                return "puede_registrar_pagos_clientes"
        return permiso_facturacion_accion(path_suffix)
    if first == "configuracion":
        return "puede_configuracion_facturacion" if unsafe else None
    if v3 and first == "cotizaciones":
        if "crear" in parts: return "puede_crear_cotizaciones"
        if "editar" in parts: return "puede_editar_cotizaciones"
        if "aprobar" in parts: return "puede_aprobar_cotizaciones"
        if "convertir-factura" in parts: return "puede_convertir_cotizaciones"
        return None
    if v3 and first == "pos":
        if "promociones" in parts: return "puede_configuracion_facturacion" if unsafe else None
        if "crear-rapido" in parts:
            return "puede_crear_clientes" if "clientes" in parts else "puede_crear_productos"
        if "enviar-whatsapp" in parts: return "puede_enviar_facturas"
        return "puede_punto_venta" if unsafe else None
    if v3 and first == "facturas" and "prefijo-manual" in parts:
        return "puede_configuracion_facturacion"
    if first == "reportes" and len(parts) > 1:
        if parts[1] == "ventas-producto":
            return "puede_ver_reporte_ventas_producto"
        if parts[1] in {"excel", "facturas-pdf"}:
            return "puede_exportar_reportes"
    if first == "inventario":
        if len(parts) > 1 and parts[1] == "kardex":
            return "puede_ver_kardex"
        if len(parts) > 2 and parts[1:3] == ["farmaceutico", "lotes"]:
            return "puede_crear_lotes"
        if len(parts) > 1 and parts[1] == "bodegas":
            return "puede_ver_bodegas"
        if len(parts) > 1 and parts[1] == "regalias":
            return "puede_registrar_salidas_inventario"
        if len(parts) > 1 and parts[1] == "entrada":
            return "puede_ajustar_inventario" if unsafe else None
        if len(parts) > 2 and parts[1:3] == ["entradas", "crear"]:
            return "puede_crear_entradas_inventario"
        if len(parts) > 2 and parts[1:3] == ["compras", "proveedores"]:
            return "puede_crear_proveedores"
        if len(parts) > 2 and parts[1:3] == ["compras", "productos"]:
            return "puede_crear_productos"
        if "eliminar-anulada" in parts:
            return "puede_eliminar_compras_anuladas"
        if "revertir" in parts:
            return "puede_revertir_pagos_proveedores"
        if len(parts) > 2 and parts[1:3] in (["farmaceutico", "traslados"], ["farmaceutico", "traslado-rapido"]):
            return "puede_transferir_inventario"
    if first == "productos" and len(parts) > 2 and parts[2] == "eliminar":
        return "puede_eliminar_productos"
    if first == "productos" and "codigos-barras" in parts:
        return "puede_productos"
    if first == "productos" and "categorias-farmaceuticas" in parts:
        return "puede_editar_productos" if unsafe else None
    if first == "inventario" and len(parts) == 1 and unsafe:
        return "puede_inventario"
    if first == "clientes" and "eliminar" in parts:
        return "puede_eliminar_clientes"
    if first == "libro-compras":
        if "importar" in parts: return "puede_importar_libro_compras"
        if "crear" in parts: return "puede_crear_registros_libro_compras"
        if "anular" in parts: return "puede_anular_registros_libro_compras"
        return "puede_editar_compras" if unsafe else None
    if first in {"cai", "impuestos"}:
        return ("puede_cai" if first == "cai" else "puede_impuestos") if unsafe or any(x in parts for x in {"crear", "editar", "eliminar"}) else None
    if first == "recibos" and "editar" in parts:
        return "puede_editar_recibos"
    if first == "cierres-caja" and unsafe:
        return "puede_operar_cierres_caja"
    if first == "comisiones":
        return "puede_revertir_pagos_proveedores" if "revertir" in parts else "puede_registrar_pagos_proveedores"
    if first == "reportes" and "power-bi" in parts and unsafe:
        return "puede_configuracion_facturacion"
    if first.isdigit() and len(parts) > 1:
        action = parts[1]
        if action == "corregir-numero":
            return "puede_correccion_fiscal_facturas"
        if action == "enviar-whatsapp":
            return "puede_enviar_facturas"
        if action == "pdf":
            return "puede_imprimir_facturas"
        if action == "pago":
            if len(parts) > 3 and parts[3] == "editar":
                return "puede_editar_pagos_clientes"
            if len(parts) > 3 and parts[3] == "anular":
                return "puede_anular_pagos_clientes"
            return "puede_registrar_pagos_clientes"
    # Los permisos heredados que siguen siendo funcionales también están
    # visibles en PERMISOS_CLINICOS; no existe autorización implícita.
    return permiso_facturacion_accion(path_suffix)


def permisos_gastos_adicionales_desde_ruta(path_suffix, method="GET"):
    """Permisos alternativos de cada ruta GA, aplicables también a roles legados.

    Un conjunto vacío deniega la ruta/método. Los buscadores aceptan crear o
    editar sin conceder acceso general al expediente ni al catálogo de productos.
    """
    parts = [part for part in (path_suffix or "").strip("/").split("/") if part]
    if parts and parts[0] == "gastos-adicionales":
        parts = parts[1:]
    method = method.upper()
    consulta = method in {"GET", "HEAD"}
    if not parts:
        return ("puede_ver_gastos_adicionales",) if consulta else ()
    if parts == ["nuevo"]:
        return ("puede_crear_gastos_adicionales",) if consulta or method == "POST" else ()
    if parts in (["pacientes", "buscar"], ["productos", "buscar"]):
        return ("puede_crear_gastos_adicionales", "puede_editar_gastos_adicionales") if consulta else ()
    if not parts[0].isdigit():
        return ()
    if len(parts) == 1 or parts[1:] == ["pdf"]:
        return ("puede_ver_gastos_adicionales",) if consulta else ()
    if parts[1:] == ["editar"]:
        return ("puede_editar_gastos_adicionales",) if consulta or method == "POST" else ()
    if parts[1:] in (["enviar-correo"], ["enviar-whatsapp"]):
        return ("puede_enviar_gastos_adicionales",) if method == "POST" else ()
    if parts[1:] == ["convertir"]:
        return ("puede_convertir_gastos_adicionales_factura",) if method == "POST" else ()
    return ()


def permiso_clinica_granular(path_suffix, method="GET", *, v2=False, v3=False):
    parts = [part for part in (path_suffix or "").strip("/").split("/") if part]
    if not parts:
        return None
    if parts[0] == "gastos-adicionales":
        permisos = permisos_gastos_adicionales_desde_ruta(path_suffix, method)
        return permisos[0] if len(permisos) == 1 else permisos or "__denegar__"
    if parts[0] == "configuracion":
        return "puede_configuracion_clinica"
    if parts[0] == "manuales-pdf":
        return "puede_enviar_manuales_pdf" if method != "GET" or "enviar" in parts else "puede_ver_manuales_pdf"
    if parts[0] in {"profesionales", "servicios"}:
        if not v3:
            return "puede_configuracion_clinica"
        if parts[0] == "profesionales":
            return "puede_editar_profesionales_clinicos" if "editar" in parts else (
                "puede_crear_profesionales_clinicos" if method != "GET" else "puede_configuracion_clinica"
            )
        if "eliminar" in parts:
            return "puede_eliminar_servicios_clinicos"
        if "editar" in parts:
            return "puede_editar_servicios_clinicos"
        return "puede_crear_servicios_clinicos" if method != "GET" else "puede_configuracion_clinica"
    if parts[0] == "citas":
        return "puede_crear_citas" if "crear" in parts else "puede_ver_calendario"
    if parts[0] == "tratamientos":
        if not v3:
            return "puede_editar_planes_tratamiento" if method != "GET" or any(x in parts for x in {"crear", "editar", "eliminar"}) else "puede_ver_planes_tratamiento"
        if "crear" in parts: return "puede_crear_planes_tratamiento"
        if "editar" in parts: return "puede_editar_planes_tratamiento"
        if "eliminar" in parts: return "puede_eliminar_planes_tratamiento"
        return "puede_editar_planes_tratamiento" if method != "GET" else "puede_ver_planes_tratamiento"
    if parts[0] == "expediente":
        return "puede_crear_historia_clinica"
    if parts[0] == "recetas":
        if len(parts) > 1 and parts[1] == "manuales":
            if "enviar-correo" in parts:
                return "puede_enviar_manuales_pdf"
            if "archivo" in parts:
                return "puede_ver_manuales_pdf"
            return "puede_administrar_manuales_pdf"
        return "puede_editar_historia_clinica"
    if parts[0] != "pacientes":
        return "__denegar__"
    if len(parts) == 1 or (len(parts) == 2 and parts[1] == "sugerencias"):
        return "puede_ver_pacientes"
    if len(parts) > 1 and parts[1] in {"crear", "enlace-registro", "enlaces-registro"}:
        return "puede_crear_pacientes"
    if len(parts) == 2 and parts[1].isdigit():
        return "puede_ver_datos_generales_paciente"
    if len(parts) < 3 or not parts[1].isdigit():
        return "puede_ver_pacientes"
    action = parts[2:]
    if not action:
        return "puede_ver_datos_generales_paciente"
    if action == ["restriccion"]:
        return PERMISOS_CONSULTA_RESTRICCION_CITAS if method in {"GET", "HEAD"} else "puede_restringir_citas_paciente"
    if action == ["restriccion", "habilitar"]:
        return PERMISOS_CONSULTA_RESTRICCION_CITAS if method in {"GET", "HEAD"} else "puede_habilitar_citas_paciente"
    if action[0] == "anexos":
        if method in {"GET", "HEAD"} and len(action) == 5 and action[3] == "archivo":
            return "puede_descargar_anexos_clinicos" if v2 else "puede_ver_anexos_clinicos"
        return "__denegar__"
    if action[0] == "editar":
        return "puede_editar_pacientes"
    if action[0] == "confirmar-sexo":
        return "puede_editar_pacientes"
    if action[0] == "eliminar":
        return "puede_eliminar_pacientes"
    if action[0] in {"seguimientos", "recordatorios-tratamiento"}:
        return "puede_crear_recordatorios_paciente"
    if action[0] == "evolucion":
        return "puede_subir_anexos_clinicos" if "registrar" in action else "puede_ver_anexos_clinicos"
    if action[0] in {"examenes", "documentos", "consentimientos"}:
        return "puede_subir_anexos_clinicos" if "subir" in action else "puede_ver_anexos_clinicos"
    if action[0] == "recetas":
        if "crear" in action:
            return "puede_crear_recetas"
        if any(x in action for x in {"imprimir", "pdf", "enviar-correo", "enviar-whatsapp"}):
            return "puede_enviar_recetas"
        return "puede_ver_recetas"
    if action[0] == "planes-tratamiento":
        if not v3:
            if action == ["planes-tratamiento", "crear", "app"]:
                return "puede_editar_planes_tratamiento" if method in {"GET", "HEAD"} else "__denegar__"
            return "puede_editar_planes_tratamiento" if method != "GET" else "puede_ver_planes_tratamiento"
        if action == ["planes-tratamiento", "crear", "app"]:
            return "puede_crear_planes_tratamiento" if method in {"GET", "HEAD"} else "__denegar__"
        return "puede_crear_planes_tratamiento" if method != "GET" else "puede_ver_planes_tratamiento"
    if action[0] == "historias":
        if "nueva" in action:
            tipo = action[-1]
            permiso_tipo = permiso_historia_por_tipo(tipo, crear=v3, editar=not v3) if v2 else None
            return permiso_tipo or {
                "enfermeria": "puede_escribir_enfermeria",
                "terapias": "puede_escribir_terapias",
                "camara-hiperbarica": "puede_escribir_camara_hiperbarica",
                "camara_hiperbarica": "puede_escribir_camara_hiperbarica",
                "terapias-postquirurgicas": "puede_escribir_postquirurgicas",
                "terapias_postquirurgicas": "puede_escribir_postquirurgicas",
            }.get(tipo, "puede_crear_historia_clinica")
        if "editar" in action:
            return PERMISOS_EDICION_HISTORIAS if v3 else "puede_editar_historia_clinica"
        return "puede_ver_historia_clinica"
    if action[0] == "historia-clinica":
        return "puede_editar_historia_clinica"
    if action[0] == "controles":
        tipo = action[1] if len(action) > 1 else ""
        return {
            "terapias": "puede_escribir_terapias",
            "camara-hiperbarica": "puede_escribir_camara_hiperbarica",
            "camara_hiperbarica": "puede_escribir_camara_hiperbarica",
            "terapias-postquirurgicas": "puede_escribir_postquirurgicas",
            "terapias_postquirurgicas": "puede_escribir_postquirurgicas",
        }.get(tipo, "puede_editar_historia_clinica")
    if action[0] == "preconsulta":
        return "puede_ver_historia_clinica" if method == "GET" else "puede_editar_historia_clinica"
    if action[0] in {"expediente"}:
        return "puede_crear_historia_clinica"
    return "__denegar__"


def permiso_agenda_granular(path_suffix, method="GET", data=None, *, v3=False):
    parts = [part for part in (path_suffix or "").strip("/").split("/") if part]
    data = data or {}
    if not parts:
        if method == "GET":
            return "puede_ver_calendario"
        accion = str(data.get("accion") or "").casefold()
        if not v3:
            return "puede_editar_citas" if data.get("cita_id") else "puede_crear_citas"
        if accion in {"cancelar", "cancelada"}:
            return "puede_cancelar_citas"
        if accion in {"estado", "confirmar", "completar", "realizada", "no_asistio"}:
            return "puede_cambiar_estado_citas"
        return "puede_editar_citas" if data.get("cita_id") else "puede_crear_citas"
    if parts == ["app", "productos"]:
        return "puede_inventario" if method == "GET" else "__denegar__"
    if parts in (["app", "modules.json"], ["app", "modulos"]):
        # Las vistas nuevas comprueban acceso a empresa y acciones existentes;
        # los metadatos de módulos no requieren poder consultar el calendario.
        return None if method == "GET" else "__denegar__"
    if parts[0] == "app":
        return "puede_crear_citas" if method != "GET" else "puede_ver_calendario"
    if parts[0] == "camara-hiperbarica":
        return "puede_ver_camara_hiperbarica"
    if parts[0] == "terapias-post-quirurgicas":
        return "puede_ver_postquirurgicas"
    if parts[0] == "disponibilidad":
        if "eliminar" in parts:
            return "puede_eliminar_bloqueos_agenda"
        return "puede_editar_bloqueos_agenda" if data.get("bloqueo_id") else "puede_crear_bloqueos_agenda"
    if parts[:2] == ["pacientes", "buscar"] or parts[:2] == ["clientes", "buscar"]:
        return "puede_ver_pacientes"
    if parts[:2] == ["pacientes", "crear-rapido"]:
        return "puede_crear_pacientes"
    if parts[:2] == ["tipos-consulta", "crear-rapido"]:
        return "puede_configuracion_clinica"
    if parts[:2] == ["tratamientos", "crear-rapido"]:
        return "puede_crear_planes_tratamiento"
    if parts[0] == "progreso-servicios":
        return "puede_ver_planes_tratamiento"
    if parts[0].isdigit():
        if "camara-hiperbarica" in parts:
            return "puede_escribir_camara_hiperbarica"
        if "terapias-post-quirurgicas" in parts:
            return "puede_escribir_postquirurgicas"
        if "eliminar" in parts:
            return "puede_eliminar_citas"
        if not v3:
            return "puede_editar_citas"
        if "cancelar-whatsapp" in parts:
            return "puede_cancelar_citas"
        if "reagendar-whatsapp" in parts:
            return "puede_reagendar_citas"
        if "estado" in parts:
            return "puede_cambiar_estado_citas"
        return "puede_editar_citas"
    return "__denegar__"
