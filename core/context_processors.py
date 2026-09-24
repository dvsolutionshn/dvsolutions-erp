from django.conf import settings
from django.db.models import Sum
from django.utils import timezone

from core.models import ConfiguracionOnix, ConsumoOnix, Empresa
from core.access import interfaz_clinica_activa, manuales_pdf_habilitados, modo_clinico_simple_activo
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
    onix_disponible = bool(empresa and onix_disponible_para_empresa(empresa))
    config_avanzada = None
    if empresa:
        try:
            config_avanzada = empresa.configuracion_avanzada
        except Exception:
            config_avanzada = None
    configuracion_onix = (
        ConfiguracionOnix.objects.filter(empresa=empresa).only(
            "herramientas_accion_activas", "limite_tokens_mensual"
        ).first()
        if onix_disponible
        else None
    )

    facturacion_activa = bool(empresa and empresa.tiene_modulo_activo("facturacion"))
    contabilidad_activa = bool(empresa and empresa.tiene_modulo_activo("contabilidad"))
    pos_activa = bool(empresa and empresa.tiene_modulo_activo("punto_venta"))
    rrhh_activa = bool(empresa and empresa.tiene_modulo_activo("rrhh"))
    crm_activa = bool(empresa and empresa.tiene_modulo_activo("crm_marketing"))
    citas_activa = bool(empresa and empresa.tiene_modulo_activo("agenda_citas"))
    clinica_activa = bool(empresa and empresa.tiene_modulo_activo("clinica_medica"))
    tecnicentro_activo = bool(empresa and empresa.tiene_modulo_activo("tecnicentro"))
    cotizaciones_activa = bool(empresa and empresa.tiene_modulo_activo("cotizaciones"))
    interfaz_clinica = interfaz_clinica_activa(empresa)
    modulo_manuales_pdf = manuales_pdf_habilitados(empresa)
    modulos_adicionales_clinica = set()
    if interfaz_clinica and config_avanzada:
        modulos_adicionales_clinica = set(
            config_avanzada.modulos_adicionales_visibles_clinica.values_list("codigo", flat=True)
        )
    email_usuario = str(getattr(user, "email", "") or "").strip().casefold()
    username_usuario = str(getattr(user, "username", "") or "").strip().casefold()
    puede_controlar_enlaces_pacientes = bool(
        empresa
        and empresa.slug == "hospital_mia"
        and user
        and user.is_authenticated
        and (
            email_usuario == "dannyvarela25@gmail.com"
            or username_usuario in {
                "dannyvarela25@gmail.com",
                "dannyvarela25",
                "danielvarela",
                "daniel_varela",
            }
        )
    )
    def permiso(nombre):
        return getattr(user, "tiene_permiso_erp", lambda *_: False)(nombre, empresa)

    def algun(modulo):
        metodo = getattr(user, f"tiene_alguna_permision_{modulo}_empresa", None)
        if metodo:
            return metodo(empresa)
        return False

    rol_actual = user.rol_para_empresa(empresa) if user and user.is_authenticated and empresa else None
    permisos_clinicos_granulares = bool(
        rol_actual and rol_actual.activo and rol_actual.usa_permisos_clinicos_granulares
    )

    base = {
        "interfaz_clinica": interfaz_clinica,
        "modo_clinico_simple": modo_clinico_simple_activo(user, empresa),
        "modulo_facturacion": facturacion_activa and algun("facturacion"),
        "modulo_contabilidad": contabilidad_activa and algun("contabilidad"),
        "modulo_pos": facturacion_activa and pos_activa and permiso("puede_punto_venta"),
        "modulo_rrhh": rrhh_activa and algun("rrhh"),
        "modulo_crm": crm_activa and algun("crm"),
        "modulo_citas": citas_activa and permiso("puede_ver_calendario" if permisos_clinicos_granulares else "puede_citas"),
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
        "cambiar_fecha_factura": permiso("puede_cambiar_fecha_factura"),
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
        "citas": citas_activa and permiso("puede_ver_calendario" if permisos_clinicos_granulares else "puede_citas"),
        "configuracion_crm": crm_activa and permiso("puede_configuracion_crm"),
        "clinica": clinica_activa and (algun("clinica") if permisos_clinicos_granulares else permiso("puede_clinica")),
        "pacientes": clinica_activa and permiso("puede_ver_pacientes" if permisos_clinicos_granulares else "puede_pacientes"),
        "expediente_clinico": clinica_activa and permiso("puede_ver_historia_clinica" if permisos_clinicos_granulares else "puede_expediente_clinico"),
        "tratamientos_clinicos": clinica_activa and permiso("puede_ver_planes_tratamiento" if permisos_clinicos_granulares else "puede_tratamientos_clinicos"),
        "configuracion_clinica": clinica_activa and permiso("puede_configuracion_clinica"),
        "ver_calendario": permiso("puede_ver_calendario") if permisos_clinicos_granulares else permiso("puede_citas"),
        "crear_citas": permiso("puede_crear_citas") if permisos_clinicos_granulares else permiso("puede_citas"),
        "editar_citas": permiso("puede_editar_citas") if permisos_clinicos_granulares else permiso("puede_citas"),
        "eliminar_citas": permiso("puede_eliminar_citas") if permisos_clinicos_granulares else permiso("puede_citas"),
        "ver_datos_generales_paciente": permiso("puede_ver_datos_generales_paciente") if permisos_clinicos_granulares else permiso("puede_pacientes"),
        "crear_pacientes": permiso("puede_crear_pacientes") if permisos_clinicos_granulares else permiso("puede_pacientes"),
        "editar_pacientes": permiso("puede_editar_pacientes") if permisos_clinicos_granulares else permiso("puede_pacientes"),
        "eliminar_pacientes": permiso("puede_eliminar_pacientes") if permisos_clinicos_granulares else permiso("puede_pacientes"),
        "crear_recordatorios_paciente": permiso("puede_crear_recordatorios_paciente") if permisos_clinicos_granulares else permiso("puede_pacientes"),
        "ver_historia_clinica": permiso("puede_ver_historia_clinica") if permisos_clinicos_granulares else permiso("puede_expediente_clinico"),
        "crear_historia_clinica": permiso("puede_crear_historia_clinica") if permisos_clinicos_granulares else permiso("puede_expediente_clinico"),
        "editar_historia_clinica": permiso("puede_editar_historia_clinica") if permisos_clinicos_granulares else permiso("puede_expediente_clinico"),
        "ver_anexos_clinicos": permiso("puede_ver_anexos_clinicos") if permisos_clinicos_granulares else permiso("puede_expediente_clinico"),
        "subir_anexos_clinicos": permiso("puede_subir_anexos_clinicos") if permisos_clinicos_granulares else permiso("puede_expediente_clinico"),
        "ver_recetas": permiso("puede_ver_recetas") if permisos_clinicos_granulares else permiso("puede_expediente_clinico"),
        "crear_recetas": permiso("puede_crear_recetas") if permisos_clinicos_granulares else permiso("puede_expediente_clinico"),
        "ver_planes_tratamiento": permiso("puede_ver_planes_tratamiento") if permisos_clinicos_granulares else permiso("puede_tratamientos_clinicos"),
        "editar_planes_tratamiento": permiso("puede_editar_planes_tratamiento") if permisos_clinicos_granulares else permiso("puede_tratamientos_clinicos"),
        "escribir_enfermeria": permiso("puede_escribir_enfermeria") if permisos_clinicos_granulares else permiso("puede_expediente_clinico"),
        "ver_terapias": permiso("puede_ver_terapias") if permisos_clinicos_granulares else permiso("puede_expediente_clinico"),
        "escribir_terapias": permiso("puede_escribir_terapias") if permisos_clinicos_granulares else permiso("puede_expediente_clinico"),
        "ver_camara_hiperbarica": permiso("puede_ver_camara_hiperbarica") if permisos_clinicos_granulares else permiso("puede_expediente_clinico"),
        "ver_postquirurgicas": permiso("puede_ver_postquirurgicas") if permisos_clinicos_granulares else permiso("puede_expediente_clinico"),
        "ver_manuales_pdf": modulo_manuales_pdf and (permiso("puede_ver_manuales_pdf") if permisos_clinicos_granulares else permiso("puede_expediente_clinico")),
        "enviar_manuales_pdf": modulo_manuales_pdf and (permiso("puede_enviar_manuales_pdf") if permisos_clinicos_granulares else permiso("puede_expediente_clinico")),
        "administrar_manuales_pdf": modulo_manuales_pdf and (permiso("puede_administrar_manuales_pdf") if permisos_clinicos_granulares else permiso("puede_configuracion_clinica")),
        "transferir_inventario": permiso("puede_transferir_inventario") if permisos_clinicos_granulares else permiso("puede_inventario"),
        "tecnicentro": tecnicentro_activo and permiso("puede_tecnicentro"),
        "recepcion_taller": tecnicentro_activo and permiso("puede_recepcion_taller"),
        "diagnostico_taller": tecnicentro_activo and permiso("puede_diagnostico_taller"),
        "operacion_taller": tecnicentro_activo and permiso("puede_operacion_taller"),
        "configuracion_taller": tecnicentro_activo and permiso("puede_configuracion_taller"),
        "usa_cierre_caja": bool(pos_activa or (config_avanzada and config_avanzada.usa_cierre_caja)),
        "usa_pagos_mixtos": bool(config_avanzada and config_avanzada.usa_pagos_mixtos),
        "usa_reporte_bancos": bool(config_avanzada and config_avanzada.usa_reporte_bancos),
        "usa_inventario_farmaceutico": bool(clinica_activa and config_avanzada and config_avanzada.usa_inventario_farmaceutico),
        "usa_control_lotes_fefo": bool(
            clinica_activa and config_avanzada and config_avanzada.usa_control_lotes_fefo
        ),
        "usa_bodegas_internas": bool(config_avanzada and config_avanzada.usa_bodegas_internas),
        "ventas_solo_desde_vitrina": bool(config_avanzada and config_avanzada.ventas_solo_desde_vitrina),
        "administrar_usuarios_clinicos": bool(
            empresa
            and interfaz_clinica
            and user
            and user.is_authenticated
            and user.puede_acceder_empresa(empresa)
            and (user.is_superuser or getattr(user, "puede_administrar_usuarios_clinicos", False))
        ),
    }
    if interfaz_clinica:
        base.update({
            "modulo_contabilidad": base["modulo_contabilidad"] and "contabilidad" in modulos_adicionales_clinica,
            "modulo_rrhh": base["modulo_rrhh"] and "rrhh" in modulos_adicionales_clinica,
            "modulo_crm": base["modulo_crm"] and "crm_marketing" in modulos_adicionales_clinica,
            "modulo_tecnicentro": base["modulo_tecnicentro"] and "tecnicentro" in modulos_adicionales_clinica,
        })
    base["configuracion_producto_clinico"] = bool(
        interfaz_clinica
        and (
            base["configuracion_clinica"]
            or base["configuracion_facturacion"]
            or base["configuracion_crm"]
            or base["administrar_usuarios_clinicos"]
            or base["administrar_manuales_pdf"]
            or base["modulo_crm"]
        )
    )
    consumo_onix_mes = 0
    limite_onix_efectivo = 0
    if onix_disponible:
        ahora = timezone.now()
        inicio_mes = ahora.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        consumo_onix_mes = int(
            ConsumoOnix.objects.filter(empresa=empresa, fecha__gte=inicio_mes).aggregate(
                total=Sum("tokens_total")
            )["total"]
            or 0
        )
        limites_onix = []
        if configuracion_onix and configuracion_onix.limite_tokens_mensual:
            limites_onix.append(int(configuracion_onix.limite_tokens_mensual))
        if getattr(settings, "ONIX_TRIAL_MODE", False):
            limite_prueba = int(getattr(settings, "ONIX_TRIAL_MONTHLY_TOKEN_LIMIT", 0))
            if limite_prueba:
                limites_onix.append(limite_prueba)
        limite_onix_efectivo = min(limites_onix) if limites_onix else 0
    porcentaje_onix = (
        min(round((consumo_onix_mes / limite_onix_efectivo) * 100, 1), 100)
        if limite_onix_efectivo
        else 0
    )
    return {
        "erp_access": base,
        "puede_controlar_enlaces_pacientes": puede_controlar_enlaces_pacientes,
        "mostrar_asistente_erp": bool(
            user
            and user.is_authenticated
            and onix_disponible
        ),
        "onix_ia_activa": bool(
            getattr(settings, "ONIX_ENABLED", False)
            and getattr(settings, "OPENAI_API_KEY", "")
        ),
        "onix_modo_prueba": bool(getattr(settings, "ONIX_TRIAL_MODE", False)),
        "onix_limite_prueba": int(getattr(settings, "ONIX_TRIAL_MONTHLY_TOKEN_LIMIT", 0)),
        "onix_consumo_mes": consumo_onix_mes,
        "onix_limite_efectivo": limite_onix_efectivo,
        "onix_consumo_porcentaje": porcentaje_onix,
        "onix_acciones_activas": bool(
            empresa
            and (
                configuracion_onix.herramientas_accion_activas
                if configuracion_onix
                else empresa.slug == "demo_1"
            )
        ),
    }
