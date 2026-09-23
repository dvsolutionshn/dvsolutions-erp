from datetime import datetime, time, timedelta

from django.utils import timezone

from .models import BloqueoDisponibilidadMedica, CitaCliente


def rango_bloqueo(*, fecha, fecha_fin=None, dia_completo, hora_inicio=None, hora_fin=None):
    zona = timezone.get_current_timezone()
    fecha_fin = fecha_fin or fecha
    inicio_hora = time.min if dia_completo else hora_inicio
    fin_fecha = fecha_fin + timedelta(days=1) if dia_completo else fecha_fin
    fin_hora = time.min if dia_completo else hora_fin
    inicio = timezone.make_aware(datetime.combine(fecha, inicio_hora), zona)
    fin = timezone.make_aware(datetime.combine(fin_fecha, fin_hora), zona)
    return inicio, fin


def rango_cita(cita):
    inicio = cita.fecha_hora
    fin = cita.cirugia_fin_estimada
    if not fin:
        minutos = cita.duracion_minutos or getattr(cita.servicio_clinico, "duracion_minutos", None) or 30
        fin = inicio + timedelta(minutes=minutos)
    return inicio, fin


def bloqueos_en_conflicto(*, empresa, profesional, inicio, fin, excluir_id=None):
    inicio_local = timezone.localtime(inicio)
    fin_local = timezone.localtime(fin - timedelta(microseconds=1))
    bloqueos = BloqueoDisponibilidadMedica.objects.filter(
        empresa=empresa,
        profesional=profesional,
        fecha__lte=fin_local.date(),
        fecha_fin__gte=inicio_local.date(),
    )
    if excluir_id:
        bloqueos = bloqueos.exclude(pk=excluir_id)
    conflictos = []
    for bloqueo in bloqueos.select_related("profesional"):
        bloqueo_inicio, bloqueo_fin = rango_bloqueo(
            fecha=bloqueo.fecha,
            fecha_fin=bloqueo.fecha_fin,
            dia_completo=bloqueo.dia_completo,
            hora_inicio=bloqueo.hora_inicio,
            hora_fin=bloqueo.hora_fin,
        )
        if inicio < bloqueo_fin and fin > bloqueo_inicio:
            conflictos.append(bloqueo)
    return conflictos


def citas_afectadas_por_bloqueo(
    *, empresa, profesional, fecha, fecha_fin=None, dia_completo, hora_inicio=None, hora_fin=None
):
    inicio, fin = rango_bloqueo(
        fecha=fecha,
        fecha_fin=fecha_fin,
        dia_completo=dia_completo,
        hora_inicio=hora_inicio,
        hora_fin=hora_fin,
    )
    candidatas = (
        CitaCliente.objects.filter(
            empresa=empresa,
            profesional_salud=profesional,
            fecha_hora__lt=fin,
            fecha_hora__gte=inicio - timedelta(days=1),
        )
        .exclude(estado="cancelada")
        .select_related("paciente", "cliente", "servicio_clinico", "producto", "profesional_salud")
        .order_by("fecha_hora")
    )
    afectadas = []
    for cita in candidatas:
        cita_inicio, cita_fin = rango_cita(cita)
        if cita_inicio < fin and cita_fin > inicio:
            afectadas.append(cita)
    return afectadas
