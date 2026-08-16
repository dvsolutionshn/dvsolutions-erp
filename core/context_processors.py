from django.conf import settings

from core.models import Empresa
from core.access import modo_clinico_simple_activo
from core.onix_access import onix_disponible_para_empresa


def _empresa_actual(request, user):
    if not user or not user.is_authenticated:
        return None

    resolver_match = getattr(request, "resolver_match", None)
    kwargs = getattr(resolver_match, "kwargs", {}) if resolver_match else {}
    slug = kwargs.get("empresa_slug") or kwargs.get("slug")
    if slug:
        empresa = Empresa.objects.filter(slug=slug, activa=True).first()
        if empresa and (user.is_superuser or user.puede_acceder_empresa(empresa)):
            return empresa

    return getattr(user, "empresa", None)


def erp_access(request):
    user = getattr(request, "user", None)
    empresa = _empresa_actual(request, user)
    config_avanzada = None
    if empresa:
        try:
            config_avanzada = empresa.configuracion_avanzada
        except Exception:
            config_avanzada = None

    facturacion_activa = bool(empresa and empresa.tiene_modulo_activo("facturacion"))
    contabilidad_activa = bool(empresa and empresa.tiene_modulo_activo("contabilidad"))
    pos_activa = bool(empresa and empresa.tiene_modulo_activo("punto_venta"))
    rrhh_activa = bool(empresa and empresa.tiene_modulo_activo("rrhh"))
    crm_activa = bool(empresa and empresa.tiene_modulo_activo("crm_marketing"))
    citas_activa = bool(empresa and empresa.tiene_modulo_activo("agenda_citas"))
    clinica_activa = bool(empresa and empresa.tiene_modulo_activo("clinica_medica"))
    tecnicentro_activo = bool(empresa and empresa.tiene_modulo_activo("tecnicentro"))
    cotizaciones_activa = bool(empresa and empresa.tiene_modulo_activo("cotizaciones"))
    def permiso(nombre):
        return getattr(user, "tiene_permiso_erp", lambda *_: False)(nombre, empresa)

    def algun(modulo):
        metodo = getattr(user, f"tiene_alguna_permision_{modulo}_empresa", None)
        if metodo:
            return metodo(empresa)
        return False

    base = {
        "modo_clinico_simple": modo_clinico_simple_activo(user, empresa),
        "modulo_facturacion": facturacion_activa and algun("facturacion"),
        "modulo_contabilidad": contabilidad_activa and algun("contabilidad"),
        "modulo_pos": facturacion_activa and pos_activa and permiso("puede_punto_venta"),
        "modulo_rrhh": rrhh_activa and algun("rrhh"),
        "modulo_crm": crm_activa and algun("crm"),
        "modulo_citas": citas_activa and permiso("puede_citas"),
        "modulo_clinica": clinica_activa and algun("clinica"),
        "modulo_tecnicentro": tecnicentro_activo and algun("tecnicentro"),
        "facturas": facturacion_activa and permiso("puede_ver_facturas"),
        "cotizaciones": facturacion_activa and cotizaciones_activa and permiso("puede_facturas"),
        "configuracion_facturacion": facturacion_activa and permiso("puede_configuracion_facturacion"),
        "cierres_caja": facturacion_activa and permiso("puede_cierres_caja"),
        "historial_cierres_caja": bool(
            empresa
            and (
                empresa.slug != "hospital_mia"
                or user.is_superuser
                or user.es_administrador_empresa
            )
        ),
        "clientes": facturacion_activa and permiso("puede_clientes"),
        "productos": facturacion_activa and permiso("puede_productos"),
        "proveedores": facturacion_activa and permiso("puede_proveedores"),
        "inventario": facturacion_activa and permiso("puede_inventario"),
        "compras": facturacion_activa and permiso("puede_compras"),
        "cai": facturacion_activa and permiso("puede_cai"),
        "impuestos": facturacion_activa and permiso("puede_impuestos"),
        "notas_credito": facturacion_activa and permiso("puede_notas_credito"),
        "recibos": facturacion_activa and permiso("puede_recibos"),
        "egresos": facturacion_activa and permiso("puede_egresos"),
        "reportes": facturacion_activa and permiso("puede_reportes"),
        "cxc": facturacion_activa and permiso("puede_cxc"),
        "cxp": facturacion_activa and permiso("puede_cxp"),
        "crear_facturas": permiso("puede_crear_facturas"),
        "editar_facturas": permiso("puede_editar_facturas"),
        "anular_facturas": permiso("puede_anular_facturas"),
        "eliminar_borradores": permiso("puede_eliminar_borradores"),
        "eliminar_facturas": permiso("puede_eliminar_facturas"),
        "registrar_pagos_clientes": permiso("puede_registrar_pagos_clientes"),
        "crear_clientes": permiso("puede_crear_clientes"),
        "editar_clientes": permiso("puede_editar_clientes"),
        "crear_productos": permiso("puede_crear_productos"),
        "editar_productos": permiso("puede_editar_productos"),
        "crear_proveedores": permiso("puede_crear_proveedores"),
        "editar_proveedores": permiso("puede_editar_proveedores"),
        "ajustar_inventario": permiso("puede_ajustar_inventario"),
        "crear_compras": permiso("puede_crear_compras"),
        "editar_compras": permiso("puede_editar_compras"),
        "aplicar_compras": permiso("puede_aplicar_compras"),
        "anular_compras": permiso("puede_anular_compras"),
        "registrar_pagos_proveedores": permiso("puede_registrar_pagos_proveedores"),
        "crear_notas_credito": permiso("puede_crear_notas_credito"),
        "editar_notas_credito": permiso("puede_editar_notas_credito"),
        "anular_notas_credito": permiso("puede_anular_notas_credito"),
        "exportar_reportes": permiso("puede_exportar_reportes"),
        "contabilidad": contabilidad_activa and permiso("puede_contabilidad"),
        "catalogo_cuentas": contabilidad_activa and permiso("puede_catalogo_cuentas"),
        "asientos_contables": contabilidad_activa and permiso("puede_crear_asientos"),
        "contabilizar_asientos": contabilidad_activa and permiso("puede_contabilizar_asientos"),
        "reportes_contables": contabilidad_activa and permiso("puede_reportes_contables"),
        "rrhh": rrhh_activa and permiso("puede_rrhh"),
        "empleados": rrhh_activa and permiso("puede_empleados"),
        "planillas": rrhh_activa and permiso("puede_planillas"),
        "vacaciones": rrhh_activa and permiso("puede_vacaciones"),
        "configuracion_rrhh": rrhh_activa and permiso("puede_configuracion_rrhh"),
        "crm": crm_activa and permiso("puede_crm"),
        "campanias": crm_activa and permiso("puede_campanias"),
        "citas": citas_activa and permiso("puede_citas"),
        "configuracion_crm": crm_activa and permiso("puede_configuracion_crm"),
        "clinica": clinica_activa and permiso("puede_clinica"),
        "pacientes": clinica_activa and permiso("puede_pacientes"),
        "expediente_clinico": clinica_activa and permiso("puede_expediente_clinico"),
        "tratamientos_clinicos": clinica_activa and permiso("puede_tratamientos_clinicos"),
        "configuracion_clinica": clinica_activa and permiso("puede_configuracion_clinica"),
        "tecnicentro": tecnicentro_activo and permiso("puede_tecnicentro"),
        "recepcion_taller": tecnicentro_activo and permiso("puede_recepcion_taller"),
        "diagnostico_taller": tecnicentro_activo and permiso("puede_diagnostico_taller"),
        "operacion_taller": tecnicentro_activo and permiso("puede_operacion_taller"),
        "configuracion_taller": tecnicentro_activo and permiso("puede_configuracion_taller"),
        "usa_cierre_caja": bool(pos_activa or (config_avanzada and config_avanzada.usa_cierre_caja)),
        "usa_pagos_mixtos": bool(config_avanzada and config_avanzada.usa_pagos_mixtos),
        "usa_reporte_bancos": bool(config_avanzada and config_avanzada.usa_reporte_bancos),
        "usa_inventario_farmaceutico": bool(clinica_activa and config_avanzada and config_avanzada.usa_inventario_farmaceutico),
        "usa_bodegas_internas": bool(config_avanzada and config_avanzada.usa_bodegas_internas),
        "ventas_solo_desde_vitrina": bool(config_avanzada and config_avanzada.ventas_solo_desde_vitrina),
        "administrar_usuarios_clinicos": bool(
            empresa
            and empresa.slug in {"hospital_mia", "serviciosmedicos", "medical_spa", "luque_aestetic"}
            and user
            and user.is_authenticated
            and user.puede_acceder_empresa(empresa)
            and (user.is_superuser or getattr(user, "puede_administrar_usuarios_clinicos", False))
        ),
    }
    if base["modo_clinico_simple"]:
        base.update({
            "modulo_contabilidad": False,
            "modulo_rrhh": False,
            "modulo_crm": False,
            "modulo_tecnicentro": False,
        })
    return {
        "erp_access": base,
        "mostrar_asistente_erp": bool(
            user
            and user.is_authenticated
            and onix_disponible_para_empresa(empresa)
        ),
        "onix_ia_activa": bool(
            getattr(settings, "ONIX_ENABLED", False)
            and getattr(settings, "OPENAI_API_KEY", "")
        ),
    }
