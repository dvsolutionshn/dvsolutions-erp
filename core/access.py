FACTURACION_PERMISSION_MAP = [
    ("configuracion/", "puede_facturas"),
    ("inventario/compras/", "puede_compras"),
    ("libro-compras/", "puede_compras"),
    ("inventario/entrada/", "puede_inventario"),
    ("inventario/entradas/", "puede_inventario"),
    ("inventario/ajuste/", "puede_inventario"),
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
    ("facturas/", "puede_facturas"),
    ("crear/", "puede_facturas"),
]

CONTABILIDAD_PERMISSION_MAP = [
    ("configuracion/", "puede_contabilidad"),
    ("periodos/", "puede_contabilidad"),
    ("clasificaciones-compras/", "puede_contabilidad"),
    ("bancos/", "puede_contabilidad"),
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


def permiso_facturacion_desde_ruta(path_suffix):
    if not path_suffix:
        return None
    normalized = path_suffix if path_suffix.endswith("/") else f"{path_suffix}/"
    if normalized.startswith(tuple(str(i) for i in range(10))):
        return "puede_facturas"
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
    if first == "reportes" and len(parts) > 1 and parts[1] == "excel":
        return "puede_exportar_reportes"

    if first.isdigit():
        if len(parts) > 1 and parts[1] == "editar":
            return "puede_editar_facturas"
        if len(parts) > 1 and parts[1] == "anular":
            return "puede_anular_facturas"
        if len(parts) > 1 and parts[1] == "eliminar":
            return "puede_eliminar_borradores"
        if len(parts) > 1 and parts[1] == "pago":
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


def permiso_contabilidad_accion(path_suffix):
    if not path_suffix:
        return None

    normalized = path_suffix if path_suffix.endswith("/") else f"{path_suffix}/"
    parts = [part for part in normalized.split("/") if part]
    if not parts:
        return None

    first = parts[0]
    if first == "periodos":
        if len(parts) > 2 and parts[2] in {"cerrar", "abrir"}:
            return "puede_contabilizar_asientos"
        return None
    if first == "bancos":
        if len(parts) > 2 and parts[1] == "movimientos" and parts[2] == "aplicar-reglas":
            return "puede_contabilidad"
        if len(parts) > 3 and parts[1] == "movimientos" and parts[3] in {"clasificar", "editar"}:
            return "puede_contabilidad"
        if len(parts) > 3 and parts[1] == "movimientos" and parts[3] in {"enlazar-factura", "enlazar-compra", "contabilizar"}:
            return "puede_contabilizar_asientos"
        if len(parts) > 3 and parts[1] == "conciliacion" and parts[3] in {"conciliar", "desconciliar"}:
            return "puede_contabilizar_asientos"
        return None
    if first == "cuentas":
        if len(parts) > 1 and parts[1] == "crear":
            return "puede_catalogo_cuentas"
        if len(parts) > 2 and parts[2] == "editar":
            return "puede_catalogo_cuentas"
        return None
    if first == "asientos":
        if len(parts) > 1 and parts[1] == "crear":
            return "puede_crear_asientos"
        if len(parts) > 2 and parts[2] == "contabilizar":
            return "puede_contabilizar_asientos"
        return None
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
