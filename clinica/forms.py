from datetime import datetime

from django import forms
from django.utils import timezone

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
    HORAS_12 = [
        (f"{hora:02d}:{minuto:02d}", f"{hora:02d}:{minuto:02d}")
        for hora in range(1, 13)
        for minuto in (0, 15, 30, 45)
    ]
    fecha_cita = forms.DateField(
        label="Fecha y hora",
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        input_formats=["%Y-%m-%d"],
    )
    hora_cita = forms.ChoiceField(label="Hora", required=False, choices=HORAS_12)
    periodo_cita = forms.ChoiceField(
        label="AM / PM", required=False, choices=(("AM", "AM"), ("PM", "PM"))
    )

    class Meta:
        model = CitaClinica
        fields = ["paciente", "profesional", "servicio", "fecha_hora", "estado", "canal", "motivo", "sala", "observaciones"]

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
        self.fields.pop("fecha_hora")
        if self.instance and self.instance.pk and self.instance.fecha_hora:
            fecha_local = timezone.localtime(self.instance.fecha_hora)
            hora_12 = fecha_local.hour % 12 or 12
            valor_hora = f"{hora_12:02d}:{fecha_local.minute:02d}"
            if valor_hora not in dict(self.HORAS_12):
                self.fields["hora_cita"].choices = [*self.HORAS_12, (valor_hora, valor_hora)]
            self.initial.update({
                "fecha_cita": fecha_local.date(),
                "hora_cita": valor_hora,
                "periodo_cita": "PM" if fecha_local.hour >= 12 else "AM",
            })
        self.order_fields([
            "paciente", "profesional", "servicio", "fecha_cita", "hora_cita",
            "periodo_cita", "estado", "canal", "motivo", "sala", "observaciones",
        ])

    def clean(self):
        cleaned_data = super().clean()
        fecha = cleaned_data.get("fecha_cita")
        hora_texto = cleaned_data.get("hora_cita")
        periodo = cleaned_data.get("periodo_cita")
        fecha_hora_anterior = (self.data.get("fecha_hora") or "").strip()
        if not all((fecha, hora_texto, periodo)) and fecha_hora_anterior:
            try:
                fecha_hora = datetime.strptime(fecha_hora_anterior, "%Y-%m-%dT%H:%M")
                cleaned_data["fecha_hora_compuesta"] = timezone.make_aware(fecha_hora)
                return cleaned_data
            except ValueError:
                pass
        if not fecha:
            self.add_error("fecha_cita", "Selecciona la fecha de la cita.")
        if not hora_texto:
            self.add_error("hora_cita", "Selecciona la hora de la cita.")
        if not periodo:
            self.add_error("periodo_cita", "Selecciona AM o PM.")
        if not all((fecha, hora_texto, periodo)):
            return cleaned_data
        hora_12, minuto = (int(parte) for parte in hora_texto.split(":"))
        hora_24 = hora_12 % 12 + (12 if periodo == "PM" else 0)
        fecha_hora = datetime.combine(fecha, datetime.min.time()).replace(hour=hora_24, minute=minuto)
        cleaned_data["fecha_hora_compuesta"] = timezone.make_aware(fecha_hora)
        return cleaned_data

    def save(self, commit=True):
        cita = super().save(commit=False)
        cita.fecha_hora = self.cleaned_data["fecha_hora_compuesta"]
        if commit:
            cita.save()
        return cita


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
]

REFERIDO_POR_CHOICES = [
    ("", "Seleccione una opcion"),
    ("facebook", "Facebook"),
    ("instagram", "Instagram"),
    ("x", "X"),
    ("tiktok", "TikTok"),
    ("youtube", "YouTube"),
    ("google", "Google"),
    ("whatsapp", "WhatsApp"),
    ("referencia", "Referencia"),
    ("otro", "Otro"),
]

DROGAS_RECREATIVAS_CHOICES = [
    ("cocaina", "Cocaina"),
    ("marihuana", "Marihuana"),
    ("crack", "Crack"),
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

PROCEDIMIENTOS_GENERALES_GRUPOS = [
    (
        "Cirugia facial",
        [
            ("blefaroplastia_superior", "Blefaroplastia superior"),
            ("blefaroplastia_inferior", "Blefaroplastia inferior"),
            ("lifting_facial", "Lifting facial"),
            ("lifting_cervical", "Lifting cervical"),
            ("rinoplastia", "Rinoplastia"),
            ("otoplastia", "Otoplastia"),
            ("lip_lift", "Lip Lift"),
            ("bichectomia", "Bichectomia"),
            ("lipoinjerto_facial", "Lipoinjerto facial"),
            ("mentoplastia", "Mentoplastia"),
        ],
    ),
    (
        "Cirugia mamaria",
        [
            ("aumento_mamario", "Aumento mamario"),
            ("levantamiento_mamario", "Levantamiento mamario"),
            ("reduccion_mamaria", "Reduccion mamaria"),
            ("recambio_implantes", "Recambio de implantes"),
            ("contractura_capsular", "Correccion de contractura capsular"),
            ("ginecomastia", "Ginecomastia"),
            ("reconstruccion_mamaria", "Reconstruccion mamaria"),
        ],
    ),
    (
        "Contorno corporal",
        [
            ("liposuccion", "Liposuccion"),
            ("liposuccion_hd", "Liposuccion HD"),
            ("lipoescultura", "Lipoescultura"),
            ("abdominoplastia", "Abdominoplastia"),
            ("lipoabdominoplastia", "Lipoabdominoplastia"),
            ("braquioplastia", "Braquioplastia (Brazos)"),
            ("musloplastia", "Musloplastia (Piernas)"),
            ("gluteoplastia", "Gluteoplastia (Gluteos)"),
            ("lipoinjerto_gluteo", "Lipoinjerto gluteo"),
            ("mommy_makeover", "Mommy Makeover"),
        ],
    ),
    (
        "Cirugia intima femenina",
        [
            ("labioplastia", "Labioplastia"),
            ("vaginoplastia", "Vaginoplastia"),
            ("hoodplasty", "Hoodplasty"),
            ("rejuvenecimiento_vaginal", "Rejuvenecimiento vaginal"),
        ],
    ),
    (
        "Capilar",
        [
            ("evaluacion_alopecia", "Evaluacion alopecia"),
            ("prp_capilar", "PRP capilar"),
            ("mesoterapia_capilar", "Mesoterapia capilar"),
            ("trasplante_capilar", "Trasplante capilar"),
        ],
    ),
    (
        "Medicina estetica",
        [
            ("toxina_botulinica", "Toxina botulinica"),
            ("acido_hialuronico", "Acido hialuronico"),
            ("bioestimuladores", "Bioestimuladores"),
            ("hilos_tensores", "Hilos tensores"),
            ("laser_co2", "Laser CO2"),
            ("ipl", "IPL"),
            ("hollywood_peel", "Hollywood Peel"),
            ("dermapen", "Dermapen"),
            ("radiofrecuencia_microaguja", "Radiofrecuencia microaguja"),
        ],
    ),
]

PROCEDIMIENTOS_GENERALES_CHOICES = [
    opcion
    for _, opciones in PROCEDIMIENTOS_GENERALES_GRUPOS
    for opcion in opciones
]

ALERGIAS_GENERALES_CHOICES = [
    ("medicamentos", "Medicamentos"),
    ("latex", "Latex"),
    ("yodo", "Yodo"),
    ("alimentos", "Alimentos"),
    ("adhesivos", "Adhesivos"),
    ("ninguna", "Ninguna"),
]

MEDICAMENTOS_ACTUALES_CHOICES = [
    ("aspirina", "Aspirina"),
    ("clopidogrel", "Clopidogrel"),
    ("warfarina", "Warfarina"),
    ("rivaroxaban", "Rivaroxaban"),
    ("apixaban", "Apixaban"),
    ("anticonceptivos", "Anticonceptivos"),
    ("terapia_hormonal", "Terapia hormonal"),
    ("corticoides", "Corticoides"),
    ("antidepresivos", "Antidepresivos"),
    ("multivitaminicos", "Multivitaminicos"),
    ("ozempic", "Ozempic (Semaglutide)"),
    ("wegovy", "Wegovy"),
    ("mounjaro", "Mounjaro (Tirzepatide)"),
    ("saxenda", "Saxenda"),
    ("ninguno", "Ninguno"),
]

RIESGO_TROMBOEMBOLICO_CHOICES = [
    ("trombosis_venosa_profunda", "Trombosis venosa profunda"),
    ("embolia_pulmonar", "Embolia pulmonar"),
    ("trombofilia", "Trombofilia"),
    ("abortos_recurrentes", "Abortos recurrentes"),
    ("varices_severas", "Varices severas"),
    ("historia_familiar_trombosis", "Historia familiar de trombosis"),
    ("ninguno", "Ninguno"),
]

FRECUENCIA_CHOICES = [
    ("nunca", "Nunca"),
    ("ocasional", "Ocasional"),
    ("frecuente", "Frecuente"),
]

SI_NO_CHOICES = [("si", "Si"), ("no", "No")]

DECISION_CIRUGIA_CHOICES = [
    ("usted", "Usted"),
    ("pareja", "Pareja"),
    ("familia", "Familia"),
    ("redes_sociales", "Redes sociales"),
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
    referido_por = forms.ChoiceField(required=False, choices=REFERIDO_POR_CHOICES, label="Como conocio Hospital MIA")
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
        label="Medicamentos de uso habitual",
    )
    antecedentes_familiares = forms.MultipleChoiceField(
        required=False,
        choices=ANTECEDENTES_FAMILIARES_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        label="Antecedentes familiares",
    )
    procedimientos_interes = forms.MultipleChoiceField(
        required=False,
        choices=PROCEDIMIENTOS_GENERALES_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        label="Que procedimiento desea realizarse",
    )
    procedimientos_interes_otros = forms.CharField(
        required=False,
        label="Otros procedimientos",
        widget=forms.Textarea(attrs={"rows": 2, "placeholder": "Especifique otro procedimiento o area de interes."}),
    )
    historia_mejorar = forms.CharField(required=False, label="Que le gustaria mejorar", widget=forms.Textarea(attrs={"rows": 2}))
    historia_tiempo_preocupacion = forms.CharField(required=False, label="Cuanto tiempo tiene esta preocupacion", widget=forms.Textarea(attrs={"rows": 2}))
    historia_tratamientos_previos = forms.CharField(required=False, label="Tratamientos previos", widget=forms.Textarea(attrs={"rows": 2}))
    historia_expectativas = forms.CharField(required=False, label="Expectativas del procedimiento", widget=forms.Textarea(attrs={"rows": 2}))
    alergias_seleccion = forms.MultipleChoiceField(
        required=False,
        choices=ALERGIAS_GENERALES_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        label="Alergias",
    )
    alergias_otras = forms.CharField(required=False, label="Otros / especifique alergias", widget=forms.Textarea(attrs={"rows": 2}))
    medicamentos_actuales_seleccion = forms.MultipleChoiceField(
        required=False,
        choices=MEDICAMENTOS_ACTUALES_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        label="Medicamentos actuales",
    )
    medicamentos_actuales_otros = forms.CharField(required=False, label="Otros medicamentos", widget=forms.Textarea(attrs={"rows": 2}))
    quirurgicos_operado = forms.MultipleChoiceField(
        required=False,
        choices=SI_NO_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        label="Ha sido operado anteriormente",
    )
    quirurgicos_detalle = forms.CharField(required=False, label="Cual / detalle de cirugias previas", widget=forms.Textarea(attrs={"rows": 2}))
    tabaco_frecuencia = forms.MultipleChoiceField(required=False, choices=FRECUENCIA_CHOICES, widget=forms.CheckboxSelectMultiple, label="Tabaco")
    alcohol_frecuencia = forms.MultipleChoiceField(required=False, choices=FRECUENCIA_CHOICES, widget=forms.CheckboxSelectMultiple, label="Alcohol")
    drogas_recreativas = forms.MultipleChoiceField(required=False, choices=SI_NO_CHOICES, widget=forms.CheckboxSelectMultiple, label="Drogas recreativas")
    drogas_recreativas_tipos = forms.MultipleChoiceField(required=False, choices=DROGAS_RECREATIVAS_CHOICES, widget=forms.CheckboxSelectMultiple, label="Cuales drogas recreativas")
    drogas_recreativas_detalle = forms.CharField(required=False, label="Otros", widget=forms.Textarea(attrs={"rows": 2}))
    riesgo_tromboembolico = forms.MultipleChoiceField(
        required=False,
        choices=RIESGO_TROMBOEMBOLICO_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        label="Riesgo tromboembolico",
    )
    riesgo_tromboembolico_otros = forms.CharField(required=False, label="Otros riesgos tromboembolicos", widget=forms.Textarea(attrs={"rows": 2}))
    gine_menarca = forms.CharField(required=False, label="Menarca")
    gine_gestas = forms.CharField(required=False, label="Gestas")
    gine_partos = forms.CharField(required=False, label="Partos")
    gine_cesareas = forms.CharField(required=False, label="Cesareas")
    gine_abortos = forms.CharField(required=False, label="Abortos")
    gine_ultima_menstruacion = forms.CharField(required=False, label="Ultima menstruacion")
    gine_embarazada = forms.MultipleChoiceField(required=False, choices=SI_NO_CHOICES, widget=forms.CheckboxSelectMultiple, label="Esta embarazada")
    gine_lactancia = forms.MultipleChoiceField(required=False, choices=SI_NO_CHOICES, widget=forms.CheckboxSelectMultiple, label="Lactancia")
    gine_mamografia = forms.MultipleChoiceField(required=False, choices=SI_NO_CHOICES, widget=forms.CheckboxSelectMultiple, label="Mamografia")
    gine_mamografia_fecha = forms.CharField(required=False, label="Fecha de mamografia")
    decision_cirugia = forms.MultipleChoiceField(required=False, choices=DECISION_CIRUGIA_CHOICES, widget=forms.CheckboxSelectMultiple, label="Quien tomo la decision de operarse")
    decision_cirugia_otros = forms.CharField(required=False, label="Otros en decision de cirugia")
    expectativas_realistas = forms.MultipleChoiceField(required=False, choices=SI_NO_CHOICES, widget=forms.CheckboxSelectMultiple, label="Expectativas realistas")
    busca_perfeccion = forms.MultipleChoiceField(required=False, choices=SI_NO_CHOICES, widget=forms.CheckboxSelectMultiple, label="Busca perfeccion absoluta")
    multiples_cirugias_insatisfaccion = forms.MultipleChoiceField(required=False, choices=SI_NO_CHOICES, widget=forms.CheckboxSelectMultiple, label="Ha tenido multiples cirugias por insatisfaccion")
    examen_peso = forms.CharField(required=False, label="Peso (kg)")
    examen_talla = forms.CharField(required=False, label="Talla (cm)")
    examen_imc = forms.CharField(required=False, label="IMC")
    examen_pa = forms.CharField(required=False, label="PA")
    examen_fc = forms.CharField(required=False, label="FC")
    examen_sato2 = forms.CharField(required=False, label="SatO2")
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
            "procedimientos_interes", "procedimientos_interes_otros",
            "motivo_consulta", "funciones_organicas", "funciones_detalle", "revision_sistemas",
            "revision_sistemas_detalle", "antecedentes_hospitalarios",
            "antecedentes_hospitalarios_detalle", "antecedentes_personales",
            "antecedentes_personales_detalle", "medicamentos_habituales",
            "medicamentos_habituales_detalle", "antecedentes_familiares",
            "antecedentes_familiares_detalle", "dieta", "ejercicio", "habitos", "alergias",
            "antecedentes_infecciosos", "historia_mejorar", "historia_tiempo_preocupacion",
            "historia_tratamientos_previos", "historia_expectativas", "alergias_seleccion",
            "alergias_otras", "medicamentos_actuales_seleccion", "medicamentos_actuales_otros",
            "quirurgicos_operado", "quirurgicos_detalle", "tabaco_frecuencia", "alcohol_frecuencia",
            "drogas_recreativas", "drogas_recreativas_tipos", "drogas_recreativas_detalle", "riesgo_tromboembolico",
            "riesgo_tromboembolico_otros", "gine_menarca", "gine_gestas", "gine_partos",
            "gine_cesareas", "gine_abortos", "gine_ultima_menstruacion", "gine_embarazada",
            "gine_lactancia", "gine_mamografia", "gine_mamografia_fecha", "decision_cirugia",
            "decision_cirugia_otros", "expectativas_realistas", "busca_perfeccion",
            "multiples_cirugias_insatisfaccion", "examen_peso", "examen_talla", "examen_imc",
            "examen_pa", "examen_fc", "examen_sato2", "consentimiento_datos",
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
        formulario_general = {}
        if self.instance and isinstance(self.instance.datos_generales, dict):
            formulario_general = self.instance.datos_generales.get("formulario_general", {}) or {}
        if formulario_general and not self.is_bound:
            for campo, valor in formulario_general.items():
                if campo in self.fields:
                    self.fields[campo].initial = valor
        for campo in [
            "procedimientos_interes", "antecedentes_personales", "medicamentos_habituales",
            "antecedentes_familiares", "alergias_seleccion", "medicamentos_actuales_seleccion",
            "quirurgicos_operado", "tabaco_frecuencia", "alcohol_frecuencia", "drogas_recreativas", "drogas_recreativas_tipos",
            "riesgo_tromboembolico", "gine_embarazada", "gine_lactancia", "gine_mamografia",
            "decision_cirugia", "expectativas_realistas", "busca_perfeccion",
            "multiples_cirugias_insatisfaccion",
        ]:
            if campo in self.fields:
                self.fields[campo].widget.attrs["class"] = "animated-check-list"

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

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("sexo") == "masculino":
            for campo in [
                "gine_menarca", "gine_gestas", "gine_partos", "gine_cesareas", "gine_abortos",
                "gine_ultima_menstruacion", "gine_embarazada", "gine_lactancia",
                "gine_mamografia", "gine_mamografia_fecha",
            ]:
                cleaned_data[campo] = [] if isinstance(self.fields.get(campo), forms.MultipleChoiceField) else ""
        if "si" not in (cleaned_data.get("drogas_recreativas") or []):
            cleaned_data["drogas_recreativas_tipos"] = []
            cleaned_data["drogas_recreativas_detalle"] = ""
        return cleaned_data

    @property
    def procedimientos_interes_grupos(self):
        valores = self["procedimientos_interes"].value() or []
        seleccionados = {str(valor) for valor in valores}
        return [
            {
                "titulo": titulo,
                "opciones": [
                    {
                        "value": valor,
                        "label": etiqueta,
                        "selected": str(valor) in seleccionados,
                    }
                    for valor, etiqueta in opciones
                ],
            }
            for titulo, opciones in PROCEDIMIENTOS_GENERALES_GRUPOS
        ]

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
        campos_generales = [
            "procedimientos_interes", "procedimientos_interes_otros", "historia_mejorar",
            "historia_tiempo_preocupacion", "historia_tratamientos_previos", "historia_expectativas",
            "alergias_seleccion", "alergias_otras", "medicamentos_actuales_seleccion",
            "medicamentos_actuales_otros", "quirurgicos_operado", "quirurgicos_detalle",
            "tabaco_frecuencia", "alcohol_frecuencia", "drogas_recreativas", "drogas_recreativas_tipos", "drogas_recreativas_detalle",
            "riesgo_tromboembolico", "riesgo_tromboembolico_otros", "gine_menarca", "gine_gestas",
            "gine_partos", "gine_cesareas", "gine_abortos", "gine_ultima_menstruacion",
            "gine_embarazada", "gine_lactancia", "gine_mamografia", "gine_mamografia_fecha",
            "decision_cirugia", "decision_cirugia_otros", "expectativas_realistas", "busca_perfeccion",
            "multiples_cirugias_insatisfaccion", "examen_peso", "examen_talla", "examen_imc",
            "examen_pa", "examen_fc", "examen_sato2",
        ]
        datos["formulario_general"] = {
            campo: self.cleaned_data.get(campo)
            for campo in campos_generales
            if self.cleaned_data.get(campo) not in (None, "", [])
        }
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
