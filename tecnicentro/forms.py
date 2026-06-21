from django import forms

from core.models import Usuario
from facturacion.models import Cliente

from .models import BahiaServicio, ConfiguracionTecnicentro, DiagnosticoVehicular, EvidenciaOrden, OrdenServicio, Vehiculo


class RecepcionOrdenForm(forms.Form):
    cliente = forms.ModelChoiceField(queryset=Cliente.objects.none(), label="Cliente / propietario")
    placa = forms.CharField(max_length=20, label="Placa")
    marca = forms.CharField(max_length=80)
    modelo = forms.CharField(max_length=80)
    anio = forms.IntegerField(required=False, min_value=1900, max_value=2100, label="Ano")
    color = forms.CharField(max_length=50, required=False)
    tipo_vehiculo = forms.ChoiceField(choices=Vehiculo.TIPO_CHOICES, label="Tipo de vehiculo")
    combustible = forms.ChoiceField(choices=Vehiculo.COMBUSTIBLE_CHOICES)
    kilometraje_entrada = forms.IntegerField(min_value=0, label="Kilometraje")
    nivel_combustible = forms.ChoiceField(choices=OrdenServicio.NIVEL_COMBUSTIBLE)
    motivo_ingreso = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), label="Motivo de ingreso / sintomas")
    observaciones_recepcion = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}), label="Estado visual y observaciones")
    prioridad = forms.ChoiceField(choices=OrdenServicio.PRIORIDAD_CHOICES)
    tiempo_espera_estimado_min = forms.IntegerField(min_value=0, initial=30, label="Espera estimada (minutos)")
    tiempo_reparacion_estimado_min = forms.IntegerField(min_value=0, initial=60, label="Reparacion estimada (minutos)")
    deja_vehiculo = forms.BooleanField(required=False, initial=True, label="El cliente deja el vehiculo")
    autoriza_whatsapp = forms.BooleanField(required=False, initial=True, label="Autoriza avisos por WhatsApp")

    def __init__(self, *args, empresa=None, **kwargs):
        self.empresa = empresa
        super().__init__(*args, **kwargs)
        self.fields["cliente"].queryset = Cliente.objects.filter(empresa=empresa, activo=True).order_by("nombre")
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "garage-input")
        self.fields["placa"].widget.attrs.update({"placeholder": "HAA 0000", "style": "text-transform:uppercase"})

    def clean_placa(self):
        return self.cleaned_data["placa"].strip().upper()


class AsignacionOrdenForm(forms.ModelForm):
    class Meta:
        model = OrdenServicio
        fields = ["tecnico_asignado", "bahia", "tiempo_reparacion_estimado_min"]

    def __init__(self, *args, empresa=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["tecnico_asignado"].queryset = Usuario.objects.filter(empresa=empresa, is_active=True).order_by("first_name", "username")
        self.fields["bahia"].queryset = BahiaServicio.objects.filter(empresa=empresa, activa=True).order_by("codigo")


class DiagnosticoForm(forms.ModelForm):
    class Meta:
        model = DiagnosticoVehicular
        fields = ["sintomas_reportados", "hallazgos", "causa_probable", "recomendaciones", "requiere_prueba_ruta", "estado"]
        widgets = {
            "sintomas_reportados": forms.Textarea(attrs={"rows": 3}),
            "hallazgos": forms.Textarea(attrs={"rows": 5}),
            "causa_probable": forms.Textarea(attrs={"rows": 3}),
            "recomendaciones": forms.Textarea(attrs={"rows": 4}),
        }


class EvidenciaForm(forms.ModelForm):
    class Meta:
        model = EvidenciaOrden
        fields = ["etapa", "imagen", "descripcion"]
        widgets = {"imagen": forms.ClearableFileInput(attrs={"accept": "image/*", "capture": "environment"})}


class ConfiguracionTecnicentroForm(forms.ModelForm):
    class Meta:
        model = ConfiguracionTecnicentro
        fields = ["nombre_comercial", "tiempo_recepcion_minutos", "tiempo_diagnostico_minutos", "notificar_whatsapp", "mensaje_recepcion", "mensaje_listo"]


class BahiaServicioForm(forms.ModelForm):
    class Meta:
        model = BahiaServicio
        fields = ["codigo", "nombre", "especialidad", "activa"]
