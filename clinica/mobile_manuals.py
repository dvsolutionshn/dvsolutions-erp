"""Presentación y navegación móvil de los manuales, sin lógica de envío."""

from urllib.parse import quote, urlencode

from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse

from core.access import manuales_pdf_habilitados
from .mobile_patients import (
    contexto_pacientes_app, es_pacientes_app, permiso_pacientes_app,
    respuesta_pacientes_app, url_paciente_app,
)
from .models import EnvioManualPDF, Paciente


es_manuales_app = es_pacientes_app
respuesta_manuales_app = respuesta_pacientes_app


def autorizar_manuales_app(request, empresa, permitido):
    if not es_manuales_app(request, empresa):
        return
    if not empresa.tiene_modulo_activo("clinica_medica") or not manuales_pdf_habilitados(empresa):
        raise Http404("Manuales PDF no está habilitado para esta empresa.")
    if not request.user.puede_acceder_empresa(empresa) or not permitido:
        raise PermissionDenied("No tiene permiso para esta acción de manuales PDF.")


def url_manual_app(vista, empresa, manual=None, **parametros):
    args = [empresa.slug] + ([manual.pk] if manual else [])
    return f"{reverse(vista, args=args)}?{urlencode({'app': '1', **parametros})}"


def id_manual_app(valor):
    try:
        identificador = int(valor)
    except (TypeError, ValueError, OverflowError):
        return None
    return identificador if 0 < identificador <= 9223372036854775807 else None


def contexto_manuales_app(request, empresa, *, can_view, can_send, can_admin, manuales=(), historial=(), manual=None):
    contexto = contexto_pacientes_app(request, empresa)
    puede_buscar = permiso_pacientes_app(request, empresa, "puede_ver_pacientes", "puede_pacientes")
    puede_preseleccionar = puede_buscar or permiso_pacientes_app(
        request, empresa, "puede_ver_datos_generales_paciente", "puede_pacientes",
    )
    datos = request.POST if request.method == "POST" else request.GET
    seleccionados = {id_manual_app(valor) for valor in datos.getlist("manuales")}
    paciente_id = id_manual_app(datos.get("paciente_id"))
    paciente = (
        Paciente.objects.filter(pk=paciente_id, empresa=empresa, activo=True).first()
        if can_send and puede_preseleccionar and paciente_id else None
    )
    contexto.update({
        "can_view_manuals": can_view, "can_send_manuals": can_send,
        "can_admin_manuals": can_admin, "can_search_manual_patients": puede_buscar,
        "can_preselect_manual_patient": puede_preseleccionar,
        "app_manuals_url": url_manual_app("clinica_manuales_pdf", empresa),
        "app_manual_send_page_url": url_manual_app("clinica_manuales_pdf_app_enviar", empresa),
        "app_manual_send_url": url_manual_app("clinica_enviar_manuales_pdf", empresa),
        "app_manual_admin_url": url_manual_app("clinica_manuales_recetas", empresa),
        "app_manual_create_url": url_manual_app("clinica_crear_manual_receta", empresa),
        "app_manual_form_url": url_manual_app("clinica_editar_manual_receta", empresa, manual) if manual else url_manual_app("clinica_crear_manual_receta", empresa),
        "app_manual_patient_search_url": url_paciente_app("clinica_pacientes_sugerencias", empresa) if puede_buscar else "",
        "mobile_manual_patient_id": paciente.pk if paciente else paciente_id or "",
        "mobile_manual_patient": {
            "id": paciente.pk, "nombre": paciente.nombre,
            "expediente": paciente.expediente_codigo, "documento": paciente.identidad or "",
            "telefono": paciente.whatsapp or paciente.telefono or "", "correo": paciente.correo or "",
        } if paciente else None,
        "mobile_manual_canal": datos.get("canal", ""),
    })
    contexto["mobile_manual_items"] = [{
        "manual": item if can_view else {"id": item.pk, "titulo": item.titulo},
        "pdf_url": url_manual_app("clinica_archivo_manual_receta", empresa, item) if can_view else "",
        "download_url": url_manual_app("clinica_archivo_manual_receta", empresa, item, descargar="1") if can_view else "",
        "edit_url": url_manual_app("clinica_editar_manual_receta", empresa, item) if can_admin else "",
        "selected": item.pk in seleccionados,
    } for item in manuales]
    # El compositor de un rol de envío no concede lectura del PDF ni del historial.
    if not can_view:
        contexto["manuales"] = [item["manual"] for item in contexto["mobile_manual_items"]]
    if can_view:
        contexto["mobile_manual_history"] = [{
            "fecha": envio.fecha_envio,
            "paciente": envio.paciente.nombre if envio.paciente.empresa_id == empresa.pk else "Paciente no disponible",
            "usuario": (envio.enviado_por.get_full_name() or envio.enviado_por.username) if envio.enviado_por else "Usuario no disponible",
            "canal": envio.get_canal_display(), "destinatario": envio.destinatario if envio.paciente.empresa_id == empresa.pk else "",
            "estado": envio.estado, "estado_label": envio.get_estado_display(),
            "manuales": [item.titulo for item in envio.manuales.all() if item.empresa_id == empresa.pk],
        } for envio in historial]
    if manual:
        contexto["app_manual_pdf_url"] = url_manual_app("clinica_archivo_manual_receta", empresa, manual) if can_view else ""
    return contexto


def render_manuales_app(request, empresa, web_template, mobile_template, contexto, **permisos):
    if es_manuales_app(request, empresa):
        contexto.update(contexto_manuales_app(
            request, empresa, manuales=contexto.get("manuales", ()),
            historial=contexto.get("historial", ()), manual=contexto.get("manual"), **permisos,
        ))
        template = mobile_template
    else:
        template = web_template
    return respuesta_manuales_app(request, empresa, render(request, template, contexto))


def volver_manuales_app(request, empresa, *, can_view, error=False, admin=False, pending_manual_ids=None):
    if not es_manuales_app(request, empresa):
        return redirect("clinica_manuales_recetas" if admin else "clinica_manuales_pdf", empresa_slug=empresa.slug)
    vista = "clinica_manuales_recetas" if admin else "clinica_manuales_pdf_app_enviar" if error or not can_view else "clinica_manuales_pdf"
    destino = url_manual_app(vista, empresa)
    if error:
        # Sólo identificadores y canal; nunca se transportan datos clínicos/contactos.
        seleccion = request.POST.getlist("manuales") if pending_manual_ids is None else pending_manual_ids
        parametros = [("manuales", str(valor)) for valor in seleccion if id_manual_app(valor)]
        paciente_id = id_manual_app(request.POST.get("paciente_id"))
        if paciente_id:
            parametros.append(("paciente_id", str(paciente_id)))
        canal = request.POST.get("canal", "")
        if canal in {valor for valor, _ in EnvioManualPDF.CANAL_CHOICES}:
            parametros.append(("canal", canal))
        if parametros:
            destino += "&" + urlencode(parametros)
    return respuesta_manuales_app(request, empresa, redirect(destino))


def whatsapp_manual_app(request, empresa, destinatario, texto):
    destino = (
        f"https://wa.me/{destinatario}?text={quote(texto)}" if es_manuales_app(request, empresa)
        else f"https://web.whatsapp.com/send?phone={destinatario}&text={quote(texto)}"
    )
    return respuesta_manuales_app(request, empresa, redirect(destino))
