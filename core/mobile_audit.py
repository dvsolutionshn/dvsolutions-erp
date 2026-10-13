"""Bitácora móvil resumida sobre la auditoría original, sin detalles privados.

La lista positiva limita únicamente esta presentación. No elimina eventos ni
modifica la auditoría web, sus filtros o sus autorizaciones administrativas.
"""

from types import SimpleNamespace
from urllib.parse import urlencode

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.shortcuts import render
from django.urls import reverse
from django.utils.dateparse import parse_date

from .access import interfaz_clinica_activa
from .clinical_permissions import EMPRESAS_CLINICAS_CON_ROLES
from .models import RegistroAuditoria


# Nombres actuales explícitos: ningún modelo nuevo se incluye automáticamente.
MODELOS_AUDITORIA_APP = {
    "core": frozenset({
        "empresa", "usuario", "usuarioempresapermiso", "empresamodulo",
        "configuracionavanzadaempresa",
    }),
    "clinica": frozenset({
        "configuracionclinica", "profesionalsalud", "paciente", "pacientefotoevolucion",
        "servicioclinico", "citaclinica", "tratamientopaciente", "plantratamientopaciente",
        "antecedenteadicionalpaciente", "antecedenteadicionalhistorial", "clasificacionalopecia",
        "expedienteevento", "historiaclinicaespecialidad", "preconsultaclinica",
        "invitacionregistropaciente", "medicamentoprescrito", "consentimientoclinico",
        "examenpaciente", "documentoclinicopaciente", "manualreceta", "enviomanualpdf",
        "recetamedica", "recetamedicadetalle", "plantillareceta", "plantillarecetadetalle",
        "seguimientopostoperatorio",
    }),
    "crm": frozenset({
        "configuracioncrm", "bloqueodisponibilidadmedica", "citacliente",
        "programacamarahiperbarica", "sesioncamarahiperbarica", "programaterapiapostquirurgica",
        "sesionterapiapostquirurgica", "opcionservicioagenda", "citacirugiafoto",
    }),
    "facturacion": frozenset({
        "producto", "inventarioproducto", "bodegainventario", "loteinventario",
        "existencialotebodega", "movimientolotebodega", "movimientoinventario",
        "entradainventariodocumento", "lineaentradainventario", "bitacoraproductoeliminado",
        "historialcostorealproducto", "perfilfarmaceuticoproducto", "categoriaproductofarmaceutico",
    }),
}

METADATOS_AUDITORIA_APP = (
    "id", "empresa_id", "usuario_id", "accion", "modulo", "app_label", "modelo",
    "objeto_id", "fecha", "usuario__id", "usuario__username", "usuario__first_name", "usuario__last_name",
)


def es_auditoria_app(request):
    return "app" in request.GET or "1" in request.POST.getlist("app")


def auditoria_disponible_app(request, empresa):
    """ACL original; no exige licencia, agenda ni módulos adicionales."""
    usuario = request.user
    return bool(
        empresa and empresa.activa and empresa.slug in EMPRESAS_CLINICAS_CON_ROLES
        and interfaz_clinica_activa(empresa)
        and getattr(usuario, "is_authenticated", False)
        and (usuario.is_superuser or (
            usuario.es_administrador_empresa and usuario.puede_acceder_empresa(empresa)
        ))
    )


def _numero_canonico(valor):
    if (not valor or not valor.isascii() or not valor.isdecimal()
            or valor.startswith("0") or len(valor) > 19
            or int(valor) > 9223372036854775807):
        raise PermissionDenied("Usuario o página de la bitácora no válidos.")


def autorizar_auditoria_app(request, empresa):
    if not auditoria_disponible_app(request, empresa):
        raise PermissionDenied("Sólo el administrador autorizado puede consultar esta bitácora clínica.")
    if request.method not in {"GET", "HEAD"}:
        raise PermissionDenied("La bitácora móvil es únicamente de consulta.")
    if request.POST or request.FILES or request.body:
        raise PermissionDenied("Esta consulta no admite datos en el cuerpo ni archivos.")
    permitidos = {"app", "q", "accion", "modulo", "usuario", "desde", "hasta", "page"}
    if (set(request.GET) - permitidos
            or any(len(request.GET.getlist(clave)) != 1 for clave in request.GET)
            or request.GET.getlist("app") != ["1"]
            or request.GET.get("accion", "").strip() not in {"", *(codigo for codigo, _ in RegistroAuditoria.ACCION_CHOICES)}):
        raise PermissionDenied("Parámetros de la bitácora móvil inválidos.")
    if "page" in request.GET:
        _numero_canonico(request.GET["page"])
    if request.GET.get("usuario", ""):
        _numero_canonico(request.GET["usuario"])
    fechas = {}
    for campo in ("desde", "hasta"):
        try:
            # El parser original acepta fechas sin padding y devuelve None
            # para formatos no reconocidos. Sólo su ValueError se cierra aquí.
            fechas[campo] = parse_date((request.GET.get(campo) or "").strip())
        except ValueError as exc:
            raise PermissionDenied("Fecha de la bitácora no válida.") from exc
    return fechas


def base_auditoria_app(queryset, empresa):
    permitidos = Q()
    for app_label, modelos in MODELOS_AUDITORIA_APP.items():
        permitidos |= Q(app_label=app_label, modelo__in=modelos)
    base = queryset.filter(empresa=empresa).filter(permitidos)
    # Únicamente este conjunto positivo: un evento excluido no bloquea la
    # consulta. Usuarios históricos, inactivos o multiempresa son legítimos.
    if base.filter(usuario_id__isnull=False).exclude(
        usuario_id__in=get_user_model().objects.values("pk"),
    ).exists():
        raise PermissionDenied("Un evento de la bitácora tiene un autor inexistente.")
    return base.values(*METADATOS_AUDITORIA_APP)


def url_auditoria_app(empresa, **filtros):
    return f"{reverse('auditoria_empresa', args=[empresa.slug])}?{urlencode({'app': '1', **filtros})}"


def _registro_presentacion(valores):
    nombre = f"{valores['usuario__first_name'] or ''} {valores['usuario__last_name'] or ''}".strip()
    nombre = nombre or valores["usuario__username"] or "Sistema"
    modelo = apps.get_model(valores["app_label"], valores["modelo"])
    return SimpleNamespace(
        id=valores["id"], empresa_id=valores["empresa_id"], usuario_id=valores["usuario_id"],
        fecha=valores["fecha"], accion=valores["accion"],
        accion_label=dict(RegistroAuditoria.ACCION_CHOICES).get(valores["accion"], valores["accion"]),
        modulo=valores["modulo"], app_label=valores["app_label"], modelo=valores["modelo"],
        modelo_label=str(modelo._meta.verbose_name), objeto_id=valores["objeto_id"], usuario_nombre=nombre,
    )


def _navegacion_auditoria(request, empresa):
    from clinica.mobile_configuration import config_disponible_app
    from facturacion.mobile_suppliers import proveedores_disponibles_app
    from facturacion.mobile_transfers import transferencia_disponible_app
    from .mobile_users import usuarios_disponibles_app

    usuario = request.user
    licencia = usuario.is_superuser or empresa.licencia_operativa
    # El hub sólo tiene la excepción sin licencia ya existente para Usuarios;
    # poder ver auditoría no concede acceso a sus operaciones ni al calendario.
    puede_modulos = bool(usuarios_disponibles_app(request, empresa) or (licencia and (
        usuario.tiene_permiso_erp("puede_citas", empresa)
        or usuario.tiene_alguna_permision_facturacion_empresa(empresa)
        or usuario.tiene_alguna_permision_clinica_empresa(empresa)
        or transferencia_disponible_app(request, empresa)
        or any(proveedores_disponibles_app(request, empresa).values())
    )))
    modulos = reverse("agenda_mobile_modules_screen", args=[empresa.slug]) if puede_modulos else ""
    puede_configurar = config_disponible_app(request, empresa)["hub"]
    configuracion = f"{reverse('clinica_configuracion', args=[empresa.slug])}?app=1" if puede_configurar else ""
    propia = url_auditoria_app(empresa)
    identidad = SimpleNamespace(pk=empresa.pk, id=empresa.pk, nombre=empresa.nombre,
                                slug=empresa.slug, tipo_solucion=empresa.tipo_solucion,
                                activa=empresa.activa, logo=empresa.logo)
    return {
        "empresa": identidad, "empresa_original": identidad, "modo_app": True,
        "app_home_disponible": False, "app_base_url": "", "can_view_mobile_audit": True,
        "can_view_app_modules": puede_modulos, "app_modules_url": modulos,
        "can_view_clinical_configuration": puede_configurar, "app_configuration_url": configuracion,
        "app_audit_url": propia, "app_audit_search_url": propia,
        "app_audit_back_url": configuracion or modulos or propia,
    }


def render_auditoria_app(request, empresa, contexto_original, fechas):
    pagina = contexto_original["registros"]
    # Sólo la página visible. El Paginator mantiene el queryset values de
    # metadatos; no se seleccionan modelos ni se recorre el historial completo.
    pagina.object_list = [_registro_presentacion(valores) for valores in pagina.object_list]
    contexto = {**contexto_original, **_navegacion_auditoria(request, empresa)}
    contexto["usuarios_auditoria"] = [
        SimpleNamespace(id=valores["id"], username=valores["username"])
        for valores in contexto_original["usuarios_auditoria"].values("id", "username")
    ]
    contexto["modulos"] = list(contexto_original["modulos"])
    contexto["filtros"] = {
        **contexto_original["filtros"],
        **{campo: fechas[campo].isoformat() if fechas[campo] else "" for campo in ("desde", "hasta")},
    }
    filtros = contexto["filtros"]
    contexto.update({
        "mobile_audit_items": pagina.object_list,
        "mobile_audit_previous_url": url_auditoria_app(empresa, **filtros, page=pagina.previous_page_number()) if pagina.has_previous() else "",
        "mobile_audit_next_url": url_auditoria_app(empresa, **filtros, page=pagina.next_page_number()) if pagina.has_next() else "",
    })
    response = render(request, "core/mobile_audit_list.html", contexto)
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    return response
