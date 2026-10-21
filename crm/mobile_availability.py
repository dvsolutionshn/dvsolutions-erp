"""Retornos móviles de la disponibilidad original, sin otro guardado.

La hoja entrenada sigue publicando su formulario original. Este adaptador
comprueba origen y agenda central antes de usar las mismas reglas y servicios;
no inicializa calendarios, pacientes, profesionales ni configuraciones.
"""

from datetime import date
from types import SimpleNamespace
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from core.access import interfaz_clinica_activa
from core.clinical_permissions import EMPRESAS_CLINICAS_CON_ROLES, rol_clinico_granular
from clinica.models import Paciente, ProfesionalSalud, ServicioClinico
from facturacion.models import Cliente, Producto
from .models import BloqueoDisponibilidadMedica, CitaCliente


SECCIONES_DISPONIBILIDAD_APP = (
    ("Profesional", ("profesional",)),
    ("Período no disponible", (
        "fecha", "fecha_fin", "dia_completo", "hora_inicio",
        "hora_inicio_periodo", "hora_fin", "hora_fin_periodo",
    )),
    ("Motivo", ("motivo",)),
)
CAMPOS_DISPONIBILIDAD_APP = frozenset(
    nombre for _, nombres in SECCIONES_DISPONIBILIDAD_APP for nombre in nombres
)


def _empresa_app(empresa):
    return bool(empresa and empresa.slug in EMPRESAS_CLINICAS_CON_ROLES
                and interfaz_clinica_activa(empresa) and empresa.activa)


def es_disponibilidad_app(request, empresa=None):
    # Cualquier opt-in explícito debe pasar las guardas, incluso fuera del
    # alcance o cuando una clave repetida intenta esconder el valor "1".
    return bool(
        "1" in request.GET.getlist("app") or "1" in request.POST.getlist("app")
        or (_empresa_app(empresa) and "app" in request.POST.getlist("regresar_a"))
    )


def _puerta_disponibilidad(request, empresa):
    usuario = request.user
    if not (_empresa_app(empresa) and usuario.is_authenticated
            and usuario.puede_acceder_empresa(empresa)
            and (usuario.is_superuser or empresa.licencia_operativa)):
        return False
    if usuario.is_superuser or usuario.es_administrador_empresa:
        return True
    if rol_clinico_granular(usuario, empresa):
        return usuario.tiene_permiso_erp("puede_editar_citas", empresa)
    return bool(
        usuario.tiene_permiso_erp("puede_citas", empresa)
        or usuario.tiene_alguna_permision_facturacion_empresa(empresa)
        or usuario.tiene_alguna_permision_clinica_empresa(empresa)
    )


def _agenda_central_valida(empresa, empresa_agenda):
    from .views import EMPRESAS_AGENDA_CENTRAL_HOSPITAL_MIA

    return bool(_empresa_app(empresa_agenda) and (
        empresa_agenda.slug == "hospital_mia"
        if empresa.slug in EMPRESAS_AGENDA_CENTRAL_HOSPITAL_MIA
        else empresa_agenda.pk == empresa.pk
    ))


def disponibilidad_disponible_app(request, empresa, empresa_agenda):
    """Sólo presentación: mismas puertas y excepción del profesional propio."""
    from .views import _puede_gestionar_bloqueos_otros

    if not (_puerta_disponibilidad(request, empresa)
            and _agenda_central_valida(empresa, empresa_agenda)
            and _puerta_disponibilidad(request, empresa_agenda)):
        return False
    return bool(
        _puede_gestionar_bloqueos_otros(request.user, empresa_agenda)
        or ProfesionalSalud.objects.filter(
            empresa=empresa_agenda, activo=True, usuario=request.user,
        ).exists()
    )


def _identificador(valor):
    if (not isinstance(valor, str) or not valor.isascii() or not valor.isdecimal()
            or valor.startswith("0") or len(valor) > 19
            or int(valor) > 9223372036854775807):
        raise PermissionDenied("Identificador de disponibilidad móvil inválido.")
    return int(valor)


def _validar_bloqueo(empresa_agenda, bloqueo_id):
    bloqueo = get_object_or_404(
        BloqueoDisponibilidadMedica.objects.only(
            "id", "empresa_id", "profesional_id", "creado_por_id",
        ), empresa=empresa_agenda, pk=bloqueo_id,
    )
    if (not ProfesionalSalud.objects.filter(
            pk=bloqueo.profesional_id, empresa=empresa_agenda).exists()
            or (bloqueo.creado_por_id and not get_user_model().objects.filter(
                pk=bloqueo.creado_por_id).exists())):
        raise PermissionDenied("El bloqueo contiene relaciones de otra empresa o inexistentes.")


def preparar_disponibilidad_app(request, empresa, *, eliminar=False, bloqueo_id=None):
    """Valida el sobre y la agenda destino antes del formulario/joins originales."""
    from .views import _empresa_origen_agenda_mobile

    if request.method != "POST" or request.FILES:
        raise PermissionDenied("Método o archivos no disponibles en disponibilidad móvil.")
    if (set(request.GET) - {"app"}
            or ("app" in request.GET and request.GET.getlist("app") != ["1"])
            or ("app" in request.POST and request.GET.getlist("app") != ["1"])):
        raise PermissionDenied("Parámetros de disponibilidad móvil inválidos.")
    permitidos = {"csrfmiddlewaretoken", "app", "regresar_a"}
    if not eliminar:
        permitidos |= CAMPOS_DISPONIBILIDAD_APP | {"bloqueo_id", "confirmar_conflictos"}
    if (set(request.POST) - permitidos
            or any(len(request.POST.getlist(nombre)) != 1 for nombre in request.POST)
            or request.POST.getlist("regresar_a") != ["app"]
            or ("app" in request.POST and request.POST.getlist("app") != ["1"])
            or ("confirmar_conflictos" in request.POST
                and request.POST.getlist("confirmar_conflictos") != ["1"])):
        raise PermissionDenied("El formulario móvil contiene datos o acciones no compatibles.")
    if not _puerta_disponibilidad(request, empresa):
        raise PermissionDenied("No tiene autorización de disponibilidad en la empresa de origen.")
    empresa_agenda = _empresa_origen_agenda_mobile(empresa)
    if not (_agenda_central_valida(empresa, empresa_agenda)
            and _puerta_disponibilidad(request, empresa_agenda)):
        raise PermissionDenied("La agenda central no está disponible o no está autorizada.")
    if eliminar:
        identificador = _identificador(str(bloqueo_id))
        partes_ruta = request.path.rstrip("/").split("/")
        if len(partes_ruta) < 2 or partes_ruta[-2] != str(identificador):
            raise PermissionDenied("El identificador de la ruta móvil no es canónico.")
    else:
        identificador = _identificador(request.POST["bloqueo_id"]) if request.POST.get("bloqueo_id") else None
        if request.POST.get("profesional"):
            _identificador(request.POST["profesional"])
    if identificador is not None:
        _validar_bloqueo(empresa_agenda, identificador)
    return empresa_agenda


def validar_citas_disponibilidad_app(empresa_agenda, profesional):
    """Valida FKs antes del servicio original que carga las citas afectadas."""
    citas = CitaCliente.objects.filter(empresa=empresa_agenda, profesional_salud=profesional)
    for campo, modelo in (
        ("paciente", Paciente), ("cliente", Cliente), ("servicio_clinico", ServicioClinico),
        ("producto", Producto), ("profesional_salud", ProfesionalSalud),
    ):
        if citas.filter(**{f"{campo}_id__isnull": False}).exclude(**{
            f"{campo}_id__in": modelo.objects.filter(empresa=empresa_agenda).values("pk"),
        }).exists():
            raise PermissionDenied("La agenda del profesional contiene relaciones no compatibles.")


def _identidad(empresa):
    return SimpleNamespace(
        pk=empresa.pk, id=empresa.pk, nombre=empresa.nombre, slug=empresa.slug,
        tipo_solucion=empresa.tipo_solucion, activa=empresa.activa, logo=empresa.logo,
    )


def _navegacion(request, empresa, fecha):
    from .views import _proteger_agenda_mobile

    puede_modulos = _proteger_agenda_mobile(request, empresa) is None
    puede_calendario = puede_modulos and (
        not rol_clinico_granular(request.user, empresa)
        or request.user.tiene_permiso_erp("puede_ver_calendario", empresa)
    )
    calendario = ""
    if puede_calendario:
        query = urlencode({"app": "agenda", "vista": "dia", "fecha": fecha.isoformat()})
        calendario = f"{reverse('agenda_mobile', args=[empresa.slug])}?{query}#agenda-app"
    modulos = reverse("agenda_mobile_modules_screen", args=[empresa.slug]) if puede_modulos else ""
    return {
        "app_home_disponible": False,
        "app_can_view_calendar": puede_calendario,
        "app_availability_calendar_url": calendario,
        "can_view_app_modules": puede_modulos, "app_modules_url": modulos,
        "app_availability_back_url": calendario or modulos,
    }


def _sin_cache(response):
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    return response


def render_disponibilidad_app(request, empresa, empresa_agenda, form, bloqueo=None,
                              citas_afectadas=None, *, status=200):
    for nombre in ("fecha", "fecha_fin"):
        form.fields[nombre].widget.format = "%Y-%m-%d"
    fecha = form.cleaned_data.get("fecha")
    if not isinstance(fecha, date):
        fecha = bloqueo.fecha if bloqueo else timezone.localdate()
    origen, destino = _identidad(empresa), _identidad(empresa_agenda)
    contexto = {
        "empresa": origen, "empresa_original": origen,
        "empresa_agenda": destino, "empresa_agenda_original": destino,
        "form": form, "bloqueo_form": form, "bloqueo": bloqueo,
        "citas_afectadas": citas_afectadas or [],
        "mobile_availability_is_confirmation": bool(citas_afectadas),
        "mobile_availability_confirmation_name": destino.nombre,
        "mobile_availability_sections": [
            {"titulo": titulo, "campos": [form[nombre] for nombre in nombres]}
            for titulo, nombres in SECCIONES_DISPONIBILIDAD_APP
        ],
        "app_availability_form_url": f"{reverse('agenda_bloqueo_guardar', args=[empresa.slug])}?app=1",
        **_navegacion(request, empresa, fecha),
    }
    return _sin_cache(render(request, "crm/mobile_availability_form.html", contexto, status=status))


def volver_disponibilidad_app(request, empresa, fecha):
    if isinstance(fecha, str):
        fecha = date.fromisoformat(fecha)
    return _sin_cache(redirect(_navegacion(request, empresa, fecha)["app_availability_back_url"]))
