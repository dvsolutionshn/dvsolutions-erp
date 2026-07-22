from collections import OrderedDict
from datetime import datetime

from django import forms
from django.utils import timezone

from core.phone_prefixes import PHONE_PREFIX_CHOICES, apply_phone_prefix, normalize_phone_prefix

from .models import (
    CitaClinica,
    ConsentimientoClinico,
    DocumentoClinicoPaciente,
    ExamenPaciente,
    ExpedienteEvento,
    HistoriaClinicaEspecialidad,
    MedicamentoPrescrito,
    Paciente,
    PacienteFotoEvolucion,
    PreconsultaClinica,
    ProfesionalSalud,
    RecetaMedica,
    ServicioClinico,
    TratamientoPaciente,
    asegurar_profesionales_agenda_base,
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
        self.fields["prefijo_telefono"].widget = forms.Select(choices=CODIGO_AREA_CHOICES)
        self.fields["identidad"].widget.attrs.update({
            "inputmode": "numeric",
            "pattern": "[0-9]*",
            "autocomplete": "off",
            "placeholder": "Solo numeros, sin guiones",
        })
        prefijo_actual = self.fields["prefijo_telefono"].initial or getattr(self.instance, "prefijo_telefono", "") or "504"
        self.fields["prefijo_telefono"].initial = normalize_phone_prefix(prefijo_actual)
        for field_name in ["primer_nombre", "primer_apellido", "identidad"]:
            self.fields[field_name].required = True

    def clean_identidad(self):
        identidad = (self.cleaned_data.get("identidad") or "").strip()
        if identidad and not identidad.isdigit():
            raise forms.ValidationError("El No. de documento solo debe contener numeros, sin guiones ni espacios.")
        if identidad and len(identidad) > 20:
            raise forms.ValidationError("El No. de documento no puede superar 20 digitos.")
        return identidad

    def clean_foto_perfil(self):
        foto = self.cleaned_data.get("foto_perfil")
        if not foto or isinstance(foto, bool):
            return foto
        if foto.size > 5 * 1024 * 1024:
            raise forms.ValidationError("La foto inicial no puede superar 5 MB. Use una imagen mas liviana.")
        if getattr(foto, "content_type", "") not in {"image/jpeg", "image/png", "image/webp"}:
            raise forms.ValidationError("Utilice una foto JPG, PNG o WebP.")
        return foto

    def clean(self):
        cleaned_data = super().clean()
        cleaned_data["prefijo_telefono"] = normalize_phone_prefix(cleaned_data.get("prefijo_telefono"))
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
        fields = ["paciente", "profesional", "servicio", "fecha_hora", "estado", "canal", "motivo", "pagada", "sala", "observaciones"]

    def __init__(self, *args, empresa=None, **kwargs):
        super().__init__(*args, empresa=empresa, **kwargs)
        if empresa:
            asegurar_profesionales_agenda_base(empresa)
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
            "periodo_cita", "estado", "canal", "motivo", "pagada", "sala", "observaciones",
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


class PacienteFotoEvolucionForm(BaseClinicaForm):
    class Meta:
        model = PacienteFotoEvolucion
        fields = ["tipo", "titulo", "descripcion", "fecha", "imagen", "video"]
        widgets = {
            "fecha": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "imagen": forms.ClearableFileInput(attrs={"accept": "image/*"}),
            "video": forms.ClearableFileInput(attrs={"accept": "video/mp4,video/webm,video/quicktime,.mp4,.mov,.webm"}),
        }
        labels = {
            "imagen": "Foto de evolucion",
            "video": "Video de evolucion",
        }
        help_texts = {
            "imagen": "Use JPG, PNG o WebP. Puede dejarlo vacio si subira video.",
            "video": "Use MP4, MOV o WebM. Puede dejarlo vacio si subira foto.",
        }

    def clean_imagen(self):
        imagen = self.cleaned_data.get("imagen")
        if imagen and imagen.size > 10 * 1024 * 1024:
            raise forms.ValidationError("La foto no puede superar 10 MB.")
        if imagen and getattr(imagen, "content_type", "") not in {"image/jpeg", "image/png", "image/webp"}:
            raise forms.ValidationError("Utilice una foto JPG, PNG o WebP.")
        return imagen

    def clean_video(self):
        video = self.cleaned_data.get("video")
        if video and video.size > 120 * 1024 * 1024:
            raise forms.ValidationError("El video no puede superar 120 MB.")
        tipos_permitidos = {"video/mp4", "video/webm", "video/quicktime", "video/x-msvideo"}
        if video and getattr(video, "content_type", "") not in tipos_permitidos:
            raise forms.ValidationError("Utilice un video MP4, MOV o WebM.")
        return video

    def clean(self):
        cleaned_data = super().clean()
        imagen = cleaned_data.get("imagen")
        video = cleaned_data.get("video")
        if not imagen and not video:
            raise forms.ValidationError("Suba una foto o un video para registrar la evolucion.")
        if imagen and video:
            raise forms.ValidationError("Suba solo una foto o solo un video por registro para mantener el historial ordenado.")
        return cleaned_data


class PlanConsentimientoPDFForm(BaseClinicaForm):
    class Meta:
        model = ConsentimientoClinico
        fields = ["titulo", "version", "firmado_por", "fecha_firma", "estado", "archivo"]
        widgets = {
            "fecha_firma": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "archivo": forms.ClearableFileInput(attrs={"accept": "application/pdf,.pdf"}),
        }
        labels = {
            "titulo": "Tipo de plan / consentimiento",
            "archivo": "PDF firmado",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["archivo"].required = True
        self.fields["fecha_firma"].required = False
        self.fields["firmado_por"].required = False
        self.fields["version"].help_text = "Ejemplo: 1.0, 2026-07 o version interna del documento."

    def clean_archivo(self):
        archivo = self.cleaned_data.get("archivo")
        if archivo and archivo.size > 15 * 1024 * 1024:
            raise forms.ValidationError("El PDF no puede superar 15 MB.")
        if archivo and getattr(archivo, "content_type", "") not in {"application/pdf", "application/x-pdf"}:
            raise forms.ValidationError("Solo se permiten archivos PDF.")
        return archivo


class ExamenPacienteForm(BaseClinicaForm):
    MAX_ARCHIVO_MB = 50

    class Meta:
        model = ExamenPaciente
        fields = ["titulo", "tipo", "fecha_examen", "laboratorio", "descripcion", "archivo"]
        widgets = {
            "fecha_examen": forms.DateInput(attrs={"type": "date"}),
            "descripcion": forms.Textarea(attrs={"rows": 4}),
            "archivo": forms.ClearableFileInput(attrs={"accept": "application/pdf,image/*,.pdf,.jpg,.jpeg,.png,.webp"}),
        }
        labels = {
            "titulo": "Nombre del examen",
            "fecha_examen": "Fecha del examen",
            "laboratorio": "Laboratorio / centro",
            "archivo": "PDF o foto del examen",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["archivo"].required = True
        self.fields["archivo"].help_text = (
            f"Puede subir PDF o imagen JPG, PNG o WebP. Tamano maximo: {self.MAX_ARCHIVO_MB} MB."
        )

    def clean_archivo(self):
        archivo = self.cleaned_data.get("archivo")
        if archivo and archivo.size > self.MAX_ARCHIVO_MB * 1024 * 1024:
            raise forms.ValidationError(f"El archivo no puede superar {self.MAX_ARCHIVO_MB} MB.")
        permitidos = {"application/pdf", "application/x-pdf", "image/jpeg", "image/png", "image/webp"}
        if archivo and getattr(archivo, "content_type", "") not in permitidos:
            raise forms.ValidationError("Solo se permiten PDF o imagenes JPG, PNG o WebP.")
        return archivo


class DocumentoClinicoPacienteForm(BaseClinicaForm):
    MAX_ARCHIVO_MB = 50
    LABELS_POR_CATEGORIA = {
        "laboratorio": {
            "titulo": "Nombre del resultado / solicitud",
            "entidad": "Laboratorio / centro",
            "archivo": "PDF o foto del resultado",
        },
        "radiologico": {
            "titulo": "Nombre del estudio radiologico",
            "entidad": "Centro de imagenologia",
            "archivo": "PDF o foto del estudio",
        },
        "documento": {
            "titulo": "Nombre del documento",
            "entidad": "Origen / area",
            "archivo": "Archivo del documento",
        },
        "remision": {
            "titulo": "Remision o contraremision",
            "entidad": "Centro o profesional externo",
            "archivo": "Archivo de remision",
        },
        "detalle_remision": {
            "titulo": "Detalle de remision",
            "entidad": "Institucion / referencia",
            "archivo": "Archivo de seguimiento",
        },
    }

    class Meta:
        model = DocumentoClinicoPaciente
        fields = ["titulo", "fecha_documento", "entidad", "descripcion", "archivo"]
        widgets = {
            "fecha_documento": forms.DateInput(attrs={"type": "date"}),
            "descripcion": forms.Textarea(attrs={"rows": 4}),
            "archivo": forms.ClearableFileInput(
                attrs={"accept": "application/pdf,image/*,.pdf,.jpg,.jpeg,.png,.webp,.doc,.docx"}
            ),
        }
        labels = {
            "titulo": "Nombre del documento",
            "fecha_documento": "Fecha del documento",
            "entidad": "Origen / centro",
            "descripcion": "Descripcion o notas",
            "archivo": "Archivo",
        }

    def __init__(self, *args, categoria=None, **kwargs):
        super().__init__(*args, **kwargs)
        labels = self.LABELS_POR_CATEGORIA.get(categoria or "", {})
        for campo, label in labels.items():
            self.fields[campo].label = label
        self.fields["archivo"].required = True
        self.fields["archivo"].help_text = (
            f"Puede subir PDF, imagen JPG/PNG/WebP o documento Word. Tamano maximo: {self.MAX_ARCHIVO_MB} MB."
        )

    def clean_archivo(self):
        archivo = self.cleaned_data.get("archivo")
        if archivo and archivo.size > self.MAX_ARCHIVO_MB * 1024 * 1024:
            raise forms.ValidationError(f"El archivo no puede superar {self.MAX_ARCHIVO_MB} MB.")
        if archivo:
            nombre = (archivo.name or "").lower()
            extensiones = (".pdf", ".jpg", ".jpeg", ".png", ".webp", ".doc", ".docx")
            if not nombre.endswith(extensiones):
                raise forms.ValidationError("Solo se permiten PDF, imagenes JPG/PNG/WebP o documentos Word.")
        return archivo


class IncapacidadClinicaForm(BaseClinicaForm):
    class Meta:
        model = DocumentoClinicoPaciente
        fields = ["titulo", "fecha_documento", "fecha_inicio", "fecha_fin", "dias", "profesional", "descripcion", "archivo"]
        widgets = {
            "fecha_documento": forms.DateInput(attrs={"type": "date"}),
            "fecha_inicio": forms.DateInput(attrs={"type": "date"}),
            "fecha_fin": forms.DateInput(attrs={"type": "date"}),
            "descripcion": forms.Textarea(attrs={"rows": 6, "placeholder": "Diagnostico, motivo clinico, reposo indicado e indicaciones generales."}),
            "archivo": forms.ClearableFileInput(attrs={"accept": "application/pdf,image/*,.pdf,.jpg,.jpeg,.png,.webp"}),
        }
        labels = {
            "titulo": "Titulo del certificado",
            "fecha_documento": "Fecha de emision",
            "fecha_inicio": "Inicio de incapacidad",
            "fecha_fin": "Fin de incapacidad",
            "dias": "Dias indicados",
            "profesional": "Profesional que emite",
            "descripcion": "Motivo e indicaciones",
            "archivo": "Adjunto firmado (opcional)",
        }

    def __init__(self, *args, empresa=None, **kwargs):
        super().__init__(*args, empresa=empresa, **kwargs)
        if empresa:
            self.fields["profesional"].queryset = ProfesionalSalud.objects.filter(empresa=empresa, activo=True).order_by("nombre")
        self.fields["archivo"].required = False
        self.fields["titulo"].initial = self.fields["titulo"].initial or "Incapacidad medica"

    def clean(self):
        cleaned_data = super().clean()
        inicio = cleaned_data.get("fecha_inicio")
        fin = cleaned_data.get("fecha_fin")
        dias = cleaned_data.get("dias")
        if inicio and fin:
            if fin < inicio:
                raise forms.ValidationError("La fecha final no puede ser anterior a la fecha inicial.")
            if not dias:
                cleaned_data["dias"] = (fin - inicio).days + 1
        return cleaned_data


class RecetaMedicaForm(BaseClinicaForm):
    class Meta:
        model = RecetaMedica
        fields = ["fecha", "profesional", "diagnostico", "productos", "indicaciones", "observaciones"]
        widgets = {
            "fecha": forms.DateInput(attrs={"type": "date"}),
            "indicaciones": forms.Textarea(attrs={"rows": 8, "placeholder": "Ejemplo:\n1. Medicamento - dosis - frecuencia - duracion.\nIndicaciones especiales..."}),
            "observaciones": forms.Textarea(attrs={"rows": 3}),
        }
        labels = {
            "productos": "Productos / medicamentos del catalogo",
            "indicaciones": "Receta e indicaciones",
        }

    def __init__(self, *args, **kwargs):
        empresa = kwargs.pop("empresa", None)
        super().__init__(*args, **kwargs)
        self.fields["productos"].required = False
        self.fields["profesional"].required = False
        if empresa:
            from facturacion.models import Producto

            self.fields["productos"].queryset = Producto.objects.filter(empresa=empresa, activo=True).order_by("nombre")
            self.fields["profesional"].queryset = ProfesionalSalud.objects.filter(empresa=empresa, activo=True).order_by("nombre")
        self.fields["productos"].widget.attrs.update({"size": "8"})


CAPILAR_FORMULARIO = [
    ("capilar_motivo", "Motivo de consulta - ¿Que le preocupa principalmente?", [
        ("caida_excesiva", "Caida excesiva del cabello"), ("adelgazamiento", "Adelgazamiento del cabello"),
        ("entradas", "Entradas pronunciadas"), ("perdida_densidad", "Perdida de densidad"),
        ("coronilla", "Alopecia en coronilla"), ("difusa", "Alopecia difusa"),
        ("alopecia_femenina", "Alopecia femenina"), ("alopecia_masculina", "Alopecia masculina"),
        ("cejas", "Perdida de cejas"), ("barba", "Perdida de barba"), ("caspa", "Caspa"),
        ("picazon", "Picazon del cuero cabelludo"), ("grasa", "Exceso de grasa"),
        ("trasplante", "Desea trasplante capilar"), ("seguimiento", "Seguimiento de tratamiento capilar"),
    ], True),
    ("capilar_inicio", "Historia de la caida - ¿Cuando inicio?", [
        ("menos_3_meses", "Menos de 3 meses"), ("3_6_meses", "3 a 6 meses"),
        ("6_12_meses", "6 meses a 1 año"), ("1_3_anos", "1 a 3 años"), ("mas_3_anos", "Mas de 3 años"),
    ], False),
    ("capilar_tipo_caida", "La caida es", [("gradual", "Gradual"), ("repentina", "Repentina"), ("intermitente", "Intermitente"), ("constante", "Constante")], False),
    ("capilar_estado_actual", "Actualmente considera que", [("estable", "Esta estable"), ("empeorando", "Esta empeorando"), ("mejorando", "Esta mejorando")], False),
    ("capilar_familiares", "Antecedentes familiares - ¿Tiene familiares con alopecia?", [
        ("padre", "Padre"), ("madre", "Madre"), ("abuelo_paterno", "Abuelo paterno"), ("abuelo_materno", "Abuelo materno"), ("hermanos", "Hermanos"), ("ninguno", "Ninguno"),
    ], True),
    ("capilar_sintomas", "Sintomas del cuero cabelludo", [
        ("picazon", "Picazon"), ("ardor", "Ardor"), ("dolor", "Dolor"), ("caspa", "Caspa"), ("descamacion", "Descamacion"), ("grasa", "Exceso de grasa"), ("enrojecimiento", "Enrojecimiento"), ("sensibilidad", "Sensibilidad"), ("ninguno", "Ninguno"),
    ], True),
    ("capilar_areas", "Areas afectadas", [
        ("entradas", "Entradas"), ("linea_frontal", "Linea frontal"), ("temporal", "Region temporal"), ("coronilla", "Coronilla"), ("superior", "Parte superior"), ("todo", "Todo el cuero cabelludo"), ("cejas", "Cejas"), ("barba", "Barba"),
    ], True),
    ("capilar_tratamientos_previos", "Tratamientos capilares previos - ¿Cuales?", [
        ("minoxidil", "Minoxidil"), ("finasteride", "Finasteride"), ("dutasteride", "Dutasteride"), ("prp", "PRP Capilar"), ("mesoterapia", "Mesoterapia Capilar"), ("exosomas", "Exosomas"), ("laser", "Laser Capilar"), ("vitaminas", "Vitaminas capilares"), ("shampoo", "Shampoo anticaida"), ("suplementos", "Suplementos nutricionales"), ("trasplante", "Trasplante capilar"),
    ], True),
    ("capilar_antecedentes_medicos", "Antecedentes medicos", [
        ("hipertension", "Hipertension"), ("diabetes", "Diabetes"), ("hipotiroidismo", "Hipotiroidismo"), ("hipertiroidismo", "Hipertiroidismo"), ("anemia", "Anemia"), ("lupus", "Lupus"), ("psoriasis", "Psoriasis"), ("dermatitis", "Dermatitis"), ("ansiedad", "Ansiedad"), ("depresion", "Depresion"), ("sop", "SOP"), ("ninguna", "Ninguna"),
    ], True),
    ("capilar_medicamentos", "Medicamentos actuales", [
        ("anticonceptivos", "Anticonceptivos"), ("hormonas", "Hormonas"), ("finasteride", "Finasteride"), ("dutasteride", "Dutasteride"), ("minoxidil_oral", "Minoxidil oral"), ("minoxidil_topico", "Minoxidil topico"), ("multivitaminicos", "Multivitaminicos"), ("ozempic", "Ozempic"), ("mounjaro", "Mounjaro"), ("ninguno", "Ninguno"),
    ], True),
    ("capilar_habitos", "Habitos", [("fuma", "Fuma"), ("vapea", "Vapea"), ("alcohol_nunca", "Alcohol: nunca"), ("alcohol_ocasional", "Alcohol: ocasional"), ("alcohol_frecuente", "Alcohol: frecuente")], True),
    ("capilar_mujeres", "Mujeres - ¿Ha presentado recientemente?", [
        ("embarazo", "Embarazo"), ("parto", "Parto"), ("lactancia", "Lactancia"), ("menopausia", "Menopausia"), ("sop", "SOP"), ("cambios_hormonales", "Cambios hormonales"), ("ninguno", "Ninguno"),
    ], True),
    ("capilar_estres", "Estres y estilo de vida", [
        ("estres_6_meses", "Estres importante en ultimos 6 meses"), ("cirugia_reciente", "Cirugia reciente"), ("enfermedad_grave", "Enfermedad grave"), ("covid", "COVID"), ("perdida_peso", "Perdida importante de peso"), ("dieta_estricta", "Dieta estricta"), ("ninguna", "Ninguna"),
    ], True),
    ("capilar_expectativas", "Expectativas - ¿Que espera lograr?", [
        ("detener_caida", "Detener la caida"), ("recuperar_densidad", "Recuperar densidad"), ("mejorar_calidad", "Mejorar calidad del cabello"), ("restaurar_entradas", "Restaurar entradas"), ("restaurar_coronilla", "Restaurar coronilla"), ("trasplante", "Trasplante capilar"), ("autoestima", "Mejorar autoestima"),
    ], True),
]

MEDICINA_ESTETICA_FORMULARIO = [
    ("estetica_motivo", "Motivo de consulta (puede marcar más de una opción)", [
        ("rejuvenecimiento_facial", "Rejuvenecimiento facial"),
        ("prevencion_envejecimiento", "Prevención del envejecimiento"),
        ("arrugas", "Arrugas o líneas de expresión"),
        ("flacidez_facial", "Flacidez facial"),
        ("volumen_facial", "Pérdida de volumen facial"),
        ("labios", "Aumento de labios"),
        ("rinomodelacion", "Rinomodelación"),
        ("mandibula_menton", "Definición de mandíbula y mentón"),
        ("ojeras", "Ojeras"),
        ("manchas_faciales", "Manchas faciales"),
        ("melasma", "Melasma"),
        ("acne", "Acné activo"),
        ("cicatrices_acne", "Cicatrices de acné"),
        ("poros_dilatados", "Poros dilatados"),
        ("piel_grasa", "Piel grasa"),
        ("piel_seca_deshidratada", "Piel seca o deshidratada"),
        ("rosacea", "Rosácea"),
        ("enrojecimiento_facial", "Enrojecimiento facial"),
        ("textura_irregular", "Textura irregular de la piel"),
        ("luminosidad_piel", "Luminosidad de la piel"),
        ("cuello_escote", "Rejuvenecimiento de cuello y escote"),
        ("papada", "Papada"),
        ("celulitis", "Celulitis"),
        ("flacidez_corporal", "Flacidez corporal"),
        ("estrias", "Estrías"),
        ("cicatrices_corporales", "Cicatrices corporales"),
        ("hiperhidrosis", "Hiperhidrosis (sudoración excesiva)"),
        ("alopecia", "Alopecia / caída del cabello"),
        ("rejuvenecimiento_intimo", "Rejuvenecimiento íntimo femenino"),
    ], True),
    ("estetica_objetivo_principal", "Objetivo principal del paciente", [
        ("verse_mas_joven", "Verse más joven"),
        ("calidad_piel", "Mejorar la calidad de la piel"),
        ("armonizar_rostro", "Armonizar el rostro"),
        ("mejorar_autoestima", "Mejorar la autoestima"),
        ("prevenir_envejecimiento", "Prevenir el envejecimiento"),
        ("corregir_preocupacion", "Corregir una preocupación específica"),
        ("mantener_resultados", "Mantener resultados previos"),
    ], True),
    ("estetica_tiempo", "¿Cuanto tiempo tiene esta preocupacion?", [("menos_6_meses", "Menos de 6 meses"), ("6_12_meses", "6 meses a 1 año"), ("1_3_anos", "1 a 3 años")], False),
    ("estetica_tratamientos_previos", "Antecedentes de tratamientos esteticos", [
        ("botox", "Botox"), ("acido_hialuronico", "Acido hialuronico"), ("bioestimuladores", "Bioestimuladores de colageno"), ("hilos", "Hilos tensores"), ("prp_facial", "PRP facial"), ("prp_capilar", "PRP capilar"), ("mesoterapia_facial", "Mesoterapia facial"), ("mesoterapia_corporal", "Mesoterapia corporal"), ("mesoterapia_capilar", "Mesoterapia capilar"), ("dermapen", "Dermapen"), ("microneedling", "Microneedling"), ("peelings", "Peelings quimicos"), ("hydrafacial", "Hydrafacial"), ("radiofrecuencia", "Radiofrecuencia"), ("rf_microagujas", "Radiofrecuencia con microagujas"), ("ipl", "IPL"), ("laser_co2", "Laser CO2"), ("hollywood_peel", "Hollywood Peel"), ("depilacion_laser", "Depilacion laser"), ("criolipolisis", "Criolipolisis"), ("exilis", "Exilis"), ("hifem", "HIFEM / Emsculpt"), ("sueroterapia", "Sueroterapia intravenosa"), ("trasplante_capilar", "Trasplante capilar"),
    ], True),
    ("estetica_ultimo_tratamiento", "¿Cuando fue su ultimo tratamiento?", [("menos_1_mes", "Menos de 1 mes"), ("1_3_meses", "1 a 3 meses"), ("3_6_meses", "3 a 6 meses"), ("mas_6_meses", "Mas de 6 meses"), ("mas_1_ano", "Mas de 1 año")], False),
    ("estetica_cirugias", "Antecedentes quirurgicos esteticos", [
        ("liposuccion", "Liposuccion"), ("abdominoplastia", "Abdominoplastia"), ("lipoabdominoplastia", "Lipoabdominoplastia"), ("aumento_mamario", "Aumento mamario"), ("levantamiento_mamario", "Levantamiento mamario"), ("reduccion_mamaria", "Reduccion mamaria"), ("rinoplastia", "Rinoplastia"), ("blefaroplastia", "Blefaroplastia"), ("otoplastia", "Otoplastia"), ("lifting_facial", "Lifting facial"), ("trasplante_capilar", "Trasplante capilar"),
    ], True),
    ("estetica_cuidado_piel", "Habitos de cuidado de la piel", [
        ("protector_solar", "Utiliza protector solar diariamente"), ("productos_piel", "Utiliza productos para cuidado de la piel"), ("lavado_1", "Lava rostro 1 vez al dia"), ("lavado_2", "Lava rostro 2 veces al dia"), ("lavado_3", "Lava rostro 3 o mas veces al dia"), ("sol_frecuente", "Se expone frecuentemente al sol"),
    ], True),
    ("estetica_satisfaccion", "Satisfaccion personal - apariencia facial", [("1", "1 Muy insatisfecho"), ("2", "2 Insatisfecho"), ("3", "3 Regular"), ("4", "4 Satisfecho"), ("5", "5 Muy satisfecho")], False),
    ("estetica_areas_cuerpo", "Evaluacion corporal - Areas que desea mejorar", [
        ("papada", "Papada"), ("brazos", "Brazos"), ("flancos", "Flancos"), ("espalda_inferior", "Espalda inferior"), ("muslos_internos", "Muslos internos"), ("muslos_externos", "Muslos externos"), ("rodillas", "Rodillas"), ("gluteos", "Gluteos"), ("piernas", "Piernas"), ("pantorrillas", "Pantorrillas"),
    ], True),
    ("estetica_preocupacion_corporal", "Principal preocupacion corporal", [
        ("grasa_localizada", "Grasa localizada"), ("flacidez", "Flacidez"), ("celulitis", "Celulitis"), ("estrias", "Estrias"), ("sobrepeso", "Sobrepeso"), ("retencion_liquidos", "Retencion de liquidos"), ("fibrosis", "Fibrosis postquirurgica"), ("cicatrices", "Cicatrices"), ("asimetria", "Asimetrias corporales"), ("postoperatorio", "Recuperacion postoperatoria"),
    ], True),
    ("estetica_medicamentos_adelgazar", "Medicamentos para adelgazar", [("ozempic", "Ozempic"), ("mounjaro", "Mounjaro"), ("saxenda", "Saxenda"), ("metformina", "Metformina"), ("fentermina", "Fentermina")], True),
    ("estetica_bariatrica", "Cirugia bariatrica", [("no", "No"), ("manga", "Manga gastrica"), ("bypass", "Bypass gastrico"), ("balon", "Balon gastrico")], False),
    ("estetica_ejercicio", "Actividad fisica", [("nunca", "Nunca"), ("1_2", "1-2 veces por semana"), ("3_4", "3-4 veces por semana"), ("5_mas", "5 o mas veces por semana")], False),
    ("estetica_intima_femenina", "Zona intima femenina - motivo de consulta", [
        ("rejuvenecimiento_vaginal", "Rejuvenecimiento vaginal"), ("flacidez_postparto", "Flacidez vaginal postparto"), ("incontinencia", "Incontinencia urinaria leve"), ("sequedad", "Sequedad vaginal"), ("sensibilidad", "Disminucion de sensibilidad sexual"), ("oscurecimiento", "Oscurecimiento intimo"), ("labios_menores", "Hipertrofia de labios menores"), ("labios_mayores", "Perdida de volumen de labios mayores"), ("asimetria", "Asimetria genital"),
    ], True),
    ("estetica_evaluacion_facial", "Evaluacion facial detallada", [
        ("piel_normal", "Piel normal"), ("piel_seca", "Piel seca"), ("piel_grasa", "Piel grasa"), ("piel_mixta", "Piel mixta"), ("piel_sensible", "Piel sensible"), ("melasma", "Melasma"), ("acne_activo", "Acne activo"), ("comedones", "Comedones"), ("rosacea", "Rosacea"), ("lineas_frontales", "Lineas frontales"), ("patas_gallo", "Patas de gallo"), ("papada", "Papada"), ("flacidez_cervical", "Flacidez cervical"),
    ], True),
    ("estetica_plan_recomendado", "Plan recomendado", [
        ("toxina", "Toxina botulinica"), ("acido_hialuronico", "Acido hialuronico"), ("skinbooster", "Skinbooster"), ("bioestimuladores", "Bioestimuladores"), ("hilos", "Hilos tensores"), ("prp_facial", "PRP facial"), ("dermapen", "Dermapen"), ("rf_microagujas", "Matrix Pro RF Microagujas"), ("ipl", "IPL Luminous Light"), ("laser_co2", "Laser CO2 fraccionado"), ("hollywood", "Hollywood Carbon Peel"), ("hydrafacial", "Hydrafacial Elite"), ("exilis_body", "Exilis RF Body"), ("hifem", "Sculpt Body Definition HIFEM"), ("drenaje", "Drenaje linfatico"), ("celulitis", "Tratamiento celulitis"), ("estrias", "Tratamiento estrias"), ("mesoterapia_corporal", "Mesoterapia corporal"), ("sueroterapia", "Sueroterapia IV"), ("prp_capilar", "PRP Capilar"), ("exosomas", "Exosomas"), ("trasplante_capilar", "Trasplante Capilar"), ("radiofrecuencia_vaginal", "Radiofrecuencia vaginal"), ("prp_intimo", "PRP intimo"), ("blanqueamiento_intimo", "Blanqueamiento intimo"),
    ], True),
]

CIRUGIA_PLASTICA_FORMULARIO = [
    ("cirugia_facial", "Cirugía facial de interés", [
        ("blefaroplastia_superior", "Blefaroplastia superior (retirar exceso de piel del párpado superior)"),
        ("blefaroplastia_inferior", "Blefaroplastia inferior (mejorar bolsas u ojeras del párpado inferior)"),
        ("lifting_facial", "Lifting facial (rejuvenecimiento y flacidez del rostro)"),
        ("lifting_cervical", "Lifting cervical (mejorar flacidez del cuello)"),
        ("rinoplastia", "Rinoplastia (cirugía estética o funcional de la nariz)"),
        ("otoplastia", "Otoplastia (corrección de orejas prominentes)"),
        ("bichectomia", "Bichectomía (reducción de bolsas de Bichat en mejillas)"),
        ("mentoplastia", "Mentoplastia (mejorar forma o proyección del mentón)"),
    ], True),
    ("cirugia_mamaria", "Cirugía mamaria de interés", [
        ("aumento_mamario", "Aumento mamario (colocación de implantes o aumento de volumen)"),
        ("levantamiento_mamario", "Levantamiento mamario / mastopexia (elevar y mejorar forma del busto)"),
        ("reduccion_mamaria", "Reducción mamaria (disminuir volumen del busto)"),
        ("recambio_implantes", "Recambio de implantes (cambiar implantes previos)"),
        ("retiro_implantes", "Retiro de implantes (extraer implantes mamarios)"),
        ("ginecomastia", "Ginecomastia (reducción de tejido mamario en hombres)"),
    ], True),
    ("contorno_corporal", "Contorno corporal de interés", [
        ("liposuccion", "Liposucción (retirar grasa localizada)"),
        ("lipoescultura", "Lipoescultura (moldear el contorno corporal)"),
        ("abdominoplastia", "Abdominoplastia (retirar exceso de piel y reparar abdomen)"),
        ("lipoabdominoplastia", "Lipoabdominoplastia (liposucción más abdominoplastia)"),
        ("braquioplastia", "Braquioplastia (brazos: retirar flacidez o exceso de piel)"),
        ("musloplastia", "Musloplastia (piernas/muslos: retirar flacidez o exceso de piel)"),
        ("gluteoplastia", "Gluteoplastia (glúteos: mejorar forma o volumen)"),
    ], True),
    ("cirugia_intima", "Cirugía íntima femenina de interés", [
        ("labioplastia", "Labioplastia (mejorar tamaño o forma de labios menores)"),
        ("rejuvenecimiento_vaginal", "Rejuvenecimiento vaginal (mejorar tonicidad o molestias íntimas)"),
        ("monte_venus", "Monte de Venus (mejorar volumen o contorno de la zona suprapúbica)"),
    ], True),
    ("riesgo_quirurgico", "Aspectos importantes para valorar riesgo quirúrgico", [
        ("fumador", "Fuma actualmente"),
        ("anticoagulantes", "Usa anticoagulantes o aspirina"),
        ("diabetes", "Diabetes"),
        ("hipertension", "Hipertensión"),
        ("trombosis", "Antecedente de trombosis"),
        ("cirugia_previa", "Cirugía previa relevante"),
        ("ninguno", "Ninguno / no aplica"),
    ], True),
]

FORMULARIOS_ESTRUCTURADOS = {
    "capilar": CAPILAR_FORMULARIO,
    "cirugia_plastica": CIRUGIA_PLASTICA_FORMULARIO,
    "medicina_estetica": MEDICINA_ESTETICA_FORMULARIO,
}

BITACORA_TIPOS = {"enfermeria", "terapias", "camara_hiperbarica"}


class HistoriaClinicaEspecialidadForm(BaseClinicaForm):
    CAMPOS_POR_TIPO = {
        "capilar": {
            "motivo_consulta": "Motivo de consulta capilar",
            "antecedentes": "Antecedentes personales y capilares",
            "historia_enfermedad_actual": "Historia de la enfermedad actual capilar",
            "examen_fisico": "Examen fisico capilar / tricologico",
            "evaluacion_clinica": "Evaluacion capilar y hallazgos",
            "analisis_clinico": "Impresion diagnostica capilar",
            "procedimiento": "Procedimiento o tecnica realizada",
            "conducta": "Conducta medica capilar",
            "plan_tratamiento": "Plan capilar",
        },
        "cirugia_plastica": {
            "motivo_consulta": "Motivo y expectativa quirurgica",
            "antecedentes": "Antecedentes medicos y quirurgicos",
            "historia_enfermedad_actual": "Historia de la enfermedad actual / motivo quirurgico",
            "examen_fisico": "Examen fisico preoperatorio",
            "evaluacion_clinica": "Examen fisico y valoracion preoperatoria",
            "analisis_clinico": "Analisis del caso e impresion quirurgica",
            "procedimiento": "Procedimiento propuesto o realizado",
            "conducta": "Conducta quirurgica",
            "plan_tratamiento": "Plan quirurgico y seguimiento",
        },
        "medicina_estetica": {
            "motivo_consulta": "Explique con sus propias palabras que desea mejorar",
            "antecedentes": "Antecedentes esteticos, quirurgicos y medicos relevantes",
            "historia_enfermedad_actual": "Historia de la enfermedad actual estetica",
            "examen_fisico": "Examen fisico / evaluacion objetiva",
            "evaluacion_clinica": "Evaluacion estetica facial/corporal",
            "analisis_clinico": "Analisis estetico e impresion clinica",
            "procedimiento": "Procedimiento recomendado o realizado",
            "conducta": "Conducta medica estetica",
            "plan_tratamiento": "HEA y plan estetico",
        },
        "enfermeria": {
            "observaciones": "Nota de bitacora de enfermeria",
        },
        "terapias": {
            "observaciones": "Nota de bitacora de terapias",
        },
        "camara_hiperbarica": {
            "observaciones": "Nota de bitacora de camara hiperbarica",
        },
    }

    class Meta:
        model = HistoriaClinicaEspecialidad
        fields = [
            "profesional",
            "fecha_atencion",
            "motivo_consulta",
            "antecedentes",
            "historia_enfermedad_actual",
            "signos_vitales",
            "examen_fisico",
            "evaluacion_clinica",
            "diagnostico",
            "analisis_clinico",
            "procedimiento",
            "conducta",
            "plan_tratamiento",
            "indicaciones",
            "observaciones",
            "notas_privadas_doctor",
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
        if self.tipo in BITACORA_TIPOS:
            for campo in [
                "motivo_consulta", "antecedentes", "historia_enfermedad_actual",
                "signos_vitales", "examen_fisico", "evaluacion_clinica",
                "diagnostico", "analisis_clinico", "procedimiento", "conducta",
                "plan_tratamiento", "indicaciones", "notas_privadas_doctor",
            ]:
                self.fields.pop(campo, None)
            self.fields["observaciones"].widget.attrs.update({
                "rows": 8,
                "placeholder": "Escriba aqui la nota de bitacora. Se guardara con fecha, hora y usuario.",
            })
        self.campos_estructurados = FORMULARIOS_ESTRUCTURADOS.get(self.tipo, [])
        datos = self.instance.datos_especialidad if self.instance and isinstance(self.instance.datos_especialidad, dict) else {}
        for nombre, etiqueta, opciones, multiple in self.campos_estructurados:
            field_class = forms.MultipleChoiceField if multiple else forms.ChoiceField
            choices = opciones if multiple else [("", "Seleccione una opcion"), *opciones]
            self.fields[nombre] = field_class(
                required=False,
                label=etiqueta,
                choices=choices,
                widget=forms.CheckboxSelectMultiple(attrs={"class": "clinical-check-list"}) if multiple else forms.RadioSelect(attrs={"class": "clinical-check-list"}),
                initial=datos.get(nombre),
            )
            self.fields[f"{nombre}_otros"] = forms.CharField(
                required=False,
                label="Otro:" if nombre in {"estetica_motivo", "estetica_objetivo_principal"} else "Otros / detalle",
                widget=forms.Textarea(attrs={"rows": 2, "placeholder": "Detalle si aplica."}),
                initial=datos.get(f"{nombre}_otros", ""),
            )
        if self.campos_estructurados:
            campos_texto_a_ocultar = [
                "motivo_consulta", "antecedentes", "historia_enfermedad_actual",
                "signos_vitales", "examen_fisico", "evaluacion_clinica",
                "diagnostico", "analisis_clinico", "procedimiento", "conducta",
                "indicaciones", "observaciones", "notas_privadas_doctor",
            ]
            for campo in campos_texto_a_ocultar:
                self.fields.pop(campo, None)
            if "plan_tratamiento" in self.fields:
                self.fields["plan_tratamiento"].label = "Diagnóstico y plan"
                self.fields["plan_tratamiento"].widget.attrs.update({
                    "rows": 8,
                    "placeholder": "Escriba aquí el diagnóstico, hallazgos relevantes, conducta médica, plan de tratamiento, indicaciones y seguimiento.",
                })
            orden = ["profesional", "fecha_atencion"]
            for nombre, *_resto in self.campos_estructurados:
                orden.extend([nombre, f"{nombre}_otros"])
            orden.extend(["plan_tratamiento", "estado"])
            self.fields = OrderedDict(
                (nombre, self.fields[nombre])
                for nombre in orden
                if nombre in self.fields
            )
        placeholders = {
            "motivo_consulta": "Resumen breve del motivo principal referido en consulta.",
            "antecedentes": "Antecedentes relevantes tomados del paciente y confirmados por la doctora.",
            "historia_enfermedad_actual": "Campo privado de la doctora: evolución, tiempo, síntomas, tratamientos previos y contexto clínico actual.",
            "signos_vitales": "PA, FC, FR, temperatura, SatO2, peso, talla, IMC u otros datos tomados por enfermería.",
            "examen_fisico": "Campo interno: hallazgos objetivos del examen físico por regiones o área evaluada.",
            "evaluacion_clinica": "Valoración clínica, hallazgos relevantes y correlación con la preconsulta.",
            "diagnostico": "Diagnóstico o diagnósticos clínicos.",
            "analisis_clinico": "Impresión clínica, criterio médico y razonamiento del caso.",
            "procedimiento": "Procedimiento recomendado, realizado o pendiente.",
            "conducta": "Decisión tomada: indicar estudios, programar procedimiento, manejo médico, referir, observar o controlar.",
            "plan_tratamiento": "Plan terapéutico, quirúrgico o estético acordado.",
            "indicaciones": "Indicaciones para el paciente, cuidados, medicamentos, alarmas y seguimiento.",
            "observaciones": "Notas complementarias visibles en esta historia.",
            "notas_privadas_doctor": "Notas privadas internas de la doctora/equipo. No corresponden al formulario público del paciente.",
        }
        labels_generales = {
            "historia_enfermedad_actual": "Historia de la enfermedad actual (privado doctora)",
            "signos_vitales": "Signos vitales / datos de enfermería",
            "examen_fisico": "Examen físico (médico o enfermería)",
            "analisis_clinico": "Análisis clínico / impresión médica",
            "conducta": "Conducta médica",
            "notas_privadas_doctor": "Notas privadas del equipo médico",
        }
        for nombre, etiqueta in labels_generales.items():
            if nombre in self.fields:
                self.fields[nombre].label = etiqueta
        for nombre, placeholder in placeholders.items():
            if nombre in self.fields:
                self.fields[nombre].widget.attrs.setdefault("placeholder", placeholder)
        for nombre in [
            "motivo_consulta", "antecedentes", "historia_enfermedad_actual",
            "signos_vitales", "examen_fisico", "evaluacion_clinica", "diagnostico",
            "analisis_clinico", "procedimiento", "conducta", "plan_tratamiento",
            "indicaciones", "observaciones", "notas_privadas_doctor",
        ]:
            if nombre in self.fields and self.tipo in FORMULARIOS_ESTRUCTURADOS:
                self.fields[nombre].widget.attrs.setdefault("rows", 3)

    def save(self, commit=True):
        historia = super().save(commit=False)
        if self.campos_estructurados:
            datos = {}
            resumen = []
            for nombre, etiqueta, opciones, multiple in self.campos_estructurados:
                valor = self.cleaned_data.get(nombre)
                otros = self.cleaned_data.get(f"{nombre}_otros")
                if valor not in (None, "", []):
                    datos[nombre] = valor
                    etiquetas = dict(opciones)
                    seleccion = valor if isinstance(valor, list) else [valor]
                    resumen.append(f"{etiqueta}: " + ", ".join(etiquetas.get(item, item) for item in seleccion))
                if otros:
                    datos[f"{nombre}_otros"] = otros
                    resumen.append(f"{etiqueta} - otros/detalle: {otros}")
            historia.datos_especialidad = datos
            if not historia.evaluacion_clinica:
                historia.evaluacion_clinica = "\n".join(resumen[:12])
        if commit:
            historia.save()
            self.save_m2m()
        return historia


ANTECEDENTES_PERSONALES_CHOICES = [
    ("no_aplica", "No aplica / no tengo diagnosticos conocidos"),
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
    ("no_aplica", "No aplica / no tomo medicamentos habituales"),
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
    ("no_aplica", "No aplica / no deseo responder"),
]

CODIGO_AREA_CHOICES = [
    ("504", "Honduras (+504)"),
    ("1", "Estados Unidos / Canadá (+1)"),
    ("52", "México (+52)"),
    ("34", "España (+34)"),
    ("502", "Guatemala (+502)"),
    ("503", "El Salvador (+503)"),
    ("505", "Nicaragua (+505)"),
    ("506", "Costa Rica (+506)"),
    ("507", "Panamá (+507)"),
    ("57", "Colombia (+57)"),
]

CODIGO_AREA_CHOICES = PHONE_PREFIX_CHOICES

INFORMANTE_CHOICES = [
    ("yo_mismo", "Yo mismo/a"),
    ("familiar", "Familiar"),
    ("amigo", "Amigo/a"),
    ("menor_edad", "Menor de edad"),
]

DROGAS_RECREATIVAS_CHOICES = [
    ("cocaina", "Cocaina"),
    ("marihuana", "Marihuana"),
    ("crack", "Crack"),
]

ANTECEDENTES_FAMILIARES_CHOICES = [
    ("no_aplica", "No aplica / no conozco antecedentes familiares"),
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

DIETA_CHOICES = [
    ("balanceada", "Balanceada / variada"),
    ("alta_carbohidratos", "Alta en carbohidratos o azucares"),
    ("alta_proteina", "Alta en proteina"),
    ("vegetariana", "Vegetariana"),
    ("vegana", "Vegana"),
    ("keto", "Keto / baja en carbohidratos"),
    ("ayuno_intermitente", "Ayuno intermitente"),
    ("dieta_medica", "Dieta indicada por medico o nutricionista"),
    ("sin_control", "Sin dieta especifica"),
    ("no_aplica", "No aplica"),
]

EJERCICIO_CHOICES = [
    ("no_realiza", "No realiza actividad fisica"),
    ("ocasional", "Ocasional"),
    ("1_2_semana", "1 a 2 veces por semana"),
    ("3_4_semana", "3 a 4 veces por semana"),
    ("5_mas_semana", "5 o mas veces por semana"),
    ("cardio", "Cardio / caminata / bicicleta"),
    ("pesas", "Pesas / entrenamiento de fuerza"),
    ("deporte", "Practica algun deporte"),
    ("rehabilitacion", "Terapia fisica o rehabilitacion"),
    ("no_aplica", "No aplica"),
]

PROCEDIMIENTOS_GENERALES_GRUPOS = [
    (
        "Cirugia facial",
        [
            ("blefaroplastia_superior", "Blefaroplastia superior (retirar exceso de piel del parpado superior)"),
            ("blefaroplastia_inferior", "Blefaroplastia inferior (mejorar bolsas u ojeras del parpado inferior)"),
            ("lifting_facial", "Lifting facial (rejuvenecimiento del rostro y flacidez facial)"),
            ("lifting_cervical", "Lifting cervical (mejorar flacidez del cuello)"),
            ("rinoplastia", "Rinoplastia (cirugia estetica o funcional de la nariz)"),
            ("otoplastia", "Otoplastia (correccion de orejas prominentes)"),
            ("lip_lift", "Lip Lift (levantamiento del labio superior)"),
            ("bichectomia", "Bichectomia (reduccion de bolsas de Bichat en mejillas)"),
            ("lipoinjerto_facial", "Lipoinjerto facial (relleno con grasa propia)"),
            ("mentoplastia", "Mentoplastia (mejorar forma o proyeccion del menton)"),
        ],
    ),
    (
        "Cirugia mamaria",
        [
            ("aumento_mamario", "Aumento mamario (colocacion de implantes o aumento de volumen)"),
            ("levantamiento_mamario", "Levantamiento mamario / Mastopexia (elevar y mejorar forma del busto)"),
            ("reduccion_mamaria", "Reduccion mamaria (disminuir volumen del busto)"),
            ("recambio_implantes", "Recambio de implantes (cambiar implantes previos)"),
            ("contractura_capsular", "Correccion de contractura capsular (dureza o molestia alrededor del implante)"),
            ("ginecomastia", "Ginecomastia (reduccion de pecho masculino)"),
            ("reconstruccion_mamaria", "Reconstruccion mamaria (reconstruccion posterior a cirugia o enfermedad)"),
        ],
    ),
    (
        "Contorno corporal",
        [
            ("liposuccion", "Liposuccion (retirar grasa localizada)"),
            ("liposuccion_hd", "Liposuccion HD (definicion corporal de alta marcacion)"),
            ("lipoescultura", "Lipoescultura (moldear el cuerpo con grasa propia)"),
            ("abdominoplastia", "Abdominoplastia (retirar exceso de piel y mejorar abdomen)"),
            ("lipoabdominoplastia", "Lipoabdominoplastia (liposuccion + abdominoplastia)"),
            ("braquioplastia", "Braquioplastia (brazos: retirar flacidez o exceso de piel)"),
            ("musloplastia", "Musloplastia (piernas/muslos: retirar flacidez o exceso de piel)"),
            ("gluteoplastia", "Gluteoplastia (gluteos: mejorar forma o volumen)"),
            ("lipoinjerto_gluteo", "Lipoinjerto gluteo (aumento de gluteos con grasa propia)"),
            ("mommy_makeover", "Mommy Makeover (combinacion de cirugias post embarazo)"),
        ],
    ),
    (
        "Cirugia intima femenina",
        [
            ("labioplastia", "Labioplastia (mejorar tamano o forma de labios menores)"),
            ("vaginoplastia", "Vaginoplastia (reparacion o estrechamiento vaginal)"),
            ("hoodplasty", "Hoodplasty (mejorar exceso de piel sobre el clitoris)"),
            ("rejuvenecimiento_vaginal", "Rejuvenecimiento vaginal (mejorar firmeza o molestias intimas)"),
        ],
    ),
    (
        "Capilar",
        [
            ("evaluacion_alopecia", "Evaluacion de alopecia (diagnostico de perdida de cabello)"),
            ("prp_capilar", "PRP capilar (plasma rico en plaquetas para estimular cabello)"),
            ("mesoterapia_capilar", "Mesoterapia capilar (microinyecciones nutritivas en cuero cabelludo)"),
            ("trasplante_capilar", "Trasplante capilar (implante de foliculos en areas con perdida)"),
        ],
    ),
    (
        "Tratamiento Estetico / Piel",
        [
            ("rejuvenecimiento_facial", "Rejuvenecimiento facial"),
            ("prevencion_envejecimiento", "Prevencion del envejecimiento"),
            ("arrugas_lineas_expresion", "Arrugas o lineas de expresion"),
            ("flacidez_facial", "Flacidez facial"),
            ("perdida_volumen_facial", "Perdida de volumen facial"),
            ("aumento_labios", "Aumento de labios"),
            ("rinomodelacion", "Rinomodelacion"),
            ("definicion_mandibula_menton", "Definicion de mandibula y menton"),
            ("ojeras", "Ojeras"),
            ("manchas_faciales", "Manchas faciales"),
            ("melasma", "Melasma"),
            ("acne_activo", "Acne activo"),
            ("cicatrices_acne", "Cicatrices de acne"),
            ("poros_dilatados", "Poros dilatados"),
            ("piel_grasa", "Piel grasa"),
            ("piel_seca_deshidratada", "Piel seca o deshidratada"),
            ("rosacea", "Rosacea"),
            ("enrojecimiento_facial", "Enrojecimiento facial"),
            ("textura_irregular_piel", "Textura irregular de la piel"),
            ("luminosidad_piel", "Luminosidad de la piel"),
            ("rejuvenecimiento_cuello_escote", "Rejuvenecimiento de cuello y escote"),
            ("papada", "Papada"),
            ("celulitis", "Celulitis"),
            ("flacidez_corporal", "Flacidez corporal"),
            ("estrias", "Estrias"),
            ("cicatrices_corporales", "Cicatrices corporales"),
            ("hiperhidrosis", "Hiperhidrosis (sudoracion excesiva)"),
            ("alopecia_caida_cabello", "Alopecia / caida del cabello"),
            ("rejuvenecimiento_intimo_femenino", "Rejuvenecimiento intimo femenino"),
            ("verse_mas_joven", "Objetivo: verse mas joven"),
            ("mejorar_calidad_piel", "Objetivo: mejorar la calidad de la piel"),
            ("armonizar_rostro", "Objetivo: armonizar el rostro"),
            ("mejorar_autoestima", "Objetivo: mejorar la autoestima"),
            ("prevenir_envejecimiento", "Objetivo: prevenir el envejecimiento"),
            ("corregir_preocupacion_especifica", "Objetivo: corregir una preocupacion especifica"),
            ("mantener_resultados_previos", "Objetivo: mantener resultados previos"),
        ],
    ),
]

MOTIVO_CATEGORIA_CHOICES = [
    *[
        (
            titulo.lower()
            .replace(" ", "_")
            .replace("í", "i")
            .replace("é", "e")
            .replace("á", "a")
            .replace("ó", "o")
            .replace("ú", "u"),
            titulo,
        )
        for titulo, _opciones in PROCEDIMIENTOS_GENERALES_GRUPOS
    ],
]

MOTIVO_CATEGORIA_CHOICES = [
    ("medicina_estetica" if etiqueta == "Tratamiento Estetico / Piel" else valor, etiqueta)
    for valor, etiqueta in MOTIVO_CATEGORIA_CHOICES
]
MOTIVO_CATEGORIA_CHOICES.extend([
    ("camara_hiperbarica", "Camara hiperbarica"),
    ("enfermeria", "Enfermeria"),
    ("tratamientos", "Tratamientos"),
])
MOTIVO_CATEGORIA_CHOICES.append(("no_aplica", "No aplica / no estoy seguro todavia"))

def _codigo_grupo_procedimiento(titulo):
    if "Tratamiento Estetico" in titulo:
        return "medicina_estetica"
    return (
        titulo.lower()
        .replace(" ", "_")
        .replace("/", "")
        .replace("Ã­", "i")
        .replace("Ã©", "e")
        .replace("Ã¡", "a")
        .replace("Ã³", "o")
        .replace("Ãº", "u")
    )


PROCEDIMIENTOS_GENERALES_CHOICES = [
    opcion
    for titulo, opciones in PROCEDIMIENTOS_GENERALES_GRUPOS
    for opcion in [*opciones, (f"no_aplica_{_codigo_grupo_procedimiento(titulo)}", "No aplica / solo deseo valoracion")]
]

ALERGIAS_GENERALES_CHOICES = [
    ("no_aplica", "No aplica"),
    ("medicamentos", "Medicamentos"),
    ("penicilina", "Penicilina u otros antibioticos"),
    ("aines", "Antiinflamatorios / analgesicos"),
    ("latex", "Latex"),
    ("yodo", "Yodo"),
    ("mariscos", "Mariscos"),
    ("mani_nueces", "Mani, nueces u otros frutos secos"),
    ("lacteos", "Leche o derivados lacteos"),
    ("huevo", "Huevo"),
    ("soya", "Soya"),
    ("alimentos", "Otros alimentos"),
    ("adhesivos", "Adhesivos"),
    ("ninguna", "Ninguna"),
]

MEDICAMENTOS_ACTUALES_CHOICES = [
    ("no_aplica", "No aplica"),
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
    ("no_aplica", "No aplica"),
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

CONSUMO_RIESGO_CHOICES = [
    ("no_aplica", "No aplica"),
    ("tabaco", "Tabaco / cigarrillo"),
    ("vape", "Vapeador"),
    ("alcohol", "Alcohol"),
    ("marihuana", "Marihuana"),
    ("cocaina", "Cocaina"),
    ("crack", "Crack"),
    ("ninguno", "Ninguno"),
]

PSICOLOGICA_CHOICES = [
    ("no_aplica", "No aplica"),
    ("ansiedad", "Ansiedad"),
    ("depresion", "Depresion"),
    ("tratamiento_psicologico", "Recibe o ha recibido tratamiento psicologico"),
    ("tratamiento_psiquiatrico", "Recibe o ha recibido tratamiento psiquiatrico"),
    ("autoestima", "Busca sentirse mejor consigo mismo/a"),
    ("perfeccion", "Siente que busca perfeccion absoluta"),
    ("presion_externa", "Siente presion de otra persona para operarse"),
    ("ninguna", "Ninguna de las anteriores"),
]


class LenientMultipleChoiceField(forms.MultipleChoiceField):
    def to_python(self, value):
        if isinstance(value, str):
            value = [value]
        return super().to_python(value)


class PreconsultaClinicaPublicaForm(forms.ModelForm):
    foto_perfil = forms.ImageField(
        required=False,
        label="Fotografía del paciente",
        help_text="Puede tomarla con la cámara o seleccionar una imagen del dispositivo.",
        widget=forms.ClearableFileInput(attrs={
            "accept": "image/jpeg,image/png,image/webp",
            "class": "photo-file-input",
        }),
    )
    nombres = forms.CharField(max_length=170, label="Primer y segundo nombre")
    apellidos = forms.CharField(max_length=170, label="Primer y segundo apellido")
    primer_nombre = forms.CharField(max_length=80, required=False, widget=forms.HiddenInput())
    segundo_nombre = forms.CharField(max_length=80, required=False, widget=forms.HiddenInput())
    primer_apellido = forms.CharField(max_length=80, required=False, widget=forms.HiddenInput())
    segundo_apellido = forms.CharField(max_length=80, required=False, widget=forms.HiddenInput())
    identidad = forms.CharField(max_length=30, label="Numero de identidad")
    fecha_nacimiento = forms.DateField(label="Fecha de nacimiento", widget=forms.DateInput(attrs={"type": "date"}))
    sexo = forms.ChoiceField(label="Sexo", choices=[("", "Seleccione una opcion"), *Paciente.SEXO_CHOICES])
    estado_civil = forms.ChoiceField(label="Estado civil", choices=[("", "Seleccione una opcion"), *Paciente.ESTADO_CIVIL_CHOICES])
    correo = forms.EmailField(required=False, label="Correo electronico")
    telefono_codigo_area = forms.ChoiceField(
        required=False,
        choices=CODIGO_AREA_CHOICES,
        initial="504",
        label="Codigo de area",
    )
    telefono = forms.CharField(max_length=30, label="Telefono o WhatsApp")
    direccion = forms.CharField(required=False, label="Lugar de residencia", widget=forms.Textarea(attrs={"rows": 2}))
    lugar_nacimiento = forms.CharField(max_length=160, required=False, label="Lugar de nacimiento")
    ocupacion = forms.CharField(max_length=160, required=False, label="Profesion u ocupacion")
    lugar_trabajo = forms.CharField(max_length=180, required=False, label="Lugar de trabajo")
    informante = forms.ChoiceField(
        choices=INFORMANTE_CHOICES,
        initial="yo_mismo",
        label="Persona que proporciona la informacion",
    )
    informante_detalle = forms.CharField(
        max_length=180,
        required=False,
        label="Nombre de la persona que proporciona la informacion",
        help_text="Completar cuando la informacion la proporciona un familiar, amigo o encargado de un menor de edad.",
    )
    contacto_emergencia_completo = forms.CharField(
        max_length=220,
        required=False,
        label="Nombre y numero de contacto de emergencia",
        help_text="Ejemplo: Maria Perez - 9999-9999",
    )
    contacto_emergencia = forms.CharField(max_length=180, required=False, widget=forms.HiddenInput())
    telefono_emergencia = forms.CharField(max_length=30, required=False, widget=forms.HiddenInput())
    referido_por = forms.ChoiceField(required=True, choices=REFERIDO_POR_CHOICES, label="Como conocio la clinica")
    referido_por_detalle = forms.CharField(
        max_length=180,
        required=False,
        label="Quien lo refirio",
        help_text="Solo complete este campo si selecciono Referencia.",
    )
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
    motivo_categoria = forms.MultipleChoiceField(
        required=True,
        choices=MOTIVO_CATEGORIA_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        label="Motivo principal de consulta",
        help_text="Seleccione el área principal para mostrar las opciones correspondientes.",
    )
    funciones_organicas = LenientMultipleChoiceField(
        required=True,
        choices=PreconsultaClinica.REVISION_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        label="Funciones organicas generales: apetito, sueno, sed, miccion y evacuaciones",
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
    medicamentos_actuales_otros = forms.CharField(required=False, label="Explique si toma alguno actualmente", widget=forms.Textarea(attrs={"rows": 2, "placeholder": "Nombre, dosis, frecuencia y desde cuando lo toma."}))
    quirurgicos_operado = forms.MultipleChoiceField(
        required=False,
        choices=SI_NO_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        label="Ha sido operado anteriormente",
    )
    quirurgicos_detalle = forms.CharField(required=False, label="Detalle cirugias previas, plasticas u otras", widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Indique año, procedimiento, lugar y si tuvo alguna complicacion."}))
    antecedentes_hospitalarios = forms.MultipleChoiceField(
        required=True,
        choices=SI_NO_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        label="He tenido hospitalizaciones, accidentes, fracturas o cirugias previas",
    )
    tabaco_frecuencia = forms.MultipleChoiceField(required=False, choices=FRECUENCIA_CHOICES, widget=forms.CheckboxSelectMultiple, label="Tabaco")
    alcohol_frecuencia = forms.MultipleChoiceField(required=False, choices=FRECUENCIA_CHOICES, widget=forms.CheckboxSelectMultiple, label="Alcohol")
    drogas_recreativas = forms.MultipleChoiceField(required=False, choices=SI_NO_CHOICES, widget=forms.CheckboxSelectMultiple, label="Drogas recreativas")
    drogas_recreativas_tipos = forms.MultipleChoiceField(required=False, choices=DROGAS_RECREATIVAS_CHOICES, widget=forms.CheckboxSelectMultiple, label="Cuales drogas recreativas")
    drogas_recreativas_detalle = forms.CharField(required=False, label="Otros", widget=forms.Textarea(attrs={"rows": 2}))
    consumo_riesgo = forms.MultipleChoiceField(
        required=False,
        choices=CONSUMO_RIESGO_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        label="Habitos de riesgo o consumo actual",
    )
    consumo_riesgo_detalle = forms.CharField(required=False, label="Detalle frecuencia o cantidad", widget=forms.Textarea(attrs={"rows": 2}))
    dieta = forms.MultipleChoiceField(
        required=False,
        choices=DIETA_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        label="Dieta o alimentacion habitual",
    )
    ejercicio = forms.MultipleChoiceField(
        required=False,
        choices=EJERCICIO_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        label="Ejercicio o actividad fisica",
    )
    riesgo_tromboembolico = forms.MultipleChoiceField(
        required=False,
        choices=RIESGO_TROMBOEMBOLICO_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        label="Riesgo tromboembolico",
    )
    riesgo_tromboembolico_otros = forms.CharField(required=False, label="Otros riesgos tromboembolicos", widget=forms.Textarea(attrs={"rows": 2}))
    gine_menarca = forms.CharField(required=False, label="Menarca (edad de su primera menstruacion)")
    gine_gestas = forms.CharField(required=False, label="Gestas (cuantas veces ha estado embarazada)")
    gine_partos = forms.CharField(required=False, label="Partos (partos vaginales)")
    gine_cesareas = forms.CharField(required=False, label="Cesareas (nacimientos por cesarea)")
    gine_abortos = forms.CharField(required=False, label="Abortos (perdidas o interrupciones del embarazo)")
    gine_ultima_menstruacion = forms.CharField(required=False, label="Ultima menstruacion (fecha aproximada)")
    gine_embarazada = forms.MultipleChoiceField(required=False, choices=SI_NO_CHOICES, widget=forms.CheckboxSelectMultiple, label="Esta embarazada")
    gine_lactancia = forms.MultipleChoiceField(required=False, choices=SI_NO_CHOICES, widget=forms.CheckboxSelectMultiple, label="Lactancia")
    gine_mamografia = forms.MultipleChoiceField(required=False, choices=SI_NO_CHOICES, widget=forms.CheckboxSelectMultiple, label="Ha tenido mamografia o ultrasonido mamario")
    gine_mamografia_fecha = forms.DateField(required=False, label="Fecha de la ultima mamografia o ultrasonido", widget=forms.DateInput(attrs={"type": "date"}))
    decision_cirugia = forms.MultipleChoiceField(required=False, choices=DECISION_CIRUGIA_CHOICES, widget=forms.CheckboxSelectMultiple, label="Quien tomo la decision de operarse")
    decision_cirugia_otros = forms.CharField(required=False, label="Otros en decision de cirugia")
    expectativas_realistas = forms.MultipleChoiceField(required=False, choices=SI_NO_CHOICES, widget=forms.CheckboxSelectMultiple, label="Expectativas realistas")
    busca_perfeccion = forms.MultipleChoiceField(required=False, choices=SI_NO_CHOICES, widget=forms.CheckboxSelectMultiple, label="Busca perfeccion absoluta")
    multiples_cirugias_insatisfaccion = forms.MultipleChoiceField(required=False, choices=SI_NO_CHOICES, widget=forms.CheckboxSelectMultiple, label="Ha tenido multiples cirugias por insatisfaccion")
    evaluacion_psicologica = forms.MultipleChoiceField(
        required=False,
        choices=PSICOLOGICA_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        label="Evaluacion psicologica y emocional",
    )
    evaluacion_psicologica_detalle = forms.CharField(
        required=False,
        label="Detalle emocional o psicologico",
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Puede contar si ha tenido ansiedad, tratamiento psicologico, quien tomo la decision o que espera sentir despues del procedimiento."}),
    )
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
            "nombres", "apellidos", "primer_nombre", "segundo_nombre", "primer_apellido", "segundo_apellido",
            "identidad", "fecha_nacimiento", "sexo", "estado_civil", "correo", "telefono_codigo_area", "telefono",
            "direccion", "lugar_nacimiento", "ocupacion", "lugar_trabajo",
            "informante", "informante_detalle", "contacto_emergencia_completo", "contacto_emergencia", "telefono_emergencia",
            "referido_por", "referido_por_detalle",
            "motivo_categoria", "procedimientos_interes", "procedimientos_interes_otros",
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
            "consumo_riesgo", "consumo_riesgo_detalle", "riesgo_tromboembolico_otros", "gine_menarca", "gine_gestas", "gine_partos",
            "gine_cesareas", "gine_abortos", "gine_ultima_menstruacion", "gine_embarazada",
            "gine_lactancia", "gine_mamografia", "gine_mamografia_fecha", "decision_cirugia",
            "decision_cirugia_otros", "expectativas_realistas", "busca_perfeccion",
            "multiples_cirugias_insatisfaccion", "evaluacion_psicologica", "evaluacion_psicologica_detalle",
            "examen_peso", "examen_talla", "examen_imc",
            "examen_pa", "examen_fc", "examen_sato2", "consentimiento_datos",
        ]
        widgets = {
            "motivo_consulta": forms.Textarea(attrs={"rows": 3, "placeholder": "Cuentenos brevemente que desea consultar o que procedimiento le interesa."}),
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
            "funciones_organicas": "Funciones organicas generales: apetito, sueno, sed, miccion y evacuaciones",
            "funciones_detalle": "Explique cualquier alteracion",
            "revision_sistemas": "Revision general de organos y sistemas",
            "revision_sistemas_detalle": "Explique sintomas o alteraciones",
            "antecedentes_hospitalarios": "He tenido hospitalizaciones, accidentes, fracturas o cirugias previas",
            "antecedentes_hospitalarios_detalle": "Detalle fechas, lugar, motivo, procedimiento y si hubo complicaciones",
            "antecedentes_personales_detalle": "Detalle condiciones seleccionadas u otras enfermedades propias",
            "medicamentos_habituales_detalle": "Indique nombre, dosis, frecuencia y desde cuando lo toma",
            "antecedentes_familiares_detalle": "Indique familiar cercano y condicion",
            "dieta": "Dieta o alimentacion habitual",
            "ejercicio": "Ejercicio o actividad fisica",
            "habitos": "Otros habitos no patologicos",
            "alergias": "Detalle alergias a medicamentos, alimentos o materiales",
            "antecedentes_infecciosos": "Enfermedades infecciosas previas, incluido COVID-19",
        }

    def __init__(self, *args, paciente=None, empresa=None, **kwargs):
        self.paciente = paciente
        self.empresa = empresa or getattr(paciente, "empresa", None)
        super().__init__(*args, **kwargs)
        if paciente is None:
            self.fields["foto_perfil"].required = True
            self.fields["foto_perfil"].widget.attrs["data-required-photo"] = "1"
        if paciente and not self.is_bound:
            for campo in [
                "primer_nombre", "segundo_nombre", "primer_apellido", "segundo_apellido",
                "identidad", "fecha_nacimiento", "sexo", "estado_civil", "correo", "direccion",
                "lugar_nacimiento", "ocupacion", "contacto_emergencia", "telefono_emergencia",
            ]:
                self.fields[campo].initial = getattr(paciente, campo, None)
            self.fields["telefono_codigo_area"].initial = (paciente.prefijo_telefono or "504").replace("Honduras (+504)", "504")
            self.fields["nombres"].initial = " ".join(
                parte for parte in [paciente.primer_nombre, paciente.segundo_nombre] if parte
            ) or paciente.nombre
            self.fields["apellidos"].initial = " ".join(
                parte for parte in [paciente.primer_apellido, paciente.segundo_apellido] if parte
            )
            self.fields["contacto_emergencia_completo"].initial = " - ".join(
                parte for parte in [paciente.contacto_emergencia, paciente.telefono_emergencia] if parte
            )
            self.fields["telefono"].initial = paciente.whatsapp or paciente.telefono
            self.fields["alergias"].initial = paciente.alergias
        self.fields["identidad"].widget.attrs.update({
            "inputmode": "numeric",
            "pattern": "[0-9]*",
            "placeholder": "Solo numeros, sin guiones",
        })
        self.fields["telefono"].widget.attrs.update({
            "inputmode": "tel",
            "placeholder": "Ejemplo: 9999-9999",
        })
        formulario_general = {}
        if self.instance and isinstance(self.instance.datos_generales, dict):
            formulario_general = self.instance.datos_generales.get("formulario_general", {}) or {}
        if formulario_general and not self.is_bound:
            for campo, valor in formulario_general.items():
                if campo in self.fields:
                    if campo == "motivo_categoria" and isinstance(valor, str):
                        valor = [valor]
                    self.fields[campo].initial = valor
        for campo in [
            "motivo_categoria", "funciones_organicas", "antecedentes_personales", "medicamentos_habituales",
            "antecedentes_familiares", "alergias_seleccion", "medicamentos_actuales_seleccion",
            "antecedentes_hospitalarios", "quirurgicos_operado", "tabaco_frecuencia", "alcohol_frecuencia", "drogas_recreativas", "drogas_recreativas_tipos",
            "consumo_riesgo", "dieta", "ejercicio", "riesgo_tromboembolico", "gine_embarazada", "gine_lactancia", "gine_mamografia",
            "decision_cirugia", "expectativas_realistas", "busca_perfeccion",
            "multiples_cirugias_insatisfaccion", "evaluacion_psicologica",
        ]:
            if campo in self.fields:
                self.fields[campo].widget.attrs["class"] = "animated-check-list"
        for campo in [
            "motivo_categoria", "procedimientos_interes", "funciones_organicas", "antecedentes_personales", "medicamentos_habituales",
            "antecedentes_familiares", "alergias_seleccion", "medicamentos_actuales_seleccion",
            "antecedentes_hospitalarios", "quirurgicos_operado", "consumo_riesgo", "dieta", "ejercicio", "riesgo_tromboembolico",
            "evaluacion_psicologica", "expectativas_realistas", "busca_perfeccion", "multiples_cirugias_insatisfaccion",
        ]:
            if campo in self.fields:
                self.fields[campo].required = True
        for campo in [
            "funciones_organicas", "funciones_detalle", "procedimientos_interes_otros",
            "antecedentes_personales_detalle", "alergias_otras", "alergias",
            "medicamentos_habituales_detalle", "medicamentos_actuales_otros",
            "antecedentes_infecciosos", "antecedentes_hospitalarios_detalle",
            "quirurgicos_detalle", "consumo_riesgo_detalle",
            "antecedentes_familiares_detalle", "riesgo_tromboembolico_otros",
            "evaluacion_psicologica_detalle",
        ]:
            if campo in self.fields:
                self.fields[campo].required = False

    def clean_identidad(self):
        identidad = (self.cleaned_data.get("identidad") or "").strip()
        if not identidad.isdigit():
            raise forms.ValidationError("Utilice solamente numeros, sin espacios ni guiones.")
        if self.empresa and Paciente.objects.filter(
            empresa=self.empresa,
            identidad=identidad,
        ).exclude(pk=getattr(self.paciente, "pk", None)).exists():
            raise forms.ValidationError("Este numero de identidad ya pertenece a otro expediente.")
        return identidad

    def clean_foto_perfil(self):
        foto = self.cleaned_data.get("foto_perfil")
        if foto and foto.size > 8 * 1024 * 1024:
            raise forms.ValidationError("La fotografía no puede superar 8 MB.")
        if foto and getattr(foto, "content_type", "") not in {"image/jpeg", "image/png", "image/webp"}:
            raise forms.ValidationError("Utilice una fotografía JPG, PNG o WebP.")
        return foto

    def clean(self):
        cleaned_data = super().clean()
        nombres = (cleaned_data.get("nombres") or "").strip()
        apellidos = (cleaned_data.get("apellidos") or "").strip()
        partes_nombre = nombres.split()
        partes_apellido = apellidos.split()
        if nombres:
            cleaned_data["primer_nombre"] = partes_nombre[0]
            cleaned_data["segundo_nombre"] = " ".join(partes_nombre[1:])
        if apellidos:
            cleaned_data["primer_apellido"] = partes_apellido[0]
            cleaned_data["segundo_apellido"] = " ".join(partes_apellido[1:])

        contacto = (cleaned_data.get("contacto_emergencia_completo") or "").strip()
        if contacto:
            if " - " in contacto:
                nombre_contacto, telefono_contacto = contacto.split(" - ", 1)
            else:
                nombre_contacto, telefono_contacto = contacto, ""
            cleaned_data["contacto_emergencia"] = nombre_contacto.strip()
            cleaned_data["telefono_emergencia"] = telefono_contacto.strip()

        cleaned_data["telefono_codigo_area"] = normalize_phone_prefix(cleaned_data.get("telefono_codigo_area"))
        cleaned_data["telefono"] = apply_phone_prefix(
            cleaned_data.get("telefono"),
            cleaned_data.get("telefono_codigo_area"),
        )

        if cleaned_data.get("informante") == "yo_mismo":
            cleaned_data["informante_detalle"] = ""
        elif not (cleaned_data.get("informante_detalle") or "").strip():
            self.add_error("informante_detalle", "Indique el nombre de la persona que proporciona la informacion.")

        if cleaned_data.get("referido_por") != "referencia":
            cleaned_data["referido_por_detalle"] = ""

        funciones_organicas = cleaned_data.get("funciones_organicas") or []
        if isinstance(funciones_organicas, list):
            if len(funciones_organicas) > 1:
                self.add_error("funciones_organicas", "Seleccione solo una opcion.")
            cleaned_data["funciones_organicas"] = funciones_organicas[0] if funciones_organicas else ""
        if cleaned_data.get("funciones_organicas") != "alterada":
            cleaned_data["funciones_detalle"] = ""

        antecedentes_hospitalarios = cleaned_data.get("antecedentes_hospitalarios") or []
        if isinstance(antecedentes_hospitalarios, list):
            if len(antecedentes_hospitalarios) > 1:
                self.add_error("antecedentes_hospitalarios", "Seleccione solo una opcion.")
            tiene_hospitalarios = antecedentes_hospitalarios[:1] == ["si"]
        else:
            tiene_hospitalarios = bool(antecedentes_hospitalarios)
        cleaned_data["antecedentes_hospitalarios"] = tiene_hospitalarios
        if tiene_hospitalarios and not (cleaned_data.get("antecedentes_hospitalarios_detalle") or "").strip():
            self.add_error("antecedentes_hospitalarios_detalle", "Detalle fechas, lugar, motivo, procedimiento y si hubo complicaciones.")
        if not tiene_hospitalarios:
            cleaned_data["antecedentes_hospitalarios_detalle"] = ""

        quirurgicos_operado = cleaned_data.get("quirurgicos_operado") or []
        if isinstance(quirurgicos_operado, list) and len(quirurgicos_operado) > 1:
            self.add_error("quirurgicos_operado", "Seleccione solo una opcion.")
        if "si" in quirurgicos_operado and not (cleaned_data.get("quirurgicos_detalle") or "").strip():
            self.add_error("quirurgicos_detalle", "Detalle cirugias previas, plasticas u otras.")
        if "si" not in quirurgicos_operado:
            cleaned_data["quirurgicos_detalle"] = ""

        categorias = set(cleaned_data.get("motivo_categoria") or [])
        categorias_sin_procedimiento = {"no_aplica", "camara_hiperbarica", "enfermeria", "tratamientos"}
        categorias_con_procedimiento = {
            _codigo_grupo_procedimiento(titulo)
            for titulo, _opciones in PROCEDIMIENTOS_GENERALES_GRUPOS
        }
        if categorias and categorias.issubset(categorias_sin_procedimiento):
            cleaned_data["procedimientos_interes"] = []
            cleaned_data["procedimientos_interes_otros"] = ""
        elif categorias.intersection(categorias_con_procedimiento) and not cleaned_data.get("procedimientos_interes"):
            self.add_error("procedimientos_interes", "Seleccione al menos una opcion o marque No aplica en el motivo principal.")

        if cleaned_data.get("sexo") == "masculino":
            for campo in [
                "gine_menarca", "gine_gestas", "gine_partos", "gine_cesareas", "gine_abortos",
                "gine_ultima_menstruacion", "gine_embarazada", "gine_lactancia",
                "gine_mamografia", "gine_mamografia_fecha",
            ]:
                cleaned_data[campo] = [] if isinstance(self.fields.get(campo), forms.MultipleChoiceField) else ""
        elif cleaned_data.get("sexo") == "femenino" and "si" in (cleaned_data.get("gine_mamografia") or []) and not cleaned_data.get("gine_mamografia_fecha"):
            self.add_error("gine_mamografia_fecha", "Indique la fecha aproximada de la ultima mamografia o ultrasonido.")
        if "si" not in (cleaned_data.get("drogas_recreativas") or []):
            cleaned_data["drogas_recreativas_tipos"] = []
            cleaned_data["drogas_recreativas_detalle"] = ""
        etiquetas_dieta = dict(DIETA_CHOICES)
        etiquetas_ejercicio = dict(EJERCICIO_CHOICES)
        dieta_valores = cleaned_data.get("dieta") or []
        ejercicio_valores = cleaned_data.get("ejercicio") or []
        cleaned_data["dieta"] = ", ".join(etiquetas_dieta.get(valor, valor) for valor in dieta_valores)
        cleaned_data["ejercicio"] = ", ".join(etiquetas_ejercicio.get(valor, valor) for valor in ejercicio_valores)
        categorias_cirugia = {
            "cirugia_facial", "cirugia_mamaria", "contorno_corporal", "cirugia_intima_femenina"
        }
        if categorias.isdisjoint(categorias_cirugia):
            cleaned_data["decision_cirugia"] = []
            cleaned_data["decision_cirugia_otros"] = ""
        elif not cleaned_data.get("decision_cirugia"):
            self.add_error("decision_cirugia", "Indique quien tomo la decision de operarse.")
        return cleaned_data

    @property
    def procedimientos_interes_grupos(self):
        valores = self["procedimientos_interes"].value() or []
        seleccionados = {str(valor) for valor in valores}

        def codigo_grupo(titulo):
            if "Tratamiento Estetico" in titulo:
                return "medicina_estetica"
            return (
                titulo.lower()
                .replace(" ", "_")
                .replace("/", "")
                .replace("í", "i")
                .replace("é", "e")
                .replace("á", "a")
                .replace("ó", "o")
                .replace("ú", "u")
            )

        return [
            {
                "titulo": titulo,
                "codigo": _codigo_grupo_procedimiento(titulo),
                "opciones": [
                    {
                        "value": valor,
                        "label": etiqueta,
                        "selected": str(valor) in seleccionados,
                    }
                    for valor, etiqueta in [
                        *opciones,
                        (f"no_aplica_{_codigo_grupo_procedimiento(titulo)}", "No aplica / solo deseo valoracion"),
                    ]
                ],
            }
            for titulo, opciones in PROCEDIMIENTOS_GENERALES_GRUPOS
        ]
    def datos_generales_limpios(self):
        campos = [
            "nombres", "apellidos", "primer_nombre", "segundo_nombre", "primer_apellido", "segundo_apellido",
            "identidad", "fecha_nacimiento", "sexo", "estado_civil", "correo", "telefono_codigo_area", "telefono",
            "direccion", "lugar_nacimiento", "ocupacion", "lugar_trabajo",
            "informante", "informante_detalle", "contacto_emergencia_completo", "contacto_emergencia", "telefono_emergencia",
            "referido_por", "referido_por_detalle",
        ]
        datos = {campo: self.cleaned_data.get(campo) for campo in campos}
        if datos.get("fecha_nacimiento"):
            datos["fecha_nacimiento"] = datos["fecha_nacimiento"].isoformat()
        campos_generales = [
            "motivo_categoria", "procedimientos_interes", "procedimientos_interes_otros", "historia_mejorar",
            "historia_tiempo_preocupacion", "historia_tratamientos_previos", "historia_expectativas",
            "alergias_seleccion", "alergias_otras", "medicamentos_actuales_seleccion",
            "medicamentos_actuales_otros", "quirurgicos_operado", "quirurgicos_detalle",
            "tabaco_frecuencia", "alcohol_frecuencia", "drogas_recreativas", "drogas_recreativas_tipos", "drogas_recreativas_detalle",
            "consumo_riesgo", "consumo_riesgo_detalle", "riesgo_tromboembolico", "riesgo_tromboembolico_otros", "gine_menarca", "gine_gestas",
            "gine_partos", "gine_cesareas", "gine_abortos", "gine_ultima_menstruacion",
            "gine_embarazada", "gine_lactancia", "gine_mamografia", "gine_mamografia_fecha",
            "decision_cirugia", "decision_cirugia_otros", "expectativas_realistas", "busca_perfeccion",
            "multiples_cirugias_insatisfaccion", "evaluacion_psicologica", "evaluacion_psicologica_detalle",
            "examen_peso", "examen_talla", "examen_imc",
            "examen_pa", "examen_fc", "examen_sato2",
        ]
        datos["formulario_general"] = {
            campo: self.cleaned_data.get(campo)
            for campo in campos_generales
            if self.cleaned_data.get(campo) not in (None, "", [])
        }
        for campo in ["gine_mamografia_fecha"]:
            if datos["formulario_general"].get(campo):
                datos["formulario_general"][campo] = datos["formulario_general"][campo].isoformat()
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
