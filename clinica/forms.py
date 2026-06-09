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

RH_CHOICES = [
    ("", "No indicado"),
    ("O+", "O+"),
    ("O-", "O-"),
    ("A+", "A+"),
    ("A-", "A-"),
    ("B+", "B+"),
    ("B-", "B-"),
    ("AB+", "AB+"),
    ("AB-", "AB-"),
]


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
            "tipo_id",
            "identidad",
            "primer_apellido",
            "segundo_apellido",
            "primer_nombre",
            "segundo_nombre",
            "nombre",
            "fecha_nacimiento",
            "rh",
            "sexo",
            "estado_civil",
            "genero",
            "foto_perfil",
            "telefono",
            "prefijo_telefono",
            "whatsapp",
            "celular_2",
            "correo",
            "recibir_email",
            "extranjero",
            "direccion",
            "departamento",
            "municipio",
            "zona_residencial",
            "codigo_postal",
            "barrio",
            "pais",
            "lugar_nacimiento",
            "ocupacion",
            "otra_ocupacion",
            "comentarios",
            "acompanante_nombre",
            "acompanante_relacion",
            "acompanante_telefono",
            "acompanante_celular",
            "acompanante_email",
            "responsable_nombre",
            "responsable_telefono",
            "responsable_relacion",
            "escolaridad",
            "pertenencia_etnica",
            "procedencia",
            "nacionalidad",
            "contacto_emergencia",
            "telefono_emergencia",
            "es_alergico",
            "alergias",
            "antecedentes_medicos",
            "medicamentos_actuales",
            "notas_privadas",
            "acepta_promociones",
            "activo",
        ]
        widgets = {
            "fecha_nacimiento": forms.DateInput(attrs={"type": "date"}),
            "foto_perfil": forms.ClearableFileInput(attrs={"accept": "image/*"}),
            "nombre": forms.HiddenInput(),
            "rh": forms.Select(choices=RH_CHOICES),
        }
        labels = {
            "tipo_id": "Tipo de ID",
            "identidad": "No. de documento",
            "primer_apellido": "1er apellido",
            "segundo_apellido": "2do apellido",
            "primer_nombre": "1er nombre",
            "segundo_nombre": "2do nombre",
            "fecha_nacimiento": "Fecha de nacimiento",
            "rh": "RH",
            "estado_civil": "Estado civil",
            "genero": "Genero",
            "foto_perfil": "Foto inicial del paciente",
            "prefijo_telefono": "Prefijo",
            "telefono": "Telefono",
            "whatsapp": "Celular",
            "celular_2": "Celular 2",
            "correo": "Email",
            "recibir_email": "Recibir email",
            "extranjero": "Extranjero",
            "direccion": "Direccion",
            "lugar_nacimiento": "Lugar de nacimiento",
            "otra_ocupacion": "Otra ocupacion",
            "zona_residencial": "Zona residencial",
            "codigo_postal": "Codigo postal",
            "pais": "Pais",
            "acompanante_nombre": "Nombre",
            "acompanante_relacion": "Relacion",
            "acompanante_telefono": "Telefono",
            "acompanante_celular": "Celular",
            "acompanante_email": "Email",
            "responsable_nombre": "Nombre responsable",
            "responsable_telefono": "Telefono responsable",
            "responsable_relacion": "Relacion responsable",
            "pertenencia_etnica": "Pertenencia etnica",
            "es_alergico": "Alergico",
            "alergias": "Detalle de alergias",
        }
        help_texts = {
            "foto_perfil": "Se guardara como primera foto de evolucion del expediente.",
            "expediente_codigo": "Codigo automatico del expediente clinico.",
            "comentarios": "Notas administrativas de ingreso o recepcion.",
            "alergias": "Indica medicamento, alimento, material o sustancia que provoca reaccion.",
        }

    def __init__(self, *args, empresa=None, **kwargs):
        super().__init__(*args, empresa=empresa, **kwargs)
        self.fields["nombre"].required = False
        self.fields["expediente_codigo"].widget.attrs["readonly"] = "readonly"
        self.fields["prefijo_telefono"].initial = self.fields["prefijo_telefono"].initial or "Honduras (+504)"
        for field_name in ["primer_nombre", "primer_apellido", "identidad"]:
            self.fields[field_name].required = True

    def clean(self):
        cleaned_data = super().clean()
        partes_nombre = [
            cleaned_data.get("primer_nombre"),
            cleaned_data.get("segundo_nombre"),
            cleaned_data.get("primer_apellido"),
            cleaned_data.get("segundo_apellido"),
        ]
        nombre = " ".join(parte.strip() for parte in partes_nombre if parte and parte.strip())
        if nombre:
            cleaned_data["nombre"] = nombre
        if not cleaned_data.get("es_alergico"):
            cleaned_data["alergias"] = ""
        elif not (cleaned_data.get("alergias") or "").strip():
            self.add_error("alergias", "Indica a que es alergico el paciente.")
        return cleaned_data


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
