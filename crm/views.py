from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.models import Empresa
from facturacion.models import Cliente, Producto

from .forms import CampaniaMarketingForm, CitaClienteForm, ConfiguracionCRMForm, PlantillaMensajeForm
from .models import CampaniaMarketing, CitaCliente, ConfiguracionCRM, EnvioCampania, PlantillaMensaje
from .services import (
    WhatsAppAPIError,
    enviar_imagen_whatsapp,
    enviar_mensaje_whatsapp_texto,
    enviar_plantilla_marketing_whatsapp,
    enviar_plantilla_whatsapp,
    subir_media_whatsapp,
)


def _empresa_desde_slug(empresa_slug):
    return get_object_or_404(Empresa, slug=empresa_slug, activa=True)


def _configuracion_crm(empresa):
    return ConfiguracionCRM.objects.get_or_create(empresa=empresa)[0]


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
    form = CitaClienteForm(request.POST or None, empresa=empresa)
    if request.method == "POST" and form.is_valid():
        cita = form.save(commit=False)
        cita.empresa = empresa
        cita.save()
        messages.success(request, "Cita guardada correctamente.")
        return redirect("crm_citas", empresa_slug=empresa.slug)
    citas_qs = CitaCliente.objects.filter(empresa=empresa).select_related("cliente", "producto")
    return render(request, "crm/citas.html", {"empresa": empresa, "form": form, "citas": citas_qs})


@login_required
def agenda_citas(request, empresa_slug):
    empresa = _empresa_desde_slug(empresa_slug)
    form = CitaClienteForm(request.POST or None, empresa=empresa)
    if request.method == "POST" and form.is_valid():
        cita = form.save(commit=False)
        cita.empresa = empresa
        cita.save()
        messages.success(request, "Cita guardada correctamente.")
        return redirect("agenda_citas", empresa_slug=empresa.slug)
    citas_qs = CitaCliente.objects.filter(empresa=empresa).select_related("cliente", "producto")
    return render(request, "crm/citas.html", {"empresa": empresa, "form": form, "citas": citas_qs, "modo_agenda": True})
