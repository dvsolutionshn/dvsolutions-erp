import calendar
from datetime import date, datetime, timedelta
import logging
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.db.models import Count, Q
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.models import Empresa
from facturacion.models import Cliente, Producto
from clinica.models import CitaClinica, Paciente, PreconsultaClinica

from .forms import CampaniaMarketingForm, CitaClienteForm, ConfiguracionCRMForm, PacienteRapidoCitaForm, PlantillaMensajeForm
from .models import CampaniaMarketing, CitaCliente, ConfiguracionCRM, EnvioCampania, PlantillaMensaje
from .appointment_notifications import procesar_notificacion, programar_notificaciones_cita
from .models import NotificacionCitaWhatsApp
from .services import (
    WhatsAppAPIError,
    enviar_imagen_whatsapp,
    enviar_mensaje_whatsapp_texto,
    enviar_plantilla_marketing_whatsapp,
    enviar_plantilla_whatsapp,
    subir_media_whatsapp,
)
from .tokens import leer_token_respuesta_cita


logger = logging.getLogger(__name__)


def _empresa_desde_slug(empresa_slug):
    return get_object_or_404(Empresa, slug=empresa_slug, activa=True)


def _proteger_agenda_mobile(request, empresa):
    if not request.user.is_authenticated:
        login_url = reverse("empresa_login", args=[empresa.slug])
        return redirect(f"{login_url}?{urlencode({'next': request.get_full_path()})}")
    if not request.user.puede_acceder_empresa(empresa):
        return HttpResponse("Acceso no autorizado.", status=403)
    if not request.user.tiene_permiso_erp("puede_citas"):
        return HttpResponse("Tu usuario no tiene permiso para gestionar citas.", status=403)
    return None


def _configuracion_crm(empresa):
    return ConfiguracionCRM.objects.get_or_create(empresa=empresa)[0]


def _asegurar_pacientes_hospital_mia(empresa):
    if empresa.slug != "hospital_mia":
        return
    from clinica.services_pacientes import asegurar_paciente_desde_cliente

    clientes_sin_paciente = (
        Cliente.objects.filter(empresa=empresa, activo=True)
        .exclude(nombre__iexact="Consumidor final")
        .filter(pacientes_clinicos__isnull=True)
        .distinct()
    )
    for cliente in clientes_sin_paciente.iterator():
        asegurar_paciente_desde_cliente(cliente)


def _fecha_agenda(valor):
    try:
        return date.fromisoformat(valor or "")
    except ValueError:
        return timezone.localdate()


def _contexto_calendario(empresa, request, form, *, modo_agenda=False, vista_predeterminada="mes"):
    es_clinica = bool(empresa.tipo_solucion == "clinica" or empresa.tiene_modulo_activo("clinica_medica"))
    vista = request.GET.get("vista", vista_predeterminada)
    if vista not in {"mes", "semana", "dia"}:
        vista = "mes"
    seleccionada = _fecha_agenda(request.GET.get("fecha"))
    if vista == "mes":
        inicio = seleccionada.replace(day=1)
        fin = (inicio.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        anterior = (inicio - timedelta(days=1)).replace(day=1)
        siguiente = fin + timedelta(days=1)
        meses = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
        titulo_periodo = f"{meses[seleccionada.month]} {seleccionada.year}"
    elif vista == "semana":
        inicio = seleccionada - timedelta(days=seleccionada.weekday())
        fin = inicio + timedelta(days=6)
        anterior, siguiente = inicio - timedelta(days=7), inicio + timedelta(days=7)
        titulo_periodo = f"{inicio:%d/%m/%Y} — {fin:%d/%m/%Y}"
    else:
        inicio = fin = seleccionada
        anterior, siguiente = seleccionada - timedelta(days=1), seleccionada + timedelta(days=1)
        titulo_periodo = seleccionada.strftime("%d/%m/%Y")

    citas = list(
        CitaCliente.objects.filter(
            empresa=empresa, fecha_hora__date__gte=inicio, fecha_hora__date__lte=fin
        ).select_related("cliente", "producto", "paciente", "servicio_clinico", "profesional_salud").order_by("fecha_hora")
    )
    por_fecha = {}
    for cita in citas:
        clave = timezone.localtime(cita.fecha_hora).date()
        por_fecha.setdefault(clave, []).append(cita)

    semanas = []
    if vista == "mes":
        calendario = calendar.Calendar(firstweekday=0)
        for semana in calendario.monthdatescalendar(seleccionada.year, seleccionada.month):
            semanas.append([
                {"fecha": dia, "es_mes": dia.month == seleccionada.month, "es_hoy": dia == timezone.localdate(), "citas": por_fecha.get(dia, [])}
                for dia in semana
            ])
    dias = [
        {"fecha": dia, "es_hoy": dia == timezone.localdate(), "citas": por_fecha.get(dia, [])}
        for dia in (inicio + timedelta(days=i) for i in range((fin - inicio).days + 1))
    ]
    paciente_busqueda_inicial = None
    pacientes_busqueda = []
    paciente_id_inicial = form["paciente"].value() if es_clinica and "paciente" in form.fields else None
    if es_clinica and "paciente" in form.fields:
        pacientes_busqueda = [
            {
                "id": paciente.id,
                "nombre": paciente.nombre,
                "documento": paciente.identidad or "",
                "expediente": paciente.expediente_codigo,
                "telefono": paciente.whatsapp or paciente.telefono or "",
                "correo": paciente.correo or "",
            }
            for paciente in form.fields["paciente"].queryset
        ]
    if paciente_id_inicial:
        try:
            paciente_busqueda_inicial = Paciente.objects.filter(
                empresa=empresa,
                id=paciente_id_inicial,
            ).first()
        except (TypeError, ValueError):
            paciente_busqueda_inicial = None

    return {
        "empresa": empresa, "form": form, "citas": citas, "modo_agenda": modo_agenda,
        "vista": vista, "fecha_seleccionada": seleccionada, "titulo_periodo": titulo_periodo,
        "fecha_anterior": anterior, "fecha_siguiente": siguiente, "semanas": semanas, "dias": dias,
        "cita_editando": getattr(form, "instance", None) if getattr(form, "instance", None) and form.instance.pk else None,
        "estados_cita": CitaCliente.ESTADO_CHOICES,
        "es_clinica": es_clinica,
        "es_hospital_mia": empresa.slug == "hospital_mia",
        "paciente_rapido_form": PacienteRapidoCitaForm(empresa=empresa) if es_clinica else None,
        "paciente_busqueda_inicial": paciente_busqueda_inicial,
        "pacientes_busqueda": pacientes_busqueda,
    }


def _sincronizar_cita_clinica(cita):
    if not cita.paciente_id:
        return
    estados = {
        "pendiente": "solicitada", "confirmada": "confirmada",
        "realizada": "completada", "cancelada": "cancelada",
    }
    valores = {
        "empresa": cita.empresa,
        "paciente": cita.paciente,
        "profesional": cita.profesional_salud,
        "servicio": cita.servicio_clinico,
        "fecha_hora": cita.fecha_hora,
        "estado": estados.get(cita.estado, "solicitada"),
        "canal": "recepcion",
        "motivo": cita.observacion or cita.titulo,
        "observaciones": cita.observacion,
    }
    if cita.cita_clinica_id:
        for campo, valor in valores.items():
            setattr(cita.cita_clinica, campo, valor)
        cita.cita_clinica.save()
    else:
        cita.cita_clinica = CitaClinica.objects.create(**valores)
        cita.save(update_fields=["cita_clinica"])


def _programar_whatsapp_cita(request, cita):
    try:
        notificaciones = programar_notificaciones_cita(cita)
        confirmacion = next(
            (item for item in notificaciones if item.tipo == NotificacionCitaWhatsApp.TIPO_CONFIRMACION),
            None,
        )
        if not confirmacion or confirmacion.estado == "enviado":
            return
        resultado = procesar_notificacion(confirmacion.id)
        if resultado.estado == "enviado":
            messages.success(request, "Confirmación de la cita enviada por WhatsApp.")
        elif resultado.estado == "error":
            messages.warning(request, f"La cita se guardó, pero WhatsApp respondió con error: {resultado.ultimo_error}")
    except Exception:
        # Una falla externa de Meta, red o configuración nunca debe impedir que
        # recepción registre la cita. El detalle completo queda en el log.
        logger.exception("No se pudo procesar WhatsApp para la cita %s", cita.pk)
        messages.warning(
            request,
            "La cita se guardó correctamente, pero WhatsApp no pudo procesarse ahora. "
            "El recordatorio podrá reintentarse automáticamente.",
        )


def cita_respuesta_publica(request, token):
    try:
        datos = leer_token_respuesta_cita(token)
    except (signing.BadSignature, signing.SignatureExpired):
        return render(
            request,
            "crm/cita_respuesta_publica.html",
            {"estado_pagina": "invalido"},
            status=410,
        )

    cita = get_object_or_404(
        CitaCliente.objects.select_related(
            "empresa", "paciente", "cliente", "servicio_clinico", "profesional_salud", "cita_clinica"
        ),
        id=datos.get("cita_id"),
        empresa__slug=datos.get("empresa"),
    )
    local = timezone.localtime(cita.fecha_hora)
    contexto = {
        "estado_pagina": "formulario",
        "empresa": cita.empresa,
        "cita": cita,
        "fecha_local": local,
    }
    if request.method == "POST":
        accion = request.POST.get("accion")
        if accion == "confirmar":
            cita.estado = "confirmada"
            nota = f"Paciente confirmó asistencia desde enlace público el {timezone.localtime(timezone.now()):%d/%m/%Y %I:%M %p}."
            cita.observacion = f"{cita.observacion}\n{nota}".strip() if cita.observacion else nota
            cita.save(update_fields=["estado", "observacion"])
            _sincronizar_cita_clinica(cita)
            contexto["estado_pagina"] = "confirmada"
        elif accion == "cancelar":
            motivo = (request.POST.get("motivo") or "").strip()
            cita.estado = "cancelada"
            nota = f"Paciente canceló desde enlace público el {timezone.localtime(timezone.now()):%d/%m/%Y %I:%M %p}."
            if motivo:
                nota = f"{nota} Motivo: {motivo}"
            cita.observacion = f"{cita.observacion}\n{nota}".strip() if cita.observacion else nota
            cita.save(update_fields=["estado", "observacion"])
            cita.notificaciones_whatsapp.filter(estado__in=["pendiente", "error"]).update(estado="omitido")
            _sincronizar_cita_clinica(cita)
            contexto["estado_pagina"] = "cancelada"
        else:
            contexto["error"] = "Selecciona si confirmas o cancelas la cita."
    return render(request, "crm/cita_respuesta_publica.html", contexto)


@login_required
def crm_dashboard(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    hoy = timezone.localdate()
    manana = hoy + timezone.timedelta(days=1)
    config = _configuracion_crm(empresa)
    clientes = Cliente.objects.filter(empresa=empresa, activo=True)
    cumpleanos_manana = clientes.filter(
        fecha_nacimiento__month=manana.month,
        fecha_nacimiento__day=manana.day,
    ).order_by("nombre")
    fecha_alerta = hoy + timezone.timedelta(days=config.dias_alerta_producto)
    productos_alerta = Producto.objects.filter(
        empresa=empresa,
        activo=True,
        fecha_alerta__isnull=False,
        fecha_alerta__lte=fecha_alerta,
    ).order_by("fecha_alerta")[:8]
    etiquetas_fuente = {
        "facebook": "Facebook",
        "instagram": "Instagram",
        "x": "X",
        "tiktok": "TikTok",
        "youtube": "YouTube",
        "google": "Google",
        "whatsapp": "WhatsApp",
        "referencia": "Referencia",
        "otro": "Otro",
    }
    fuentes_preconsulta = [
        {
            "fuente": etiquetas_fuente.get(item["datos_generales__referido_por"], item["datos_generales__referido_por"] or "No indicado"),
            "total": item["total"],
        }
        for item in PreconsultaClinica.objects.filter(
            empresa=empresa,
            datos_generales__referido_por__isnull=False,
        )
        .exclude(datos_generales__referido_por="")
        .values("datos_generales__referido_por")
        .annotate(total=Count("id"))
        .order_by("-total", "datos_generales__referido_por")[:8]
    ]
    return render(
        request,
        "crm/dashboard.html",
        {
            "empresa": empresa,
            "config": config,
            "resumen": {
                "clientes": clientes.count(),
                "aceptan_promos": clientes.filter(acepta_promociones=True).count(),
                "campanias": CampaniaMarketing.objects.filter(empresa=empresa).count(),
            },
            "cumpleanos_manana": cumpleanos_manana,
            "productos_alerta": productos_alerta,
            "fuentes_preconsulta": fuentes_preconsulta,
        },
    )


@login_required
def configuracion_crm(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    config = _configuracion_crm(empresa)
    form = ConfiguracionCRMForm(request.POST or None, instance=config)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Configuracion CRM actualizada correctamente.")
        return redirect("crm_dashboard", empresa_slug=empresa.slug)
    return render(request, "crm/form.html", {"empresa": empresa, "form": form, "titulo": "Configuracion CRM"})


@login_required
@require_POST
def enviar_prueba_whatsapp(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    config = _configuracion_crm(empresa)
    if not config.whatsapp_activo:
        messages.error(request, "Activa WhatsApp en la configuracion CRM antes de enviar pruebas.")
        return redirect("crm_configuracion", empresa_slug=empresa.slug)
    try:
        respuesta = enviar_plantilla_whatsapp(config, config.whatsapp_numero_prueba)
        messages.success(request, f"Prueba enviada correctamente. Respuesta Meta: {respuesta}")
    except WhatsAppAPIError as exc:
        messages.error(request, f"No se pudo enviar la prueba WhatsApp. {exc}")
    return redirect("crm_configuracion", empresa_slug=empresa.slug)


@login_required
def plantillas(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    form = PlantillaMensajeForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        plantilla = form.save(commit=False)
        plantilla.empresa = empresa
        plantilla.save()
        messages.success(request, "Plantilla guardada correctamente.")
        return redirect("crm_plantillas", empresa_slug=empresa.slug)
    plantillas_qs = PlantillaMensaje.objects.filter(empresa=empresa)
    return render(request, "crm/plantillas.html", {"empresa": empresa, "form": form, "plantillas": plantillas_qs})


@login_required
def campanias(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    campanias_qs = CampaniaMarketing.objects.filter(empresa=empresa).select_related("plantilla")
    return render(request, "crm/campanias.html", {"empresa": empresa, "campanias": campanias_qs})


@login_required
def crear_campania(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    form = CampaniaMarketingForm(request.POST or None, empresa=empresa)
    if request.method == "POST" and form.is_valid():
        campania = form.save(commit=False)
        campania.empresa = empresa
        campania.creado_por = request.user
        campania.save()
        messages.success(request, "Campania creada correctamente.")
        return redirect("crm_ver_campania", empresa_slug=empresa.slug, campania_id=campania.id)
    return render(request, "crm/form.html", {"empresa": empresa, "form": form, "titulo": "Nueva Campania"})


@login_required
def ver_campania(request, empresa_slug, campania_id):
    empresa = _empresa_desde_slug(empresa_slug)
    campania = get_object_or_404(CampaniaMarketing.objects.select_related("plantilla"), id=campania_id, empresa=empresa)
    envios = campania.envios.select_related("cliente")
    return render(request, "crm/ver_campania.html", {"empresa": empresa, "campania": campania, "envios": envios})


@login_required
@require_POST
def preparar_envios_campania(request, empresa_slug, campania_id):
    empresa = _empresa_desde_slug(empresa_slug)
    campania = get_object_or_404(CampaniaMarketing.objects.select_related("plantilla"), id=campania_id, empresa=empresa)
    if not campania.plantilla:
        messages.error(request, "La campania necesita una plantilla para preparar envios.")
        return redirect("crm_ver_campania", empresa_slug=empresa.slug, campania_id=campania.id)
    creados = 0
    for cliente in campania.clientes_objetivo():
        envio, creado = EnvioCampania.objects.get_or_create(
            campania=campania,
            cliente=cliente,
            canal=campania.plantilla.canal if campania.plantilla.canal != "ambos" else "whatsapp",
            defaults={"mensaje": campania.plantilla.render(cliente=cliente), "estado": "preparado"},
        )
        if creado:
            creados += 1
        elif envio.estado == "pendiente":
            envio.mensaje = campania.plantilla.render(cliente=cliente)
            envio.estado = "preparado"
            envio.save(update_fields=["mensaje", "estado"])
    messages.success(request, f"Envios preparados para {creados} cliente(s) nuevos.")
    return redirect("crm_ver_campania", empresa_slug=empresa.slug, campania_id=campania.id)


def _resumen_promocion(campania, envio):
    mensaje = (envio.mensaje or "").replace("\r", " ").replace("\n", " ").strip()
    if mensaje:
        return mensaje[:900]
    if campania.plantilla and campania.plantilla.mensaje:
        return campania.plantilla.mensaje[:900]
    return campania.nombre


def _vigencia_promocion(campania):
    if campania.fecha_programada:
        return timezone.localtime(campania.fecha_programada).strftime("%d/%m/%Y")
    return "por tiempo limitado"


def _enlace_whatsapp_empresa(config, empresa):
    numero = "".join(ch for ch in (config.whatsapp_numero_prueba or "") if ch.isdigit())
    if numero:
        return f"https://wa.me/{numero}"
    slug = getattr(empresa, "slug", "") or "empresa"
    return f"responde a este mensaje o visita el enlace de {slug}"


@login_required
@require_POST
def enviar_campania_plantilla_prueba(request, empresa_slug, campania_id):
    empresa = _empresa_desde_slug(empresa_slug)
    config = _configuracion_crm(empresa)
    campania = get_object_or_404(CampaniaMarketing.objects.select_related("plantilla"), id=campania_id, empresa=empresa)
    if not config.whatsapp_activo:
        messages.error(request, "Activa WhatsApp Cloud API en la configuracion CRM antes de enviar la prueba masiva.")
        return redirect("crm_ver_campania", empresa_slug=empresa.slug, campania_id=campania.id)

    envios = campania.envios.select_related("cliente")
    if not envios.exists():
        messages.error(request, "No hay envios preparados. Primero prepara los mensajes para todos.")
        return redirect("crm_ver_campania", empresa_slug=empresa.slug, campania_id=campania.id)

    enviados = 0
    errores = 0
    nombre_plantilla = config.whatsapp_plantilla_prueba or "hello_world"
    idioma = config.whatsapp_idioma_plantilla or "en_US"
    for envio in envios:
        numero = envio.cliente.telefono_whatsapp or envio.cliente.telefono
        try:
            respuesta = enviar_plantilla_whatsapp(config, numero, nombre_plantilla=nombre_plantilla, idioma=idioma)
            envio.estado = "enviado"
            envio.respuesta = f"Prueba plantilla {nombre_plantilla}: {respuesta}"
            envio.fecha_envio = timezone.now()
            envio.save(update_fields=["estado", "respuesta", "fecha_envio"])
            enviados += 1
        except WhatsAppAPIError as exc:
            envio.estado = "error"
            envio.respuesta = f"Prueba plantilla {nombre_plantilla}: {exc}"
            envio.save(update_fields=["estado", "respuesta"])
            errores += 1

    if errores:
        messages.warning(request, f"Prueba masiva procesada: {enviados} enviado(s), {errores} con error.")
    else:
        messages.success(request, f"Prueba masiva enviada correctamente a {enviados} cliente(s) con {nombre_plantilla}.")
    return redirect("crm_ver_campania", empresa_slug=empresa.slug, campania_id=campania.id)


@login_required
@require_POST
def enviar_campania_whatsapp_api(request, empresa_slug, campania_id):
    empresa = _empresa_desde_slug(empresa_slug)
    config = _configuracion_crm(empresa)
    campania = get_object_or_404(CampaniaMarketing.objects.select_related("plantilla"), id=campania_id, empresa=empresa)
    if not config.whatsapp_activo:
        messages.error(request, "Activa WhatsApp Cloud API en la configuracion CRM antes de enviar campanias.")
        return redirect("crm_ver_campania", empresa_slug=empresa.slug, campania_id=campania.id)

    envios = campania.envios.select_related("cliente").exclude(estado="enviado")
    if not envios.exists():
        messages.error(request, "No hay envios pendientes. Primero prepara los mensajes para todos.")
        return redirect("crm_ver_campania", empresa_slug=empresa.slug, campania_id=campania.id)

    media_id = None
    if campania.plantilla and campania.plantilla.imagen_promocional:
        try:
            media_id = subir_media_whatsapp(config, campania.plantilla.imagen_promocional)
        except WhatsAppAPIError as exc:
            messages.error(request, f"No se pudo subir la imagen promocional a WhatsApp. {exc}")
            return redirect("crm_ver_campania", empresa_slug=empresa.slug, campania_id=campania.id)

    enviados = 0
    errores = 0
    usar_plantilla_marketing = bool(config.whatsapp_plantilla_marketing)
    for envio in envios:
        numero = envio.cliente.telefono_whatsapp or envio.cliente.telefono
        try:
            if usar_plantilla_marketing:
                respuesta = enviar_plantilla_marketing_whatsapp(
                    config,
                    numero,
                    nombre_cliente=envio.cliente.nombre,
                    promocion=_resumen_promocion(campania, envio),
                    vigencia=_vigencia_promocion(campania),
                    enlace=_enlace_whatsapp_empresa(config, empresa),
                    media_id=media_id,
                )
            elif media_id:
                respuesta = enviar_imagen_whatsapp(config, numero, media_id, envio.mensaje)
            else:
                respuesta = enviar_mensaje_whatsapp_texto(config, numero, envio.mensaje)
            envio.estado = "enviado"
            envio.respuesta = str(respuesta)
            envio.fecha_envio = timezone.now()
            envio.save(update_fields=["estado", "respuesta", "fecha_envio"])
            enviados += 1
        except WhatsAppAPIError as exc:
            envio.estado = "error"
            envio.respuesta = str(exc)
            envio.save(update_fields=["estado", "respuesta"])
            errores += 1

    if errores:
        messages.warning(request, f"Campania procesada: {enviados} enviado(s), {errores} con error.")
    else:
        campania.estado = "enviada"
        campania.save(update_fields=["estado"])
        messages.success(request, f"Campania enviada correctamente a {enviados} cliente(s).")
    return redirect("crm_ver_campania", empresa_slug=empresa.slug, campania_id=campania.id)


@login_required
def citas(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    cita_id = request.POST.get("cita_id") or request.GET.get("editar")
    objeto = get_object_or_404(CitaCliente, empresa=empresa, id=cita_id) if cita_id else None
    form = CitaClienteForm(request.POST or None, empresa=empresa, instance=objeto)
    if request.method == "POST" and form.is_valid():
        cita = form.save(commit=False)
        cita.empresa = empresa
        cita.save()
        _sincronizar_cita_clinica(cita)
        _programar_whatsapp_cita(request, cita)
        messages.success(request, "Cita guardada correctamente.")
        return redirect("crm_citas", empresa_slug=empresa.slug)
    return render(request, "crm/citas.html", _contexto_calendario(empresa, request, form))


@login_required
def agenda_citas(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    _asegurar_pacientes_hospital_mia(empresa)
    cita_id = request.POST.get("cita_id") or request.GET.get("editar")
    objeto = get_object_or_404(CitaCliente, empresa=empresa, id=cita_id) if cita_id else None
    form = CitaClienteForm(request.POST or None, empresa=empresa, instance=objeto)
    if request.method == "POST" and form.is_valid():
        cita = form.save(commit=False)
        cita.empresa = empresa
        cita.save()
        _sincronizar_cita_clinica(cita)
        _programar_whatsapp_cita(request, cita)
        messages.success(request, "Cita actualizada correctamente." if objeto else "Cita guardada correctamente.")
        return redirect("agenda_citas", empresa_slug=empresa.slug)
    return render(request, "crm/citas.html", _contexto_calendario(empresa, request, form, modo_agenda=True))


def agenda_mobile(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    acceso_denegado = _proteger_agenda_mobile(request, empresa)
    if acceso_denegado:
        return acceso_denegado
    cita_id = request.POST.get("cita_id") or request.GET.get("editar")
    objeto = get_object_or_404(CitaCliente, empresa=empresa, id=cita_id) if cita_id else None
    form = CitaClienteForm(request.POST or None, empresa=empresa, instance=objeto)
    if request.method == "POST" and form.is_valid():
        cita = form.save(commit=False)
        cita.empresa = empresa
        cita.save()
        _sincronizar_cita_clinica(cita)
        _programar_whatsapp_cita(request, cita)
        messages.success(request, "Cita actualizada correctamente." if objeto else "Cita creada correctamente.")
        fecha = timezone.localtime(cita.fecha_hora).date().isoformat()
        return redirect(f"{reverse('agenda_mobile', args=[empresa.slug])}?fecha={fecha}")

    contexto = _contexto_calendario(
        empresa,
        request,
        form,
        modo_agenda=True,
        vista_predeterminada="dia",
    )
    seleccionada = contexto["fecha_seleccionada"]
    dias_semana = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
    dias_semana_largos = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    meses = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
    contexto["titulo_fecha_mobile"] = (
        f"{dias_semana_largos[seleccionada.weekday()]}, "
        f"{seleccionada.day} de {meses[seleccionada.month - 1]}"
    )
    inicio_tira = seleccionada - timedelta(days=3)
    fin_tira = seleccionada + timedelta(days=3)
    citas_tira = CitaCliente.objects.filter(
        empresa=empresa,
        fecha_hora__date__gte=inicio_tira,
        fecha_hora__date__lte=fin_tira,
    )
    conteos = {
        fila["fecha_hora__date"]: fila["total"]
        for fila in citas_tira.values("fecha_hora__date").annotate(total=Count("id"))
    }
    contexto["dias_moviles"] = [
        {
            "fecha": inicio_tira + timedelta(days=indice),
            "dia_corto": dias_semana[(inicio_tira + timedelta(days=indice)).weekday()],
            "total": conteos.get(inicio_tira + timedelta(days=indice), 0),
        }
        for indice in range(7)
    ]
    ahora = timezone.now()
    proximas = list(
        CitaCliente.objects.filter(
            empresa=empresa,
            fecha_hora__gte=ahora,
            fecha_hora__lte=ahora + timedelta(hours=24),
        )
        .select_related("paciente", "cliente", "servicio_clinico", "producto")
        .order_by("fecha_hora")[:20]
    )
    contexto["proximas_app"] = [
        {
            "id": cita.id,
            "title": f"Cita: {cita.display_cliente}",
            "body": f"{cita.display_servicio} · {timezone.localtime(cita.fecha_hora).strftime('%I:%M %p')}",
            "at": int(cita.fecha_hora.timestamp() * 1000),
            "url": f"{reverse('agenda_mobile', args=[empresa.slug])}?fecha={timezone.localtime(cita.fecha_hora).date().isoformat()}&editar={cita.id}#editor",
        }
        for cita in proximas
    ]
    contexto["citas_hoy_total"] = CitaCliente.objects.filter(
        empresa=empresa,
        fecha_hora__date=timezone.localdate(),
    ).count()
    contexto["pendientes_hoy"] = CitaCliente.objects.filter(
        empresa=empresa,
        fecha_hora__date=timezone.localdate(),
        estado__in=["pendiente", "confirmada"],
    ).count()
    return render(request, "crm/agenda_mobile.html", contexto)


def agenda_mobile_manifest(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    acceso_denegado = _proteger_agenda_mobile(request, empresa)
    if acceso_denegado:
        return acceso_denegado
    inicio = reverse("agenda_mobile", args=[empresa.slug])
    icono = empresa.logo.url if empresa.logo else "/static/crm/hospital-mia-app.svg"
    icon_type = "image/svg+xml"
    if icono.lower().endswith((".jpg", ".jpeg")):
        icon_type = "image/jpeg"
    elif icono.lower().endswith(".png"):
        icon_type = "image/png"
    return JsonResponse(
        {
            "name": f"Agenda · {empresa.nombre}",
            "short_name": "Agenda MIA",
            "description": "Calendario móvil de citas conectado a DV Solutions ERP.",
            "id": inicio,
            "start_url": inicio,
            "scope": inicio,
            "display": "standalone",
            "orientation": "portrait-primary",
            "background_color": "#f4f8fb",
            "theme_color": "#12324a",
            "icons": [
                {"src": icono, "sizes": "192x192", "type": icon_type, "purpose": "any"},
                {"src": icono, "sizes": "512x512", "type": icon_type, "purpose": "any maskable"},
            ],
        },
        content_type="application/manifest+json",
    )


def agenda_mobile_service_worker(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    acceso_denegado = _proteger_agenda_mobile(request, empresa)
    if acceso_denegado:
        return acceso_denegado
    inicio = reverse("agenda_mobile", args=[empresa.slug])
    script = f"""
const APP_HOME = {inicio!r};
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", event => event.waitUntil(self.clients.claim()));
self.addEventListener("notificationclick", event => {{
  event.notification.close();
  const target = event.notification.data?.url || APP_HOME;
  event.waitUntil(self.clients.matchAll({{type:"window", includeUncontrolled:true}}).then(clients => {{
    const visible = clients.find(client => "focus" in client);
    if (visible) {{ visible.navigate(target); return visible.focus(); }}
    return self.clients.openWindow(target);
  }}));
}});
self.addEventListener("message", event => {{
  if (event.data?.type !== "SHOW_APPOINTMENT") return;
  const payload = event.data.payload || {{}};
  self.registration.showNotification(payload.title || "Próxima cita", {{
    body: payload.body || "",
    icon: payload.icon || "/static/crm/hospital-mia-app.svg",
    badge: "/static/crm/hospital-mia-app.svg",
    tag: `cita-${{payload.id || "agenda"}}`,
    data: {{url: payload.url || APP_HOME}},
  }});
}});
"""
    response = HttpResponse(script, content_type="application/javascript")
    response["Service-Worker-Allowed"] = inicio
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@login_required
def buscar_pacientes_cita(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    if not request.user.puede_acceder_empresa(empresa):
        return JsonResponse({"results": [], "error": "Acceso no autorizado."}, status=403)
    if not request.user.tiene_permiso_erp("puede_citas"):
        return JsonResponse({"results": [], "error": "Sin permiso para gestionar citas."}, status=403)

    query = " ".join((request.GET.get("q") or "").split())
    pacientes = Paciente.objects.filter(empresa=empresa, activo=True)
    for termino in query.split():
        pacientes = pacientes.filter(
            Q(nombre__icontains=termino)
            | Q(primer_nombre__icontains=termino)
            | Q(segundo_nombre__icontains=termino)
            | Q(primer_apellido__icontains=termino)
            | Q(segundo_apellido__icontains=termino)
            | Q(identidad__icontains=termino)
            | Q(expediente_codigo__icontains=termino)
            | Q(telefono__icontains=termino)
            | Q(whatsapp__icontains=termino)
            | Q(correo__icontains=termino)
        )
    pacientes = pacientes.order_by("-fecha_creacion", "nombre")[:12]
    return JsonResponse({
        "results": [
            {
                "id": paciente.id,
                "nombre": paciente.nombre,
                "documento": paciente.identidad or "",
                "expediente": paciente.expediente_codigo,
                "telefono": paciente.whatsapp or paciente.telefono or "",
                "correo": paciente.correo or "",
            }
            for paciente in pacientes
        ]
    })


@login_required
@require_POST
def crear_paciente_rapido_cita(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    es_clinica = empresa.tipo_solucion == "clinica" or empresa.tiene_modulo_activo("clinica_medica")
    if not es_clinica:
        return JsonResponse(
            {"ok": False, "error": "La creación de pacientes solo está disponible para empresas clínicas."},
            status=403,
        )

    form = PacienteRapidoCitaForm(request.POST, empresa=empresa)
    if not form.is_valid():
        errores = {
            campo: [str(error) for error in lista]
            for campo, lista in form.errors.items()
        }
        return JsonResponse(
            {"ok": False, "error": "Revisa los datos indicados.", "errors": errores},
            status=400,
        )

    from clinica.views import _proximo_codigo_expediente, _sincronizar_cliente_facturacion_paciente

    with transaction.atomic():
        Empresa.objects.select_for_update().get(pk=empresa.pk)
        codigo = _proximo_codigo_expediente(empresa)
        prefijo, numero = codigo.rsplit("-", 1)
        while Paciente.objects.filter(empresa=empresa, expediente_codigo=codigo).exists():
            numero = str(int(numero) + 1).zfill(5)
            codigo = f"{prefijo}-{numero}"

        paciente = form.save(commit=False)
        paciente.empresa = empresa
        paciente.expediente_codigo = codigo
        paciente.creado_por = request.user
        paciente.activo = True
        paciente.save()
        _sincronizar_cliente_facturacion_paciente(paciente)

    return JsonResponse({
        "ok": True,
        "paciente": {
            "id": paciente.id,
            "nombre": paciente.nombre,
            "expediente": paciente.expediente_codigo,
            "documento": paciente.identidad or "",
            "telefono": paciente.whatsapp or paciente.telefono or "",
            "label": str(paciente),
        },
    })


@login_required
@require_POST
def actualizar_estado_cita(request, empresa_slug, cita_id):
    empresa = _empresa_desde_slug(empresa_slug)
    cita = get_object_or_404(CitaCliente, empresa=empresa, id=cita_id)
    estado = request.POST.get("estado")
    estados_validos = {codigo for codigo, _ in CitaCliente.ESTADO_CHOICES}
    if estado not in estados_validos:
        messages.error(request, "El estado solicitado no es válido.")
    else:
        cita.estado = estado
        cita.save(update_fields=["estado"])
        _sincronizar_cita_clinica(cita)
        programar_notificaciones_cita(cita)
        messages.success(request, f"Cita marcada como {cita.get_estado_display()}.")
    regreso_movil = request.POST.get("return_to") == "mobile"
    vista = request.POST.get("vista", "dia" if regreso_movil else "mes")
    fecha = request.POST.get("fecha", timezone.localdate().isoformat())
    url = reverse("agenda_mobile" if regreso_movil else "agenda_citas", args=[empresa.slug])
    return redirect(f"{url}?vista={vista}&fecha={fecha}")


@login_required
@require_POST
def eliminar_cita(request, empresa_slug, cita_id):
    empresa = _empresa_desde_slug(empresa_slug)
    cita = get_object_or_404(CitaCliente, empresa=empresa, id=cita_id)
    motivo = (request.POST.get("motivo_eliminacion") or "").strip()
    regreso_movil = request.POST.get("return_to") == "mobile"
    vista = request.POST.get("vista", "dia" if regreso_movil else "mes")
    fecha = request.POST.get("fecha", timezone.localdate().isoformat())
    url = reverse("agenda_mobile" if regreso_movil else "agenda_citas", args=[empresa.slug])

    if len(motivo) < 5:
        messages.error(request, "Explica el motivo de la eliminación con al menos 5 caracteres.")
        return redirect(f"{url}?vista={vista}&fecha={fecha}")

    referencia = cita.display_servicio or cita.titulo
    paciente = cita.display_cliente
    cita_clinica = cita.cita_clinica
    with transaction.atomic():
        # Los recordatorios de WhatsApp se eliminan en cascada junto con la cita.
        cita.delete()
        # La agenda clínica es el registro operativo vinculado; no debe quedar huérfano.
        if cita_clinica:
            cita_clinica.delete()

    messages.success(request, f"Cita eliminada: {referencia} · {paciente}.")
    return redirect(f"{url}?vista={vista}&fecha={fecha}")
