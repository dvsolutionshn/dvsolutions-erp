"""Catálogo de presentación móvil sobre permisos y módulos existentes.

No autoriza operaciones ni crea una API paralela de negocio. Las vistas de cada
módulo siguen comprobando sus permisos; los destinos pendientes no son enlaces.
"""

from types import SimpleNamespace

from django.urls import reverse

from core.access import interfaz_clinica_activa
from core.clinical_permissions import EMPRESAS_CLINICAS_CON_ROLES, rol_clinico_granular
from core.context_processors import erp_access


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

    def agregar(key, nombre, descripcion, categoria, permitido, estado="pendiente", destino=""):
        if permitido:
            modulos.append({
                "key": key, "nombre": nombre, "descripcion": descripcion,
                "categoria": categoria, "estado": estado,
                "url": destino if estado != "pendiente" else "",
                "panel": destino[len(inicio) + 1:] if estado != "pendiente" and destino.startswith(f"{inicio}#") else "",
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
    for key, nombre, descripcion, autorizado in (
        ("expediente", "Expediente clínico", "Historias, diagnósticos y evolución del paciente.", acceso["expediente_clinico"]),
        ("recetas", "Recetas", "Prescripciones, PDF y envío al paciente.", acceso["ver_recetas"]),
        ("anexos", "Exámenes y anexos", "Documentos y archivos del expediente.", acceso["ver_anexos_clinicos"]),
        ("tratamientos", "Tratamientos", "Planes, controles y seguimiento clínico.", acceso["tratamientos_clinicos"]),
        ("terapias", "Terapias", "Registro y seguimiento de terapias.", acceso["ver_terapias"]),
        ("camara", "Cámara hiperbárica", "Programas y controles de las sesiones.", acceso["ver_camara_hiperbarica"]),
        ("postquirurgicas", "Seguimiento postquirúrgico", "Controles y terapias posteriores a cirugía.", acceso["ver_postquirurgicas"]),
        ("manuales", "Manuales PDF", "Consultar y enviar manuales autorizados.", acceso["ver_manuales_pdf"]),
    ):
        agregar(key, nombre, descripcion, "Atención clínica", acceso["modulo_clinica"] and autorizado)

    inicio_disponible = inicio_app_disponible(request, empresa)
    factura_rapida = acceso["modulo_pos"] and inicio_disponible
    factura_movil = factura_rapida or (acceso["facturas"] and inicio_disponible)
    factura_destino = "factura-app" if factura_rapida else "facturas-app"
    agregar("facturacion", "Facturación", "Factura rápida y acciones fiscales actuales; cobros y reportes en próxima etapa.",
            "Operación", acceso["modulo_facturacion"] and (acceso["crear_facturas"] or acceso["facturas"] or acceso["modulo_pos"]),
            "parcial" if factura_movil else "pendiente", f"{inicio}#{factura_destino}")
    inventario_movil = acceso["inventario"]
    agregar("inventario", "Productos y existencias", "Existencias, bodegas y lotes; ampliación progresiva a las cuatro empresas.",
            "Operación", acceso["productos"] or acceso["inventario"],
            "parcial" if inventario_movil else "pendiente", reverse("agenda_mobile_inventory", args=[empresa.slug]))
    for key, nombre, permiso in (
        ("transferencias", "Transferencias", acceso["modulo_facturacion"] and acceso["transferir_inventario"]),
        ("compras", "Compras", acceso["compras"]),
        ("proveedores", "Proveedores", acceso["proveedores"]),
        ("cotizaciones", "Cotizaciones", acceso["cotizaciones"]),
        ("recibos", "Recibos y cobros", acceso["recibos"]),
        ("notas_credito", "Notas de crédito", acceso["notas_credito"]),
        ("egresos", "Gastos y egresos", acceso["egresos"]),
        ("cxc", "Cuentas por cobrar", acceso["cxc"]),
        ("cxp", "Cuentas por pagar", acceso["cxp"]),
        ("cierres", "Cierres de caja", acceso["cierres_caja"] and acceso["usa_cierre_caja"]),
        ("reportes", "Reportes", acceso["reportes"]),
    ):
        agregar(key, nombre, "Pendiente de adaptar a pantalla móvil; no abre la versión web.", "Operación", permiso)
    for key, nombre, permiso in (
        ("configuracion", "Configuración clínica", acceso["configuracion_producto_clinico"]),
        ("usuarios", "Usuarios y permisos", acceso["administrar_usuarios_clinicos"]),
        ("contabilidad", "Contabilidad", acceso["modulo_contabilidad"]),
        ("rrhh", "Recursos humanos", acceso["modulo_rrhh"]),
        ("crm", "CRM y comunicación", acceso["modulo_crm"]),
        ("tecnicentro", "Tecnicentro", acceso["modulo_tecnicentro"]),
    ):
        agregar(key, nombre, "Solo estará disponible con los permisos actuales de esta empresa.", "Administración", permiso)

    rol = usuario.rol_para_empresa(empresa)
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
