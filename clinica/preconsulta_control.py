"""Seguimiento de los enlaces clínicos usando sus registros existentes."""

from types import SimpleNamespace
from urllib.parse import urlencode

from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import F, Q
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date

from core.access import interfaz_clinica_activa
from core.clinical_permissions import (
    EMPRESAS_CLINICAS_CON_ROLES,
    historial_paciente_limitado,
    pacientes_solo_tarjetas,
    rol_clinico_granular,
)
from .models import PreconsultaClinica


ESTADOS_CONTROL_HISTORIA = (
    ("pendiente", "Pendiente"),
    ("abierto", "Abierto"),
    ("en_progreso", "En proceso"),
    ("validacion", "Faltan datos"),
    ("error_tecnico", "Error"),
    ("completado", "Completado"),
    ("vencido", "Vencido"),
    ("revocado", "Revocado"),
)

_FECHAS_ACTIVIDAD = (
    "fecha_primera_apertura", "fecha_ultima_actividad", "fecha_ultimo_intento",
)
_CAMPOS_ACTIVIDAD = (
    *_FECHAS_ACTIVIDAD, "paso_maximo", "cantidad_aperturas", "intentos_envio", "ultimo_resultado",
)


def puede_ver_respuestas_preconsulta(usuario, empresa):
    if not (
        empresa and getattr(usuario, "is_authenticated", False)
        and usuario.puede_acceder_empresa(empresa)
    ):
        return False
    if pacientes_solo_tarjetas(usuario, empresa) or historial_paciente_limitado(usuario, empresa):
        return False
    permiso = "puede_ver_historia_clinica" if rol_clinico_granular(usuario, empresa) else "puede_expediente_clinico"
    return usuario.tiene_permiso_erp(permiso, empresa)


def puede_controlar_preconsultas(usuario, empresa):
    if not (
        empresa and empresa.slug in EMPRESAS_CLINICAS_CON_ROLES and empresa.activa
        and getattr(usuario, "is_authenticated", False)
        and usuario.puede_acceder_empresa(empresa)
        and (usuario.is_superuser or empresa.licencia_operativa)
        and interfaz_clinica_activa(empresa) and empresa.tiene_modulo_activo("clinica_medica")
    ):
        return False
    return puede_ver_respuestas_preconsulta(usuario, empresa) or all(
        usuario.tiene_permiso_erp(permiso, empresa)
        for permiso in ("puede_ver_pacientes", "puede_enviar_enlace_historia_clinica")
    )


def registrar_actividad_preconsulta(preconsulta, evento, paso=0, resultado=None):
    """Registrar actividad real sin recibir respuestas ni cambiar el estado final."""
    if evento not in {"apertura", "progreso", "intento"}:
        raise ValueError("Evento de preconsulta no válido.")
    if resultado not in {None, "sin_intento", "validacion", "error_tecnico"}:
        raise ValueError("Resultado de actividad no válido.")
    with transaction.atomic():
        actual = PreconsultaClinica.objects.select_for_update().get(
            pk=preconsulta.pk, empresa_id=preconsulta.empresa_id, paciente_id=preconsulta.paciente_id,
        )
        if not actual.vigente:
            return actual
        ahora = timezone.now()
        campos = {"fecha_ultima_actividad"}
        actual.fecha_ultima_actividad = ahora
        if evento == "apertura":
            actual.cantidad_aperturas += 1
            campos.add("cantidad_aperturas")
        if evento in {"apertura", "progreso"} and not actual.fecha_primera_apertura:
            actual.fecha_primera_apertura = ahora
            campos.add("fecha_primera_apertura")
        if evento == "progreso":
            actual.paso_maximo = max(actual.paso_maximo, min(max(int(paso), 1), 20))
            campos.add("paso_maximo")
        if evento == "intento":
            actual.intentos_envio += 1
            actual.fecha_ultimo_intento = ahora
            actual.ultimo_resultado = resultado or "sin_intento"
            campos.update({"intentos_envio", "fecha_ultimo_intento", "ultimo_resultado"})
        PreconsultaClinica.objects.filter(pk=actual.pk).update(
            **{campo: getattr(actual, campo) for campo in campos},
        )
        for campo in campos:
            setattr(preconsulta, campo, getattr(actual, campo))
        return actual


def _actividad_registrada(preconsulta):
    """Las invitaciones convertidas conservan las fechas que sí registraron."""
    datos = {campo: getattr(preconsulta, campo) for campo in _CAMPOS_ACTIVIDAD}
    try:
        invitacion = preconsulta.invitacion_registro
    except ObjectDoesNotExist:
        invitacion = None
    if invitacion and (
        invitacion.empresa_id == preconsulta.empresa_id and invitacion.paciente_id == preconsulta.paciente_id
    ):
        for campo in _FECHAS_ACTIVIDAD:
            fechas = [valor for valor in (datos[campo], getattr(invitacion, campo)) if valor]
            if fechas:
                datos[campo] = min(fechas) if campo == "fecha_primera_apertura" else max(fechas)
        for campo in ("paso_maximo", "cantidad_aperturas", "intentos_envio"):
            datos[campo] = max(datos[campo], getattr(invitacion, campo))
        if invitacion.fecha_ultimo_intento and (
            not preconsulta.fecha_ultimo_intento or invitacion.fecha_ultimo_intento > preconsulta.fecha_ultimo_intento
        ):
            datos["ultimo_resultado"] = invitacion.ultimo_resultado
    fechas_registradas = [
        datos["fecha_primera_apertura"], datos["fecha_ultima_actividad"],
        datos["fecha_ultimo_intento"], preconsulta.fecha_completada,
    ]
    fechas_registradas = [fecha for fecha in fechas_registradas if fecha]
    if fechas_registradas:
        datos["fecha_ultima_actividad"] = max(fechas_registradas)
    return SimpleNamespace(**datos)


def estado_control_preconsulta(preconsulta, ahora=None, actividad=None):
    actividad = actividad or _actividad_registrada(preconsulta)
    ahora = ahora or timezone.now()
    if preconsulta.estado == "completada":
        return "completado", "Completado", "El envío final está guardado."
    if preconsulta.estado == "revocada":
        return "revocado", "Revocado", "El enlace fue desactivado."
    if preconsulta.fecha_expiracion <= ahora:
        return "vencido", "Vencido", "El plazo del enlace terminó."
    if actividad.ultimo_resultado == "error_tecnico":
        return "error_tecnico", "Error", "El último envío registró un error de guardado."
    if actividad.ultimo_resultado == "validacion":
        return "validacion", "Faltan datos", "El último envío necesita correcciones antes de guardarse."
    if actividad.paso_maximo or actividad.intentos_envio:
        return "en_progreso", "En proceso", "Hay actividad registrada, sin envío final guardado."
    if actividad.fecha_primera_apertura or actividad.cantidad_aperturas:
        return "abierto", "Abierto", "El enlace registra apertura, sin envío final guardado."
    return "pendiente", "Pendiente", "Sin apertura registrada."


def _url_control(empresa, modo_app=False, **filtros):
    ruta = reverse("clinica_control_enlaces_historia", args=[empresa.slug])
    parametros = {**({"app": "1"} if modo_app else {}), **filtros}
    return f"{ruta}?{urlencode(parametros)}" if parametros else ruta


def _fecha_filtro(valor):
    try:
        return parse_date(valor) if valor else None
    except ValueError:
        return None


def control_preconsultas(request, empresa):
    if not puede_controlar_preconsultas(request.user, empresa) or request.method not in {"GET", "HEAD"}:
        raise PermissionDenied("No tiene permiso para consultar la bitácora de enlaces clínicos.")
    if request.POST or request.FILES or request.body:
        raise PermissionDenied("La bitácora de enlaces clínicos es de consulta.")
    modo_app = request.GET.get("app") == "1"
    if "app" in request.GET and not modo_app:
        raise PermissionDenied("Parámetro de aplicación inválido.")
    consulta = (request.GET.get("q") or "").strip()
    estado = (request.GET.get("estado") or "").strip()
    if estado not in {"", *(codigo for codigo, _ in ESTADOS_CONTROL_HISTORIA)}:
        raise PermissionDenied("Estado de enlace clínico inválido.")
    fecha_desde = _fecha_filtro(request.GET.get("desde", ""))
    fecha_hasta = _fecha_filtro(request.GET.get("hasta", ""))
    puede_respuestas = puede_ver_respuestas_preconsulta(request.user, empresa)
    enlaces = PreconsultaClinica.objects.filter(empresa=empresa)
    if not puede_respuestas:
        enlaces = enlaces.filter(creada_por=request.user)
    if enlaces.exclude(paciente__empresa=empresa).exists() or enlaces.filter(cita__isnull=False).exclude(
        cita__empresa=empresa, cita__paciente_id=F("paciente_id"),
    ).exists():
        raise PermissionDenied("El enlace tiene una relación clínica de otra empresa o paciente.")
    if consulta:
        enlaces = enlaces.filter(Q(paciente__nombre__icontains=consulta) | Q(token_preview__icontains=consulta))
    if fecha_desde:
        enlaces = enlaces.filter(fecha_creacion__date__gte=fecha_desde)
    if fecha_hasta:
        enlaces = enlaces.filter(fecha_creacion__date__lte=fecha_hasta)
    paciente_filtro = (request.GET.get("paciente") or "").strip()
    if paciente_filtro:
        if not paciente_filtro.isascii() or not paciente_filtro.isdecimal():
            raise PermissionDenied("Identificador de paciente inválido.")
        enlaces = enlaces.filter(paciente_id=paciente_filtro)
    campos = [
        "id", "empresa_id", "paciente_id", "cita_id", "tipo", "creada_por_id", "estado",
        "fecha_creacion", "fecha_expiracion", "fecha_completada", *_CAMPOS_ACTIVIDAD,
        "paciente__id", "paciente__nombre", "cita__id", "cita__empresa_id", "cita__paciente_id", "cita__fecha_hora",
        "creada_por__id", "creada_por__username", "creada_por__first_name", "creada_por__last_name",
        "invitacion_registro__id", "invitacion_registro__empresa_id", "invitacion_registro__paciente_id",
        "invitacion_registro__preconsulta_id",
        *(f"invitacion_registro__{campo}" for campo in _CAMPOS_ACTIVIDAD),
    ]
    enlaces = enlaces.select_related("paciente", "cita", "creada_por", "invitacion_registro").only(*campos)
    conteos = dict.fromkeys((codigo for codigo, _ in ESTADOS_CONTROL_HISTORIA), 0)
    conteos["total"] = 0
    filas = []
    for enlace in enlaces:
        actividad = _actividad_registrada(enlace)
        codigo, etiqueta, explicacion = estado_control_preconsulta(enlace, actividad=actividad)
        conteos["total"] += 1
        conteos[codigo] += 1
        if estado and codigo != estado:
            continue
        respuestas_url = ""
        if puede_respuestas and enlace.estado == "completada":
            respuestas_url = reverse("clinica_preconsulta_detalle", args=[empresa.slug, enlace.paciente_id, enlace.pk])
            if modo_app:
                respuestas_url += "?app=1"
        filas.append({
            "id": enlace.pk, "paciente_nombre": enlace.paciente.nombre,
            "cita_id": enlace.cita_id, "cita_fecha": enlace.cita.fecha_hora if enlace.cita else None,
            "tipo_nombre": enlace.get_tipo_display(), "estado_codigo": codigo,
            "estado_etiqueta": etiqueta, "estado_explicacion": explicacion,
            "fecha_creacion": enlace.fecha_creacion, "fecha_expiracion": enlace.fecha_expiracion,
            "fecha_completada": enlace.fecha_completada, "actividad": actividad,
            "creador_nombre": enlace.creada_por.get_full_name() or enlace.creada_por.username if enlace.creada_por else "Sin registro",
            "respuestas_url": respuestas_url,
        })
    pagina = Paginator(filas, 40).get_page(request.GET.get("pagina"))
    filtros = {
        "q": consulta, "estado": estado, "desde": fecha_desde.isoformat() if fecha_desde else "",
        "hasta": fecha_hasta.isoformat() if fecha_hasta else "", "paciente": paciente_filtro,
    }
    volver_url = reverse("clinica_pacientes", args=[empresa.slug]) + ("?app=1" if modo_app else "")
    contexto = {
        "empresa": empresa, "pagina": pagina, "conteos": conteos, "consulta": consulta,
        "estado_filtro": estado, "estados_control": ESTADOS_CONTROL_HISTORIA,
        "fecha_desde": filtros["desde"], "fecha_hasta": filtros["hasta"], "paciente_filtro": paciente_filtro,
        "requieren_revision": conteos["validacion"] + conteos["error_tecnico"],
        "modo_app": modo_app, "app_home_disponible": False, "puede_ver_respuestas": puede_respuestas,
        "control_url": _url_control(empresa, modo_app), "volver_url": volver_url,
        "anterior_url": _url_control(empresa, modo_app, **filtros, pagina=pagina.previous_page_number()) if pagina.has_previous() else "",
        "siguiente_url": _url_control(empresa, modo_app, **filtros, pagina=pagina.next_page_number()) if pagina.has_next() else "",
    }
    template = "clinica/mobile_control_enlaces_historia.html" if modo_app else "clinica/control_enlaces_historia.html"
    respuesta = render(request, template, contexto)
    respuesta["Cache-Control"] = "private, no-store"
    return respuesta
