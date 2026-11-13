"""Autoriza las URL de archivos clínicos guardados antes de usar enlaces protegidos."""

from django.http import Http404
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe

from core.clinical_permissions import rol_clinico_granular
from crm.models import CitaCirugiaFoto

from .mobile_annexes import permiso_anexos_app, respuesta_archivo_anexo_app
from .models import ConsentimientoClinico, DocumentoClinicoPaciente, ExamenPaciente, PacienteFotoEvolucion


PREFIJO_CIRUGIAS = "crm/citas/cirugias/"
ARCHIVOS_CLINICOS = {
    "clinica/pacientes/evolucion/": (PacienteFotoEvolucion, ("imagen", "video")),
    "clinica/examenes/": (ExamenPaciente, ("archivo",)),
    "clinica/documentos/": (DocumentoClinicoPaciente, ("archivo",)),
    "clinica/consentimientos/": (ConsentimientoClinico, ("archivo",)),
}
PREFIJOS_MEDIA_CLINICA = (*ARCHIVOS_CLINICOS, PREFIJO_CIRUGIAS)


def puede_abrir_archivo_clinico(request, empresa):
    usuario = request.user
    return (
        usuario.is_authenticated
        and empresa.activa
        and usuario.puede_acceder_empresa(empresa)
        and (usuario.is_superuser or empresa.licencia_operativa)
        and empresa.tiene_modulo_activo("clinica_medica")
        and permiso_anexos_app(request, empresa)
        and (
            not rol_clinico_granular(usuario, empresa)
            or usuario.tiene_permiso_erp("puede_descargar_anexos_clinicos", empresa)
        )
    )


def _referencias_clinicas(nombre, modelo, campos):
    for campo in campos:
        registros = modelo.objects.filter(**{campo: nombre}).select_related("empresa").only(
            "empresa", "paciente_id", "paciente__empresa_id", campo,
        ).select_related("paciente")
        for registro in registros:
            if registro.paciente.empresa_id != registro.empresa_id:
                raise Http404("Archivo clínico no disponible.")
            yield registro.empresa, registro.paciente_id, getattr(registro, campo)


def _referencias_cirugia(nombre):
    adjuntos = CitaCirugiaFoto.objects.filter(imagen=nombre).select_related("empresa", "cita__paciente").only(
        "empresa", "imagen", "cita__empresa_id", "cita__paciente_id", "cita__paciente__empresa_id",
    )
    for adjunto in adjuntos:
        cita = adjunto.cita
        if cita.empresa_id != adjunto.empresa_id or (cita.paciente_id and cita.paciente.empresa_id != cita.empresa_id):
            raise Http404("Archivo clínico no disponible.")
        yield adjunto.empresa, cita.paciente_id, adjunto.imagen
    yield from _referencias_clinicas(nombre, PacienteFotoEvolucion, ("imagen", "video"))


@never_cache
@require_safe
def archivo_media_clinica(request, nombre):
    if not request.user.is_authenticated or any(
        parte in {"", ".", ".."} for parte in nombre.split("/")
    ) or "\\" in nombre or "\0" in nombre:
        raise Http404("Archivo clínico no disponible.")

    if nombre.startswith(PREFIJO_CIRUGIAS):
        referencias = list(_referencias_cirugia(nombre))
    else:
        for prefijo, (modelo, campos) in ARCHIVOS_CLINICOS.items():
            if nombre.startswith(prefijo):
                referencias = list(_referencias_clinicas(nombre, modelo, campos))
                break
        else:
            referencias = []
    # Dos pacientes o empresas no pueden compartir el acceso a un mismo nombre de archivo.
    if len({(empresa.pk, paciente_id) for empresa, paciente_id, _ in referencias}) != 1:
        raise Http404("Archivo clínico no disponible.")
    empresa, _, archivo = referencias[0]
    if not puede_abrir_archivo_clinico(request, empresa):
        raise Http404("Archivo clínico no disponible.")
    return respuesta_archivo_anexo_app(request, empresa, archivo)
