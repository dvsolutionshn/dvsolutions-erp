def erp_access(request):
    user = getattr(request, "user", None)
    empresa = getattr(user, "empresa", None) if user and user.is_authenticated else None
    config_avanzada = None
    if empresa:
        try:
            config_avanzada = empresa.configuracion_avanzada
        except Exception:
            config_avanzada = None

    facturacion_activa = bool(empresa and empresa.tiene_modulo_activo("facturacion"))
    contabilidad_activa = bool(empresa and empresa.tiene_modulo_activo("contabilidad"))
    rrhh_activa = bool(empresa and empresa.tiene_modulo_activo("rrhh"))
    crm_activa = bool(empresa and empresa.tiene_modulo_activo("crm_marketing"))
    citas_activa = bool(empresa and empresa.tiene_modulo_activo("agenda_citas"))
    base = {
        "modulo_facturacion": facturacion_activa and getattr(user, "tiene_alguna_permision_facturacion", False),
        "modulo_contabilidad": contabilidad_activa and getattr(user, "tiene_alguna_permision_contabilidad", False),
        "modulo_rrhh": rrhh_activa and getattr(user, "tiene_alguna_permision_rrhh", False),
        "modulo_crm": crm_activa and getattr(user, "tiene_alguna_permision_crm", False),
        "modulo_citas": citas_activa and getattr(user, "tiene_permiso_erp", lambda *_: False)("puede_citas"),
        "facturas": facturacion_activa and getattr(user, "tiene_permiso_erp", lambda *_: False)("puede_facturas"),
        "clientes": facturacion_activa and getattr(user, "tiene_permiso_erp", lambda *_: False)("puede_clientes"),
        "productos": facturacion_activa and getattr(user, "tiene_permiso_erp", lambda *_: False)("puede_productos"),
        "proveedores": facturacion_activa and getattr(user, "tiene_permiso_erp", lambda *_: False)("puede_proveedores"),
        "inventario": facturacion_activa and getattr(user, "tiene_permiso_erp", lambda *_: False)("puede_inventario"),
        "compras": facturacion_activa and getattr(user, "tiene_permiso_erp", lambda *_: False)("puede_compras"),
        "cai": facturacion_activa and getattr(user, "tiene_permiso_erp", lambda *_: False)("puede_cai"),
        "impuestos": facturacion_activa and getattr(user, "tiene_permiso_erp", lambda *_: False)("puede_impuestos"),
        "notas_credito": facturacion_activa and getattr(user, "tiene_permiso_erp", lambda *_: False)("puede_notas_credito"),
        "recibos": facturacion_activa and getattr(user, "tiene_permiso_erp", lambda *_: False)("puede_recibos"),
        "egresos": facturacion_activa and getattr(user, "tiene_permiso_erp", lambda *_: False)("puede_egresos"),
        "reportes": facturacion_activa and getattr(user, "tiene_permiso_erp", lambda *_: False)("puede_reportes"),
        "cxc": facturacion_activa and getattr(user, "tiene_permiso_erp", lambda *_: False)("puede_cxc"),
        "cxp": facturacion_activa and getattr(user, "tiene_permiso_erp", lambda *_: False)("puede_cxp"),
        "contabilidad": contabilidad_activa and getattr(user, "tiene_permiso_erp", lambda *_: False)("puede_contabilidad"),
        "catalogo_cuentas": contabilidad_activa and getattr(user, "tiene_permiso_erp", lambda *_: False)("puede_catalogo_cuentas"),
        "asientos_contables": contabilidad_activa and getattr(user, "tiene_permiso_erp", lambda *_: False)("puede_crear_asientos"),
        "contabilizar_asientos": contabilidad_activa and getattr(user, "tiene_permiso_erp", lambda *_: False)("puede_contabilizar_asientos"),
        "reportes_contables": contabilidad_activa and getattr(user, "tiene_permiso_erp", lambda *_: False)("puede_reportes_contables"),
        "rrhh": rrhh_activa and getattr(user, "tiene_permiso_erp", lambda *_: False)("puede_rrhh"),
        "empleados": rrhh_activa and getattr(user, "tiene_permiso_erp", lambda *_: False)("puede_empleados"),
        "planillas": rrhh_activa and getattr(user, "tiene_permiso_erp", lambda *_: False)("puede_planillas"),
        "vacaciones": rrhh_activa and getattr(user, "tiene_permiso_erp", lambda *_: False)("puede_vacaciones"),
        "configuracion_rrhh": rrhh_activa and getattr(user, "tiene_permiso_erp", lambda *_: False)("puede_configuracion_rrhh"),
        "crm": crm_activa and getattr(user, "tiene_permiso_erp", lambda *_: False)("puede_crm"),
        "campanias": crm_activa and getattr(user, "tiene_permiso_erp", lambda *_: False)("puede_campanias"),
        "citas": citas_activa and getattr(user, "tiene_permiso_erp", lambda *_: False)("puede_citas"),
        "configuracion_crm": crm_activa and getattr(user, "tiene_permiso_erp", lambda *_: False)("puede_configuracion_crm"),
        "usa_cierre_caja": bool(config_avanzada and config_avanzada.usa_cierre_caja),
        "usa_pagos_mixtos": bool(config_avanzada and config_avanzada.usa_pagos_mixtos),
        "usa_reporte_bancos": bool(config_avanzada and config_avanzada.usa_reporte_bancos),
        "usa_inventario_farmaceutico": bool(config_avanzada and config_avanzada.usa_inventario_farmaceutico),
        "usa_bodegas_internas": bool(config_avanzada and config_avanzada.usa_bodegas_internas),
        "ventas_solo_desde_vitrina": bool(config_avanzada and config_avanzada.ventas_solo_desde_vitrina),
    }
    return {"erp_access": base}
