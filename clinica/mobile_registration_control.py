"""Consulta móvil del control privado original de enlaces de pacientes.

No genera enlaces ni calcula estados nuevos. El listado original conserva sus
filtros, conteos y paginación; sólo se limita y proyecta lo que llega a la app.
"""

from types import SimpleNamespace
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.shortcuts import render
from django.urls import reverse

from core.access import interfaz_clinica_activa, permiso_clinica_desde_ruta
from core.clinical_permissions import permiso_clinica_granular, rol_clinico_granular
from .models import InvitacionRegistroPaciente, Paciente


ESTADOS_CONTROL_APP = (
    ("generado", "Generado / no abierto"), ("abierto", "Abierto"),
    ("en_progreso", "En progreso"), ("validacion", "Detenido por validación"),
    ("error_tecnico", "Error técnico"), ("completado", "Completado"),
    ("vencido", "Vencido"), ("revocado", "Revocado"),
)


def es_control_enlaces_app(request):
    # Una bandera explícita inválida tampoco debe caer en la pantalla web.
    return "app" in request.GET or "1" in request.POST.getlist("app")


def control_enlaces_disponible_app(request, empresa):
    """Mismas puertas del control y su ruta; no consulta invitaciones."""
    from .views import _puede_controlar_enlaces_pacientes

    usuario = request.user
    if not (
        empresa and empresa.slug == "hospital_mia" and empresa.activa
        and interfaz_clinica_activa(empresa)
        and getattr(usuario, "is_authenticated", False)
        and (usuario.is_superuser or usuario.puede_acceder_empresa(empresa))
        and (usuario.is_superuser or empresa.licencia_operativa)
        and empresa.tiene_modulo_activo("clinica_medica")
        and _puede_controlar_enlaces_pacientes(usuario)
    ):
        return False
    # El propietario nominal sigue siendo obligatorio, incluso para estos
    # bypass originales del middleware; no se sustituye por Super/Admin/CFG.
    if usuario.is_superuser or usuario.es_administrador_empresa:
        return True
    permiso = (permiso_clinica_granular("pacientes/enlaces-registro", "GET")
               if rol_clinico_granular(usuario, empresa)
               else permiso_clinica_desde_ruta("pacientes/enlaces-registro"))
    return bool(permiso and permiso != "__denegar__" and usuario.tiene_permiso_erp(permiso, empresa))


def autorizar_control_enlaces_app(request, empresa):
    if not control_enlaces_disponible_app(request, empresa):
        raise PermissionDenied("Este control móvil es privado para el propietario autorizado del ERP.")
    if request.method not in {"GET", "HEAD"}:
        raise PermissionDenied("El control móvil de enlaces es únicamente de consulta.")
    if request.POST or request.FILES or request.body:
        raise PermissionDenied("El control móvil de enlaces es únicamente de consulta.")
    permitidos = {"app", "q", "estado", "desde", "hasta", "pagina"}
    if (set(request.GET) - permitidos
            or any(len(request.GET.getlist(clave)) != 1 for clave in request.GET)
            or request.GET.getlist("app") != ["1"]
            or request.GET.get("estado", "").strip() not in {"", *(codigo for codigo, _ in ESTADOS_CONTROL_APP)}):
        raise PermissionDenied("Parámetros del control móvil de enlaces inválidos.")
    if "pagina" in request.GET:
        pagina = request.GET["pagina"]
        if (not pagina or not pagina.isascii() or not pagina.isdecimal()
                or pagina.startswith("0") or len(pagina) > 19
                or int(pagina) > 9223372036854775807):
            raise PermissionDenied("Página del control de enlaces no válida.")
    # Antes de filtros y JOIN: una importación inválida no debe ocultarse por
    # búsqueda/estado/página. El autor puede pertenecer a varias empresas.
    invitaciones = InvitacionRegistroPaciente.objects.filter(empresa=empresa)
    if invitaciones.filter(paciente_id__isnull=False).exclude(
        paciente_id__in=Paciente.objects.filter(empresa=empresa).values("pk"),
    ).exists():
        raise PermissionDenied("El enlace tiene una relación de paciente no válida.")
    if invitaciones.filter(creada_por_id__isnull=False).exclude(
        creada_por_id__in=get_user_model().objects.values("pk"),
    ).exists():
        raise PermissionDenied("El enlace tiene una relación de creador inexistente.")


def consulta_control_enlaces_app(invitaciones):
    """No seleccionar hash, IP, preconsulta ni datos de contacto/clínicos."""
    return invitaciones.only(
        "id", "empresa_id", "paciente_id", "creada_por_id", "token_preview",
        "estado", "fecha_expiracion", "fecha_completada", "ultimo_resultado",
        "paso_maximo", "fecha_primera_apertura", "cantidad_aperturas",
        "fecha_ultima_actividad", "fecha_creacion", "intentos_envio", "fecha_ultimo_intento",
        "paciente__id", "paciente__nombre", "paciente__identidad", "paciente__expediente_codigo",
        "creada_por__id", "creada_por__username", "creada_por__first_name", "creada_por__last_name",
    )


def url_control_enlaces_app(empresa, **filtros):
    return f"{reverse('clinica_control_enlaces_registro_paciente', args=[empresa.slug])}?{urlencode({'app': '1', **filtros})}"


def _fila_presentacion(registro):
    invitacion = registro["invitacion"]
    paciente = invitacion.paciente
    creador = invitacion.creada_por
    codigo = str(invitacion.token_preview or "")
    datos = SimpleNamespace(
        code_masked=f"{codigo[:8]}…" if codigo else "Sin código",
        paciente=SimpleNamespace(nombre=paciente.nombre, identidad=paciente.identidad,
                                 expediente_codigo=paciente.expediente_codigo) if paciente else None,
        creada_por=SimpleNamespace(nombre=creador.get_full_name() or creador.username) if creador else None,
        fecha_creacion=invitacion.fecha_creacion, fecha_expiracion=invitacion.fecha_expiracion,
        fecha_completada=invitacion.fecha_completada, fecha_primera_apertura=invitacion.fecha_primera_apertura,
        fecha_ultima_actividad=invitacion.fecha_ultima_actividad, fecha_ultimo_intento=invitacion.fecha_ultimo_intento,
        cantidad_aperturas=invitacion.cantidad_aperturas, intentos_envio=invitacion.intentos_envio,
    )
    return {**registro, "invitacion": datos}


def _navegacion_control(request, empresa):
    from core.mobile_users import usuarios_disponibles_app
    from facturacion.mobile_suppliers import proveedores_disponibles_app
    from facturacion.mobile_transfers import transferencia_disponible_app
    from .mobile_configuration import config_disponible_app

    usuario = request.user
    # Las mismas capacidades puras del hub adicional, sin abrir el calendario
    # ni invocar una vista/constructor que prepare registros.
    puede_modulos = bool(
        usuario.tiene_permiso_erp("puede_citas", empresa)
        or usuario.tiene_alguna_permision_facturacion_empresa(empresa)
        or usuario.tiene_alguna_permision_clinica_empresa(empresa)
        or transferencia_disponible_app(request, empresa)
        or any(proveedores_disponibles_app(request, empresa).values())
        or usuarios_disponibles_app(request, empresa)
    )
    modulos = reverse("agenda_mobile_modules_screen", args=[empresa.slug]) if puede_modulos else ""
    puede_configurar = config_disponible_app(request, empresa)["hub"]
    configuracion = f"{reverse('clinica_configuracion', args=[empresa.slug])}?app=1" if puede_configurar else ""
    propia = url_control_enlaces_app(empresa)
    identidad = SimpleNamespace(pk=empresa.pk, id=empresa.pk, nombre=empresa.nombre,
                                slug=empresa.slug, tipo_solucion=empresa.tipo_solucion,
                                activa=empresa.activa, logo=empresa.logo)
    return {
        "empresa": identidad, "empresa_original": identidad,
        "modo_app": True, "app_home_disponible": False, "app_base_url": "",
        "can_view_registration_control": True,
        "can_view_app_modules": puede_modulos, "app_modules_url": modulos,
        "can_view_clinical_configuration": puede_configurar, "app_configuration_url": configuracion,
        "app_registration_url": propia, "app_registration_search_url": propia,
        "app_registration_back_url": configuracion or modulos or propia,
    }


def render_control_enlaces_app(request, empresa, contexto_original):
    pagina_original = contexto_original["pagina"]
    # Ni Page ni su Paginator conservan modelos privados accesibles desde el
    # template: también se proyecta el conjunto filtrado de páginas restantes.
    seguras = [_fila_presentacion(registro) for registro in pagina_original.paginator.object_list]
    pagina = Paginator(seguras, pagina_original.paginator.per_page).page(pagina_original.number)
    contexto = {**contexto_original, **_navegacion_control(request, empresa), "pagina": pagina}
    filtros = {
        "q": contexto["consulta"], "estado": contexto["estado_filtro"],
        "desde": contexto["fecha_desde"], "hasta": contexto["fecha_hasta"],
    }
    contexto.update({
        "mobile_registration_items": pagina.object_list,
        "mobile_registration_states": [{"codigo": codigo, "nombre": nombre} for codigo, nombre in ESTADOS_CONTROL_APP],
        "mobile_registration_previous_url": url_control_enlaces_app(empresa, **filtros, pagina=pagina.previous_page_number()) if pagina.has_previous() else "",
        "mobile_registration_next_url": url_control_enlaces_app(empresa, **filtros, pagina=pagina.next_page_number()) if pagina.has_next() else "",
    })
    response = render(request, "clinica/mobile_registration_control.html", contexto)
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    return response
