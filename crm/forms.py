from django import forms

from facturacion.models import Cliente, Producto

from .models import CampaniaMarketing, CitaCliente, ConfiguracionCRM, PlantillaMensaje


class ConfiguracionCRMForm(forms.ModelForm):
    class Meta:
        model = ConfiguracionCRM
        fields = [
            "whatsapp_activo",
            "whatsapp_api_version",
            "whatsapp_phone_number_id",
            "whatsapp_business_account_id",
            "whatsapp_token",
            "whatsapp_numero_prueba",
            "whatsapp_plantilla_prueba",
            "whatsapp_idioma_plantilla",
            "whatsapp_plantilla_marketing",
            "whatsapp_idioma_marketing",
            "remitente_correo",
            "recordatorio_cumpleanos_activo",
            "recordatorio_citas_activo",
            "dias_alerta_producto",
        ]
        widgets = {
            "whatsapp_token": forms.PasswordInput(render_value=True),
        }
        help_texts = {
            "whatsapp_token": "Token de Meta/WhatsApp Cloud API. Guardalo solo si el cliente ya tiene credenciales.",
            "whatsapp_numero_prueba": "Numero autorizado para probar, con codigo de pais. Ejemplo: 50499999999.",
            "whatsapp_plantilla_prueba": "Para pruebas de Meta normalmente se usa hello_world.",
            "whatsapp_idioma_plantilla": "Para hello_world normalmente es en_US.",
            "whatsapp_plantilla_marketing": "Nombre exacto de la plantilla comercial aprobada en Meta. Ejemplo: promo_general_imagen.",
            "whatsapp_idioma_marketing": "Idioma aprobado de la plantilla comercial. Para Spanish normalmente usa es.",
            "dias_alerta_producto": "Dias antes para alertar productos con fecha de seguimiento o vencimiento.",
        }


class PlantillaMensajeForm(forms.ModelForm):
    class Meta:
        model = PlantillaMensaje
        fields = ["nombre", "tipo", "canal", "asunto", "mensaje", "imagen_promocional", "activa"]
        widgets = {
            "mensaje": forms.Textarea(attrs={"rows": 5}),
        }


class CampaniaMarketingForm(forms.ModelForm):
    class Meta:
        model = CampaniaMarketing
        fields = ["nombre", "plantilla", "audiencia", "fecha_programada", "estado"]
        widgets = {
            "fecha_programada": forms.DateTimeInput(attrs={"type": "datetime-local"}),
        }

    def __init__(self, *args, **kwargs):
        empresa = kwargs.pop("empresa", None)
        super().__init__(*args, **kwargs)
        if empresa:
            self.fields["plantilla"].queryset = PlantillaMensaje.objects.filter(empresa=empresa, activa=True)
        else:
            self.fields["plantilla"].queryset = PlantillaMensaje.objects.none()


class CitaClienteForm(forms.ModelForm):
    class Meta:
        model = CitaCliente
        fields = ["cliente", "producto", "titulo", "fecha_hora", "responsable", "estado", "observacion"]
        widgets = {
            "fecha_hora": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "observacion": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        empresa = kwargs.pop("empresa", None)
        super().__init__(*args, **kwargs)
        if empresa:
            self.fields["cliente"].queryset = Cliente.objects.filter(empresa=empresa, activo=True).order_by("nombre")
            self.fields["producto"].queryset = Producto.objects.filter(empresa=empresa, activo=True).order_by("nombre")
        else:
            self.fields["cliente"].queryset = Cliente.objects.none()
            self.fields["producto"].queryset = Producto.objects.none()
        self.fields["producto"].required = False
