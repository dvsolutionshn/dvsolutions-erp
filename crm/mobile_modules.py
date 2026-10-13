"""Catálogo de presentación móvil sobre permisos y módulos existentes.

No autoriza operaciones ni crea una API paralela de negocio. Las vistas de cada
módulo siguen comprobando sus permisos; los destinos pendientes no son enlaces.
"""

from types import SimpleNamespace

from django.urls import reverse

from core.access import interfaz_clinica_activa
from core.clinical_permissions import EMPRESAS_CLINICAS_CON_ROLES, rol_clinico_granular
from core.context_processors import erp_access


# Alcance de navegación elegido para la doctora. No revoca autorizaciones ni
# elimina las adaptaciones existentes: sus rutas mantienen todos sus controles.
MODULOS_OCULTOS_APP_CLINICA = frozenset({
    "egresos", "cxc", "cxp", "notas_credito", "cierres", "reportes",
    "contabilidad", "rrhh", "tecnicentro", "crm", "cotizaciones",
    # Accesos ya presentes en Inicio: ocultar sólo su tarjeta en Más funciones.
    "pacientes", "agenda", "facturacion", "inventario",
})


def inicio_app_disponible(request, empresa):
    """Disponibilidad del inicio entrenado, sin cambiar su autorización."""
    return not rol_clinico_granular(request.user, empresa) or request.user.tiene_permiso_erp("puede_ver_calendario", empresa)


def url_modulos_app(request, empresa):
    if inicio_app_disponible(request, empresa):
        return f"{reverse('agenda_mobile', args=[empresa.slug])}#mas-funciones-app"
    return reverse("agenda_mobile_modules_screen", args=[empresa.slug])


def construir_contexto_modulos_app(request, empresa):
    contexto = {
        "puede_ver_modulos_app": False,
        "modulos_app": [],
        "grupos_modulos_app": [],
        "rol_app": "",
    }
    usuario = request.user
    if not (
        empresa.slug in EMPRESAS_CLINICAS_CON_ROLES
        and interfaz_clinica_activa(empresa)
        and usuario.is_authenticated
        and usuario.puede_acceder_empresa(empresa)
    ):
        return contexto

    # Reutilizar el mismo resolver de menús web, fijando la empresa autorizada
    # de la URL incluso cuando se use este helper sin un resolver_match.
    acceso = erp_access(SimpleNamespace(
        user=usuario,
        resolver_match=SimpleNamespace(kwargs={"empresa_slug": empresa.slug}),
    ))["erp_access"]
    inicio = reverse("agenda_mobile", args=[empresa.slug])
    modulos = []

    def agregar(key, nombre, descripcion, categoria, permitido, estado="pendiente", destino="", motivo_pendiente=""):
        if permitido and key not in MODULOS_OCULTOS_APP_CLINICA:
            modulos.append({
                "key": key, "nombre": nombre, "descripcion": descripcion,
                "categoria": categoria, "estado": estado,
                "url": destino if estado != "pendiente" else "",
                "panel": destino[len(inicio) + 1:] if estado != "pendiente" and destino.startswith(f"{inicio}#") else "",
                **({"motivo_pendiente": motivo_pendiente} if estado == "pendiente" and motivo_pendiente else {}),
            })

    pacientes_destino = "clinica_pacientes" if acceso["pacientes"] else "clinica_crear_paciente"
    agregar("pacientes", "Pacientes", "Consultar expediente, crear y editar datos según tus permisos.",
            "Atención clínica", acceso["modulo_clinica"] and (acceso["pacientes"] or acceso["crear_pacientes"]),
            "parcial", f"{reverse(pacientes_destino, args=[empresa.slug])}?app=1")
    agregar("agenda", "Agenda y citas", "Conserva la agenda móvil actual y la programación en Hospital Mía.",
            "Atención clínica", acceso["modulo_citas"], "disponible", f"{inicio}#agenda-app")
    gastos_destino = "clinica_gastos_adicionales" if acceso["ver_gastos_adicionales"] else "clinica_gasto_adicional_crear"
    agregar("gastos_adicionales", "Gastos Adicionales", "Productos, cirugía, PDF, envío y conversión según tus permisos.",
            "Atención clínica", acceso["modulo_gastos_adicionales"], "disponible",
            f"{reverse(gastos_destino, args=[empresa.slug])}?app=1")
    recetas_autorizadas = acceso["ver_recetas"] or acceso["crear_recetas"]
    recetas_destino = "recetas" if acceso["ver_recetas"] else "crear_receta"
    agregar("recetas", "Recetas", "Elige un paciente para consultar o crear recetas, abrir PDF y enviar según tus permisos.",
            "Atención clínica", acceso["modulo_clinica"] and recetas_autorizadas,
            "disponible" if acceso["pacientes"] else "pendiente",
            f"{reverse('clinica_pacientes', args=[empresa.slug])}?app=1&destino={recetas_destino}")
    plantillas_autorizadas = acceso["editar_historia_clinica"] or (
        not rol_clinico_granular(usuario, empresa) and acceso["configuracion_clinica"]
    )
    agregar("plantillas_recetas", "Plantillas de recetas", "Crear y editar medicamentos e indicaciones reutilizables según tus permisos.",
            "Atención clínica", acceso["modulo_clinica"] and plantillas_autorizadas,
            "disponible", f"{reverse('clinica_plantillas_recetas', args=[empresa.slug])}?app=1")
    manuales_autorizados = acceso["ver_manuales_pdf"] or acceso["enviar_manuales_pdf"] or acceso["administrar_manuales_pdf"]
    manuales_destino = "clinica_manuales_pdf" if acceso["ver_manuales_pdf"] or acceso["administrar_manuales_pdf"] else "clinica_manuales_pdf_app_enviar"
    agregar("manuales", "Manuales PDF", "Consultar documentos, compartir con pacientes y administrar según tus permisos.",
            "Atención clínica", acceso["modulo_clinica"] and manuales_autorizados,
            "disponible", f"{reverse(manuales_destino, args=[empresa.slug])}?app=1")
    anexos_autorizados = acceso["ver_anexos_clinicos"] or acceso["subir_anexos_clinicos"]
    anexos_destino = "anexos" if acceso["ver_anexos_clinicos"] else "crear_anexo"
    agregar("anexos", "Exámenes y anexos", "Archivos, evolución, consentimientos e incapacidades según tus permisos.",
            "Atención clínica", acceso["modulo_clinica"] and anexos_autorizados,
            "disponible" if acceso["pacientes"] else "pendiente",
            f"{reverse('clinica_pacientes', args=[empresa.slug])}?app=1&destino={anexos_destino}")
    agregar("expediente", "Expediente clínico", "Consultar historias y formularios por área; editar notas con la autorización actual.",
            "Atención clínica", acceso["modulo_clinica"] and acceso["expediente_clinico"],
            "disponible" if acceso["pacientes"] else "pendiente",
            f"{reverse('clinica_pacientes', args=[empresa.slug])}?app=1&destino=historias",
            "Seleccionar un expediente requiere el permiso actual de consultar pacientes.")
    historias_creables = acceso["crear_historia_clinica"] or acceso["escribir_enfermeria"] or acceso["escribir_terapias"]
    agregar("registrar_historia", "Registrar nota clínica", "Historia capilar, cirugía plástica, medicina estética, enfermería y terapias según tus permisos.",
            "Atención clínica", acceso["modulo_clinica"] and historias_creables,
            "disponible" if acceso["pacientes"] else "pendiente",
            f"{reverse('clinica_pacientes', args=[empresa.slug])}?app=1&destino=crear_historia&seleccionar=area",
            "Seleccionar un paciente requiere el permiso actual de consultar pacientes.")
    evaluaciones_autorizadas = acceso["ver_historia_clinica"] and acceso["editar_historia_clinica"]
    agregar("evaluaciones", "Evaluación y tricopigmentación", "Registrar evaluación integral, funciones orgánicas, examen físico y controles de tricopigmentación.",
            "Atención clínica", acceso["modulo_clinica"] and evaluaciones_autorizadas,
            "disponible" if acceso["pacientes"] else "pendiente",
            f"{reverse('clinica_pacientes', args=[empresa.slug])}?app=1&destino=evaluacion_clinica&seleccionar=formulario",
            "Seleccionar un paciente requiere el permiso actual de consultar pacientes.")
    from clinica.mobile_antecedents import antecedentes_disponibles_app

    agregar("antecedentes", "Antecedentes adicionales", "Actualizar texto y consultar sus cambios registrados, sin borrar el historial.",
            "Atención clínica", acceso["modulo_clinica"] and antecedentes_disponibles_app(request, empresa),
            "disponible" if acceso["pacientes"] else "pendiente",
            f"{reverse('clinica_pacientes', args=[empresa.slug])}?app=1&destino=antecedentes")
    from clinica.mobile_treatments import tratamientos_disponibles_app

    tratamientos = tratamientos_disponibles_app(request, empresa)
    agregar("tratamientos", "Tratamientos", "Consultar tratamientos asignados y registrar uno nuevo según tus permisos.",
            "Atención clínica", tratamientos["ver"] or tratamientos["crear"], "disponible",
            f"{reverse('clinica_tratamientos' if tratamientos['ver'] else 'clinica_crear_tratamiento', args=[empresa.slug])}?app=1")
    from clinica.mobile_agenda_catalog import catalogo_agenda_disponible_app, url_catalogo_agenda_app

    catalogo_agenda = catalogo_agenda_disponible_app(request, empresa)
    agregar("catalogo_tratamientos", "Catálogo de tratamientos de agenda",
            "Opciones de esta empresa para citas; administración sólo con la autorización existente.",
            "Administración", catalogo_agenda["ver"], "disponible", url_catalogo_agenda_app(empresa))
    from clinica.mobile_plans import planes_disponibles_app

    planes = planes_disponibles_app(request, empresa)
    agregar("planes_tratamiento", "Planes por paciente", "Consultar registros anteriores y guardar indicaciones nuevas sin sobrescribirlos.",
            "Atención clínica", planes["ver"] or planes["crear"],
            "disponible" if acceso["pacientes"] else "pendiente",
            f"{reverse('clinica_pacientes', args=[empresa.slug])}?app=1&destino={'planes' if planes['ver'] else 'crear_plan'}")
    terapias_consultables = acceso["pacientes"] and acceso["ver_historia_clinica"]
    agregar("terapias", "Terapias", "Selecciona un paciente y consulta sus notas de Terapias, sin mezclar las demás áreas.",
            "Atención clínica", acceso["modulo_clinica"] and acceso["ver_terapias"],
            "disponible" if terapias_consultables else "pendiente",
            f"{reverse('clinica_pacientes', args=[empresa.slug])}?app=1&destino=historias&tipo=terapias" if terapias_consultables else "",
            "Este recorrido requiere consultar pacientes e historias clínicas; ver Terapias no concede esos permisos.")
    agregar("registrar_terapia", "Registrar terapia", "Elige un paciente para registrar una nota de Terapias con el formulario clínico existente.",
            "Atención clínica", acceso["modulo_clinica"] and acceso["escribir_terapias"],
            "disponible" if acceso["pacientes"] else "pendiente",
            f"{reverse('clinica_pacientes', args=[empresa.slug])}?app=1&destino=crear_historia&tipo=terapias" if acceso["pacientes"] else "",
            "Seleccionar un paciente requiere el permiso actual de consultar pacientes.")
    # Los tableros del expediente exigen escribir el control (no basta el
    # permiso de consultar la agenda). El helper conserva esos accesos.
    from clinica.mobile_controls import controles_disponibles_app

    controles = {item["codigo"] for item in controles_disponibles_app(request, empresa)}
    for key, codigo, nombre, consultar in (
        ("camara", "camara_hiperbarica", "Cámara hiperbárica", acceso["ver_camara_hiperbarica"]),
        ("postquirurgicas", "terapias_postquirurgicas", "Seguimiento postquirúrgico", acceso["ver_postquirurgicas"]),
    ):
        adaptado = codigo in controles and acceso["pacientes"]
        agregar(key, nombre, "Programas, fases y controles sin cita con los formularios actuales; las sesiones de agenda conservan su vínculo.",
                "Atención clínica", acceso["modulo_clinica"] and (consultar or codigo in controles),
                "disponible" if adaptado else "pendiente",
                f"{reverse('clinica_pacientes', args=[empresa.slug])}?app=1&destino=control_clinico&tipo={codigo}",
                "Los controles sin cita requieren el permiso original de registrar este tipo y consultar pacientes; las atenciones con cita siguen separadas.")

    # Accesos adicionales de agenda: nunca sustituyen los controles directos
    # ni conceden calendario o pacientes por el permiso de consultar sesiones.
    from .mobile_appointment_controls import controles_cita_disponibles_app

    for control in controles_cita_disponibles_app(request, empresa):
        key, nombre = {
            "camara_hiperbarica": ("camara_citas", "Cámara desde citas"),
            "terapias_postquirurgicas": ("postquirurgicas_citas", "Postquirúrgicas desde citas"),
        }[control["codigo"]]
        agregar(key, nombre, "Consultar y registrar sesiones ligadas a citas de Hospital Mía según tus permisos.",
                "Atención clínica", True, "disponible", control["url"])

    inicio_disponible = inicio_app_disponible(request, empresa)
    factura_rapida = acceso["modulo_pos"] and inicio_disponible
    factura_movil = factura_rapida or (acceso["facturas"] and inicio_disponible)
    factura_destino = "factura-app" if factura_rapida else "facturas-app"
    agregar("facturacion", "Facturación", "Factura rápida, acciones fiscales y cobros adaptados; reportes en próxima etapa.",
            "Operación", acceso["modulo_facturacion"] and (acceso["crear_facturas"] or acceso["facturas"] or acceso["modulo_pos"]),
            "parcial" if factura_movil else "pendiente", f"{inicio}#{factura_destino}")
    inventario_movil = acceso["inventario"]
    agregar("inventario", "Productos y existencias", "Existencias, bodegas y lotes; ampliación progresiva a las cuatro empresas.",
            "Operación", acceso["productos"] or acceso["inventario"],
            "parcial" if inventario_movil else "pendiente", reverse("agenda_mobile_inventory", args=[empresa.slug]))
    from facturacion.mobile_products import productos_disponibles_app, url_productos_app

    agregar("catalogo_productos", "Catálogo de productos", "Consultar productos y servicios, código, unidad y precio registrado; no modifica existencias.",
            "Operación", productos_disponibles_app(request, empresa), "disponible", url_productos_app(empresa))
    from facturacion.mobile_product_management import gestion_productos_disponible_app, url_gestion_producto_app
    from facturacion.mobile_stock_operations import stock_disponible_app, url_stock_app

    gestion_productos = gestion_productos_disponible_app(request, empresa)
    agregar("crear_producto", "Crear producto", "Registrar un producto o servicio con el formulario y los permisos actuales.",
            "Operación", gestion_productos["crear"], "disponible", url_gestion_producto_app("crear_producto_facturacion", empresa))
    gestionar_stock = stock_disponible_app(request, empresa)
    agregar("entradas_lotes", "Agregar existencia por lote", "Producto, bodega, lote y vencimiento con el control FEFO existente.",
            "Operación", gestionar_stock, "disponible", url_stock_app(empresa))
    agregar("traslado_avanzado", "Traslado entre bodegas", "Elegir una existencia y trasladar su lote a una bodega de esta empresa.",
            "Operación", gestionar_stock, "disponible", url_stock_app(empresa, "transferencia"))
    from facturacion.mobile_kardex import kardex_disponible_app

    agregar("kardex", "Historial de inventario", "Consultar movimientos generales y por lote, con las existencias registradas antes y después.",
            "Operación", kardex_disponible_app(request, empresa), "disponible",
            f"{reverse('kardex_inventario', args=[empresa.slug])}?app=1")
    from facturacion.mobile_transfers import transferencia_disponible_app

    agregar("transferencias", "Transferencias rápidas", "Trasladar un lote entre las bodegas existentes, con el permiso y registro actuales.",
            "Operación", transferencia_disponible_app(request, empresa), "parcial",
            f"{reverse('traslado_rapido_farmaceutico', args=[empresa.slug])}?app=1")
    from facturacion.mobile_receipts import recibos_disponibles_app

    agregar("recibos", "Recibos", "Consultar documentos y abrir su PDF; no es una pantalla de contabilidad.",
            "Operación", recibos_disponibles_app(request, empresa), "parcial",
            f"{reverse('recibos_dashboard', args=[empresa.slug])}?app=1")
    from facturacion.mobile_payments import cobros_disponibles_app

    agregar("cobros", "Registrar y corregir cobros", "Seleccionar una factura desde Ver Facturas, registrar abonos o corregir un pago con su autorización actual.",
            "Operación", cobros_disponibles_app(request, empresa),
            "parcial" if acceso["facturas"] and inicio_disponible else "pendiente",
            f"{inicio}#facturas-app")
    from facturacion.mobile_egresos import egresos_disponibles_app

    agregar("egresos", "Comprobantes de egreso", "Consultar documentos de pago a proveedores, buscar y abrir su PDF original.",
            "Operación", egresos_disponibles_app(request, empresa), "disponible",
            f"{reverse('egresos_dashboard', args=[empresa.slug])}?app=1")
    from facturacion.mobile_suppliers import proveedores_disponibles_app

    proveedores = proveedores_disponibles_app(request, empresa)
    agregar("proveedores", "Proveedores", "Consultar fichas y compras asociadas, crear o editar datos con los permisos actuales.",
            "Operación", any(proveedores.values()),
            "disponible" if proveedores["ver"] or proveedores["crear"] else "pendiente",
            f"{reverse('proveedores_facturacion' if proveedores['ver'] else 'crear_proveedor_facturacion', args=[empresa.slug])}?app=1")
    from facturacion.mobile_purchases import compras_disponibles_app

    agregar("compras", "Compras", "Consultar documentos, productos, pagos registrados, movimientos y PDF; las operaciones siguen pendientes.",
            "Operación", compras_disponibles_app(request, empresa), "parcial",
            f"{reverse('compras_dashboard', args=[empresa.slug])}?app=1")
    from facturacion.mobile_cxc import cxc_disponible_app

    agregar("cxc", "Cuentas por cobrar", "Consultar cartera por cliente, saldos y antigüedad del reporte actual, sin modificar pagos.",
            "Operación", cxc_disponible_app(request, empresa), "disponible",
            f"{reverse('reporte_cxc', args=[empresa.slug])}?app=1")
    for key, nombre, permiso in (
        ("cotizaciones", "Cotizaciones", acceso["cotizaciones"]),
        ("notas_credito", "Notas de crédito", acceso["notas_credito"]),
        ("cxp", "Cuentas por pagar", acceso["cxp"]),
        ("cierres", "Cierres de caja", acceso["cierres_caja"] and acceso["usa_cierre_caja"]),
        ("reportes", "Reportes", acceso["reportes"]),
    ):
        agregar(key, nombre, "Pendiente de adaptar a pantalla móvil; no abre la versión web.", "Operación", permiso)
    from clinica.mobile_configuration import config_disponible_app

    configuracion = config_disponible_app(request, empresa)
    agregar("configuracion", "Configuración clínica", "Profesionales, servicios y accesos clínicos adaptados según tus permisos; otras opciones siguen pendientes.",
            "Administración", acceso["configuracion_producto_clinico"],
            "parcial" if configuracion["hub"] else "pendiente",
            f"{reverse('clinica_configuracion', args=[empresa.slug])}?app=1")
    from core.mobile_users import usuarios_disponibles_app

    agregar("usuarios", "Usuarios y permisos", "Ver usuarios vinculados y administrar su rol en esta empresa con la autorización existente.",
            "Administración", usuarios_disponibles_app(request, empresa), "disponible",
            f"{reverse('usuarios_clinicos', args=[empresa.slug])}?app=1")
    from core.mobile_company import configuracion_empresa_disponible_app

    agregar("empresa", "Configuración de empresa", "Datos, logo, plan, licencia y módulos con la autorización de superadministración existente.",
            "Administración", configuracion_empresa_disponible_app(request, empresa), "disponible",
            f"{reverse('superadmin_empresa_edit', args=[empresa.pk])}?app=1")
    from core.mobile_advanced_configuration import configuracion_avanzada_disponible_app

    avanzadas = configuracion_avanzada_disponible_app(request, empresa)
    agregar("configuracion_avanzada", "Opciones avanzadas", "Consultar o configurar inventario, bodegas, manuales y opciones existentes con los permisos administrativos de Django.",
            "Administración", any(avanzadas.values()), "disponible",
            (f"{reverse('configuracion_avanzada_empresa_app', args=[empresa.pk])}?app=1" if avanzadas["ver"]
             else f"{reverse('configuracion_avanzada_empresas_app')}?app=1"))
    from .mobile_crm_configuration import configuracion_crm_disponible_app

    agregar("configuracion_crm", "Agenda y comunicación", "Ajustes, plantillas y textos existentes de esta empresa; la credencial se mantiene fuera del formulario.",
            "Administración", configuracion_crm_disponible_app(request, empresa), "disponible",
            f"{reverse('crm_configuracion', args=[empresa.slug])}?app=1")
    for key, nombre, permiso in (
        ("contabilidad", "Contabilidad", acceso["modulo_contabilidad"]),
        ("rrhh", "Recursos humanos", acceso["modulo_rrhh"]),
        ("crm", "CRM y comunicación", acceso["modulo_crm"]),
        ("tecnicentro", "Tecnicentro", acceso["modulo_tecnicentro"]),
    ):
        agregar(key, nombre, "Solo estará disponible con los permisos actuales de esta empresa.", "Administración", permiso)

    rol = usuario.rol_para_empresa(empresa)
    # El permiso administrativo original no depende de licencia. Cuando el
    # catálogo se abre sólo por esa excepción, no ofrecer destinos operativos
    # bloqueados: cada operación conserva su comprobación de licencia.
    if not usuario.is_superuser and not empresa.licencia_operativa:
        modulos = [item for item in modulos if item["key"] == "usuarios"]
    contexto.update({
        "puede_ver_modulos_app": True,
        "modulos_app": modulos,
        "grupos_modulos_app": [
            {"nombre": categoria, "modulos": [m for m in modulos if m["categoria"] == categoria]}
            for categoria in ("Atención clínica", "Operación", "Administración")
            if any(m["categoria"] == categoria for m in modulos)
        ],
        "rol_app": rol.nombre if rol else "Acceso autorizado",
        "modulos_disponibles_app": sum(bool(m["url"]) for m in modulos),
    })
    return contexto
