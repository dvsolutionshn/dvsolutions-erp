"""Política transversal de acceso de sólo lectura por módulo."""

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

SOLO_LECTURA_CAMPOS = {
    "facturacion": "solo_lectura_facturacion",
    "inventario": "solo_lectura_inventario",
    "contabilidad": "solo_lectura_contabilidad",
    "rrhh": "solo_lectura_rrhh",
    "crm": "solo_lectura_crm",
    "agenda": "solo_lectura_agenda",
    "clinica": "solo_lectura_clinica",
    "tecnicentro": "solo_lectura_tecnicentro",
}

PERMISOS_MODULO = {
    "facturacion": {
        "puede_punto_venta", "puede_configuracion_facturacion", "puede_cierres_caja",
        "puede_facturas", "puede_ver_facturas", "puede_clientes", "puede_cai",
        "puede_impuestos", "puede_notas_credito", "puede_recibos", "puede_egresos",
        "puede_reportes", "puede_cxc", "puede_crear_facturas", "puede_editar_facturas",
        "puede_cambiar_fecha_factura", "puede_anular_facturas", "puede_eliminar_borradores",
        "puede_eliminar_facturas", "puede_registrar_pagos_clientes", "puede_editar_pagos_clientes",
        "puede_anular_pagos_clientes", "puede_correccion_fiscal_facturas", "puede_imprimir_facturas",
        "puede_enviar_facturas", "puede_ver_reporte_ventas_producto", "puede_crear_clientes",
        "puede_editar_clientes", "puede_crear_notas_credito", "puede_editar_notas_credito",
        "puede_anular_notas_credito", "puede_exportar_reportes",
    },
    "inventario": {
        "puede_productos", "puede_proveedores", "puede_inventario", "puede_compras", "puede_cxp",
        "puede_crear_productos", "puede_editar_productos", "puede_eliminar_productos",
        "puede_crear_proveedores", "puede_editar_proveedores", "puede_ajustar_inventario",
        "puede_ver_kardex", "puede_crear_lotes", "puede_ver_bodegas",
        "puede_registrar_salidas_inventario", "puede_crear_compras", "puede_editar_compras",
        "puede_aplicar_compras", "puede_anular_compras", "puede_registrar_pagos_proveedores",
        "puede_transferir_inventario",
    },
    "contabilidad": {
        "puede_contabilidad", "puede_catalogo_cuentas", "puede_crear_asientos",
        "puede_contabilizar_asientos", "puede_reportes_contables",
    },
    "rrhh": {
        "puede_rrhh", "puede_empleados", "puede_planillas", "puede_vacaciones",
        "puede_configuracion_rrhh",
    },
    "crm": {"puede_crm", "puede_campanias", "puede_configuracion_crm"},
    "agenda": {
        "puede_citas", "puede_ver_calendario", "puede_crear_citas", "puede_editar_citas",
        "puede_eliminar_citas", "puede_crear_bloqueos_agenda", "puede_editar_bloqueos_agenda",
        "puede_eliminar_bloqueos_agenda", "puede_marcar_citas_pagadas", "puede_marcar_citas_cortesia",
    },
    "clinica": {
        "puede_clinica", "puede_pacientes", "puede_expediente_clinico", "puede_tratamientos_clinicos",
        "puede_configuracion_clinica", "puede_ver_pacientes", "puede_ver_datos_generales_paciente",
        "puede_crear_pacientes", "puede_editar_pacientes", "puede_eliminar_pacientes",
        "puede_crear_recordatorios_paciente", "puede_restringir_citas_paciente",
        "puede_habilitar_citas_paciente", "puede_ver_motivo_restriccion_paciente",
        "puede_ver_historia_clinica", "puede_crear_historia_clinica", "puede_editar_historia_clinica",
        "puede_ver_anexos_clinicos", "puede_subir_anexos_clinicos", "puede_eliminar_anexos_clinicos",
        "puede_descargar_anexos_clinicos", "puede_ver_recetas", "puede_crear_recetas",
        "puede_enviar_recetas", "puede_ver_gastos_adicionales", "puede_crear_gastos_adicionales",
        "puede_editar_gastos_adicionales", "puede_enviar_gastos_adicionales",
        "puede_convertir_gastos_adicionales_factura", "puede_ver_planes_tratamiento",
        "puede_editar_planes_tratamiento", "puede_ver_enfermeria", "puede_escribir_enfermeria",
        "puede_ver_terapias", "puede_escribir_terapias", "puede_ver_camara_hiperbarica",
        "puede_escribir_camara_hiperbarica", "puede_ver_postquirurgicas",
        "puede_escribir_postquirurgicas", "puede_ver_manuales_pdf", "puede_enviar_manuales_pdf",
        "puede_administrar_manuales_pdf", "puede_ver_antecedentes_clinicos",
        "puede_editar_antecedentes_clinicos", "puede_ver_examen_fisico", "puede_editar_examen_fisico",
        "puede_ver_diagnosticos", "puede_editar_diagnosticos", "puede_ver_evaluacion_integral",
        "puede_editar_evaluacion_integral", "puede_ver_capilar", "puede_editar_capilar",
        "puede_ver_tricopigmentacion", "puede_editar_tricopigmentacion", "puede_ver_cirugia_clinica",
        "puede_editar_cirugia_clinica", "puede_ver_medicina_estetica", "puede_editar_medicina_estetica",
    },
    "tecnicentro": {
        "puede_tecnicentro", "puede_recepcion_taller", "puede_diagnostico_taller",
        "puede_operacion_taller", "puede_configuracion_taller",
    },
}

PERMISOS_LECTURA = {
    "facturacion": {
        "puede_facturas", "puede_ver_facturas", "puede_clientes", "puede_cierres_caja",
        "puede_notas_credito", "puede_recibos", "puede_egresos", "puede_reportes", "puede_cxc",
        "puede_imprimir_facturas", "puede_ver_reporte_ventas_producto",
    },
    "inventario": {
        "puede_productos", "puede_proveedores", "puede_inventario", "puede_compras", "puede_cxp",
        "puede_ver_kardex", "puede_ver_bodegas",
    },
    "contabilidad": {"puede_contabilidad", "puede_catalogo_cuentas", "puede_reportes_contables"},
    "rrhh": {"puede_rrhh", "puede_empleados", "puede_planillas", "puede_vacaciones"},
    "crm": {"puede_crm", "puede_campanias"},
    "agenda": {"puede_citas", "puede_ver_calendario"},
    "clinica": {
        "puede_clinica", "puede_pacientes", "puede_expediente_clinico", "puede_tratamientos_clinicos",
        "puede_ver_pacientes", "puede_ver_datos_generales_paciente", "puede_ver_motivo_restriccion_paciente",
        "puede_ver_historia_clinica", "puede_ver_anexos_clinicos", "puede_descargar_anexos_clinicos",
        "puede_ver_recetas", "puede_ver_gastos_adicionales", "puede_ver_planes_tratamiento",
        "puede_ver_enfermeria", "puede_ver_terapias", "puede_ver_camara_hiperbarica",
        "puede_ver_postquirurgicas", "puede_ver_manuales_pdf", "puede_ver_antecedentes_clinicos",
        "puede_ver_examen_fisico", "puede_ver_diagnosticos", "puede_ver_evaluacion_integral",
        "puede_ver_capilar", "puede_ver_tricopigmentacion", "puede_ver_cirugia_clinica",
        "puede_ver_medicina_estetica",
    },
    "tecnicentro": {"puede_tecnicentro", "puede_recepcion_taller", "puede_diagnostico_taller", "puede_operacion_taller"},
}

SEGMENTOS_ESCRITURA = frozenset({
    "crear", "nuevo", "nueva", "editar", "eliminar", "borrar", "anular", "aplicar", "pago", "pagar",
    "ajuste", "entrada", "registrar", "subir", "enviar", "guardar", "validar", "duplicar", "transferir",
    "traslado-rapido", "regalias", "cerrar", "abrir", "clasificar", "enlazar-factura", "enlazar-compra",
    "contabilizar", "reversar", "regresar-borrador", "regularizar-moneda", "revisar-conversion",
    "cambiar-fecha", "corregir-numero", "configuracion", "aprobar", "convertir", "convertir-factura",
    "importar", "sustituir-rumi", "cargar", "generar", "reabrir", "conciliar", "desconciliar",
    "preparar-envios", "conectar-whatsapp-meta", "enviar-prueba-whatsapp", "enviar-prueba-masiva",
    "enviar-whatsapp-api", "whatsapp-api", "email", "estado", "asignar", "evidencia", "inspeccion",
    "confirmar-sexo", "agregar", "agregar-sesion", "completar", "restriccion", "habilitar",
})


def modulo_facturacion_desde_ruta(path_suffix):
    primero = next((p for p in (path_suffix or "").strip("/").split("/") if p), "")
    return "inventario" if primero in {"inventario", "libro-compras", "productos", "proveedores", "cxp"} else "facturacion"


def rol_solo_lectura(usuario, empresa, modulo):
    if not usuario or not getattr(usuario, "is_authenticated", False) or usuario.is_superuser or usuario.es_administrador_empresa:
        return None
    rol = usuario.rol_para_empresa(empresa)
    campo = SOLO_LECTURA_CAMPOS.get(modulo)
    return rol if rol and rol.activo and campo and getattr(rol, campo, False) else None


def permiso_solo_lectura(rol, permiso):
    """None conserva la autorización normal; bool aplica el override restrictivo."""
    if not rol or not rol.activo:
        return None
    for modulo, campo in SOLO_LECTURA_CAMPOS.items():
        if getattr(rol, campo, False) and permiso in PERMISOS_MODULO[modulo]:
            return permiso in PERMISOS_LECTURA[modulo]
    return None


def evaluar_ruta_solo_lectura(request, empresa, modulo, path_suffix):
    """None: rol normal; True: lectura permitida; False: acción bloqueada."""
    if not rol_solo_lectura(request.user, empresa, modulo):
        return None
    request.solo_lectura_modulo = modulo
    if request.method not in SAFE_METHODS:
        return False
    partes = {p.casefold() for p in (path_suffix or "").strip("/").split("/") if p}
    if partes & SEGMENTOS_ESCRITURA:
        return False
    if any(key in request.GET for key in ("editar", "eliminar", "crear", "accion")):
        return False
    return True
