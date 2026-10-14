from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import CuentaContable, validar_fecha_contable_abierta
from .moneda import validar_moneda_tipo_cambio


class RegularizacionMonedaFacturaForm(forms.Form):
    fecha = forms.DateField(
        label="Fecha del ajuste en un período abierto", initial=timezone.localdate,
        widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
    )
    tipo_cambio = forms.DecimalField(
        label="Tipo de cambio histórico revisado (HNL por USD)",
        max_digits=14, decimal_places=6, min_value=Decimal("0.000001"),
        help_text="Indica la tasa autorizada para convertir el documento original, respaldada por la revisión contable.",
        widget=forms.NumberInput(attrs={"step": "0.000001", "min": "0.000001"}),
    )
    cuenta_contrapartida = forms.ModelChoiceField(
        label="Cuenta de contrapartida autorizada", queryset=CuentaContable.objects.none(),
        help_text="La revisión contable debe determinar la cuenta que recibe la diferencia del saldo por cobrar.",
        empty_label="Selecciona una cuenta de esta empresa",
    )
    motivo = forms.CharField(
        label="Motivo y respaldo de la regularización", min_length=10, max_length=2000,
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text="Describe la corrección y el respaldo usado para autorizarla.",
    )
    confirmar = forms.BooleanField(
        required=False, label="Confirmo la fecha, tasa, contrapartida y diferencia de la revisión mostrada.",
    )
    revision_token = forms.CharField(required=False, widget=forms.HiddenInput())

    def __init__(self, *args, empresa, cuenta_cliente=None, fecha_origen=None, exigir_confirmacion=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.empresa = empresa
        self.fecha_origen = fecha_origen
        if fecha_origen:
            self.fields["fecha"].widget.attrs["min"] = fecha_origen.isoformat()
        cuentas = CuentaContable.objects.filter(empresa=empresa, activa=True, acepta_movimientos=True).order_by("codigo", "nombre")
        if cuenta_cliente:
            cuentas = cuentas.exclude(pk=cuenta_cliente.pk)
        self.fields["cuenta_contrapartida"].queryset = cuentas
        self.fields["confirmar"].required = exigir_confirmacion
        self.fields["confirmar"].error_messages["required"] = "Confirma los datos del ajuste después de revisar la vista previa."

    def clean_fecha(self):
        fecha = self.cleaned_data["fecha"]
        if self.fecha_origen and fecha < self.fecha_origen:
            raise forms.ValidationError("La fecha del ajuste debe ser igual o posterior a la fecha del asiento original y de sus aplicaciones contabilizadas.")
        try:
            validar_fecha_contable_abierta(self.empresa.pk, fecha)
        except ValidationError as error:
            raise forms.ValidationError(error.messages)
        return fecha

    def clean_tipo_cambio(self):
        _moneda, tasa = validar_moneda_tipo_cambio("USD", self.cleaned_data["tipo_cambio"])
        return tasa
