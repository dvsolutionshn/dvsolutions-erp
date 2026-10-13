"""Integridad de las notas que ya consulta el expediente móvil existente."""

from django.core.exceptions import PermissionDenied

from .mobile_patients import es_pacientes_app
from .models import HistoriaClinicaEspecialidad, Paciente, ProfesionalSalud


def validar_historias_lectura_app(request, empresa, paciente):
    """Revisar todo el paciente antes de filtrar áreas o cargar relaciones.

    No altera el alcance de lectura ni consulta otros pacientes. Los autores
    pueden ser usuarios compartidos entre empresas; no se exige su empresa.
    Las comprobaciones usan identificadores y subconsultas, nunca JOINs que
    puedan ocultar una relación importada huérfana.
    """
    if not es_pacientes_app(request, empresa):
        return
    if paciente.empresa_id != empresa.pk:
        raise PermissionDenied("El paciente no pertenece a esta empresa.")
    historias = HistoriaClinicaEspecialidad.objects.filter(paciente_id=paciente.pk)
    pacientes_propios = Paciente.objects.filter(empresa_id=empresa.pk).values("pk")
    if historias.exclude(empresa_id=empresa.pk, paciente_id__in=pacientes_propios).exists():
        raise PermissionDenied("Una nota no pertenece al expediente de esta empresa.")
    profesionales_propios = ProfesionalSalud.objects.filter(empresa_id=empresa.pk).values("pk")
    if historias.filter(profesional_id__isnull=False).exclude(profesional_id__in=profesionales_propios).exists():
        raise PermissionDenied("Una nota contiene un profesional no compatible con este expediente.")
