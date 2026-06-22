from django import forms

from facturacion.models import Cliente, Producto
from clinica.models import Paciente, ProfesionalSalud, ServicioClinico

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
            "whatsapp_plantilla_cita",
            "whatsapp_idioma_cita",
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
            "whatsapp_plantilla_cita": "Nombre exacto de la plantilla transaccional aprobada en Meta. Debe tener 6 variables: paciente, aviso, fecha, hora, tipo de consulta y profesional.",
            "whatsapp_idioma_cita": "Código de idioma aprobado para la plantilla de citas, normalmente es.",
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
        fields = ["cliente", "paciente", "producto", "servicio_clinico", "titulo", "fecha_hora", "duracion_minutos", "responsable", "profesional_salud", "estado", "observacion", "enviar_confirmacion_whatsapp", "recordatorio_semana_whatsapp", "recordatorio_dia_whatsapp"]
        widgets = {
            "fecha_hora": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
            "observacion": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        empresa = kwargs.pop("empresa", None)
        self.empresa = empresa
        super().__init__(*args, **kwargs)
        self.es_clinica = bool(empresa and (empresa.tipo_solucion == "clinica" or empresa.tiene_modulo_activo("clinica_medica")))
        self.es_hospital_mia = bool(empresa and empresa.slug == "hospital_mia")
        if empresa:
            self.fields["cliente"].queryset = Cliente.objects.filter(empresa=empresa, activo=True).order_by("nombre")
            self.fields["producto"].queryset = Producto.objects.filter(empresa=empresa, activo=True).order_by("nombre")
            self.fields["paciente"].queryset = Paciente.objects.filter(empresa=empresa, activo=True).order_by("nombre")
            self.fields["servicio_clinico"].queryset = ServicioClinico.objects.filter(empresa=empresa, activo=True).order_by("nombre")
            self.fields["profesional_salud"].queryset = ProfesionalSalud.objects.filter(empresa=empresa, activo=True).order_by("nombre")
        else:
            self.fields["cliente"].queryset = Cliente.objects.none()
            self.fields["producto"].queryset = Producto.objects.none()
            self.fields["paciente"].queryset = Paciente.objects.none()
            self.fields["servicio_clinico"].queryset = ServicioClinico.objects.none()
            self.fields["profesional_salud"].queryset = ProfesionalSalud.objects.none()
        self.fields["producto"].required = False
        self.fields["duracion_minutos"].label = "Duración (minutos)"
        self.fields["fecha_hora"].input_formats = ["%Y-%m-%dT%H:%M"]
        if self.es_clinica:
            for nombre in ["cliente", "producto", "titulo", "responsable", "duracion_minutos"]:
                self.fields.pop(nombre)
            self.fields["paciente"].label = "Paciente"
            self.fields["paciente"].required = True
            self.fields["servicio_clinico"].label = "Tipo de consulta"
            self.fields["servicio_clinico"].required = True
            self.fields["profesional_salud"].label = "Doctor / profesional"
            self.fields["profesional_salud"].required = True
            self.fields["observacion"].label = "Motivo o notas de la cita"
            self.fields["enviar_confirmacion_whatsapp"].label = "Enviar confirmación por WhatsApp al guardar"
            self.fields["recordatorio_semana_whatsapp"].label = "Recordar 7 días antes"
            self.fields["recordatorio_dia_whatsapp"].label = "Recordar 1 día antes"
            if not self.es_hospital_mia:
                for nombre in ["enviar_confirmacion_whatsapp", "recordatorio_semana_whatsapp", "recordatorio_dia_whatsapp"]:
                    self.fields.pop(nombre)
            self.order_fields(["paciente", "servicio_clinico", "profesional_salud", "fecha_hora", "estado", "observacion", "enviar_confirmacion_whatsapp", "recordatorio_semana_whatsapp", "recordatorio_dia_whatsapp"])
        else:
            for nombre in ["paciente", "servicio_clinico", "profesional_salud", "enviar_confirmacion_whatsapp", "recordatorio_semana_whatsapp", "recordatorio_dia_whatsapp"]:
                self.fields.pop(nombre)

    def save(self, commit=True):
        cita = super().save(commit=False)
        if self.es_clinica:
            cita.titulo = cita.servicio_clinico.nombre
            cita.responsable = cita.profesional_salud.nombre
            cita.cliente = cita.paciente.cliente
            cita.producto = None
            cita.duracion_minutos = cita.servicio_clinico.duracion_minutos
        if commit:
            cita.save()
        return cita
