"""Estado administrativo del enlace inicial, sin cambiar respuestas históricas."""

from django.core.exceptions import PermissionDenied
from django.db.models import F, Q

from .models import PreconsultaClinica


MENSAJE_HISTORIA_COMPLETA = (
    "Expediente completo. La historia clínica ya fue completada; no es necesario enviar otro enlace."
)


def _es_respuesta_completa(registro):
    from .preconsulta_respuestas import registro_inicial_sin_historia

    if registro.tipo != "general" or registro.estado != "completada":
        return False
    datos = registro.datos_generales
    if isinstance(datos, dict):
        if datos.get("registro_interno") is True:
            return False
        general = datos.get("formulario_general")
        if general is not None and not isinstance(general, dict):
            return False
    return not registro_inicial_sin_historia(registro)


def _historias_completadas(empresa, paciente_ids):
    return PreconsultaClinica.objects.filter(
        empresa=empresa, paciente__empresa=empresa, paciente_id__in=paciente_ids,
        tipo="general", estado="completada",
    ).filter(
        Q(cita__isnull=True) | Q(cita__empresa_id=F("empresa_id"), cita__paciente_id=F("paciente_id")),
    ).order_by("-fecha_completada", "-fecha_creacion", "-id")


def historia_general_completada(empresa, paciente):
    """Una respuesta pública anterior basta, aunque haya pendientes más nuevos."""
    if paciente.empresa_id != empresa.pk:
        raise PermissionDenied("El paciente no pertenece a esta empresa.")
    return next((registro for registro in _historias_completadas(empresa, [paciente.pk]).iterator()
                 if _es_respuesta_completa(registro)), None)


def contexto_estado_historia(empresa, paciente):
    completo = historia_general_completada(empresa, paciente) is not None
    return {
        "expediente_historia_completo": completo,
        "expediente_historia_estado": "Expediente completo" if completo else "Expediente pendiente de terminar",
    }


def anotar_estados_historia(empresa, pacientes):
    """Una consulta por listado; sólo se agregan el estado y su etiqueta al contexto."""
    objetos = list(pacientes)
    if not objetos:
        return
    if any(paciente.empresa_id != empresa.pk for paciente in objetos):
        raise PermissionDenied("El listado contiene pacientes de otra empresa.")
    completados = set()
    for registro in _historias_completadas(empresa, [p.pk for p in objetos]).iterator():
        if registro.paciente_id not in completados and _es_respuesta_completa(registro):
            completados.add(registro.paciente_id)
    for paciente in objetos:
        paciente.expediente_historia_completo = paciente.pk in completados
        paciente.expediente_historia_estado = (
            "Expediente completo" if paciente.expediente_historia_completo else "Expediente pendiente de terminar"
        )
