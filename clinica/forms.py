from django import forms

from .models import (
    CitaClinica,
    ConsentimientoClinico,
    ExpedienteEvento,
    HistoriaClinicaEspecialidad,
    MedicamentoPrescrito,
    Paciente,
    PreconsultaClinica,
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
        self.fields["identidad"].widget.attrs.update({
            "inputmode": "numeric",
            "pattern": "[0-9]*",
            "autocomplete": "off",
            "placeholder": "Solo numeros, sin guiones",
        })
        self.fields["prefijo_telefono"].initial = self.fields["prefijo_telefono"].initial or "Honduras (+504)"
        for field_name in ["primer_nombre", "primer_apellido", "identidad"]:
            self.fields[field_name].required = True

    def clean_identidad(self):
        identidad = (self.cleaned_data.get("identidad") or "").strip()
        if identidad and not identidad.isdigit():
            raise forms.ValidationError("El No. de documento solo debe contener numeros, sin guiones ni espacios.")
        return identidad

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


class HistoriaClinicaEspecialidadForm(BaseClinicaForm):
    CAMPOS_POR_TIPO = {
        "capilar": {
            "motivo_consulta": "Motivo de consulta capilar",
            "antecedentes": "Antecedentes personales y capilares",
            "evaluacion_clinica": "Evaluacion capilar y hallazgos",
            "procedimiento": "Procedimiento o tecnica realizada",
            "plan_tratamiento": "Plan capilar",
        },
        "cirugia_plastica": {
            "motivo_consulta": "Motivo y expectativa quirurgica",
            "antecedentes": "Antecedentes medicos y quirurgicos",
            "evaluacion_clinica": "Examen fisico y valoracion preoperatoria",
            "procedimiento": "Procedimiento propuesto o realizado",
            "plan_tratamiento": "Plan quirurgico y seguimiento",
        },
        "enfermeria": {
            "motivo_consulta": "Motivo de atencion",
            "antecedentes": "Antecedentes relevantes para enfermeria",
            "evaluacion_clinica": "Valoracion de enfermeria",
            "procedimiento": "Cuidados, medicamentos e insumos aplicados",
            "plan_tratamiento": "Plan de cuidados",
        },
        "terapias": {
            "motivo_consulta": "Motivo y objetivo de la terapia",
            "antecedentes": "Antecedentes funcionales",
            "evaluacion_clinica": "Evaluacion inicial o evolucion funcional",
            "procedimiento": "Tecnicas y terapia aplicada",
            "plan_tratamiento": "Plan de sesiones y objetivos",
        },
        "camara_hiperbarica": {
            "motivo_consulta": "Indicacion de terapia hiperbarica",
            "antecedentes": "Antecedentes y contraindicaciones",
            "evaluacion_clinica": "Evaluacion previa y posterior a la sesion",
            "procedimiento": "Presion, duracion y numero de sesion",
            "plan_tratamiento": "Protocolo de sesiones",
        },
    }

    class Meta:
        model = HistoriaClinicaEspecialidad
        fields = [
            "profesional",
            "fecha_atencion",
            "motivo_consulta",
            "antecedentes",
            "signos_vitales",
            "evaluacion_clinica",
            "diagnostico",
            "procedimiento",
            "plan_tratamiento",
            "indicaciones",
            "observaciones",
            "estado",
        ]
        widgets = {
            "fecha_atencion": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
        }

    def __init__(self, *args, empresa=None, tipo=None, **kwargs):
        super().__init__(*args, empresa=empresa, **kwargs)
        self.tipo = tipo or getattr(self.instance, "tipo", None)
        if empresa:
            self.fields["profesional"].queryset = ProfesionalSalud.objects.filter(
                empresa=empresa,
                activo=True,
            ).order_by("nombre")
        else:
            self.fields["profesional"].queryset = ProfesionalSalud.objects.none()
        self.fields["profesional"].required = False
        self.fields["fecha_atencion"].input_formats = ["%Y-%m-%dT%H:%M"]
        for campo, etiqueta in self.CAMPOS_POR_TIPO.get(self.tipo, {}).items():
            self.fields[campo].label = etiqueta


ANTECEDENTES_PERSONALES_CHOICES = [
    ("diabetes", "Diabetes mellitus"),
    ("asma", "Asma bronquial"),
    ("tiroides", "Enfermedad tiroidea"),
    ("hipertension", "Hipertension arterial"),
    ("infarto_cardiaco", "Infarto al corazon"),
    ("evento_cerebral", "Infarto o evento cerebral"),
    ("tromboflebitis", "Tromboflebitis"),
    ("tromboembolia", "Tromboembolia"),
    ("tuberculosis", "Tuberculosis"),
    ("cirrosis", "Cirrosis"),
    ("pancreatitis", "Pancreatitis"),
    ("renal", "Enfermedad renal"),
    ("gastrointestinal", "Enfermedad gastrointestinal"),
    ("cancer", "Cancer"),
    ("ovario_poliquistico", "Sindrome de ovario poliquistico"),
    ("salud_mental", "Condicion psicologica o psiquiatrica"),
    ("hernia", "Hernia abdominal"),
    ("obesidad", "Obesidad"),
    ("otra", "Otra condicion"),
]

MEDICAMENTOS_HABITUALES_CHOICES = [
    ("ibuprofeno", "Ibuprofeno"),
    ("acetaminofen", "Acetaminofen o paracetamol"),
    ("aspirina", "Aspirina"),
    ("esteroides", "Esteroides"),
    ("diclofenaco", "Diclofenaco"),
    ("naproxeno", "Naproxeno"),
    ("vitamina_k", "Vitamina K"),
    ("vitamina_e", "Vitamina E"),
    ("anticonceptivos", "Anticonceptivos orales"),
    ("anticoagulantes", "Anticoagulantes"),
    ("antibioticos", "Antibioticos"),
    ("alcohol", "Alcohol"),
    ("sustancias", "Otras sustancias o drogas"),
    ("otro", "Otro medicamento"),
]

ANTECEDENTES_FAMILIARES_CHOICES = [
    ("diabetes", "Diabetes mellitus"),
    ("asma", "Asma bronquial"),
    ("tiroides", "Enfermedad tiroidea"),
    ("hipertension", "Hipertension arterial"),
    ("cardiaco", "Enfermedad cardiaca"),
    ("cerebral", "Evento cerebral"),
    ("trombosis", "Trombosis o tromboembolia"),
    ("renal", "Enfermedad renal"),
    ("cancer", "Cancer"),
    ("obesidad", "Obesidad"),
    ("otra", "Otra condicion familiar"),
]


class PreconsultaClinicaPublicaForm(forms.ModelForm):
    primer_nombre = forms.CharField(max_length=80, label="Primer nombre")
    segundo_nombre = forms.CharField(max_length=80, required=False, label="Segundo nombre")
    primer_apellido = forms.CharField(max_length=80, label="Primer apellido")
    segundo_apellido = forms.CharField(max_length=80, required=False, label="Segundo apellido")
    identidad = forms.CharField(max_length=30, label="Numero de identidad")
    fecha_nacimiento = forms.DateField(label="Fecha de nacimiento", widget=forms.DateInput(attrs={"type": "date"}))
    sexo = forms.ChoiceField(label="Sexo", choices=Paciente.SEXO_CHOICES)
    estado_civil = forms.ChoiceField(label="Estado civil", choices=Paciente.ESTADO_CIVIL_CHOICES)
    correo = forms.EmailField(required=False, label="Correo electronico")
    telefono = forms.CharField(max_length=30, label="Telefono o WhatsApp")
    direccion = forms.CharField(required=False, label="Lugar de residencia", widget=forms.Textarea(attrs={"rows": 2}))
    lugar_nacimiento = forms.CharField(max_length=160, required=False, label="Lugar de nacimiento")
    ocupacion = forms.CharField(max_length=160, required=False, label="Profesion u ocupacion")
    lugar_trabajo = forms.CharField(max_length=180, required=False, label="Lugar de trabajo")
    redes_sociales = forms.CharField(max_length=180, required=False, label="Redes sociales")
    informante = forms.CharField(max_length=180, required=False, label="Persona que proporciona la informacion")
    contacto_emergencia = forms.CharField(max_length=180, required=False, label="Contacto de emergencia")
    telefono_emergencia = forms.CharField(max_length=30, required=False, label="Telefono de emergencia")
    referido_por = forms.CharField(max_length=180, required=False, label="Como conocio Hospital MIA")
    antecedentes_personales = forms.MultipleChoiceField(
        required=False,
        choices=ANTECEDENTES_PERSONALES_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        label="Condiciones diagnosticadas por un medico",
    )
    medicamentos_habituales = forms.MultipleChoiceField(
        required=False,
        choices=MEDICAMENTOS_HABITUALES_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        label="Medicamentos o sustancias de uso habitual",
    )
    antecedentes_familiares = forms.MultipleChoiceField(
        required=False,
        choices=ANTECEDENTES_FAMILIARES_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        label="Antecedentes familiares",
    )
    consentimiento_datos = forms.BooleanField(
        required=True,
        label="Confirmo que la informacion es correcta y autorizo su uso para mi atencion medica.",
    )

    class Meta:
        model = PreconsultaClinica
        fields = [
            "primer_nombre", "segundo_nombre", "primer_apellido", "segundo_apellido",
            "identidad", "fecha_nacimiento", "sexo", "estado_civil", "correo", "telefono",
            "direccion", "lugar_nacimiento", "ocupacion", "lugar_trabajo", "redes_sociales",
            "informante", "contacto_emergencia", "telefono_emergencia", "referido_por",
            "motivo_consulta", "funciones_organicas", "funciones_detalle", "revision_sistemas",
            "revision_sistemas_detalle", "antecedentes_hospitalarios",
            "antecedentes_hospitalarios_detalle", "antecedentes_personales",
            "antecedentes_personales_detalle", "medicamentos_habituales",
            "medicamentos_habituales_detalle", "antecedentes_familiares",
            "antecedentes_familiares_detalle", "dieta", "ejercicio", "habitos", "alergias",
            "antecedentes_infecciosos", "consentimiento_datos",
        ]
        widgets = {
            "motivo_consulta": forms.Textarea(attrs={"rows": 3, "placeholder": "Cuentenos brevemente que desea consultar o que procedimiento le interesa."}),
            "funciones_organicas": forms.RadioSelect,
            "funciones_detalle": forms.Textarea(attrs={"rows": 2}),
            "revision_sistemas": forms.RadioSelect,
            "revision_sistemas_detalle": forms.Textarea(attrs={"rows": 2}),
            "antecedentes_hospitalarios_detalle": forms.Textarea(attrs={"rows": 3}),
            "antecedentes_personales_detalle": forms.Textarea(attrs={"rows": 3}),
            "medicamentos_habituales_detalle": forms.Textarea(attrs={"rows": 3}),
            "antecedentes_familiares_detalle": forms.Textarea(attrs={"rows": 3}),
            "dieta": forms.Textarea(attrs={"rows": 2}),
            "ejercicio": forms.Textarea(attrs={"rows": 2}),
            "habitos": forms.Textarea(attrs={"rows": 2}),
            "alergias": forms.Textarea(attrs={"rows": 2}),
            "antecedentes_infecciosos": forms.Textarea(attrs={"rows": 2}),
        }
        labels = {
            "motivo_consulta": "Motivo principal de consulta",
            "funciones_organicas": "Apetito, sueno, sed, miccion y evacuaciones",
            "funciones_detalle": "Explique cualquier alteracion",
            "revision_sistemas": "Revision general de organos y sistemas",
            "revision_sistemas_detalle": "Explique sintomas o alteraciones",
            "antecedentes_hospitalarios": "He tenido hospitalizaciones, accidentes, fracturas o cirugias previas",
            "antecedentes_hospitalarios_detalle": "Detalle fechas, lugar, motivo y procedimiento",
            "antecedentes_personales_detalle": "Detalle condiciones seleccionadas u otras enfermedades",
            "medicamentos_habituales_detalle": "Indique nombre, dosis, frecuencia y desde cuando",
            "antecedentes_familiares_detalle": "Indique familiar y condicion",
            "dieta": "Como describe su alimentacion",
            "ejercicio": "Actividad fisica, frecuencia y duracion",
            "habitos": "Habitos relevantes",
            "alergias": "Alergias a medicamentos, alimentos o materiales",
            "antecedentes_infecciosos": "Enfermedades infecciosas previas, incluido COVID-19",
        }

    def __init__(self, *args, paciente=None, **kwargs):
        self.paciente = paciente
        super().__init__(*args, **kwargs)
        if paciente and not self.is_bound:
            for campo in [
                "primer_nombre", "segundo_nombre", "primer_apellido", "segundo_apellido",
                "identidad", "fecha_nacimiento", "sexo", "estado_civil", "correo", "direccion",
                "lugar_nacimiento", "ocupacion", "contacto_emergencia", "telefono_emergencia",
            ]:
                self.fields[campo].initial = getattr(paciente, campo, None)
            self.fields["telefono"].initial = paciente.whatsapp or paciente.telefono
            self.fields["alergias"].initial = paciente.alergias
        self.fields["identidad"].widget.attrs.update({
            "inputmode": "numeric",
            "pattern": "[0-9]*",
            "placeholder": "Solo numeros, sin guiones",
        })

    def clean_identidad(self):
        identidad = (self.cleaned_data.get("identidad") or "").strip()
        if not identidad.isdigit():
            raise forms.ValidationError("Utilice solamente numeros, sin espacios ni guiones.")
        if self.paciente and Paciente.objects.filter(
            empresa=self.paciente.empresa,
            identidad=identidad,
        ).exclude(pk=self.paciente.pk).exists():
            raise forms.ValidationError("Este numero de identidad ya pertenece a otro expediente.")
        return identidad

    def datos_generales_limpios(self):
        campos = [
            "primer_nombre", "segundo_nombre", "primer_apellido", "segundo_apellido",
            "identidad", "fecha_nacimiento", "sexo", "estado_civil", "correo", "telefono",
            "direccion", "lugar_nacimiento", "ocupacion", "lugar_trabajo", "redes_sociales",
            "informante", "contacto_emergencia", "telefono_emergencia", "referido_por",
        ]
        datos = {campo: self.cleaned_data.get(campo) for campo in campos}
        if datos.get("fecha_nacimiento"):
            datos["fecha_nacimiento"] = datos["fecha_nacimiento"].isoformat()
        return datos


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
