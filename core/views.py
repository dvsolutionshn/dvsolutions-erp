from functools import wraps
import logging
import re
from urllib.parse import quote

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.mail import send_mail
from django.core.paginator import Paginator
from django.db import OperationalError, transaction
from django.db.models import Count, Prefetch, Q
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import get_random_string
from django.utils.dateparse import parse_date
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .assistant import responder_consulta
from .access import modo_clinico_simple_activo
from .access_tokens import emitir_token_acceso, enviar_correo_acceso, hash_token_acceso
from .backup_service import generar_respaldo_empresa
from .backup_tokens import generar_token_respaldo, hash_token_respaldo
from .forms import (
    EmpresaControlForm,
    PagoLicenciaEmpresaForm,
    PlanComercialForm,
    RolSistemaForm,
    SolicitudComercialPublicaForm,
    SolicitarRecuperacionForm,
    EstablecerAccesoForm,
    SuperAdminLoginForm,
    UsuarioControlCreateForm,
    UsuarioControlUpdateForm,
)
from .models import Empresa
from .models import EmpresaModulo
from .models import (
    PagoLicenciaEmpresa,
    PlanComercial,
    PlanModulo,
    RespaldoEmpresa,
    RegistroAuditoria,
    RolSistema,
    SolicitudComercial,
    TokenRespaldoEmpresa,
    TokenAccesoUsuario,
    Usuario,
    UsuarioEmpresaPermiso,
)


HOST_LOCAL_PATTERNS = {
    "localhost",
    "127.0.0.1",
    "::1",
}
logger = logging.getLogger(__name__)
SESSION_EXPIRED_MESSAGE_KEY = "dvsolutions_session_expired_message"
SESSION_EXPIRED_MESSAGE = "Vuelve a iniciar sesion para continuar."
BACKUP_TOKEN_MAX_ATTEMPTS = 5
BACKUP_TOKEN_WINDOW_SECONDS = 15 * 60
EMPRESAS_CLINICAS_CON_CONTROL_USUARIOS = frozenset({
    "hospital_mia",
    "serviciosmedicos",
    "medical_spa",
    "luque_aestetic",
})

PERMISOS_ROL_CLINICO = (
    ("Facturación", (
        ("puede_facturas", "Ingresar al módulo de facturación"),
        ("puede_crear_facturas", "Crear facturas"),
        ("puede_ver_facturas", "Ver facturas anteriores"),
        ("puede_editar_facturas", "Editar facturas"),
        ("puede_anular_facturas", "Anular facturas"),
        ("puede_eliminar_borradores", "Eliminar facturas en borrador"),
        ("puede_eliminar_facturas", "Eliminar facturas"),
        ("puede_registrar_pagos_clientes", "Registrar pagos"),
        ("puede_punto_venta", "Punto de venta"),
        ("puede_cierres_caja", "Cierres de caja"),
        ("puede_clientes", "Ver clientes"),
        ("puede_crear_clientes", "Crear clientes"),
        ("puede_editar_clientes", "Editar clientes"),
        ("puede_recibos", "Recibos"),
        ("puede_egresos", "Egresos"),
        ("puede_notas_credito", "Notas de crédito"),
        ("puede_crear_notas_credito", "Crear notas de crédito"),
        ("puede_editar_notas_credito", "Editar notas de crédito"),
        ("puede_anular_notas_credito", "Anular notas de crédito"),
        ("puede_reportes", "Reportes de facturación"),
        ("puede_exportar_reportes", "Exportar reportes"),
        ("puede_cxc", "Cuentas por cobrar"),
    )),
    ("Pacientes y clínica", (
        ("puede_clinica", "Ingresar a Clínica"),
        ("puede_pacientes", "Ver pacientes"),
        ("puede_expediente_clinico", "Trabajar expedientes"),
        ("puede_tratamientos_clinicos", "Tratamientos clínicos"),
        ("puede_configuracion_clinica", "Configuración clínica"),
        ("puede_citas", "Agenda y citas"),
    )),
    ("Inventario y compras", (
        ("puede_productos", "Ver productos"),
        ("puede_crear_productos", "Crear productos"),
        ("puede_editar_productos", "Editar productos"),
        ("puede_proveedores", "Ver proveedores"),
        ("puede_crear_proveedores", "Crear proveedores"),
        ("puede_editar_proveedores", "Editar proveedores"),
        ("puede_inventario", "Ver inventario"),
        ("puede_ajustar_inventario", "Ajustar inventario"),
        ("puede_compras", "Ver compras"),
        ("puede_crear_compras", "Crear compras"),
        ("puede_editar_compras", "Editar compras"),
        ("puede_aplicar_compras", "Aplicar compras"),
        ("puede_anular_compras", "Anular compras"),
        ("puede_registrar_pagos_proveedores", "Pagar proveedores"),
        ("puede_cxp", "Cuentas por pagar"),
    )),
    ("Contabilidad y configuración", (
        ("puede_contabilidad", "Contabilidad"),
        ("puede_catalogo_cuentas", "Catálogo de cuentas"),
        ("puede_crear_asientos", "Crear asientos contables"),
        ("puede_contabilizar_asientos", "Contabilizar asientos"),
        ("puede_reportes_contables", "Reportes contables"),
        ("puede_cai", "Administrar CAI"),
        ("puede_impuestos", "Administrar impuestos"),
        ("puede_configuracion_facturacion", "Configuración de facturación"),
    )),
    ("Recursos Humanos", (
        ("puede_rrhh", "Ingresar a Recursos Humanos"),
        ("puede_empleados", "Empleados"),
        ("puede_planillas", "Planillas"),
        ("puede_vacaciones", "Vacaciones"),
        ("puede_configuracion_rrhh", "Configuración de Recursos Humanos"),
    )),
    ("CRM y agenda", (
        ("puede_crm", "Ingresar a CRM y Marketing"),
        ("puede_campanias", "Campañas"),
        ("puede_configuracion_crm", "Configuración de CRM"),
    )),
)


MODULOS_PERMISOS_PRESENTACION = (
    {
        "codigo": "facturacion",
        "descripcion": "Ventas, documentos fiscales, cobros, caja, clientes y reportes.",
    },
    {
        "codigo": "clinica",
        "descripcion": "Pacientes, expedientes, tratamientos y agenda clínica.",
    },
    {
        "codigo": "inventario",
        "descripcion": "Catálogo, proveedores, existencias y ciclo completo de compras.",
    },
    {
        "codigo": "contabilidad",
        "descripcion": "Operación contable, configuración fiscal, CAI e impuestos.",
    },
    {
        "codigo": "rrhh",
        "descripcion": "Empleados, planillas, vacaciones y configuración laboral.",
    },
    {
        "codigo": "crm",
        "descripcion": "Relación con clientes, campañas y configuración comercial.",
    },
)

CATEGORIAS_PERMISOS_CLINICOS = {
    "puede_facturas": "Acceso y documentos",
    "puede_crear_facturas": "Acceso y documentos",
    "puede_ver_facturas": "Acceso y documentos",
    "puede_editar_facturas": "Acceso y documentos",
    "puede_anular_facturas": "Acceso y documentos",
    "puede_eliminar_borradores": "Acceso y documentos",
    "puede_eliminar_facturas": "Acceso y documentos",
    "puede_registrar_pagos_clientes": "Cobros y caja",
    "puede_punto_venta": "Cobros y caja",
    "puede_cierres_caja": "Cobros y caja",
    "puede_recibos": "Cobros y caja",
    "puede_egresos": "Cobros y caja",
    "puede_clientes": "Clientes",
    "puede_crear_clientes": "Clientes",
    "puede_editar_clientes": "Clientes",
    "puede_notas_credito": "Notas de crédito",
    "puede_crear_notas_credito": "Notas de crédito",
    "puede_editar_notas_credito": "Notas de crédito",
    "puede_anular_notas_credito": "Notas de crédito",
    "puede_reportes": "Reportes y cartera",
    "puede_exportar_reportes": "Reportes y cartera",
    "puede_cxc": "Reportes y cartera",
    "puede_clinica": "Acceso clínico",
    "puede_pacientes": "Acceso clínico",
    "puede_expediente_clinico": "Atención al paciente",
    "puede_tratamientos_clinicos": "Atención al paciente",
    "puede_citas": "Agenda clínica",
    "puede_configuracion_clinica": "Administración clínica",
    "puede_productos": "Productos",
    "puede_crear_productos": "Productos",
    "puede_editar_productos": "Productos",
    "puede_proveedores": "Proveedores",
    "puede_crear_proveedores": "Proveedores",
    "puede_editar_proveedores": "Proveedores",
    "puede_inventario": "Inventario",
    "puede_ajustar_inventario": "Inventario",
    "puede_compras": "Compras",
    "puede_crear_compras": "Compras",
    "puede_editar_compras": "Compras",
    "puede_aplicar_compras": "Compras",
    "puede_anular_compras": "Compras",
    "puede_registrar_pagos_proveedores": "Compras",
    "puede_cxp": "Compras",
    "puede_contabilidad": "Operación contable",
    "puede_catalogo_cuentas": "Operación contable",
    "puede_crear_asientos": "Asientos contables",
    "puede_contabilizar_asientos": "Asientos contables",
    "puede_reportes_contables": "Informes contables",
    "puede_cai": "Configuración fiscal",
    "puede_impuestos": "Configuración fiscal",
    "puede_configuracion_facturacion": "Configuración fiscal",
    "puede_rrhh": "Acceso a Recursos Humanos",
    "puede_empleados": "Gestión del personal",
    "puede_planillas": "Gestión del personal",
    "puede_vacaciones": "Gestión del personal",
    "puede_configuracion_rrhh": "Configuración de RR. HH.",
    "puede_crm": "Acceso a CRM",
    "puede_campanias": "Campañas",
    "puede_configuracion_crm": "Configuración de CRM",
}


def _puede_administrar_usuarios_clinicos(usuario, empresa):
    return bool(
        usuario
        and usuario.is_authenticated
        and empresa.slug in EMPRESAS_CLINICAS_CON_CONTROL_USUARIOS
        and usuario.puede_acceder_empresa(empresa)
        and (
            usuario.is_superuser
            or getattr(usuario, "puede_administrar_usuarios_clinicos", False)
        )
    )


def _permisos_visibles_rol(rol):
    if not rol:
        return []
    return [
        {
            "grupo": grupo,
            "permisos": [etiqueta for campo, etiqueta in permisos if getattr(rol, campo, False)],
        }
        for grupo, permisos in PERMISOS_ROL_CLINICO
        if any(getattr(rol, campo, False) for campo, _ in permisos)
    ]


def _usuarios_operativos_de_empresa(empresa):
    """Usuarios administrables vinculados realmente a una empresa.

    La baja desde Control conserva la identidad para la trazabilidad histórica,
    pero el usuario deja de formar parte de las pantallas operativas y de los
    endpoints de permisos de todas sus empresas.
    """
    return (
        Usuario.objects
        .filter(
            Q(empresa=empresa) | Q(empresas_acceso=empresa),
            is_superuser=False,
            retirado_control=False,
        )
        .distinct()
    )


def _usuario_empresas_config(form, request):
    empresas = list(Empresa.objects.filter(activa=True).order_by("nombre"))
    roles = list(RolSistema.objects.filter(activo=True).order_by("nombre"))
    seleccionadas = set()
    if request.method == "POST":
        seleccionadas.update(int(valor) for valor in request.POST.getlist("empresas_acceso") if valor.isdigit())
        empresa_principal = request.POST.get("empresa")
        if empresa_principal and empresa_principal.isdigit():
            seleccionadas.add(int(empresa_principal))
    else:
        usuario = getattr(form, "instance", None)
        if usuario and usuario.pk:
            seleccionadas.update(usuario.empresas_acceso.values_list("id", flat=True))
            if usuario.empresa_id:
                seleccionadas.add(usuario.empresa_id)
        else:
            inicial = form.fields.get("empresas_acceso").initial if "empresas_acceso" in form.fields else None
            if inicial:
                seleccionadas.update(getattr(item, "id", item) for item in inicial)

    permisos_actuales = {}
    usuario = getattr(form, "instance", None)
    if usuario and usuario.pk:
        permisos_actuales = {
            permiso.empresa_id: permiso.rol_sistema_id
            for permiso in usuario.permisos_por_empresa.filter(activo=True)
        }

    config = []
    for empresa in empresas:
        posted_role = request.POST.get(f"empresa_rol_{empresa.id}") if request.method == "POST" else None
        config.append({
            "empresa": empresa,
            "seleccionada": empresa.id in seleccionadas,
            "rol_id": int(posted_role) if posted_role and posted_role.isdigit() else permisos_actuales.get(empresa.id),
        })
    return {"empresas_config": config, "roles_empresa": roles}


def _guardar_roles_por_empresa(usuario, post_data):
    empresas_ids = {int(valor) for valor in post_data.getlist("empresas_acceso") if valor.isdigit()}
    if usuario.empresa_id:
        empresas_ids.add(usuario.empresa_id)

    UsuarioEmpresaPermiso.objects.filter(usuario=usuario).exclude(empresa_id__in=empresas_ids).delete()
    for empresa_id in empresas_ids:
        rol_id = post_data.get(f"empresa_rol_{empresa_id}")
        if rol_id and str(rol_id).isdigit():
            UsuarioEmpresaPermiso.objects.update_or_create(
                usuario=usuario,
                empresa_id=empresa_id,
                defaults={"rol_sistema_id": int(rol_id), "activo": True},
            )
        else:
            UsuarioEmpresaPermiso.objects.filter(usuario=usuario, empresa_id=empresa_id).delete()


def _public_demo_catalog():
    return {
        "facturacion": {
            "slug": "facturacion",
            "titulo": "Facturacion y cobros",
            "subtitulo": "Demo de factura",
            "descripcion": "Visualizacion de facturas, cobros, impuestos, retenciones y lectura ejecutiva de la operacion comercial.",
            "metricas": [
                ("Factura", "000-001-01-00000364"),
                ("Total", "L 53,229.42"),
                ("Estado", "Emitida"),
            ],
            "lineas": [
                "Factura premium con resumen fiscal, subtotal, impuesto y total final.",
                "Historial de pagos, recibos y lectura de saldo pendiente.",
                "Formato pensado para gestion comercial y control operativo.",
            ],
            "detalle_titulo": "Vista demo de factura empresarial",
            "detalle_intro": "Esta demo reproduce la sensacion visual de una factura premium dentro del ecosistema DV Solutions, con lectura comercial, fiscal y financiera lista para presentar al cliente.",
            "detalle_bloques": [
                ("Cliente", "Constructora del Norte, S. de R.L."),
                ("RTN", "08011999123456"),
                ("Metodo de pago", "Transferencia bancaria"),
                ("Estado", "Emitida y lista para cobro"),
            ],
            "detalle_items": [
                ("Implementacion de modulo comercial", "L 28,950.00"),
                ("Configuracion fiscal y CAI", "L 9,850.00"),
                ("Capacitacion operativa", "L 7,486.45"),
                ("ISV 15%", "L 6,942.97"),
            ],
            "detalle_total": "L 53,229.42",
            "cta_label": "Solicitar una demo comercial de facturacion",
        },
        "rrhh": {
            "slug": "rrhh",
            "titulo": "Recursos humanos",
            "subtitulo": "Demo RRHH",
            "descripcion": "Gestion de empleados, planillas, vacaciones y estructura interna con una vista mas clara para operaciones administrativas.",
            "metricas": [
                ("Empleados", "128"),
                ("Planilla", "Mensual"),
                ("Alertas", "4"),
            ],
            "lineas": [
                "Expedientes, vacaciones, bonos y deducciones en un solo flujo.",
                "Panel preparado para seguimiento administrativo y soporte operativo.",
                "Diseno pensado para empresas que necesitan control sin complejidad visual.",
            ],
            "detalle_titulo": "Vista demo de planilla y gestion de personal",
            "detalle_intro": "Esta demo muestra como DV Solutions puede presentar planillas, equipo humano y alertas de RRHH con una lectura ejecutiva, limpia y lista para operacion real.",
            "detalle_bloques": [
                ("Periodo", "Mayo 2026"),
                ("Empleados liquidados", "128"),
                ("Neto a pagar", "L 1,284,540.20"),
                ("Estado", "Planilla lista para aprobacion"),
            ],
            "detalle_items": [
                ("Sueldos base", "L 1,020,000.00"),
                ("Horas extra y bonos", "L 142,880.00"),
                ("IHSS + RAP + ISR", "L 86,450.30"),
                ("Neto a depositar", "L 1,076,429.70"),
            ],
            "detalle_total": "L 1,284,540.20",
            "cta_label": "Solicitar demo de RRHH y planilla",
        },
        "crm": {
            "slug": "crm",
            "titulo": "CRM y seguimiento",
            "subtitulo": "Demo comercial",
            "descripcion": "Campanas, citas, prospectos y acciones comerciales coordinadas desde una capa mas estrategica del negocio.",
            "metricas": [
                ("Campanas", "12"),
                ("Citas", "26"),
                ("Prospectos", "41"),
            ],
            "lineas": [
                "Seguimiento a leads, campanas y conversaciones desde el mismo ecosistema.",
                "Ideal para equipos que venden, dan seguimiento o convierten demos en clientes.",
                "Conexion natural entre marketing, operacion y ventas.",
            ],
            "detalle_titulo": "Vista demo de CRM y mensajes masivos",
            "detalle_intro": "Esta demo muestra como un equipo comercial puede lanzar mensajes masivos, mover prospectos por etapa y coordinar citas sin salir del mismo sistema.",
            "detalle_bloques": [
                ("Campana activa", "Lanzamiento ERP regional"),
                ("Mensajes enviados", "1,240"),
                ("Respuestas recibidas", "214"),
                ("Estado", "Seguimiento comercial en curso"),
            ],
            "detalle_items": [
                ("WhatsApp masivo segmentado", "Campana enviada a prospectos filtrados por interes"),
                ("Agenda de citas", "26 reuniones en ejecucion"),
                ("Embudo comercial", "41 prospectos activos"),
                ("Tablero de conversion", "12 oportunidades en propuesta"),
            ],
            "detalle_total": "Operacion comercial en tiempo real",
            "cta_label": "Solicitar demo de CRM y automatizacion",
        },
    }


def _notify_new_commercial_request(solicitud):
    recipients = getattr(settings, "COMMERCIAL_REQUEST_RECIPIENTS", [])
    if not recipients:
        return "skipped"

    subject = f"Nueva solicitud comercial - {solicitud.nombre_contacto}"
    body = (
        f"Nombre: {solicitud.nombre_contacto}\n"
        f"Empresa: {solicitud.empresa_interesada or '-'}\n"
        f"RTN: {solicitud.rtn_empresa or '-'}\n"
        f"Correo: {solicitud.correo}\n"
        f"Telefono: {solicitud.telefono or '-'}\n"
        f"Servicio: {solicitud.get_servicio_interes_display()}\n"
        f"Solicita prueba: {'Si' if solicitud.solicita_prueba else 'No'}\n"
        f"Estado inicial: {solicitud.get_estado_display()}\n\n"
        f"Mensaje:\n{solicitud.mensaje}\n"
    )

    try:
        send_mail(
            subject=subject,
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=recipients,
            fail_silently=False,
        )
        if "console.EmailBackend" in settings.EMAIL_BACKEND:
            return "console"
        return "sent"
    except Exception:
        logger.exception("No se pudo enviar la notificacion de solicitud comercial %s", solicitud.id)
        return "failed"


def _client_ip(request):
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "unknown")


def _normalizar_whatsapp_number(raw_number):
    if not raw_number:
        return ""
    return "".join(ch for ch in str(raw_number) if ch.isdigit())


def _build_whatsapp_link(message, number=None):
    target = _normalizar_whatsapp_number(number or getattr(settings, "PUBLIC_WHATSAPP_NUMBER", ""))
    if not target:
        return ""
    return f"https://wa.me/{target}?text={quote(message)}"


def _login_throttle_key(scope, request):
    return f"login-throttle:{scope}:{_client_ip(request)}"


def _login_block_seconds(scope, request):
    throttle_data = cache.get(_login_throttle_key(scope, request))
    if not throttle_data:
        return 0

    locked_until = throttle_data.get("locked_until")
    if not locked_until:
        return 0

    remaining = int(locked_until - timezone.now().timestamp())
    if remaining <= 0:
        cache.delete(_login_throttle_key(scope, request))
        return 0
    return remaining


def _register_login_failure(scope, request):
    throttle_key = _login_throttle_key(scope, request)
    window_seconds = settings.LOGIN_THROTTLE_WINDOW_SECONDS
    throttle_limit = settings.LOGIN_THROTTLE_LIMIT
    now_ts = timezone.now().timestamp()
    throttle_data = cache.get(throttle_key) or {
        "count": 0,
        "first_failure": now_ts,
        "locked_until": 0,
    }

    first_failure = throttle_data.get("first_failure", now_ts)
    if first_failure + window_seconds <= now_ts:
        throttle_data = {
            "count": 0,
            "first_failure": now_ts,
            "locked_until": 0,
        }

    throttle_data["count"] += 1
    if throttle_data["count"] >= throttle_limit:
        throttle_data["locked_until"] = now_ts + window_seconds

    cache.set(throttle_key, throttle_data, timeout=window_seconds * 2)
    return _login_block_seconds(scope, request)


def _clear_login_failures(scope, request):
    cache.delete(_login_throttle_key(scope, request))


def _backup_token_throttle_key(empresa, request):
    return f"backup-token:{empresa.pk}:{_client_ip(request)}"


def _backup_token_attempts(empresa, request):
    return cache.get(_backup_token_throttle_key(empresa, request), 0)


def _register_backup_token_failure(empresa, request):
    key = _backup_token_throttle_key(empresa, request)
    attempts = cache.get(key, 0) + 1
    cache.set(key, attempts, timeout=BACKUP_TOKEN_WINDOW_SECONDS)
    return attempts


def _clear_backup_token_failures(empresa, request):
    cache.delete(_backup_token_throttle_key(empresa, request))


def _host_sin_puerto(request):
    return (request.get_host() or "").split(":")[0].strip().lower()


def _empresa_desde_host(request):
    host = _host_sin_puerto(request)
    if not host or host in HOST_LOCAL_PATTERNS or re.match(r"^\d{1,3}(\.\d{1,3}){3}$", host):
        return None

    labels = [label for label in host.split(".") if label]
    if len(labels) < 3:
        return None

    subdominio = labels[0]
    if subdominio in {"www", "erp", "test", "app"}:
        return None

    empresa = Empresa.objects.filter(slug=subdominio, activa=True).first()
    if empresa:
        return empresa

    # Los slugs internos existentes usan guion bajo, pero los nombres DNS solo
    # deben usar letras, numeros y guiones. Ej.: hospital-mia -> hospital_mia.
    slug_compatible = subdominio.replace("-", "_")
    if slug_compatible != subdominio:
        return Empresa.objects.filter(slug=slug_compatible, activa=True).first()

    return None


def _resolver_empresa_request(request, slug=None):
    if slug:
        return get_object_or_404(Empresa, slug=slug, activa=True)

    empresa = _empresa_desde_host(request)
    if empresa:
        return empresa

    raise Http404("No se encontro una empresa valida para este acceso.")


def _usa_host_empresa(request, empresa):
    empresa_host = _empresa_desde_host(request)
    return bool(empresa_host and empresa_host.id == empresa.id)


def _redirect_login_empresa(request, empresa):
    if _usa_host_empresa(request, empresa):
        return redirect("empresa_login_host")
    return redirect("empresa_login", slug=empresa.slug)


def _flash_session_expired_message(request):
    mensaje = request.session.pop(SESSION_EXPIRED_MESSAGE_KEY, None)
    if mensaje:
        messages.warning(request, mensaje)


def csrf_failure(request, reason=""):
    request.session[SESSION_EXPIRED_MESSAGE_KEY] = SESSION_EXPIRED_MESSAGE
    path = request.path or ""
    if path.startswith("/control/"):
        return redirect("superadmin_login")

    empresa_host = _empresa_desde_host(request)
    if empresa_host:
        return redirect("empresa_login_host")

    partes = [parte for parte in path.split("/") if parte]
    if partes:
        empresa = Empresa.objects.filter(slug=partes[0]).first()
        if empresa:
            return redirect("empresa_login", slug=empresa.slug)

    return redirect("public_access")


def _redirect_dashboard_empresa(request, empresa):
    if _usa_host_empresa(request, empresa):
        return redirect("dashboard_host")
    return redirect("dashboard", slug=empresa.slug)


def _minutes_remaining(seconds):
    if seconds <= 0:
        return 1
    return max(1, (seconds + 59) // 60)


def _es_perfil_clinico(empresa):
    return bool(
        empresa.tipo_solucion == "clinica"
        or empresa.slug in {"hospital_mia", "medical_spa"}
        or empresa.tiene_modulo_activo("clinica_medica")
    )


def empresa_login(request, slug=None):
    empresa = _resolver_empresa_request(request, slug)
    if empresa.tipo_solucion == "tecnicentro":
        return redirect("tecnicentro_login", empresa_slug=empresa.slug)
    es_perfil_clinico = _es_perfil_clinico(empresa)
    # Todas las empresas comparten la experiencia premium; Tecnicentro conserva
    # su acceso Garage OS independiente definido arriba.
    template_name = "core/login_hospital_mia.html"
    _flash_session_expired_message(request)
    throttle_scope = f"empresa:{empresa.slug}"

    if request.method == 'POST':
        bloqueo_restante = _login_block_seconds(throttle_scope, request)
        if bloqueo_restante > 0:
            messages.error(
                request,
                f"Por seguridad bloqueamos temporalmente este acceso. Intenta nuevamente en {_minutes_remaining(bloqueo_restante)} minuto(s).",
            )
            return render(request, template_name, {'empresa': empresa, 'es_perfil_clinico': es_perfil_clinico})

        username = (request.POST.get('username') or "").strip()
        password = request.POST.get('password')

        user = authenticate(request, username=username, password=password, empresa=empresa)

        if user is not None:
            if user.puede_acceder_empresa(empresa):
                _clear_login_failures(throttle_scope, request)
                login(request, user)
                siguiente = (request.POST.get("next") or request.GET.get("next") or "").strip()
                if siguiente and url_has_allowed_host_and_scheme(
                    siguiente,
                    allowed_hosts={request.get_host()},
                    require_https=request.is_secure(),
                ):
                    return redirect(siguiente)
                if es_perfil_clinico:
                    if (
                        empresa.tiene_modulo_activo("clinica_medica")
                        and user.tiene_alguna_permision_clinica_empresa(empresa)
                    ):
                        return redirect("clinica_dashboard", empresa_slug=empresa.slug)
                    if (
                        empresa.tiene_modulo_activo("agenda_citas")
                        and user.tiene_permiso_erp("puede_citas", empresa)
                    ):
                        return redirect("agenda_citas", empresa_slug=empresa.slug)
                return _redirect_dashboard_empresa(request, empresa)
            else:
                bloqueo_restante = _register_login_failure(throttle_scope, request)
                messages.error(request, "El correo no pertenece a esta empresa.")
                if bloqueo_restante > 0:
                    messages.error(
                        request,
                        f"Por seguridad bloqueamos temporalmente este acceso. Intenta nuevamente en {_minutes_remaining(bloqueo_restante)} minuto(s).",
                    )
        else:
            bloqueo_restante = _register_login_failure(throttle_scope, request)
            messages.error(request, "Correo o contrasena incorrectos.")
            if bloqueo_restante > 0:
                messages.error(
                    request,
                    f"Por seguridad bloqueamos temporalmente este acceso. Intenta nuevamente en {_minutes_remaining(bloqueo_restante)} minuto(s).",
                )

    return render(request, template_name, {'empresa': empresa, 'es_perfil_clinico': es_perfil_clinico})


def solicitar_recuperacion(request, slug=None):
    empresa = _resolver_empresa_request(request, slug)
    form = SolicitarRecuperacionForm(request.POST or None)
    enviado = False

    if request.method == "POST" and form.is_valid():
        throttle_key = f"password-recovery:{empresa.pk}:{_client_ip(request)}"
        intentos = cache.get(throttle_key, 0)
        if intentos < 3:
            cache.set(throttle_key, intentos + 1, timeout=15 * 60)
            usuario = Usuario.objects.filter(
                Q(empresa=empresa) | Q(empresas_acceso=empresa),
                email__iexact=form.cleaned_data["email"],
                is_active=True,
            ).distinct().first()
            if usuario:
                token_raw, token = emitir_token_acceso(
                    usuario,
                    TokenAccesoUsuario.TIPO_RECUPERACION,
                    request=request,
                    horas=2,
                )
                try:
                    enviar_correo_acceso(
                        request,
                        usuario,
                        token_raw,
                        token,
                        TokenAccesoUsuario.TIPO_RECUPERACION,
                    )
                except Exception:
                    logger.exception("No se pudo enviar la recuperacion de acceso para el usuario %s", usuario.pk)
        enviado = True

    return render(
        request,
        "core/solicitar_recuperacion.html",
        {"empresa": empresa, "form": form, "enviado": enviado},
    )


def establecer_acceso(request, token_raw):
    token = (
        TokenAccesoUsuario.objects.select_related("usuario", "usuario__empresa")
        .filter(
            token_hash=hash_token_acceso(token_raw),
            revocado=False,
            fecha_uso__isnull=True,
            fecha_expiracion__gt=timezone.now(),
        )
        .first()
    )
    if not token:
        return render(request, "core/establecer_acceso.html", {"token_invalido": True}, status=400)

    usuario = token.usuario
    form = EstablecerAccesoForm(usuario, request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            form.save()
            usuario.is_active = True
            usuario.save(update_fields=["password", "is_active"])
            token.fecha_uso = timezone.now()
            token.save(update_fields=["fecha_uso"])
            TokenAccesoUsuario.objects.filter(
                usuario=usuario,
                fecha_uso__isnull=True,
                revocado=False,
            ).exclude(pk=token.pk).update(
                revocado=True,
                fecha_revocacion=timezone.now(),
            )

        messages.success(request, "Tu contrasena fue creada correctamente. Ya puedes iniciar sesion con tu correo.")
        if usuario.empresa_id:
            return redirect("empresa_login", slug=usuario.empresa.slug)
        return redirect("superadmin_login")

    return render(
        request,
        "core/establecer_acceso.html",
        {"form": form, "usuario_obj": usuario, "token_obj": token},
    )


def _public_site_context(form=None):
    demos_catalogo = list(_public_demo_catalog().values())
    whatsapp_url = _build_whatsapp_link(
        "Hola DV Solutions, quiero informacion sobre una propuesta o una demo del sistema."
    )
    return {
        "public_form": form or SolicitudComercialPublicaForm(),
        "erp_login_url": "/acceso/",
        "control_login_url": "/control/login/",
        "public_whatsapp_url": whatsapp_url,
        "eslogan_principal": "No te tienes que adaptar al sistema, el sistema se adapta a ti.",
        "servicios_destacados": [
            {
                "titulo": "Software a medida",
                "descripcion": "Plataformas internas, sistemas administrativos y herramientas operativas hechas exactamente para el flujo del cliente.",
            },
            {
                "titulo": "Sitios web y portales",
                "descripcion": "Web corporativa, paginas comerciales, portales privados y experiencias digitales que proyectan una marca solida.",
            },
            {
                "titulo": "Aplicaciones moviles",
                "descripcion": "Apps para ventas, supervision, campo, autoservicio o continuidad operativa desde cualquier dispositivo.",
            },
            {
                "titulo": "ERP, automatizacion e integraciones",
                "descripcion": "Conectamos facturacion, contabilidad, CRM, RRHH, procesos internos y servicios externos en una sola arquitectura.",
            },
        ],
        "capacidades_principales": [
            "Analisis funcional y diseno de procesos",
            "UX/UI para software profesional y productos digitales",
            "Dashboards ejecutivos y paneles administrativos",
            "Integraciones con APIs, bancos, WhatsApp y servicios externos",
            "Infraestructura cloud, despliegue y soporte continuo",
            "Automatizacion comercial y operativa",
        ],
        "proceso": [
            "Entendemos la operacion, el cuello de botella y el contexto del negocio.",
            "Disenamos la solucion con enfoque tecnico, visual y comercial.",
            "Construimos, validamos y lanzamos con acompanamiento real.",
            "Escalamos la plataforma contigo segun crecimiento, nuevos modulos o nuevas integraciones.",
        ],
        "demos": demos_catalogo,
        "estadisticas": {
            "clientes_activos": Empresa.objects.filter(activa=True).count(),
            "empresas_prueba": Empresa.objects.filter(estado_licencia="prueba").count(),
            "modulos_comerciales": EmpresaModulo.objects.filter(activo=True).count(),
        },
    }


def public_home(request):
    empresa_host = _empresa_desde_host(request)
    if empresa_host:
        return empresa_login(request, slug=empresa_host.slug)

    form = SolicitudComercialPublicaForm(request.POST or None)
    request_success = request.session.pop("public_request_success", None)
    if request.method == "POST":
        if form.is_valid():
            try:
                solicitud = form.save()
            except OperationalError as exc:
                if "core_solicitudcomercial" in str(exc):
                    logger.exception("La tabla de solicitudes comerciales no existe en la base local.")
                    messages.error(
                        request,
                        "La bandeja comercial aun no esta creada en tu base local. Ejecuta 'python manage.py migrate' y vuelve a intentarlo.",
                    )
                else:
                    logger.exception("No se pudo guardar la solicitud comercial.")
                    messages.error(
                        request,
                        "No pudimos registrar la solicitud por un problema interno. Revisa la base local o vuelve a intentarlo en un momento.",
                    )
            else:
                email_status = _notify_new_commercial_request(solicitud)
                whatsapp_message = (
                    f"Hola DV Solutions, ya envie una solicitud desde la web. "
                    f"Mi nombre es {solicitud.nombre_contacto} y mi empresa es {solicitud.empresa_interesada or 'sin empresa registrada'}."
                )
                request.session["public_request_success"] = {
                    "nombre": solicitud.nombre_contacto,
                    "solicita_prueba": solicitud.solicita_prueba,
                    "email_status": email_status,
                    "whatsapp_url": _build_whatsapp_link(whatsapp_message),
                }
                logger.info("Nueva solicitud comercial registrada: %s", solicitud.id)
                return redirect(f"{reverse('public_home')}#contacto")
        messages.error(request, "Revisa los datos del formulario para poder registrar tu solicitud correctamente.")

    return render(
        request,
        "core/public_home.html",
        {
            **_public_site_context(form=form),
            "request_success": request_success,
        },
    )


def public_access(request):
    empresa_host = _empresa_desde_host(request)
    if empresa_host:
        return empresa_login(request, slug=empresa_host.slug)

    destino_slug = (request.POST.get("slug") or "").strip().strip("/")
    if request.method == "POST" and destino_slug:
        empresa = Empresa.objects.filter(slug=destino_slug, activa=True).first()
        if empresa:
            return redirect("empresa_login", slug=empresa.slug)
        messages.error(request, "No encontramos una empresa activa con ese enlace. Verifica el slug o solicitanos ayuda desde el formulario comercial.")

    return render(request, "core/public_access.html", {
        "erp_login_url": "/acceso/",
        "control_login_url": "/control/login/",
    })


def public_demo_detail(request, demo_slug):
    empresa_host = _empresa_desde_host(request)
    if empresa_host:
        return empresa_login(request, slug=empresa_host.slug)

    demo = _public_demo_catalog().get(demo_slug)
    if not demo:
        raise Http404("No se encontro la demo solicitada.")

    return render(request, "core/public_demo.html", {
        **_public_site_context(),
        "demo": demo,
    })


def dashboard(request, slug=None):
    empresa = _resolver_empresa_request(request, slug)

    if not request.user.is_authenticated:
        return _redirect_login_empresa(request, empresa)

    if not request.user.puede_acceder_empresa(empresa):
        return _redirect_login_empresa(request, empresa)

    if not request.user.is_superuser and not empresa.licencia_operativa:
        messages.error(request, "La licencia comercial de esta empresa no se encuentra operativa. Contactate con el administrador de DV Solutions para revisar la activacion del servicio.")
        logout(request)
        return _redirect_login_empresa(request, empresa)

    modulos_activos = empresa.modulos_habilitados()
    if modo_clinico_simple_activo(request.user, empresa):
        modulos_activos = modulos_activos.filter(codigo__in=["clinica_medica", "facturacion"])

    return render(request, 'core/dashboard_premium.html', {
        'empresa': empresa,
        'modulos': modulos_activos
    })


def empresa_respaldo(request, slug=None):
    empresa = _resolver_empresa_request(request, slug)

    if not request.user.is_authenticated:
        return _redirect_login_empresa(request, empresa)
    if not request.user.is_superuser and not request.user.puede_acceder_empresa(empresa):
        return _redirect_login_empresa(request, empresa)
    if not request.user.is_superuser and not request.user.es_administrador_empresa:
        messages.error(request, "Solo el administrador de la empresa puede descargar respaldos.")
        return _redirect_dashboard_empresa(request, empresa)
    if not request.user.is_superuser and not empresa.licencia_operativa:
        messages.error(
            request,
            "La licencia comercial no se encuentra operativa. Contactate con el administrador de DV Solutions.",
        )
        return _redirect_dashboard_empresa(request, empresa)

    if request.method == "POST":
        if _backup_token_attempts(empresa, request) >= BACKUP_TOKEN_MAX_ATTEMPTS:
            messages.error(
                request,
                "Se alcanzó el limite de intentos. Espera 15 minutos antes de probar otro codigo.",
            )
            return redirect("empresa_respaldo", slug=empresa.slug)

        token_raw = (request.POST.get("token_respaldo") or "").strip()
        token_hash = hash_token_respaldo(token_raw)

        try:
            with transaction.atomic():
                autorizacion = (
                    TokenRespaldoEmpresa.objects.select_for_update()
                    .filter(
                        empresa=empresa,
                        token_hash=token_hash,
                        revocado=False,
                        fecha_uso__isnull=True,
                        fecha_expiracion__gt=timezone.now(),
                    )
                    .first()
                )
                if not autorizacion:
                    _register_backup_token_failure(empresa, request)
                    messages.error(
                        request,
                        "El codigo no es valido, ya fue utilizado o ha vencido.",
                    )
                    return redirect("empresa_respaldo", slug=empresa.slug)

                registro = RespaldoEmpresa.objects.create(
                    empresa=empresa,
                    generado_por=request.user,
                    estado="generando",
                )
                try:
                    resultado = generar_respaldo_empresa(empresa)
                except Exception as exc:
                    logger.exception("No se pudo generar el respaldo autorizado de la empresa %s", empresa.pk)
                    registro.estado = "fallido"
                    registro.detalle_error = str(exc)[:4000]
                    registro.fecha_finalizacion = timezone.now()
                    registro.save(update_fields=["estado", "detalle_error", "fecha_finalizacion"])
                    messages.error(
                        request,
                        "No se pudo preparar el respaldo. El codigo sigue disponible para volver a intentarlo.",
                    )
                    return redirect("empresa_respaldo", slug=empresa.slug)

                registro.estado = "exitoso"
                registro.nombre_archivo = resultado["nombre"]
                registro.registros_incluidos = resultado["registros"]
                registro.archivos_incluidos = resultado["archivos"]
                registro.tamano_bytes = resultado["tamano_bytes"]
                registro.sha256 = resultado["sha256"]
                registro.fecha_finalizacion = timezone.now()
                registro.save(
                    update_fields=[
                        "estado",
                        "nombre_archivo",
                        "registros_incluidos",
                        "archivos_incluidos",
                        "tamano_bytes",
                        "sha256",
                        "fecha_finalizacion",
                    ]
                )

                autorizacion.fecha_uso = timezone.now()
                autorizacion.usado_por = request.user
                autorizacion.save(update_fields=["fecha_uso", "usado_por"])
                _clear_backup_token_failures(empresa, request)
        except Exception:
            logger.exception("Fallo inesperado al validar el token de respaldo de la empresa %s", empresa.pk)
            messages.error(request, "No fue posible validar el codigo en este momento.")
            return redirect("empresa_respaldo", slug=empresa.slug)

        response = FileResponse(
            resultado["archivo"],
            as_attachment=True,
            filename=resultado["nombre"],
            content_type="application/zip",
        )
        response["X-DVSolutions-Backup-SHA256"] = resultado["sha256"]
        return response

    return render(
        request,
        "core/empresa_respaldo.html",
        {
            "empresa": empresa,
            "respaldos_empresa": empresa.respaldos.filter(estado="exitoso").select_related("generado_por")[:10],
        },
    )


@login_required
@require_POST
def asistente_consulta(request, slug=None):
    empresa = _resolver_empresa_request(request, slug)

    if not request.user.is_superuser and not request.user.puede_acceder_empresa(empresa):
        return JsonResponse({"error": "No autorizado para consultar esta empresa."}, status=403)

    from .onix_access import onix_disponible_para_empresa

    if not onix_disponible_para_empresa(empresa):
        return JsonResponse(
            {"error": "Onix esta disponible solamente para las empresas piloto autorizadas."},
            status=403,
        )

    pregunta = (request.POST.get("pregunta") or "").strip()
    pagina = (request.POST.get("pagina") or "").strip()

    if not pregunta:
        return JsonResponse(
            {
                "error": "Escribe una consulta para que el asistente pueda ayudarte.",
            },
            status=400,
        )

    return JsonResponse(
        responder_consulta(
            pregunta,
            pagina,
            empresa=empresa,
            usuario=request.user,
        )
    )


@login_required
@require_POST
def asistente_accion(request, slug, accion_id):
    empresa = _resolver_empresa_request(request, slug)

    if not request.user.is_superuser and not request.user.puede_acceder_empresa(empresa):
        return JsonResponse({"error": "No autorizado para operar en esta empresa."}, status=403)

    from .onix_access import onix_disponible_para_empresa

    if not onix_disponible_para_empresa(empresa):
        return JsonResponse(
            {"error": "Onix esta disponible solamente para las empresas piloto autorizadas."},
            status=403,
        )

    decision = (request.POST.get("decision") or "").strip().lower()
    if decision not in {"confirmar", "cancelar"}:
        return JsonResponse({"error": "Selecciona confirmar o descartar la accion."}, status=400)

    from .onix_actions import cancelar_accion, ejecutar_accion

    try:
        if decision == "confirmar":
            accion = ejecutar_accion(
                accion_id=accion_id,
                empresa=empresa,
                usuario=request.user,
            )
            if accion.get("status") == "expirada":
                return JsonResponse(
                    {
                        "error": accion.get("error") or "La vista previa vencio.",
                        "action": accion,
                    },
                    status=400,
                )
            mensaje = "Factura creada correctamente como borrador."
        else:
            accion = cancelar_accion(
                accion_id=accion_id,
                empresa=empresa,
                usuario=request.user,
            )
            mensaje = "La accion fue descartada; no se creo ninguna factura."
    except PermissionDenied as exc:
        return JsonResponse({"error": str(exc)}, status=403)
    except ValidationError as exc:
        return JsonResponse({"error": " ".join(exc.messages)}, status=400)

    return JsonResponse({"message": mensaje, "action": accion})

from core.models import Modulo


#def modulo_view(request, slug, codigo):
  #  empresa = get_object_or_404(Empresa, slug=slug, activa=True)

  #  if not request.user.is_authenticated:
        #return redirect('empresa_login', slug=slug)

   # if request.user.empresa != empresa:
       # return redirect('empresa_login', slug=slug)

   # modulo = get_object_or_404(Modulo, codigo=codigo)

   # return render(request, 'core/modulo_base.html', {
        #'empresa': empresa,
       # 'modulo': modulo
  #  })
def cerrar_sesion(request, slug=None):
    empresa = _resolver_empresa_request(request, slug)
    logout(request)
    return _redirect_login_empresa(request, empresa)


def _superadmin_base_context():
    return {
        "superadmin_dashboard_url": "/control/",
        "superadmin_empresas_url": "/control/empresas/",
        "superadmin_usuarios_url": "/control/usuarios/",
        "superadmin_planes_url": "/control/planes/",
        "superadmin_roles_url": "/control/roles/",
        "superadmin_modulos_url": "/control/modulos/",
        "superadmin_licencias_url": "/control/licencias/",
        "superadmin_solicitudes_url": "/control/solicitudes/",
        "superadmin_respaldos_url": "/control/respaldos/",
        "superadmin_auditoria_url": "/control/auditoria/",
        "enable_django_admin": settings.ENABLE_DJANGO_ADMIN,
        "django_admin_url": f"/{settings.DJANGO_ADMIN_PATH.strip('/')}/" if settings.ENABLE_DJANGO_ADMIN else None,
    }


def _aplicar_filtros_auditoria(queryset, request):
    q = (request.GET.get("q") or "").strip()
    accion = (request.GET.get("accion") or "").strip()
    modulo = (request.GET.get("modulo") or "").strip()
    usuario_id = (request.GET.get("usuario") or "").strip()
    desde = parse_date((request.GET.get("desde") or "").strip())
    hasta = parse_date((request.GET.get("hasta") or "").strip())
    if q:
        queryset = queryset.filter(
            Q(objeto_representacion__icontains=q)
            | Q(objeto_id__icontains=q)
            | Q(modelo__icontains=q)
            | Q(motivo__icontains=q)
            | Q(usuario__username__icontains=q)
            | Q(usuario__email__icontains=q)
        )
    if accion:
        queryset = queryset.filter(accion=accion)
    if modulo:
        queryset = queryset.filter(modulo=modulo)
    if usuario_id.isdigit():
        queryset = queryset.filter(usuario_id=int(usuario_id))
    if desde:
        queryset = queryset.filter(fecha__date__gte=desde)
    if hasta:
        queryset = queryset.filter(fecha__date__lte=hasta)
    return queryset


def _contexto_filtros_auditoria(queryset_base, request):
    return {
        "acciones": RegistroAuditoria.ACCION_CHOICES,
        "modulos": queryset_base.order_by("modulo").values_list("modulo", flat=True).distinct(),
        "usuarios_auditoria": Usuario.objects.filter(
            id__in=queryset_base.exclude(usuario_id=None).values_list("usuario_id", flat=True)
        ).order_by("username"),
        "filtros": {
            "q": (request.GET.get("q") or "").strip(),
            "accion": (request.GET.get("accion") or "").strip(),
            "modulo": (request.GET.get("modulo") or "").strip(),
            "usuario": (request.GET.get("usuario") or "").strip(),
            "desde": (request.GET.get("desde") or "").strip(),
            "hasta": (request.GET.get("hasta") or "").strip(),
        },
    }


@login_required
def usuarios_clinicos(request, slug):
    empresa = _resolver_empresa_request(request, slug)
    if not _puede_administrar_usuarios_clinicos(request.user, empresa):
        return JsonResponse({"error": "No tiene permiso para administrar usuarios de esta empresa."}, status=403)

    usuarios = (
        _usuarios_operativos_de_empresa(empresa)
        .select_related("rol_sistema")
        .prefetch_related("permisos_por_empresa__rol_sistema")
        .order_by("first_name", "last_name", "username")
    )
    filas = []
    for usuario in usuarios:
        rol = usuario.rol_para_empresa(empresa)
        filas.append({
            "usuario": usuario,
            "rol": rol,
            "permisos": _permisos_visibles_rol(rol),
            "total_permisos": sum(
                1
                for _, permisos in PERMISOS_ROL_CLINICO
                for campo, _ in permisos
                if rol and getattr(rol, campo, False)
            ),
        })

    return render(request, "core/usuarios_clinicos.html", {
        "empresa": empresa,
        "filas": filas,
        "total_usuarios": len(filas),
    })


@login_required
def usuario_clinico_permisos(request, slug, usuario_id):
    empresa = _resolver_empresa_request(request, slug)
    if not _puede_administrar_usuarios_clinicos(request.user, empresa):
        return JsonResponse({"error": "No tiene permiso para administrar usuarios de esta empresa."}, status=403)

    # El filtro empresarial también protege GET y POST. No se acepta un ID de
    # usuario que pertenezca a otra empresa aunque se manipule la URL/formulario.
    usuario = get_object_or_404(_usuarios_operativos_de_empresa(empresa), pk=usuario_id)
    rol_actual = usuario.rol_para_empresa(empresa)

    if request.method == "POST":
        valores = {
            campo: request.POST.get(campo) == "1"
            for _, permisos in PERMISOS_ROL_CLINICO
            for campo, _ in permisos
        }
        codigo_rol = f"clinico-{empresa.pk}-{usuario.pk}"
        nombre_usuario = usuario.get_full_name().strip() or usuario.username
        with transaction.atomic():
            rol_personalizado, _ = RolSistema.objects.update_or_create(
                codigo=codigo_rol,
                defaults={
                    "nombre": f"Permisos de {nombre_usuario} - {empresa.nombre}"[:120],
                    "descripcion": (
                        f"Configuración individual de {nombre_usuario} para {empresa.nombre}."
                    ),
                    "activo": True,
                    **valores,
                },
            )
            UsuarioEmpresaPermiso.objects.update_or_create(
                usuario=usuario,
                empresa=empresa,
                defaults={"rol_sistema": rol_personalizado, "activo": True},
            )
        messages.success(
            request,
            f"Los permisos de {nombre_usuario} se actualizaron solamente para {empresa.nombre}.",
        )
        return redirect("usuarios_clinicos", slug=empresa.slug)

    grupos = []
    total_permisos_activos = 0
    total_permisos_disponibles = 0
    for indice, (grupo, permisos) in enumerate(PERMISOS_ROL_CLINICO):
        meta = MODULOS_PERMISOS_PRESENTACION[indice]
        categorias = {}
        permisos_modulo = []
        for campo, etiqueta in permisos:
            permiso = {
                "campo": campo,
                "etiqueta": etiqueta,
                "activo": bool(rol_actual and getattr(rol_actual, campo, False)),
            }
            categoria = CATEGORIAS_PERMISOS_CLINICOS.get(campo, "Funciones del módulo")
            categorias.setdefault(categoria, []).append(permiso)
            permisos_modulo.append(permiso)

        activos = sum(1 for permiso in permisos_modulo if permiso["activo"])
        total_permisos_activos += activos
        total_permisos_disponibles += len(permisos_modulo)
        grupos.append({
            "nombre": grupo,
            "codigo": meta["codigo"],
            "descripcion": meta["descripcion"],
            "permisos": permisos_modulo,
            "categorias": [
                {"nombre": nombre, "permisos": permisos_categoria}
                for nombre, permisos_categoria in categorias.items()
            ],
            "activos": activos,
            "total": len(permisos_modulo),
        })

    return render(request, "core/usuario_clinico_permisos.html", {
        "empresa": empresa,
        "usuario_gestionado": usuario,
        "rol_actual": rol_actual,
        "grupos": grupos,
        "total_permisos_activos": total_permisos_activos,
        "total_permisos_disponibles": total_permisos_disponibles,
        "total_modulos_activos": sum(1 for grupo in grupos if grupo["activos"]),
    })


@login_required
def auditoria_empresa(request, slug):
    empresa = _resolver_empresa_request(request, slug)
    if not request.user.is_superuser and (
        not request.user.puede_acceder_empresa(empresa) or not request.user.es_administrador_empresa
    ):
        return JsonResponse({"error": "Solo el administrador de la empresa puede consultar la bitacora."}, status=403)
    base = RegistroAuditoria.objects.filter(empresa=empresa).select_related("usuario")
    registros = _aplicar_filtros_auditoria(base, request)
    page = Paginator(registros, 50).get_page(request.GET.get("page"))
    context = {
        "empresa": empresa,
        "registros": page,
        "total_registros": registros.count(),
        **_contexto_filtros_auditoria(base, request),
    }
    return render(request, "core/auditoria_empresa.html", context)


@login_required
def auditoria_objeto(request, slug, app_label, modelo, objeto_id):
    empresa = _resolver_empresa_request(request, slug)
    if not request.user.is_superuser and (
        not request.user.puede_acceder_empresa(empresa) or not request.user.es_administrador_empresa
    ):
        return JsonResponse({"error": "No autorizado."}, status=403)
    registros = RegistroAuditoria.objects.filter(
        empresa=empresa,
        app_label=app_label,
        modelo=modelo,
        objeto_id=str(objeto_id),
    ).select_related("usuario")
    return render(request, "core/auditoria_objeto.html", {
        "empresa": empresa,
        "registros": registros,
        "objeto_titulo": registros.first().objeto_representacion if registros.exists() else f"{modelo} #{objeto_id}",
    })


@login_required(login_url="/control/login/")
def superadmin_auditoria(request):
    if not request.user.is_superuser:
        return redirect("superadmin_login")
    base = RegistroAuditoria.objects.select_related("empresa", "usuario")
    empresa_id = (request.GET.get("empresa") or "").strip()
    if empresa_id.isdigit():
        base_filtrada = base.filter(empresa_id=int(empresa_id))
    else:
        base_filtrada = base
    registros = _aplicar_filtros_auditoria(base_filtrada, request)
    page = Paginator(registros, 75).get_page(request.GET.get("page"))
    context = {
        **_superadmin_base_context(),
        "registros": page,
        "total_registros": registros.count(),
        "empresas": Empresa.objects.order_by("nombre"),
        "empresa_seleccionada": empresa_id,
        **_contexto_filtros_auditoria(base_filtrada, request),
    }
    return render(request, "core/superadmin_auditoria.html", context)


def _enriquecer_empresa(empresa):
    empresa.modulos_habilitados_lista = list(empresa.modulos_habilitados())
    empresa.modulos_habilitados_preview = empresa.modulos_habilitados_lista[:4]
    empresa.modulos_habilitados_total = len(empresa.modulos_habilitados_lista)
    empresa.usuarios_relacionados = list(
        Usuario.objects.filter(Q(empresa=empresa) | Q(empresas_acceso=empresa))
        .select_related("rol_sistema")
        .distinct()
        .order_by("username")
    )
    empresa.usuarios_preview = empresa.usuarios_relacionados[:4]
    empresa.usuarios_total = len(empresa.usuarios_relacionados)
    empresa.pagos_licencia_recientes = list(empresa.pagos_licencia.select_related("plan_comercial")[:5])
    empresa.pagos_licencia_total = empresa.pagos_licencia.count()
    empresa.estado_licencia_resuelto = empresa.estado_licencia_actual
    empresa.licencia_operativa_flag = empresa.licencia_operativa
    if empresa.fecha_vencimiento_plan:
        empresa.dias_restantes_plan = (empresa.fecha_vencimiento_plan - timezone.localdate()).days
    else:
        empresa.dias_restantes_plan = None
    return empresa


def _matriz_modulos_empresa(empresa):
    modulos_catalogo = list(Modulo.objects.filter(es_comercial=True).order_by("nombre"))
    modulos_plan_ids = set()
    if empresa.plan_comercial_id:
        modulos_plan_ids = set(
            PlanModulo.objects.filter(plan=empresa.plan_comercial, activo=True).values_list("modulo_id", flat=True)
        )
    modulos_manual_ids = set(
        EmpresaModulo.objects.filter(empresa=empresa, activo=True).values_list("modulo_id", flat=True)
    )

    resultado = []
    for modulo in modulos_catalogo:
        incluido_plan = modulo.id in modulos_plan_ids
        activo_manual = modulo.id in modulos_manual_ids
        activo_total = incluido_plan or activo_manual
        if incluido_plan and activo_manual:
            origen = "Incluido en plan y ajuste manual"
        elif incluido_plan:
            origen = "Incluido en el plan"
        elif activo_manual:
            origen = "Activado manualmente"
        else:
            origen = "No contratado"
        resultado.append({
            "modulo": modulo,
            "incluido_plan": incluido_plan,
            "activo_manual": activo_manual,
            "activo_total": activo_total,
            "origen": origen,
        })
    return resultado


def superadmin_required(view_func):
    @wraps(view_func)
    @login_required(login_url="/control/login/")
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_superuser:
            messages.error(request, "Este panel privado es exclusivo para superadministradores.")
            return redirect("superadmin_login")
        return view_func(request, *args, **kwargs)

    return _wrapped_view


def superadmin_login(request):
    _flash_session_expired_message(request)
    if request.user.is_authenticated and request.user.is_superuser:
        return redirect("superadmin_dashboard")

    form = SuperAdminLoginForm(request=request, data=request.POST or None)
    if request.method == "POST":
        bloqueo_restante = _login_block_seconds("superadmin", request)
        if bloqueo_restante > 0:
            messages.error(
                request,
                f"Por seguridad bloqueamos temporalmente el acceso maestro. Intenta nuevamente en {_minutes_remaining(bloqueo_restante)} minuto(s).",
            )
        elif form.is_valid():
            _clear_login_failures("superadmin", request)
            login(request, form.get_user())
            return redirect("superadmin_dashboard")
        else:
            bloqueo_restante = _register_login_failure("superadmin", request)
            if bloqueo_restante > 0:
                messages.error(
                    request,
                    f"Por seguridad bloqueamos temporalmente el acceso maestro. Intenta nuevamente en {_minutes_remaining(bloqueo_restante)} minuto(s).",
                )

    return render(request, "core/superadmin_login.html", {"form": form})


def superadmin_logout(request):
    logout(request)
    return redirect("superadmin_login")


@superadmin_required
def superadmin_dashboard(request):
    todas_empresas = list(
        Empresa.objects.annotate(
            usuarios_count=Count("usuario", distinct=True),
            modulos_activos_count=Count("empresamodulo", filter=Q(empresamodulo__activo=True), distinct=True),
        ).order_by("-fecha_creacion")
    )
    todas_empresas = [_enriquecer_empresa(empresa) for empresa in todas_empresas]
    empresas = todas_empresas[:6]
    context = {
        **_superadmin_base_context(),
        "total_empresas": len(todas_empresas),
        "total_empresas_activas": Empresa.objects.filter(activa=True).count(),
        "total_empresas_inactivas": Empresa.objects.filter(activa=False).count(),
        "total_usuarios": Usuario.objects.count(),
        "total_admin_empresa": Usuario.objects.filter(es_administrador_empresa=True).count(),
        "total_modulos_activos": EmpresaModulo.objects.filter(activo=True).count(),
        "total_planes": PlanComercial.objects.filter(activo=True).count(),
        "total_roles": RolSistema.objects.filter(activo=True).count(),
        "total_licencias_operativas": sum(1 for empresa in todas_empresas if empresa.licencia_operativa_flag),
        "total_licencias_vencidas": Empresa.objects.filter(fecha_vencimiento_plan__lt=timezone.localdate()).count(),
        "total_licencias_prueba": Empresa.objects.filter(estado_licencia="prueba").count(),
        "total_solicitudes_comerciales": SolicitudComercial.objects.count(),
        "total_solicitudes_prueba": SolicitudComercial.objects.filter(solicita_prueba=True).count(),
        "solicitudes_recientes": SolicitudComercial.objects.all()[:5],
        "empresas_recientes": empresas,
    }
    return render(request, "core/superadmin_dashboard.html", context)


@superadmin_required
def superadmin_empresas(request):
    empresas = Empresa.objects.annotate(
        usuarios_count=Count("usuario", distinct=True),
        modulos_activos_count=Count("empresamodulo", filter=Q(empresamodulo__activo=True), distinct=True),
    ).order_by("nombre")
    empresas = [_enriquecer_empresa(empresa) for empresa in empresas]
    context = {
        **_superadmin_base_context(),
        "empresas": empresas,
        "resumen": {
            "total": len(empresas),
            "activas": sum(1 for empresa in empresas if empresa.activa),
            "inactivas": sum(1 for empresa in empresas if not empresa.activa),
            "modulos": EmpresaModulo.objects.filter(activo=True).count(),
            "prueba": sum(1 for empresa in empresas if empresa.estado_licencia_resuelto == "prueba"),
            "vencidas": sum(1 for empresa in empresas if empresa.estado_licencia_resuelto == "vencida"),
        },
    }
    return render(request, "core/superadmin_empresas.html", context)


@superadmin_required
def superadmin_empresa_detail(request, empresa_id):
    empresa = _enriquecer_empresa(get_object_or_404(Empresa, id=empresa_id))
    matriz_modulos = _matriz_modulos_empresa(empresa)
    context = {
        **_superadmin_base_context(),
        "empresa_obj": empresa,
        "usuarios_empresa": empresa.usuarios_relacionados,
        "modulos_empresa": empresa.modulos_habilitados_lista,
        "matriz_modulos": matriz_modulos,
        "pagos_licencia": empresa.pagos_licencia.select_related("plan_comercial"),
        "respaldos_empresa": empresa.respaldos.select_related("generado_por")[:8],
        "tokens_respaldo": empresa.tokens_respaldo.select_related("creado_por", "usado_por")[:8],
    }
    return render(request, "core/superadmin_empresa_detalle.html", context)


@superadmin_required
def superadmin_respaldos(request):
    empresas = Empresa.objects.order_by("nombre")
    respaldos = RespaldoEmpresa.objects.select_related("empresa", "generado_por")[:100]
    context = {
        **_superadmin_base_context(),
        "empresas": empresas,
        "respaldos": respaldos,
        "tokens_respaldo": TokenRespaldoEmpresa.objects.select_related(
            "empresa", "creado_por", "usado_por"
        )[:100],
        "resumen": {
            "total": RespaldoEmpresa.objects.count(),
            "exitosos": RespaldoEmpresa.objects.filter(estado="exitoso").count(),
            "fallidos": RespaldoEmpresa.objects.filter(estado="fallido").count(),
            "empresas_respaldadas": RespaldoEmpresa.objects.filter(estado="exitoso")
            .values("empresa_id")
            .distinct()
            .count(),
        },
    }
    return render(request, "core/superadmin_respaldos.html", context)


@superadmin_required
@require_POST
def superadmin_empresa_generar_token_respaldo(request, empresa_id):
    empresa = get_object_or_404(Empresa, id=empresa_id)
    try:
        horas_vigencia = int(request.POST.get("horas_vigencia") or 24)
    except (TypeError, ValueError):
        horas_vigencia = 24
    horas_vigencia = min(max(horas_vigencia, 1), 168)
    referencia_pago = (request.POST.get("referencia_pago") or "").strip()[:160]
    ahora = timezone.now()

    token_raw, token_hash, token_preview = generar_token_respaldo()
    with transaction.atomic():
        empresa.tokens_respaldo.filter(
            revocado=False,
            fecha_uso__isnull=True,
        ).update(revocado=True, fecha_revocacion=ahora)
        autorizacion = TokenRespaldoEmpresa.objects.create(
            empresa=empresa,
            token_hash=token_hash,
            token_preview=token_preview,
            creado_por=request.user,
            referencia_pago=referencia_pago,
            fecha_expiracion=ahora + timezone.timedelta(hours=horas_vigencia),
        )

    return render(
        request,
        "core/superadmin_respaldo_token.html",
        {
            **_superadmin_base_context(),
            "empresa_obj": empresa,
            "token_respaldo": token_raw,
            "autorizacion": autorizacion,
        },
    )


@superadmin_required
@require_POST
def superadmin_empresa_generar_respaldo(request, empresa_id):
    empresa = get_object_or_404(Empresa, id=empresa_id)
    registro = RespaldoEmpresa.objects.create(
        empresa=empresa,
        generado_por=request.user,
        estado="generando",
    )
    try:
        resultado = generar_respaldo_empresa(empresa)
    except Exception as exc:
        logger.exception("No se pudo generar el respaldo de la empresa %s", empresa.pk)
        registro.estado = "fallido"
        registro.detalle_error = str(exc)[:4000]
        registro.fecha_finalizacion = timezone.now()
        registro.save(update_fields=["estado", "detalle_error", "fecha_finalizacion"])
        messages.error(request, f"No se pudo generar el respaldo de {empresa.nombre}. Revisa el registro tecnico.")
        return redirect("superadmin_respaldos")

    registro.estado = "exitoso"
    registro.nombre_archivo = resultado["nombre"]
    registro.registros_incluidos = resultado["registros"]
    registro.archivos_incluidos = resultado["archivos"]
    registro.tamano_bytes = resultado["tamano_bytes"]
    registro.sha256 = resultado["sha256"]
    registro.fecha_finalizacion = timezone.now()
    registro.save(
        update_fields=[
            "estado",
            "nombre_archivo",
            "registros_incluidos",
            "archivos_incluidos",
            "tamano_bytes",
            "sha256",
            "fecha_finalizacion",
        ]
    )

    response = FileResponse(
        resultado["archivo"],
        as_attachment=True,
        filename=resultado["nombre"],
        content_type="application/zip",
    )
    response["X-DVSolutions-Backup-SHA256"] = resultado["sha256"]
    return response


@superadmin_required
def superadmin_empresa_create(request):
    form = EmpresaControlForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        empresa = form.save()
        messages.success(request, f"Empresa {empresa.nombre} creada correctamente.")
        return redirect("superadmin_empresas")

    return render(request, "core/superadmin_empresa_form.html", {
        **_superadmin_base_context(),
        "form": form,
        "titulo": "Nueva Empresa",
    })


@superadmin_required
def superadmin_empresa_edit(request, empresa_id):
    empresa = get_object_or_404(Empresa, id=empresa_id)
    form = EmpresaControlForm(request.POST or None, request.FILES or None, instance=empresa)
    if request.method == "POST" and form.is_valid():
        empresa = form.save()
        messages.success(request, f"Empresa {empresa.nombre} actualizada correctamente.")
        return redirect("superadmin_empresas")

    return render(request, "core/superadmin_empresa_form.html", {
        **_superadmin_base_context(),
        "form": form,
        "titulo": f"Editar Empresa: {empresa.nombre}",
    })


@superadmin_required
def superadmin_usuarios(request):
    estado = (request.GET.get("estado") or "operativos").strip().lower()
    if estado not in {"operativos", "retirados", "todos"}:
        estado = "operativos"

    usuarios_base = Usuario.objects.select_related("empresa", "rol_sistema").prefetch_related(
        "groups",
        "empresas_acceso",
        Prefetch(
            "tokens_acceso",
            queryset=TokenAccesoUsuario.objects.filter(
                tipo=TokenAccesoUsuario.TIPO_INVITACION
            ).order_by("-fecha_creacion"),
            to_attr="invitaciones_recientes",
        ),
    )
    if estado == "retirados":
        usuarios = usuarios_base.filter(retirado_control=True)
    elif estado == "todos":
        usuarios = usuarios_base
    else:
        usuarios = usuarios_base.filter(retirado_control=False)
    usuarios = usuarios.order_by("email", "username")

    resumen_base = Usuario.objects.all()
    context = {
        **_superadmin_base_context(),
        "usuarios": usuarios,
        "estado_seleccionado": estado,
        "resumen": {
            "total": resumen_base.filter(retirado_control=False).count(),
            "superadmins": resumen_base.filter(is_superuser=True, retirado_control=False).count(),
            "admins_empresa": resumen_base.filter(es_administrador_empresa=True, retirado_control=False).count(),
            "activos": resumen_base.filter(is_active=True, retirado_control=False).count(),
            "retirados": resumen_base.filter(retirado_control=True).count(),
        },
    }
    return render(request, "core/superadmin_usuarios.html", context)


@superadmin_required
def superadmin_planes(request):
    planes = PlanComercial.objects.annotate(
        modulos_count=Count("planmodulo", filter=Q(planmodulo__activo=True), distinct=True),
        empresas_count=Count("empresas", distinct=True),
    ).order_by("nombre")
    context = {
        **_superadmin_base_context(),
        "planes": planes,
    }
    return render(request, "core/superadmin_planes.html", context)


@superadmin_required
def superadmin_plan_create(request):
    form = PlanComercialForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        plan = form.save()
        messages.success(request, f"Plan {plan.nombre} creado correctamente.")
        return redirect("superadmin_planes")

    return render(request, "core/superadmin_plan_form.html", {
        **_superadmin_base_context(),
        "form": form,
        "titulo": "Nuevo Plan Comercial",
    })


@superadmin_required
def superadmin_plan_edit(request, plan_id):
    plan = get_object_or_404(PlanComercial, id=plan_id)
    form = PlanComercialForm(request.POST or None, instance=plan)
    if request.method == "POST" and form.is_valid():
        plan = form.save()
        messages.success(request, f"Plan {plan.nombre} actualizado correctamente.")
        return redirect("superadmin_planes")

    return render(request, "core/superadmin_plan_form.html", {
        **_superadmin_base_context(),
        "form": form,
        "titulo": f"Editar Plan: {plan.nombre}",
    })


@superadmin_required
def superadmin_roles(request):
    roles = RolSistema.objects.order_by("nombre")
    context = {
        **_superadmin_base_context(),
        "roles": roles,
    }
    return render(request, "core/superadmin_roles.html", context)


@superadmin_required
def superadmin_modulos(request):
    modulos = []
    for modulo in Modulo.objects.filter(es_comercial=True).order_by("nombre"):
        modulos.append({
            "modulo": modulo,
            "planes_count": PlanModulo.objects.filter(modulo=modulo, activo=True).values("plan_id").distinct().count(),
            "empresas_count": EmpresaModulo.objects.filter(modulo=modulo, activo=True).values("empresa_id").distinct().count(),
        })

    return render(request, "core/superadmin_modulos.html", {
        **_superadmin_base_context(),
        "modulos": modulos,
    })


@superadmin_required
def superadmin_licencias(request):
    pagos = PagoLicenciaEmpresa.objects.select_related("empresa", "plan_comercial").order_by("-fecha_pago", "-id")
    empresas = [_enriquecer_empresa(empresa) for empresa in Empresa.objects.order_by("nombre")]
    return render(request, "core/superadmin_licencias.html", {
        **_superadmin_base_context(),
        "pagos": pagos[:30],
        "empresas": empresas,
        "resumen": {
            "operativas": sum(1 for empresa in empresas if empresa.licencia_operativa_flag),
            "prueba": sum(1 for empresa in empresas if empresa.estado_licencia_resuelto == "prueba"),
            "suspendidas": sum(1 for empresa in empresas if empresa.estado_licencia_resuelto == "suspendida"),
            "vencidas": sum(1 for empresa in empresas if empresa.estado_licencia_resuelto == "vencida"),
        },
    })


@superadmin_required
def superadmin_solicitudes(request):
    solicitudes = SolicitudComercial.objects.all()
    return render(request, "core/superadmin_solicitudes.html", {
        **_superadmin_base_context(),
        "solicitudes": solicitudes,
        "resumen": {
            "total": solicitudes.count(),
            "nuevas": solicitudes.filter(estado="nueva").count(),
            "prueba": solicitudes.filter(solicita_prueba=True).count(),
            "demo": solicitudes.filter(estado="demo").count(),
        },
    })


@superadmin_required
def superadmin_empresa_registrar_pago_licencia(request, empresa_id):
    empresa = _enriquecer_empresa(get_object_or_404(Empresa, id=empresa_id))
    form = PagoLicenciaEmpresaForm(request.POST or None, empresa=empresa)
    if request.method == "POST" and form.is_valid():
        pago = form.save(commit=False)
        pago.empresa = empresa
        if not pago.plan_comercial_id:
            pago.plan_comercial = empresa.plan_comercial
        pago.save()
        if pago.plan_comercial_id and empresa.plan_comercial_id != pago.plan_comercial_id:
            empresa.plan_comercial = pago.plan_comercial
            empresa.save(update_fields=["plan_comercial"])
        empresa.aplicar_pago_licencia(pago)
        messages.success(request, f"Pago de licencia registrado para {empresa.nombre}. La empresa quedo activa hasta {empresa.fecha_vencimiento_plan}.")
        return redirect("superadmin_empresa_detail", empresa_id=empresa.id)

    return render(request, "core/superadmin_licencia_pago_form.html", {
        **_superadmin_base_context(),
        "form": form,
        "empresa_obj": empresa,
        "titulo": f"Registrar pago de licencia: {empresa.nombre}",
    })


@superadmin_required
@require_POST
def superadmin_empresa_suspender_licencia(request, empresa_id):
    empresa = get_object_or_404(Empresa, id=empresa_id)
    empresa.suspender_licencia()
    messages.warning(request, f"La empresa {empresa.nombre} fue suspendida. Su informacion se conserva, pero queda bloqueada para operar.")
    return redirect("superadmin_empresa_detail", empresa_id=empresa.id)


@superadmin_required
@require_POST
def superadmin_empresa_activar_licencia(request, empresa_id):
    empresa = get_object_or_404(Empresa, id=empresa_id)
    if empresa.activar_licencia_manual():
        messages.success(request, f"La empresa {empresa.nombre} fue activada nuevamente con su vigencia actual.")
    else:
        messages.error(request, "No se pudo activar manualmente porque la licencia ya esta vencida. Usa Renovar para registrar un pago y extender la vigencia.")
    return redirect("superadmin_empresa_detail", empresa_id=empresa.id)


@superadmin_required
def superadmin_rol_create(request):
    form = RolSistemaForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        rol = form.save()
        messages.success(request, f"Rol {rol.nombre} creado correctamente.")
        return redirect("superadmin_roles")

    return render(request, "core/superadmin_rol_form.html", {
        **_superadmin_base_context(),
        "form": form,
        "titulo": "Nuevo Rol",
    })


@superadmin_required
def superadmin_rol_edit(request, rol_id):
    rol = get_object_or_404(RolSistema, id=rol_id)
    form = RolSistemaForm(request.POST or None, instance=rol)
    if request.method == "POST" and form.is_valid():
        rol = form.save()
        messages.success(request, f"Rol {rol.nombre} actualizado correctamente.")
        return redirect("superadmin_roles")

    return render(request, "core/superadmin_rol_form.html", {
        **_superadmin_base_context(),
        "form": form,
        "titulo": f"Editar Rol: {rol.nombre}",
    })


@superadmin_required
def superadmin_usuario_create(request):
    form = UsuarioControlCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        usuario = form.save()
        _guardar_roles_por_empresa(usuario, request.POST)
        if form.cleaned_data["modo_creacion"] == UsuarioControlCreateForm.MODO_RAPIDO:
            messages.success(
                request,
                f"Usuario {usuario.email} creado y activado. Ya puede iniciar sesion con la contrasena asignada.",
            )
            return redirect("superadmin_usuarios")

        token_raw, token = emitir_token_acceso(
            usuario,
            TokenAccesoUsuario.TIPO_INVITACION,
            creado_por=request.user,
            request=request,
            horas=48,
        )
        try:
            enviar_correo_acceso(
                request,
                usuario,
                token_raw,
                token,
                TokenAccesoUsuario.TIPO_INVITACION,
            )
        except Exception:
            logger.exception("No se pudo enviar la invitacion del usuario %s", usuario.pk)
            messages.warning(
                request,
                f"El usuario fue creado, pero no se pudo enviar el correo a {usuario.email}. Puedes reenviarlo desde Usuarios.",
            )
        else:
            messages.success(
                request,
                f"Invitacion enviada a {usuario.email}. El enlace sera valido durante 48 horas.",
            )
        return redirect("superadmin_usuarios")

    return render(request, "core/superadmin_usuario_form.html", {
        **_superadmin_base_context(),
        "form": form,
        "titulo": "Nuevo Usuario",
        **_usuario_empresas_config(form, request),
    })


@superadmin_required
def superadmin_usuario_edit(request, usuario_id):
    usuario = get_object_or_404(Usuario, id=usuario_id)
    email_anterior = usuario.email
    form = UsuarioControlUpdateForm(request.POST or None, instance=usuario)
    if request.method == "POST" and form.is_valid():
        usuario = form.save()
        _guardar_roles_por_empresa(usuario, request.POST)
        if not usuario.is_active and email_anterior.lower() != usuario.email.lower():
            TokenAccesoUsuario.objects.filter(
                usuario=usuario,
                fecha_uso__isnull=True,
                revocado=False,
            ).update(revocado=True, fecha_revocacion=timezone.now())
            messages.warning(
                request,
                "El correo fue actualizado. Reenvia la invitacion para entregar un enlace valido a la nueva direccion.",
            )
        messages.success(request, f"Usuario {usuario.username} actualizado correctamente.")
        return redirect("superadmin_usuarios")

    return render(request, "core/superadmin_usuario_form.html", {
        **_superadmin_base_context(),
        "form": form,
        "titulo": f"Editar Usuario: {usuario.username}",
        **_usuario_empresas_config(form, request),
    })


@superadmin_required
@require_POST
def superadmin_usuario_reset_password_temporal(request, usuario_id):
    usuario = get_object_or_404(Usuario, id=usuario_id)
    if usuario.pk == request.user.pk:
        messages.error(
            request,
            "Por seguridad no puedes generar una contrasena temporal para tu propia sesion desde aqui.",
        )
        return redirect("superadmin_usuario_edit", usuario_id=usuario.id)

    password_temporal = f"DVS-{timezone.now():%y%m}-{get_random_string(8, allowed_chars='ABCDEFGHJKLMNPQRSTUVWXYZ23456789')}"
    usuario.set_password(password_temporal)
    usuario.is_active = True
    usuario.save(update_fields=["password", "is_active"])
    TokenAccesoUsuario.objects.filter(
        usuario=usuario,
        fecha_uso__isnull=True,
        revocado=False,
    ).update(revocado=True, fecha_revocacion=timezone.now())
    messages.success(
        request,
        (
            f"Contrasena temporal generada para {usuario.email or usuario.username}: "
            f"{password_temporal} | Copiala ahora; por seguridad no se volvera a mostrar."
        ),
    )
    return redirect("superadmin_usuario_edit", usuario_id=usuario.id)


@superadmin_required
def superadmin_usuario_delete(request, usuario_id):
    usuario = get_object_or_404(Usuario.objects.select_related("empresa", "rol_sistema"), id=usuario_id)

    if request.method == "POST":
        motivo = (request.POST.get("motivo_eliminacion") or "").strip()
        if usuario.pk == request.user.pk:
            messages.error(request, "No puedes eliminar el usuario con el que tienes abierta la sesion.")
            return redirect("superadmin_usuarios")
        if len(motivo) < 5:
            messages.error(request, "Explica el motivo de la eliminacion con al menos 5 caracteres.")
        elif (
            usuario.empresa_id
            and usuario.es_administrador_empresa
            and usuario.is_active
            and not Usuario.objects.filter(
                empresa_id=usuario.empresa_id,
                es_administrador_empresa=True,
                is_active=True,
            ).exclude(pk=usuario.pk).exists()
        ):
            messages.error(
                request,
                "No puedes eliminar el ultimo administrador activo de la empresa. Asigna otro administrador primero.",
            )
        else:
            identificacion = usuario.email or usuario.username
            with transaction.atomic():
                usuario.is_active = False
                usuario.retirado_control = True
                usuario.fecha_retiro_control = timezone.now()
                usuario.motivo_retiro_control = motivo
                usuario.save(update_fields=[
                    "is_active",
                    "retirado_control",
                    "fecha_retiro_control",
                    "motivo_retiro_control",
                ])
                TokenAccesoUsuario.objects.filter(
                    usuario=usuario,
                    fecha_uso__isnull=True,
                    revocado=False,
                ).update(revocado=True, fecha_revocacion=timezone.now())
            messages.success(
                request,
                f"Usuario {identificacion} retirado del control activo. Su historial permanece intacto.",
            )
            return redirect("superadmin_usuarios")

    return render(request, "core/superadmin_usuario_confirm_delete.html", {
        **_superadmin_base_context(),
        "usuario_obj": usuario,
    })


@superadmin_required
@require_POST
def superadmin_usuario_restore(request, usuario_id):
    usuario = get_object_or_404(Usuario, id=usuario_id, retirado_control=True)
    usuario.is_active = True
    usuario.retirado_control = False
    usuario.fecha_retiro_control = None
    usuario.motivo_retiro_control = ""
    usuario.save(update_fields=[
        "is_active",
        "retirado_control",
        "fecha_retiro_control",
        "motivo_retiro_control",
    ])
    messages.success(
        request,
        f"Usuario {usuario.email or usuario.username} restaurado y habilitado correctamente.",
    )
    return redirect(f"{reverse('superadmin_usuarios')}?estado=retirados")


@superadmin_required
@require_POST
def superadmin_usuario_reenviar_invitacion(request, usuario_id):
    usuario = get_object_or_404(Usuario, id=usuario_id)
    if not usuario.email:
        messages.error(request, "El usuario necesita un correo antes de poder recibir una invitacion.")
        return redirect("superadmin_usuario_edit", usuario_id=usuario.id)
    if usuario.is_active and usuario.has_usable_password():
        messages.error(
            request,
            "Este usuario ya activo debe utilizar la opcion 'Olvide mi contrasena' desde el login de su empresa.",
        )
        return redirect("superadmin_usuarios")

    usuario.is_active = False
    usuario.set_unusable_password()
    usuario.save(update_fields=["is_active", "password"])
    token_raw, token = emitir_token_acceso(
        usuario,
        TokenAccesoUsuario.TIPO_INVITACION,
        creado_por=request.user,
        request=request,
        horas=48,
    )
    try:
        enviar_correo_acceso(
            request,
            usuario,
            token_raw,
            token,
            TokenAccesoUsuario.TIPO_INVITACION,
        )
    except Exception:
        logger.exception("No se pudo reenviar la invitacion del usuario %s", usuario.pk)
        messages.error(request, f"No se pudo enviar el correo a {usuario.email}. Revisa la configuracion SMTP.")
    else:
        messages.success(request, f"Invitacion reenviada a {usuario.email}.")
    return redirect("superadmin_usuarios")
