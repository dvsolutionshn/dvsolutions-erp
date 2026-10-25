from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from core.models import Empresa

from .models import Paciente
from .restricciones_citas import cambiar_restriccion_citas, contar_citas_futuras, puede_ver_motivo


class RestriccionCitasForm(forms.Form):
    motivo = forms.CharField(label="Motivo", max_length=2000, widget=forms.Textarea(attrs={"rows": 3}))
    observacion = forms.CharField(label="Observación (opcional)", required=False, max_length=4000, widget=forms.Textarea(attrs={"rows": 4}))
    confirmar = forms.BooleanField(label="Confirmo el cambio de disponibilidad para nuevas citas.")


def _restriccion(request, empresa_slug, paciente_id, *, habilitar=False):
    empresa = get_object_or_404(Empresa, slug=empresa_slug)
    if not request.user.puede_acceder_empresa(empresa):
        raise PermissionDenied("No tiene acceso a esta empresa.")
    paciente = get_object_or_404(Paciente, pk=paciente_id, empresa=empresa)
    modo_app = request.GET.get("app") == "1" or request.POST.get("app") == "1"
    permiso = "puede_habilitar_citas_paciente" if habilitar else "puede_restringir_citas_paciente"
    puede_cambiar = request.user.tiene_permiso_erp(permiso, empresa)
    puede_historial = any(request.user.tiene_permiso_erp(nombre, empresa) for nombre in (
        "puede_restringir_citas_paciente", "puede_habilitar_citas_paciente", "puede_ver_motivo_restriccion_paciente",
    ))
    if not puede_historial or (request.method == "POST" and not puede_cambiar):
        raise PermissionDenied("No tiene permiso para cambiar esta restricción.")
    form = RestriccionCitasForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        try:
            paciente, cambiado, futuras = cambiar_restriccion_citas(
                paciente=paciente, usuario=request.user, restringir=not habilitar,
                motivo=form.cleaned_data["motivo"], observacion=form.cleaned_data["observacion"],
            )
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            if cambiado:
                messages.success(request, "Paciente habilitado nuevamente para citas." if habilitar else "Paciente marcado como NO AGENDAR.")
                if not habilitar and futuras:
                    messages.warning(request, f"Este paciente tiene {futuras} cita(s) futura(s). Se mantienen para revisión manual; no se cancelaron.")
            else:
                messages.info(request, "El paciente ya tiene el estado seleccionado.")
            destino = reverse("clinica_pacientes", args=[empresa.slug])
            return redirect(f"{destino}?app=1" if modo_app else destino)
    historial = paciente.historial_restricciones_citas.filter(empresa=empresa).select_related("usuario")
    if not puede_ver_motivo(request.user, empresa):
        historial = historial.defer("motivo", "observacion")
    return render(request, "clinica/paciente_restriccion_citas.html", {
        "empresa": empresa, "paciente": paciente, "habilitar": habilitar, "form": form, "modo_app": modo_app,
        "puede_cambiar_restriccion": puede_cambiar,
        "citas_futuras": contar_citas_futuras(paciente), "historial_restricciones": historial,
    })


@login_required
@require_http_methods(["GET", "POST"])
def restringir(request, empresa_slug, paciente_id):
    return _restriccion(request, empresa_slug, paciente_id)


@login_required
@require_http_methods(["GET", "POST"])
def habilitar(request, empresa_slug, paciente_id):
    return _restriccion(request, empresa_slug, paciente_id, habilitar=True)
