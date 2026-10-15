"""Instalación y enlaces del catálogo propio; ninguna acción abarca otras empresas."""

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods
import json

from .alcance_catalogo import es_empresa_rumi
from .catalogo_rumi_roles import CAMPOS_CONFIG, ROLES, usa_catalogo_rumi, validar_cuenta_rol_rumi
from .models import (
    ConfiguracionContableEmpresa, CuentaContable, RegistroSustitucionCatalogoRumi,
    VinculoCuentaOperativaRumi, bloquear_empresas_contables,
)
from .sustitucion_catalogo_rumi import (
    aplicar_sustitucion, leer_catalogo_para_sustitucion, preparar_sustitucion,
)

SALT = "contabilidad.sustitucion_catalogo_rumi.v1"


def _empresa_autorizada(request, empresa_slug):
    from .views import _empresa_desde_slug, _verificar_permiso_contable
    empresa = _empresa_desde_slug(empresa_slug)
    _verificar_permiso_contable(request, empresa, "puede_catalogo_cuentas")
    if not es_empresa_rumi(empresa):
        raise Http404("Esta opción pertenece exclusivamente a RUMI.")
    return empresa


class ArchivoCatalogoRumiForm(forms.Form):
    archivo = forms.FileField(
        label="Catálogo de cuentas RUMI",
        help_text="Seleccione el Excel original. Se revisan las cuentas base y sus enlaces antes de confirmar.",
        widget=forms.ClearableFileInput(attrs={"accept": ".xlsx,.xlsm"}),
    )

    def clean_archivo(self):
        archivo = self.cleaned_data["archivo"]
        if not archivo.name.lower().endswith((".xlsx", ".xlsm")):
            raise forms.ValidationError("Seleccione un archivo Excel .xlsx o .xlsm.")
        return archivo


@login_required
@require_http_methods(["GET", "POST"])
def sustituir_catalogo_rumi(request, empresa_slug):
    empresa = _empresa_autorizada(request, empresa_slug)
    if usa_catalogo_rumi(empresa):
        messages.info(request, "RUMI ya utiliza su catálogo. Puede revisar sus enlaces contables.")
        return redirect("configuracion_contable", empresa_slug=empresa.slug)
    form = ArchivoCatalogoRumiForm(request.POST or None, request.FILES or None)
    plan, token, error = None, "", ""
    accion = request.POST.get("accion", "revisar")
    if request.method == "POST":
        try:
            if accion == "aplicar":
                revision = signing.loads(request.POST.get("revision_token", ""), salt=SALT, max_age=1800)
                if (revision.get("empresa_id"), revision.get("usuario_id"), revision.get("version")) != (
                    empresa.pk, request.user.pk, 1,
                ):
                    raise ValidationError("Esta revisión pertenece a otra empresa o usuario.")
                if not forms.BooleanField(required=False).clean(request.POST.get("confirmar")):
                    raise ValidationError("Confirme que revisó la sustitución antes de aplicarla.")
                resultado = aplicar_sustitucion(
                    empresa, revision["datos"], firma=revision["firma"], usuario=request.user,
                )
                messages.success(request,
                    f"Catálogo RUMI instalado: {resultado['importadas']} cuentas importadas, "
                    f"{resultado['eliminadas']} cuentas base sin uso retiradas y "
                    f"{resultado['historicas_conservadas']} conservadas con sus enlaces. "
                    f"Hay {resultado['pendientes']} operaciones pendientes de configurar."
                )
                return redirect("configuracion_contable", empresa_slug=empresa.slug)
            if accion != "revisar":
                raise ValidationError("Revise el archivo o aplique una revisión válida.")
            if form.is_valid():
                lectura = leer_catalogo_para_sustitucion(empresa, form.cleaned_data["archivo"])
                plan = preparar_sustitucion(empresa, lectura["datos"])
                # El respaldo permanece en el servidor; la revisión sólo firma archivo y estado.
                token = signing.dumps({
                    "version": 1, "empresa_id": empresa.pk, "usuario_id": request.user.pk,
                    "datos": lectura["datos"], "firma": plan["firma"],
                }, salt=SALT, compress=True)
                plan = {clave: valor for clave, valor in plan.items() if clave != "respaldo"}
                plan.update({"archivo": form.cleaned_data["archivo"].name, "hoja": lectura["hoja"],
                             "condicionadas": lectura.get("condicionadas", 0), "controles": lectura.get("controles", 0)})
        except (signing.BadSignature, signing.SignatureExpired):
            error = "La revisión venció o fue modificada. Vuelva a subir y revisar el archivo."
        except ValidationError as excepcion:
            error = "; ".join(excepcion.messages)
        except (KeyError, TypeError):
            error = "La revisión está incompleta. Vuelva a revisar el archivo."
    return render(request, "contabilidad/sustituir_catalogo_rumi.html", {
        "empresa": empresa, "form": form, "plan": plan, "revision_token": token, "error": error,
    })


class EnlacesContablesRumiForm(forms.Form):
    isv_compras_recuperable = forms.BooleanField(required=False, label="RUMI recupera el ISV de compras")

    def __init__(self, *args, empresa, configuracion, **kwargs):
        super().__init__(*args, **kwargs)
        self.empresa, self.configuracion = empresa, configuracion
        vinculos = {v.clave: v.cuenta_id for v in VinculoCuentaOperativaRumi.objects.filter(empresa=empresa)}
        cuentas = CuentaContable.objects.filter(empresa=empresa, activa=True, acepta_movimientos=True)
        self.sugerencias = []
        self.initial["isv_compras_recuperable"] = configuracion.isv_compras_recuperable
        self.fields["isv_compras_recuperable"].help_text = "Se aplica a nuevas compras. Conserva la valoración de las compras ya aplicadas."
        for clave, definicion in ROLES.items():
            nombre = "rol_" + clave
            campo = CAMPOS_CONFIG.get(clave)
            valor = (getattr(configuracion, campo + "_id") or vinculos.get(clave)) if campo else vinculos.get(clave)
            self.initial[nombre] = valor
            sugerida = CuentaContable.objects.filter(empresa=empresa, codigo=definicion.get("codigo_sugerido")).first()
            ayuda = "Seleccione la cuenta usada para esta operación. Si queda pendiente, la operación solicitará el enlace antes de contabilizar."
            if sugerida:
                ayuda += f" Referencia del catálogo: {sugerida.codigo} - {sugerida.nombre}."
                if not sugerida.activa:
                    ayuda += " Está condicionada e inactiva; revise su aplicación antes de activarla."
                    self.sugerencias.append(sugerida)
            self.fields[nombre] = forms.ModelChoiceField(
                required=False, label=definicion["etiqueta"],
                queryset=cuentas.filter(tipo__in=definicion["tipos"]).order_by("codigo", "nombre"),
                empty_label="Pendiente de configurar", help_text=ayuda,
            )

    def clean(self):
        datos = super().clean()
        for clave in ROLES:
            cuenta = datos.get("rol_" + clave)
            if cuenta:
                try:
                    validar_cuenta_rol_rumi(self.empresa.pk, clave, cuenta)
                except ValidationError as error:
                    self.add_error("rol_" + clave, error)
        return datos

    def save(self):
        with transaction.atomic():
            bloquear_empresas_contables([self.empresa.pk])
            if not usa_catalogo_rumi(self.empresa):
                raise ValidationError("Instale el catálogo RUMI antes de configurar sus enlaces.")
            configuracion = ConfiguracionContableEmpresa.objects.select_for_update().get(empresa=self.empresa)
            for clave in ROLES:
                cuenta = self.cleaned_data.get("rol_" + clave)
                if cuenta:
                    cuenta.refresh_from_db()
                    validar_cuenta_rol_rumi(self.empresa.pk, clave, cuenta)
                campo = CAMPOS_CONFIG.get(clave)
                if campo:
                    setattr(configuracion, campo, cuenta)
                    VinculoCuentaOperativaRumi.objects.filter(empresa=self.empresa, clave=clave).delete()
                elif cuenta:
                    VinculoCuentaOperativaRumi.objects.update_or_create(
                        empresa=self.empresa, clave=clave, defaults={"cuenta": cuenta},
                    )
                else:
                    VinculoCuentaOperativaRumi.objects.filter(empresa=self.empresa, clave=clave).delete()
            configuracion.isv_compras_recuperable = self.cleaned_data["isv_compras_recuperable"]
            configuracion.save()


def configuracion_rumi(request, empresa):
    from .views import _verificar_permiso_contable
    _verificar_permiso_contable(request, empresa, "puede_catalogo_cuentas")
    configuracion = ConfiguracionContableEmpresa.objects.get(empresa=empresa)
    form = EnlacesContablesRumiForm(request.POST or None, empresa=empresa, configuracion=configuracion)
    if request.method == "POST" and form.is_valid():
        try:
            form.save()
            messages.success(request, "Enlaces contables de RUMI guardados correctamente.")
            return redirect("configuracion_contable", empresa_slug=empresa.slug)
        except ValidationError as error:
            form.add_error(None, error)
    registro = RegistroSustitucionCatalogoRumi.objects.filter(empresa=empresa).order_by("-pk").first()
    return render(request, "contabilidad/configuracion_rumi.html", {
        "empresa": empresa, "form": form, "registro": registro,
        "sugerencias": {c.pk: c for c in form.sugerencias}.values(),
        "cuentas_disponibles": CuentaContable.objects.filter(empresa=empresa, activa=True, acepta_movimientos=True).count(),
    })


@login_required
@require_http_methods(["GET"])
def respaldo_catalogo_rumi(request, empresa_slug, registro_id):
    empresa = _empresa_autorizada(request, empresa_slug)
    registro = get_object_or_404(RegistroSustitucionCatalogoRumi, empresa=empresa, pk=registro_id)
    respuesta = HttpResponse(json.dumps({
        "empresa": empresa.slug, "registro": registro.pk, "respaldo": registro.respaldo,
        "resumen": registro.resumen,
    }, ensure_ascii=False, indent=2), content_type="application/json; charset=utf-8")
    respuesta["Content-Disposition"] = f'attachment; filename="respaldo_catalogo_rumi_{registro.pk}.json"'
    return respuesta
