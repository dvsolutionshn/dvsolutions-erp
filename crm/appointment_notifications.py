from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .models import CitaCliente, ConfiguracionCRM, NotificacionCitaWhatsApp
from .services import WhatsAppAPIError, enviar_plantilla_cita_whatsapp


def _numero_cita(cita):
    if cita.paciente_id:
        return cita.paciente.whatsapp or cita.paciente.telefono or ""
    if cita.cliente_id:
        return cita.cliente.telefono_whatsapp or cita.cliente.telefono or ""
    return ""


def programar_notificaciones_cita(cita, ahora=None):
    if cita.empresa.slug != "hospital_mia":
        return []
    ahora = ahora or timezone.now()
    reglas = [
        (NotificacionCitaWhatsApp.TIPO_CONFIRMACION, cita.enviar_confirmacion_whatsapp, ahora),
        (NotificacionCitaWhatsApp.TIPO_SEMANA, cita.recordatorio_semana_whatsapp, cita.fecha_hora - timedelta(days=7)),
        (NotificacionCitaWhatsApp.TIPO_DIA, cita.recordatorio_dia_whatsapp, cita.fecha_hora - timedelta(days=1)),
    ]
    resultado = []
    for tipo, activo, programada in reglas:
        existente = NotificacionCitaWhatsApp.objects.filter(cita=cita, tipo=tipo).first()
        if not activo:
            if existente and existente.estado in {"pendiente", "error"}:
                existente.estado = "omitido"
                existente.save(update_fields=["estado", "fecha_actualizacion"])
            continue
        # Si la cita fue creada después del momento de un recordatorio, no lo enviamos tarde.
        if not existente and tipo != NotificacionCitaWhatsApp.TIPO_CONFIRMACION and programada <= ahora:
            continue
        if not existente:
            notificacion = NotificacionCitaWhatsApp.objects.create(
                cita=cita, tipo=tipo, programada_para=programada
            )
        else:
            notificacion = existente
        # Solo reprogramamos avisos todavía futuros; los que ya vencieron deben
        # quedar disponibles para que el procesador los envíe y controle reintentos.
        if (
            notificacion.estado != "enviado"
            and programada > ahora
            and notificacion.programada_para != programada
        ):
            notificacion.programada_para = programada
            notificacion.estado = "pendiente"
            notificacion.intentos = 0
            notificacion.ultimo_error = ""
            notificacion.save(update_fields=["programada_para", "estado", "intentos", "ultimo_error", "fecha_actualizacion"])
        resultado.append(notificacion)
    return resultado


def procesar_notificacion(notificacion_id, ahora=None):
    ahora = ahora or timezone.now()
    with transaction.atomic():
        notificacion = NotificacionCitaWhatsApp.objects.select_for_update().select_related(
            "cita__empresa", "cita__paciente", "cita__cliente", "cita__servicio_clinico", "cita__profesional_salud"
        ).get(id=notificacion_id)
        if notificacion.estado == "enviado" or notificacion.programada_para > ahora:
            return notificacion
        cita = notificacion.cita
        if cita.estado in {"cancelada", "realizada"} or cita.fecha_hora <= ahora:
            notificacion.estado = "omitido"
            notificacion.save(update_fields=["estado", "fecha_actualizacion"])
            return notificacion
        config = ConfiguracionCRM.objects.filter(empresa=cita.empresa).first()
        if not config or not config.whatsapp_activo or not config.recordatorio_citas_activo:
            return notificacion
        local = timezone.localtime(cita.fecha_hora)
        aviso = {
            "confirmacion": "confirmación de cita",
            "semana": "recordatorio: falta una semana",
            "dia": "recordatorio: su cita es mañana",
        }[notificacion.tipo]
        try:
            respuesta = enviar_plantilla_cita_whatsapp(
                config,
                _numero_cita(cita),
                paciente=cita.display_cliente,
                aviso=aviso,
                fecha=local.strftime("%d/%m/%Y"),
                hora=local.strftime("%I:%M %p"),
                consulta=cita.display_servicio,
                profesional=cita.display_responsable,
            )
        except WhatsAppAPIError as exc:
            notificacion.estado = "error"
            notificacion.intentos += 1
            notificacion.ultimo_error = str(exc)
            notificacion.save(update_fields=["estado", "intentos", "ultimo_error", "fecha_actualizacion"])
            return notificacion
        notificacion.estado = "enviado"
        notificacion.intentos += 1
        notificacion.ultimo_error = ""
        notificacion.respuesta = respuesta or {}
        notificacion.enviada_en = ahora
        notificacion.save(update_fields=["estado", "intentos", "ultimo_error", "respuesta", "enviada_en", "fecha_actualizacion"])
        return notificacion


def procesar_recordatorios_hospital_mia(ahora=None):
    ahora = ahora or timezone.now()
    citas = CitaCliente.objects.filter(
        empresa__slug="hospital_mia",
        fecha_hora__gt=ahora,
        estado__in=["pendiente", "confirmada"],
    ).select_related("empresa")
    for cita in citas.iterator():
        programar_notificaciones_cita(cita, ahora=ahora)
    pendientes = list(NotificacionCitaWhatsApp.objects.filter(
        cita__empresa__slug="hospital_mia",
        estado__in=["pendiente", "error"],
        intentos__lt=3,
        programada_para__lte=ahora,
    ).values_list("id", flat=True))
    resultados = [procesar_notificacion(notificacion_id, ahora=ahora) for notificacion_id in pendientes]
    return {
        "procesadas": len(resultados),
        "enviadas": sum(1 for item in resultados if item.estado == "enviado"),
        "errores": sum(1 for item in resultados if item.estado == "error"),
    }
