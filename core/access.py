FACTURACION_PERMISSION_MAP = [
    ("pos/", "puede_punto_venta"),
    ("configuracion/", "puede_configuracion_facturacion"),
    ("cierres-caja/", "puede_cierres_caja"),
    ("inventario/compras/", "puede_compras"),
    ("libro-compras/", "puede_compras"),
    ("inventario/entrada/", "puede_inventario"),
    ("inventario/entradas/", "puede_inventario"),
    ("inventario/ajuste/", "puede_inventario"),
    ("inventario/bodegas/", "puede_inventario"),
    ("inventario/kardex/", "puede_inventario"),
    ("inventario/", "puede_inventario"),
    ("clientes/", "puede_clientes"),
    ("productos/", "puede_productos"),
    ("proveedores/", "puede_proveedores"),
    ("cai/", "puede_cai"),
    ("impuestos/", "puede_impuestos"),
    ("notas-credito/", "puede_notas_credito"),
    ("recibos/", "puede_recibos"),
    ("egresos/", "puede_egresos"),
    ("reportes/", "puede_reportes"),
    ("cxc/", "puede_cxc"),
    ("cxp/", "puede_cxp"),
    ("cotizaciones/", "puede_facturas"),
    ("facturas/", "puede_ver_facturas"),
    ("crear/", "puede_crear_facturas"),
]


def interfaz_clinica_activa(empresa):
    """Indica si la empresa usa la experiencia del producto clinico."""
    return bool(empresa and getattr(empresa, "tipo_solucion", "") == "clinica")


EMPRESAS_CON_GASTOS_ADICIONALES = frozenset({
    "hospital_mia", "medical_spa", "serviciosmedicos", "luque_aestetic",
})


def gastos_adicionales_habilitados(empresa):
    """Ofrece el módulo únicamente a las clínicas existentes autorizadas."""
    return bool(
        empresa
        and empresa.slug in EMPRESAS_CON_GASTOS_ADICIONALES
        and empresa.tiene_modulo_activo("gastos_adicionales")
    )


def manuales_pdf_habilitados(empresa):
    """Controla por configuracion, no por slug, donde se ofrece el modulo de manuales."""
    if not interfaz_clinica_activa(empresa):
        return False
    try:
        configuracion = empresa.configuracion_avanzada
    except Exception:
        return False
    return bool(configuracion.manuales_pdf_habilitados)


def modo_clinico_simple_activo(user, empresa):
    """Limita la interfaz clínica simplificada a las empresas autorizadas."""
    return bool(
        user
        and getattr(user, "is_authenticated", False)
        and empresa
        and interfaz_clinica_activa(empresa)
        and getattr(user, "modo_clinico_simple", False)
    )

CONTABILIDAD_PERMISSION_MAP = [
    ("configuracion/", "puede_contabilidad"),
    ("periodos/", "puede_contabilidad"),
    ("clasificaciones-compras/", "puede_contabilidad"),
    ("bancos/", "puede_ver_bancos_contables"),
    ("cuentas/", "puede_catalogo_cuentas"),
    ("asientos/", "puede_contabilidad"),
    ("reportes/", "puede_reportes_contables"),
]

RRHH_PERMISSION_MAP = [
    ("configuracion/", "puede_configuracion_rrhh"),
    ("empleados/", "puede_empleados"),
    ("planillas/", "puede_planillas"),
    ("movimientos/", "puede_planillas"),
    ("vacaciones/", "puede_vacaciones"),
    ("voucher/", "puede_planillas"),
]

CRM_PERMISSION_MAP = [
    ("configuracion/", "puede_configuracion_crm"),
    ("plantillas/", "puede_campanias"),
    ("campanias/", "puede_campanias"),
    ("citas/", "puede_citas"),
]

CLINICA_PERMISSION_MAP = [
    ("pacientes/", "puede_pacientes"),
    ("citas/", "puede_clinica"),
    ("expediente/", "puede_expediente_clinico"),
    ("tratamientos/", "puede_tratamientos_clinicos"),
    ("profesionales/", "puede_configuracion_clinica"),
    ("servicios/", "puede_configuracion_clinica"),
]

TECNICENTRO_PERMISSION_MAP = [
    ("agenda/", "puede_recepcion_taller"),
    ("recepcion/", "puede_recepcion_taller"),
    ("diagnosticos/", "puede_diagnostico_taller"),
    ("ordenes/", "puede_operacion_taller"),
    ("configuracion/", "puede_configuracion_taller"),
]


def permiso_facturacion_desde_ruta(path_suffix):
    if not path_suffix:
        return None
    normalized = path_suffix if path_suffix.endswith("/") else f"{path_suffix}/"
    if normalized.startswith(tuple(str(i) for i in range(10))):
        return "puede_ver_facturas"
    for prefix, permiso in FACTURACION_PERMISSION_MAP:
        if normalized.startswith(prefix):
            return permiso
    return None


def permiso_facturacion_accion(path_suffix):
    if not path_suffix:
        return None

    normalized = path_suffix if path_suffix.endswith("/") else f"{path_suffix}/"
    parts = [part for part in normalized.split("/") if part]
    if not parts:
        return None

    first = parts[0]

    # La captura mensual permite consulta; cada POST comprueba su permiso en la vista.

    if first == "crear":
        return "puede_crear_facturas"
    if first == "clientes":
        if len(parts) > 1 and parts[1] == "crear":
            return "puede_crear_clientes"
        if len(parts) > 2 and parts[2] == "editar":
            return "puede_editar_clientes"
        return None
    if first == "productos":
        if len(parts) > 1 and parts[1] == "crear":
            return "puede_crear_productos"
        if len(parts) > 2 and parts[2] == "editar":
            return "puede_editar_productos"
        return None
    if first == "proveedores":
        if len(parts) > 1 and parts[1] == "crear":
            return "puede_crear_proveedores"
        if len(parts) > 2 and parts[2] == "editar":
            return "puede_editar_proveedores"
        return None
    if first == "inventario":
        if len(parts) > 2 and parts[1] == "farmaceutico" and parts[2] == "traslado-rapido":
            return "puede_transferir_inventario"
        if len(parts) > 1 and parts[1] == "ajuste":
            return "puede_ajustar_inventario"
        if len(parts) > 2 and parts[1] == "compras" and parts[2] == "crear":
            return "puede_crear_compras"
        if len(parts) > 3 and parts[1] == "compras" and parts[3] == "editar":
            return "puede_editar_compras"
        if len(parts) > 3 and parts[1] == "compras" and parts[3] == "aplicar":
            return "puede_aplicar_compras"
        if len(parts) > 3 and parts[1] == "compras" and parts[3] == "anular":
            return "puede_anular_compras"
        if len(parts) > 3 and parts[1] == "compras" and parts[3] == "pago":
            return "puede_registrar_pagos_proveedores"
        return None
    if first == "notas-credito":
        if len(parts) > 1 and parts[1] == "crear":
            return "puede_crear_notas_credito"
        if len(parts) > 2 and parts[2] == "editar":
            return "puede_editar_notas_credito"
        if len(parts) > 2 and parts[2] == "anular":
            return "puede_anular_notas_credito"
        if len(parts) > 1 and parts[1] == "factura":
            return "puede_crear_notas_credito"
        return None
    if first == "reportes" and len(parts) > 1 and parts[1] in {"excel", "facturas-pdf"}:
        return "puede_exportar_reportes"

    if first.isdigit():
        if len(parts) > 1 and parts[1] == "cambiar-fecha":
            return "puede_cambiar_fecha_factura"
        if len(parts) > 1 and parts[1] == "corregir-numero":
            return "puede_editar_facturas"
        if len(parts) > 1 and parts[1] == "editar":
            return "puede_editar_facturas"
        if len(parts) > 1 and parts[1] == "validar":
            return "puede_editar_facturas"
        if len(parts) > 1 and parts[1] == "duplicar":
            return "puede_crear_facturas"
        if len(parts) > 1 and parts[1] == "anular":
            return "puede_anular_facturas"
        if len(parts) > 1 and parts[1] == "eliminar":
            return "puede_eliminar_facturas"
        if len(parts) > 1 and parts[1] == "eliminar-borrador":
            return "puede_eliminar_borradores"
        if len(parts) > 1 and parts[1] == "pago":
            if len(parts) > 3 and parts[3] == "anular":
                return "puede_anular_facturas"
            return "puede_registrar_pagos_clientes"
        if len(parts) > 1 and parts[1] == "nota-credito":
            return "puede_crear_notas_credito"
        return None

    return None


def permiso_contabilidad_desde_ruta(path_suffix):
    if not path_suffix:
        return None
    normalized = path_suffix if path_suffix.endswith("/") else f"{path_suffix}/"
    if normalized.startswith(tuple(str(i) for i in range(10))):
        return "puede_contabilidad"
    for prefix, permiso in CONTABILIDAD_PERMISSION_MAP:
        if normalized.startswith(prefix):
            return permiso
    return None


def permiso_contabilidad_accion(path_suffix, method="GET", *, v3=False):
    if not path_suffix:
        return None

    normalized = path_suffix if path_suffix.endswith("/") else f"{path_suffix}/"
    parts = [part for part in normalized.split("/") if part]
    if not parts:
        return None

    first = parts[0]
    unsafe = method.upper() not in {"GET", "HEAD", "OPTIONS"}
    if not v3:
        if first == "asientos" and len(parts) > 1:
            if parts[1] == "crear" or (len(parts) > 2 and parts[2] in {"editar", "duplicar"}):
                return "puede_crear_asientos"
            if len(parts) > 2 and parts[2] in {"contabilizar", "reversar", "regresar-borrador"}:
                return "puede_contabilizar_asientos"
        if first == "periodos" and len(parts) > 2 and parts[2] in {"cerrar", "abrir"}:
            return "puede_contabilizar_asientos"
    if first == "configuracion":
        return "puede_configurar_contabilidad" if unsafe else None
    if first == "periodos":
        if len(parts) > 1 and parts[1] == "crear":
            return "puede_crear_periodos_contables"
        if len(parts) > 2 and parts[2] == "editar":
            return "puede_editar_periodos_contables"
        if len(parts) > 2 and parts[2] in {"checklist-cierre", "cerrar"}:
            return "puede_cerrar_periodos_contables"
        if len(parts) > 2 and parts[2] == "abrir":
            return "puede_reabrir_periodos_contables"
        return None
    if first == "clasificaciones-compras":
        return "puede_administrar_clasificaciones_contables" if unsafe or "crear" in parts or "editar" in parts else None
    if first == "bancos":
        if parts[1:3] == ["cuentas", "crear"]:
            return "puede_administrar_cuentas_financieras"
        if len(parts) > 1 and parts[1] == "importar":
            return "puede_importar_movimientos_bancarios"
        if len(parts) > 1 and parts[1] in {"clasificaciones", "reglas"}:
            return "puede_administrar_reglas_bancarias" if unsafe or "crear" in parts or "editar" in parts else None
        if len(parts) > 2 and parts[1] == "movimientos" and parts[2] == "aplicar-reglas":
            return "puede_clasificar_movimientos_bancarios"
        if len(parts) > 2 and parts[1] == "movimientos" and parts[2] == "clasificar-lote":
            return "puede_clasificar_movimientos_bancarios"
        if len(parts) > 3 and parts[1] == "movimientos" and parts[3] in {"clasificar", "editar"}:
            return "puede_clasificar_movimientos_bancarios"
        if len(parts) > 3 and parts[1] == "movimientos" and parts[3] in {"enlazar-factura", "enlazar-compra"}:
            return "puede_enlazar_movimientos_bancarios"
        if len(parts) > 3 and parts[1] == "movimientos" and parts[3] == "contabilizar":
            return "puede_contabilizar_asientos"
        if len(parts) > 3 and parts[1] == "conciliacion" and parts[3] in {"conciliar", "desconciliar"}:
            return "puede_conciliar_movimientos_bancarios"
        return None
    if first == "cuentas":
        if len(parts) > 1 and parts[1] == "crear":
            return "puede_crear_cuentas_contables"
        if len(parts) > 2 and parts[2] == "editar":
            return "puede_editar_cuentas_contables"
        if len(parts) > 1 and parts[1] in {"catalogo-base-honduras", "importar", "sustituir-rumi"}:
            return "puede_importar_catalogo_cuentas"
        return None
    if first == "asientos":
        if len(parts) > 1 and parts[1] == "crear":
            return "puede_crear_asientos"
        if len(parts) > 2 and parts[2] == "editar":
            return "puede_editar_asientos"
        if len(parts) > 2 and parts[2] == "duplicar":
            return "puede_duplicar_asientos"
        if len(parts) > 2 and parts[2] == "contabilizar":
            return "puede_contabilizar_asientos"
        if len(parts) > 2 and parts[2] == "reversar":
            return "puede_reversar_asientos"
        if len(parts) > 2 and parts[2] == "regresar-borrador":
            return "puede_regresar_asientos_borrador"
        if len(parts) > 2 and parts[2] in {"regularizar-moneda", "revisar-conversion"}:
            return "puede_regularizar_asientos"
        return None
    if first == "reportes" and parts[-1] in {"excel", "csv"}:
        return "puede_exportar_reportes_contables"
    return None


def permiso_rrhh_accion(path_suffix, method="GET"):
    parts = [part for part in (path_suffix or "").strip("/").split("/") if part]
    if not parts:
        return None
    unsafe = method.upper() not in {"GET", "HEAD", "OPTIONS"}
    if parts[0] == "configuracion":
        return "puede_configuracion_rrhh" if unsafe else None
    if parts[0] == "empleados":
        if len(parts) > 1 and parts[1] == "crear": return "puede_crear_empleados"
        if len(parts) > 2 and parts[2] == "editar": return "puede_editar_empleados"
        if len(parts) > 2 and parts[2] == "eliminar": return "puede_eliminar_empleados"
    if parts[0] == "planillas":
        if len(parts) > 1 and parts[1] == "crear": return "puede_crear_planillas"
        if len(parts) > 1 and parts[1] == "editores": return "puede_configurar_editores_planilla"
        if "eliminar" in parts: return "puede_eliminar_planillas"
        if "generar" in parts: return "puede_generar_planillas"
        if "cerrar" in parts: return "puede_cerrar_planillas"
        if "reabrir" in parts: return "puede_reabrir_planillas"
        if "pagar" in parts: return "puede_pagar_planillas"
        if "editar" in parts: return "puede_editar_planillas"
    if parts[0] == "movimientos" and unsafe: return "puede_gestionar_movimientos_planilla"
    if parts[0] == "vacaciones" and unsafe: return "puede_gestionar_vacaciones"
    if parts[0] == "voucher" and parts[-1] in {"whatsapp-api", "email"}: return "puede_enviar_vouchers_planilla"
    return None


def permiso_rrhh_desde_ruta(path_suffix):
    if not path_suffix:
        return None
    normalized = path_suffix if path_suffix.endswith("/") else f"{path_suffix}/"
    if normalized.startswith(tuple(str(i) for i in range(10))):
        return "puede_rrhh"
    for prefix, permiso in RRHH_PERMISSION_MAP:
        if normalized.startswith(prefix):
            return permiso
    return None


def permiso_crm_desde_ruta(path_suffix):
    if not path_suffix:
        return None
    normalized = path_suffix if path_suffix.endswith("/") else f"{path_suffix}/"
    if normalized.startswith(tuple(str(i) for i in range(10))):
        return "puede_crm"
    for prefix, permiso in CRM_PERMISSION_MAP:
        if normalized.startswith(prefix):
            return permiso
    return None


def permiso_crm_accion(path_suffix, method="GET", data=None):
    parts = [part for part in (path_suffix or "").strip("/").split("/") if part]
    if not parts:
        return None
    unsafe = method.upper() not in {"GET", "HEAD", "OPTIONS"}
    data = data or {}
    if parts[0] == "configuracion":
        if "conectar-whatsapp-meta" in parts: return "puede_conectar_whatsapp_crm"
        if "enviar-prueba-whatsapp" in parts: return "puede_probar_whatsapp_crm"
        return "puede_configuracion_crm" if unsafe else None
    if parts[0] == "plantillas" and (unsafe or "editar" in parts): return "puede_administrar_plantillas_crm"
    if parts[0] == "campanias":
        if "crear" in parts: return "puede_crear_campanias"
        if "preparar-envios" in parts: return "puede_preparar_campanias"
        if "enviar-prueba-masiva" in parts: return "puede_enviar_pruebas_campanias"
        if "enviar-whatsapp-api" in parts: return "puede_enviar_campanias"
    if parts[0] == "citas" and unsafe:
        return "puede_editar_citas" if data.get("cita_id") else "puede_crear_citas"
    return None


def permiso_clinica_desde_ruta(path_suffix, method="GET"):
    if not path_suffix:
        return None
    normalized = path_suffix if path_suffix.endswith("/") else f"{path_suffix}/"
    parts = [part for part in normalized.split("/") if part]
    if parts and parts[0] == "pacientes":
        if len(parts) > 2 and parts[1].isdigit():
            if parts[2:] in (["restriccion"], ["restriccion", "habilitar"]):
                from .clinical_permissions import PERMISOS_CONSULTA_RESTRICCION_CITAS

                if method in {"GET", "HEAD"}:
                    return PERMISOS_CONSULTA_RESTRICCION_CITAS
                return "puede_habilitar_citas_paciente" if parts[-1] == "habilitar" else "puede_restringir_citas_paciente"
        if any(part in {"historias", "preconsulta"} for part in parts):
            return "puede_expediente_clinico"
    if normalized.startswith(tuple(str(i) for i in range(10))):
        return "puede_clinica"
    for prefix, permiso in CLINICA_PERMISSION_MAP:
        if normalized.startswith(prefix):
            return permiso
    return None


def permiso_tecnicentro_desde_ruta(path_suffix):
    if not path_suffix:
        return None
    normalized = path_suffix if path_suffix.endswith("/") else f"{path_suffix}/"
    for prefix, permiso in TECNICENTRO_PERMISSION_MAP:
        if normalized.startswith(prefix):
            return permiso
    return "puede_tecnicentro"


def permiso_tecnicentro_accion(path_suffix, method="GET", data=None):
    parts = [part for part in (path_suffix or "").strip("/").split("/") if part]
    if not parts:
        return None
    unsafe = method.upper() not in {"GET", "HEAD", "OPTIONS"}
    data = data or {}
    if parts[0] == "recepcion":
        if "inspeccion" in parts: return "puede_registrar_inspecciones_taller"
        if unsafe: return "puede_crear_recepciones_taller"
    if parts[0] == "agenda":
        if "estado" in parts: return "puede_cambiar_estado_citas_taller"
        if unsafe: return "puede_crear_citas_taller"
    if parts[0] == "ordenes":
        if "estado" in parts: return "puede_cambiar_estado_ordenes_taller"
        if "asignar" in parts: return "puede_asignar_ordenes_taller"
    if parts[0] == "diagnosticos":
        if "evidencia" in parts: return "puede_agregar_evidencias_taller"
        if unsafe: return "puede_editar_diagnosticos_taller"
    if parts[0] == "configuracion" and unsafe:
        return "puede_crear_bahias_taller" if data.get("accion") == "crear_bahia" else "puede_editar_configuracion_taller"
    return None
