from django import forms

from .models import (
    CitaClinica,
    ConsentimientoClinico,
    ExpedienteEvento,
    MedicamentoPrescrito,
    Paciente,
    ProfesionalSalud,
    ServicioClinico,
    TratamientoPaciente,
)


class BaseClinicaForm(forms.ModelForm):
    def __init__(self, *args, empresa=None, **kwargs):
        self.empresa = empresa
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.Textarea):
                field.widget.attrs.setdefault("rows", 3)


class PacienteForm(BaseClinicaForm):
    class Meta:
        model = Paciente
        fields = [
            "expediente_codigo",
            "identidad",
            "nombre",
            "fecha_nacimiento",
            "sexo",
            "telefono",
            "whatsapp",
            "correo",
            "direccion",
            "contacto_emergencia",
            "telefono_emergencia",
            "alergias",
            "antecedentes_medicos",
            "medicamentos_actuales",
            "notas_privadas",
            "acepta_promociones",
            "activo",
        ]
        widgets = {
            "fecha_nacimiento": forms.DateInput(attrs={"type": "date"}),
        }


class ProfesionalSaludForm(BaseClinicaForm):
    class Meta:
        model = ProfesionalSalud
        fields = ["nombre", "especialidad", "colegiacion", "telefono", "activo"]


class ServicioClinicoForm(BaseClinicaForm):
    class Meta:
        model = ServicioClinico
        fields = ["nombre", "categoria", "duracion_minutos", "precio_referencia", "requiere_consentimiento", "activo"]


class CitaClinicaForm(BaseClinicaForm):
    class Meta:
        model = CitaClinica
        fields = ["paciente", "profesional", "servicio", "fecha_hora", "estado", "canal", "motivo", "sala", "observaciones"]
        widgets = {
            "fecha_hora": forms.DateTimeInput(attrs={"type": "datetime-local"}),
        }

    def __init__(self, *args, empresa=None, **kwargs):
        super().__init__(*args, empresa=empresa, **kwargs)
        if empresa:
            self.fields["paciente"].queryset = Paciente.objects.filter(empresa=empresa, activo=True).order_by("nombre")
            self.fields["profesional"].queryset = ProfesionalSalud.objects.filter(empresa=empresa, activo=True).order_by("nombre")
            self.fields["servicio"].queryset = ServicioClinico.objects.filter(empresa=empresa, activo=True).order_by("nombre")
        else:
            self.fields["paciente"].queryset = Paciente.objects.none()
            self.fields["profesional"].queryset = ProfesionalSalud.objects.none()
            self.fields["servicio"].queryset = ServicioClinico.objects.none()
        self.fields["profesional"].required = False
        self.fields["servicio"].required = False


class TratamientoPacienteForm(BaseClinicaForm):
    class Meta:
        model = TratamientoPaciente
        fields = ["paciente", "servicio", "profesional", "nombre", "fecha_inicio", "fecha_fin_estimada", "estado", "objetivo", "plan_clinico", "monto_estimado"]
        widgets = {
            "fecha_inicio": forms.DateInput(attrs={"type": "date"}),
            "fecha_fin_estimada": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, empresa=None, **kwargs):
        super().__init__(*args, empresa=empresa, **kwargs)
        if empresa:
            self.fields["paciente"].queryset = Paciente.objects.filter(empresa=empresa, activo=True).order_by("nombre")
            self.fields["servicio"].queryset = ServicioClinico.objects.filter(empresa=empresa, activo=True).order_by("nombre")
            self.fields["profesional"].queryset = ProfesionalSalud.objects.filter(empresa=empresa, activo=True).order_by("nombre")
        else:
            self.fields["paciente"].queryset = Paciente.objects.none()
            self.fields["servicio"].queryset = ServicioClinico.objects.none()
            self.fields["profesional"].queryset = ProfesionalSalud.objects.none()
        self.fields["servicio"].required = False
        self.fields["profesional"].required = False


class ExpedienteEventoForm(BaseClinicaForm):
    class Meta:
        model = ExpedienteEvento
        fields = ["paciente", "cita", "tratamiento", "profesional", "tipo", "fecha", "titulo", "descripcion", "diagnostico", "plan", "signos_vitales"]
        widgets = {
            "fecha": forms.DateTimeInput(attrs={"type": "datetime-local"}),
        }

    def __init__(self, *args, empresa=None, paciente=None, **kwargs):
        super().__init__(*args, empresa=empresa, **kwargs)
        if empresa:
            pacientes = Paciente.objects.filter(empresa=empresa, activo=True).order_by("nombre")
            if paciente:
                pacientes = pacientes.filter(id=paciente.id)
            self.fields["paciente"].queryset = pacientes
            self.fields["cita"].queryset = CitaClinica.objects.filter(empresa=empresa).order_by("-fecha_hora")
            self.fields["tratamiento"].queryset = TratamientoPaciente.objects.filter(empresa=empresa).order_by("-fecha_inicio")
            self.fields["profesional"].queryset = ProfesionalSalud.objects.filter(empresa=empresa, activo=True).order_by("nombre")
        else:
            self.fields["paciente"].queryset = Paciente.objects.none()
            self.fields["cita"].queryset = CitaClinica.objects.none()
            self.fields["tratamiento"].queryset = TratamientoPaciente.objects.none()
            self.fields["profesional"].queryset = ProfesionalSalud.objects.none()
        self.fields["cita"].required = False
        self.fields["tratamiento"].required = False
        self.fields["profesional"].required = False


class MedicamentoPrescritoForm(BaseClinicaForm):
    class Meta:
        model = MedicamentoPrescrito
        fields = ["paciente", "tratamiento", "medicamento", "dosis", "frecuencia", "duracion", "indicaciones", "activo", "fecha_prescripcion"]
        widgets = {
            "fecha_prescripcion": forms.DateInput(attrs={"type": "date"}),
        }


class ConsentimientoClinicoForm(BaseClinicaForm):
    class Meta:
        model = ConsentimientoClinico
        fields = ["paciente", "tratamiento", "cita", "titulo", "version", "contenido", "firmado_por", "fecha_firma", "archivo", "estado"]
        widgets = {
            "fecha_firma": forms.DateTimeInput(attrs={"type": "datetime-local"}),
        }
