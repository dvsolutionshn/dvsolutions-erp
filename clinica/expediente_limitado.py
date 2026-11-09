from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Prefetch
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.decorators.http import require_safe

from core.access import interfaz_clinica_activa
from core.clinical_permissions import LECTURAS_EXPEDIENTE_LIMITADO, puede_abrir_expediente_limitado
from core.models import Empresa
from crm.models import (
    ProgramaCamaraHiperbarica, ProgramaTerapiaPostQuirurgica,
    SesionCamaraHiperbarica, SesionTerapiaPostQuirurgica,
)

from .mobile_patients import es_pacientes_app, notas_historia_app, render_pacientes_app
from .models import HistoriaClinicaEspecialidad, Paciente


SECCIONES = {
    "terapias": ("Terapias", "puede_escribir_terapias"),
    "camara_hiperbarica": ("Cámara hiperbárica", "puede_escribir_camara_hiperbarica"),
    "terapias_postquirurgicas": ("Terapias postquirúrgicas", "puede_escribir_postquirurgicas"),
}


def _paciente_autorizado(request, empresa_slug, paciente_id):
    empresa = get_object_or_404(Empresa, slug=empresa_slug, activa=True)
    if not request.user.puede_acceder_empresa(empresa) or not puede_abrir_expediente_limitado(request.user, empresa):
        raise PermissionDenied("No tiene permiso para abrir este expediente.")
    if not empresa.licencia_operativa or not interfaz_clinica_activa(empresa) or not empresa.tiene_modulo_activo("clinica_medica"):
        raise Http404("El expediente clínico no está disponible.")
    paciente = get_object_or_404(Paciente, empresa=empresa, pk=paciente_id)
    return empresa, paciente


def _url(request, empresa, vista, *args):
    ruta = reverse(vista, args=[empresa.slug, *args])
    return f"{ruta}?app=1" if es_pacientes_app(request, empresa) else ruta


def _respuesta(request, empresa, web, app, contexto):
    respuesta = render_pacientes_app(request, empresa, web, app, contexto)
    respuesta["Cache-Control"] = "private, no-store"
    return respuesta


@login_required
@require_safe
def expediente(request, empresa_slug, paciente_id):
    empresa, paciente = _paciente_autorizado(request, empresa_slug, paciente_id)
    acciones = []

    def agregar(titulo, vista, *args):
        acciones.append({"titulo": titulo, "url": _url(request, empresa, vista, paciente.pk, *args)})

    if request.user.tiene_permiso_erp("puede_ver_planes_tratamiento", empresa):
        agregar("Plan y tratamiento", "clinica_planes_tratamiento_paciente")
    if request.user.tiene_permiso_erp("puede_ver_anexos_clinicos", empresa):
        agregar("Anexos", "clinica_examenes_paciente")
        agregar("Anexos · Consentimientos", "clinica_consentimientos_paciente")
        agregar("Anexos · Fotografías y videos", "clinica_evolucion_paciente")
        for categoria, titulo in (
            ("documento", "Documentos"), ("laboratorio", "Laboratorio"),
            ("radiologico", "Radiografías"), ("remision", "Remisiones"),
            ("detalle_remision", "Detalle de remisiones"), ("incapacidad", "Incapacidades"),
        ):
            agregar(f"Anexos · {titulo}", "clinica_documentos_categoria_paciente", categoria)
    if request.user.tiene_permiso_erp("puede_subir_anexos_clinicos", empresa):
        agregar("Subir anexo", "clinica_subir_documento_categoria_paciente", "documento")
    for tipo, (titulo, permiso_escritura) in SECCIONES.items():
        if request.user.tiene_permiso_erp(LECTURAS_EXPEDIENTE_LIMITADO[tipo], empresa):
            agregar(titulo, "clinica_paciente_expediente_seccion", tipo)
        if request.user.tiene_permiso_erp(permiso_escritura, empresa):
            agregar(f"Registrar · {titulo}", "clinica_crear_historia_especialidad", tipo)

    return _respuesta(request, empresa, "clinica/paciente_expediente.html", "clinica/mobile_patient_expediente.html", {
        "empresa": empresa,
        "paciente": paciente,
        "acciones_expediente": acciones,
        "pacientes_url": _url(request, empresa, "clinica_pacientes"),
    })


@login_required
@require_safe
def consultar_seccion(request, empresa_slug, paciente_id, tipo):
    if tipo not in SECCIONES:
        raise Http404("Sección clínica no disponible.")
    empresa, paciente = _paciente_autorizado(request, empresa_slug, paciente_id)
    if not request.user.tiene_permiso_erp(LECTURAS_EXPEDIENTE_LIMITADO[tipo], empresa):
        raise PermissionDenied("No tiene permiso para consultar esta sección clínica.")

    contexto = {
        "empresa": empresa,
        "paciente": paciente,
        "tipo": tipo,
        "seccion_titulo": SECCIONES[tipo][0],
        "expediente_url": _url(request, empresa, "clinica_paciente_expediente", paciente.pk),
    }
    if tipo == "terapias":
        historias = HistoriaClinicaEspecialidad.objects.filter(empresa=empresa, paciente=paciente, tipo=tipo)
        contexto["notas"] = notas_historia_app(historias.select_related("profesional", "creado_por", "actualizado_por"))
    else:
        Programa, Sesion = (
            (ProgramaCamaraHiperbarica, SesionCamaraHiperbarica)
            if tipo == "camara_hiperbarica"
            else (ProgramaTerapiaPostQuirurgica, SesionTerapiaPostQuirurgica)
        )
        contexto["programas"] = Programa.objects.filter(empresa=empresa, paciente=paciente).prefetch_related(
            Prefetch("sesiones", queryset=Sesion.objects.filter(empresa=empresa, paciente=paciente)),
        )
    return _respuesta(request, empresa, "clinica/paciente_expediente_seccion.html", "clinica/mobile_patient_expediente_seccion.html", contexto)
