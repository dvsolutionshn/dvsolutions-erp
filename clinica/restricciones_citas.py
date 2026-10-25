"""Regla compartida para nuevas reservas y cambios auditados de restricción."""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import CitaClinica, HistorialRestriccionCitasPaciente, Paciente


MENSAJE_NO_AGENDAR = (
    "No se puede agendar una nueva cita para este paciente. Está marcado como NO AGENDAR."
)


def puede_ver_motivo(usuario, empresa):
    return bool(
        usuario and usuario.is_authenticated
        and usuario.puede_acceder_empresa(empresa)
        and usuario.tiene_permiso_erp("puede_ver_motivo_restriccion_paciente", empresa)
    )


def mensaje_restriccion(paciente, usuario=None):
    mensaje = MENSAJE_NO_AGENDAR
    if paciente.motivo_no_agendar and puede_ver_motivo(usuario, paciente.empresa):
        mensaje += f"\nMotivo: {paciente.motivo_no_agendar}"
    return mensaje


def validar_paciente_para_cita(paciente_id, empresa_id, *, usuario=None, cliente_id=None, bloquear=False):
    """Consulta el estado persistido; también cubre agendas antiguas con sólo cliente.

    Los guardados pasan bloquear=True dentro de una transacción para serializar
    la reserva con el cambio de restricción sobre el mismo paciente.
    """
    if not empresa_id or not (paciente_id or cliente_id):
        return
    pacientes = Paciente.objects.filter(empresa_id=empresa_id)
    if paciente_id:
        pacientes = pacientes.filter(pk=paciente_id)
    else:
        pacientes = pacientes.filter(cliente_id=cliente_id).order_by("pk")
    if bloquear:
        pacientes = pacientes.select_for_update()
    encontrados = list(pacientes)
    if paciente_id and not encontrados:
        raise ValidationError("El paciente no pertenece a la empresa de la cita.", code="paciente_empresa_invalida")
    for paciente in encontrados:
        if paciente.no_agendar:
            raise ValidationError(mensaje_restriccion(paciente, usuario), code="paciente_no_agendar")


def contar_citas_futuras(paciente):
    """Una cita sincronizada en ambas agendas se cuenta una sola vez."""
    from crm.models import CitaCliente

    ahora = timezone.now()
    filtro_paciente = Q(paciente_id=paciente.pk)
    if paciente.cliente_id:
        filtro_paciente |= Q(paciente__isnull=True, cliente_id=paciente.cliente_id)
    agenda = CitaCliente.objects.filter(empresa_id=paciente.empresa_id).filter(filtro_paciente)
    futuras_agenda = agenda.filter(fecha_hora__gte=ahora, estado__in=["pendiente", "confirmada"])
    espejos = agenda.exclude(cita_clinica_id=None).values("cita_clinica_id")
    clinicas_sin_espejo = CitaClinica.objects.filter(
        empresa_id=paciente.empresa_id, paciente_id=paciente.pk,
        fecha_hora__gte=ahora, estado__in=["solicitada", "confirmada", "en_atencion"],
    ).exclude(pk__in=espejos)
    return futuras_agenda.count() + clinicas_sin_espejo.count()


@transaction.atomic
def cambiar_restriccion_citas(*, paciente, usuario, restringir, motivo, observacion=""):
    permiso = "puede_restringir_citas_paciente" if restringir else "puede_habilitar_citas_paciente"
    if not usuario.puede_acceder_empresa(paciente.empresa) or not usuario.tiene_permiso_erp(permiso, paciente.empresa):
        raise PermissionDenied("No tiene permiso para cambiar esta restricción.")
    motivo = (motivo or "").strip()
    observacion = (observacion or "").strip()
    if not motivo:
        raise ValidationError({"motivo": "Indique el motivo de este cambio."})
    paciente = Paciente.objects.select_for_update().get(pk=paciente.pk, empresa_id=paciente.empresa_id)
    if paciente.no_agendar == restringir:
        return paciente, False, contar_citas_futuras(paciente)
    anterior = paciente.no_agendar
    paciente.no_agendar = restringir
    paciente.motivo_no_agendar = motivo if restringir else ""
    paciente.observacion_no_agendar = observacion if restringir else ""
    paciente._actualizando_restriccion_citas = True
    try:
        paciente.save(update_fields=["no_agendar", "motivo_no_agendar", "observacion_no_agendar", "fecha_actualizacion"])
    finally:
        paciente._actualizando_restriccion_citas = False
    HistorialRestriccionCitasPaciente.objects.create(
        empresa_id=paciente.empresa_id, paciente=paciente,
        estado_anterior=anterior, estado_nuevo=restringir, motivo=motivo, observacion=observacion,
        usuario=usuario, usuario_nombre=(usuario.get_full_name().strip() or usuario.username)[:180],
    )
    return paciente, True, contar_citas_futuras(paciente)
