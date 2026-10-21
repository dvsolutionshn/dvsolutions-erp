"""Captura explícita del respaldo monetario de cada partida histórica."""
from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.forms import BaseFormSet, formset_factory
from django.utils import timezone

from .models import CuentaContable, validar_fecha_contable_abierta


class FiltrosHistoricosUSDForm(forms.Form):
    desde = forms.DateField(required=False, label="Desde", widget=forms.DateInput(attrs={"type": "date"}))
    hasta = forms.DateField(required=False, label="Hasta", widget=forms.DateInput(attrs={"type": "date"}))
    fuente = forms.ChoiceField(required=False, label="Documento", choices=[
        ("", "Todos los documentos"), ("factura", "Facturas"), ("pago_factura", "Cobros y comisiones"),
        ("nota_credito", "Notas de crédito"), ("pago_comision_proveedor", "Pagos de comisión"),
    ])
    estado = forms.ChoiceField(required=False, label="Revisión", choices=[
        ("", "Todos los asientos históricos"), ("pendiente", "Pendiente de revisión integral"),
        ("parcial", "Saldo de cliente regularizado"), ("integral", "Partidas revisadas"),
        ("inconsistente", "Fuente inconsistente"), ("sin_asiento", "Documentos sin asiento"),
    ])

    def clean(self):
        datos = super().clean()
        if datos.get("desde") and datos.get("hasta") and datos["desde"] > datos["hasta"]:
            raise forms.ValidationError("La fecha inicial debe ser igual o anterior a la fecha final.")
        return datos


class RevisionConversionForm(forms.Form):
    fecha = forms.DateField(label="Fecha de revisión en un período abierto", initial=timezone.localdate,
                           widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}))
    cuenta_contrapartida = forms.ModelChoiceField(label="Contrapartida autorizada, si existe diferencia",
        required=False, queryset=CuentaContable.objects.none(), empty_label="Sin contrapartida",
        help_text="Selecciona una cuenta de esta empresa si los importes revisados requieren compensar una diferencia.")
    motivo = forms.CharField(label="Respaldo y motivo de la revisión", min_length=10, max_length=2000,
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text="Identifica los documentos, tasas y comprobantes que respaldan cada partida revisada.")
    confirmar = forms.BooleanField(required=False,
        label="Confirmo los importes, monedas, tasas, cuentas, fecha y diferencias de la vista previa.")
    revision_token = forms.CharField(required=False, widget=forms.HiddenInput())

    def __init__(self, *args, empresa, exigir_confirmacion=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.empresa = empresa
        self.fields["cuenta_contrapartida"].queryset = CuentaContable.objects.filter(
            empresa=empresa, activa=True, acepta_movimientos=True).order_by("codigo", "nombre")
        self.fields["confirmar"].required = exigir_confirmacion
        self.fields["confirmar"].error_messages["required"] = "Confirma los datos después de revisar las partidas."

    def clean_fecha(self):
        fecha = self.cleaned_data["fecha"]
        try:
            validar_fecha_contable_abierta(self.empresa.pk, fecha)
        except ValidationError as error:
            raise forms.ValidationError(error.messages)
        return fecha


class PartidaRevisionForm(forms.Form):
    linea_id = forms.IntegerField(min_value=1, widget=forms.HiddenInput())
    moneda = forms.ChoiceField(label="Tratamiento revisado", choices=[
        ("HNL", "HNL · conservar importe registrado"), ("USD", "USD · convertir según respaldo"),
    ])
    importe_original = forms.DecimalField(label="Importe según respaldo", max_digits=14, decimal_places=2,
        min_value=Decimal("0.01"), widget=forms.NumberInput(attrs={"step": "0.01", "min": "0.01"}))
    tipo_cambio = forms.DecimalField(label="HNL por unidad de origen", max_digits=14, decimal_places=6,
        min_value=Decimal("0.000001"), widget=forms.NumberInput(attrs={"step": "0.000001", "min": "0.000001"}))


class PartidasRevisionBaseFormSet(BaseFormSet):
    def __init__(self, *args, lineas, **kwargs):
        self.lineas = list(lineas)
        super().__init__(*args, **kwargs)

    def clean(self):
        super().clean()
        if any(self.errors):
            return
        ids = [form.cleaned_data["linea_id"] for form in self.forms]
        if len(ids) != len(set(ids)) or set(ids) != {linea.pk for linea in self.lineas}:
            raise forms.ValidationError("Revisa todas las partidas originales exactamente una vez, sin agregar partidas de otro asiento.")
        por_id = {linea.pk: linea for linea in self.lineas}
        for form in self.forms:
            datos = form.cleaned_data
            if datos["moneda"] == "HNL":
                linea = por_id[datos["linea_id"]]
                if datos["tipo_cambio"] != 1 or datos["importe_original"] != linea.debe + linea.haber:
                    raise forms.ValidationError("Una partida conservada en HNL debe mantener su importe registrado y una tasa de 1.")


PartidasRevisionFormSet = formset_factory(PartidaRevisionForm, formset=PartidasRevisionBaseFormSet, extra=0,
    max_num=500, validate_max=True, absolute_max=500)
