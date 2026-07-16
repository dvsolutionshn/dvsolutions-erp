import unicodedata

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from core.models import Empresa
from facturacion.models import Cliente, Producto

EMPRESAS_IDENTIDAD_PACIENTE_OBLIGATORIA = frozenset({"hospital_mia", "medical_spa"})


def _normalizar_texto(valor):
    texto = unicodedata.normalize("NFKD", str(valor or "")).encode("ascii", "ignore").decode("ascii")
    return texto.lower().strip()


class ConfiguracionClinica(models.Model):
    empresa = models.OneToOneField(Empresa, on_delete=models.CASCADE, related_name="configuracion_clinica")
    nombre_comercial = models.CharField(max_length=180, default="Unidad Clinica")
    especialidad_principal = models.CharField(max_length=140, default="Cirugia plastica y medicina estetica")
    requiere_consentimiento_procedimientos = models.BooleanField(default=True)
    alertas_postoperatorias_activas = models.BooleanField(default=True)
    dias_alerta_cumpleanos = models.PositiveIntegerField(default=7)
    notas = models.TextField(blank=True, null=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Configuracion clinica"
        verbose_name_plural = "Configuraciones clinicas"

    def __str__(self):
        return f"Clinica - {self.empresa.nombre}"


class ProfesionalSalud(models.Model):
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="profesionales_salud")
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    nombre = models.CharField(max_length=160)
    especialidad = models.CharField(max_length=140, blank=True, null=True)
    colegiacion = models.CharField(max_length=80, blank=True, null=True)
    telefono = models.CharField(max_length=30, blank=True, null=True)
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ["nombre"]
        verbose_name = "Profesional de salud"
        verbose_name_plural = "Profesionales de salud"

    def __str__(self):
        return self.nombre


PROFESIONALES_AGENDA_BASE = [
    ("Licenciada en enfermeria", "Enfermeria"),
    ("Enfermera", "Enfermeria"),
]


def asegurar_profesionales_agenda_base(empresa):
    if not empresa or empresa.slug not in {"hospital_mia", "medical_spa", "luque_aestetic"}:
        return
    for nombre, especialidad in PROFESIONALES_AGENDA_BASE:
        ProfesionalSalud.objects.get_or_create(
            empresa=empresa,
            nombre=nombre,
            defaults={"especialidad": especialidad, "activo": True},
        )


class Paciente(models.Model):
    TIPO_ID_CHOICES = [
        ("cc", "CC Cedula ciudadania"),
        ("dni", "DNI"),
        ("pasaporte", "Pasaporte"),
        ("rtn", "RTN"),
        ("otro", "Otro"),
    ]
    SEXO_CHOICES = [
        ("femenino", "Femenino"),
        ("masculino", "Masculino"),
        ("otro", "Otro"),
        ("no_indicado", "No indicado"),
    ]
    ESTADO_CIVIL_CHOICES = [
        ("soltero", "Soltero/a"),
        ("casado", "Casado/a"),
        ("union_libre", "Union libre"),
        ("divorciado", "Divorciado/a"),
        ("viudo", "Viudo/a"),
        ("no_indicado", "No indicado"),
    ]
    GENERO_CHOICES = [
        ("femenino", "Femenino"),
        ("masculino", "Masculino"),
        ("no_binario", "No binario"),
        ("otro", "Otro"),
        ("no_indicado", "No indicado"),
    ]
    ZONA_RESIDENCIAL_CHOICES = [
        ("urbana", "Urbana"),
        ("rural", "Rural"),
        ("no_indicada", "No indicada"),
    ]
    RELACION_CHOICES = [
        ("madre", "Madre"),
        ("padre", "Padre"),
        ("hijo", "Hijo/a"),
        ("conyuge", "Conyuge"),
        ("familiar", "Familiar"),
        ("amigo", "Amigo/a"),
        ("tutor", "Tutor/a"),
        ("otro", "Otro"),
        ("no_indicada", "No indicada"),
    ]
    ESCOLARIDAD_CHOICES = [
        ("ninguna", "Ninguna"),
        ("primaria", "Primaria"),
        ("secundaria", "Secundaria"),
        ("tecnica", "Tecnica"),
        ("universitaria", "Universitaria"),
        ("postgrado", "Postgrado"),
        ("no_indicada", "No indicada"),
    ]
    ETNIA_CHOICES = [
        ("mestizo", "Mestizo"),
        ("garifuna", "Garifuna"),
        ("lenca", "Lenca"),
        ("miskito", "Miskito"),
        ("tolupan", "Tolupan"),
        ("chorti", "Chorti"),
        ("pech", "Pech"),
        ("tawahka", "Tawahka"),
        ("nahua", "Nahua"),
        ("otra", "Otra"),
        ("no_indicada", "No indicada"),
    ]

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="pacientes")
    cliente = models.ForeignKey(Cliente, on_delete=models.SET_NULL, null=True, blank=True, related_name="pacientes_clinicos")
    expediente_codigo = models.CharField(max_length=40)
    tipo_id = models.CharField(max_length=20, choices=TIPO_ID_CHOICES, default="cc")
    identidad = models.CharField(max_length=30, blank=True, null=True)
    nombre = models.CharField(max_length=160)
    primer_nombre = models.CharField(max_length=80, blank=True, null=True)
    segundo_nombre = models.CharField(max_length=80, blank=True, null=True)
    primer_apellido = models.CharField(max_length=80, blank=True, null=True)
    segundo_apellido = models.CharField(max_length=80, blank=True, null=True)
    fecha_nacimiento = models.DateField(blank=True, null=True)
    sexo = models.CharField(max_length=20, choices=SEXO_CHOICES, default="no_indicado")
    genero = models.CharField(max_length=20, choices=GENERO_CHOICES, default="no_indicado")
    estado_civil = models.CharField(max_length=20, choices=ESTADO_CIVIL_CHOICES, default="no_indicado")
    rh = models.CharField(max_length=6, blank=True, null=True)
    foto_perfil = models.ImageField(upload_to="clinica/pacientes/perfil/", blank=True, null=True)
    telefono = models.CharField(max_length=30, blank=True, null=True)
    prefijo_telefono = models.CharField(max_length=20, default="Honduras (+504)")
    whatsapp = models.CharField(max_length=30, blank=True, null=True)
    celular_2 = models.CharField(max_length=30, blank=True, null=True)
    correo = models.EmailField(blank=True, null=True)
    recibir_email = models.BooleanField(default=False)
    extranjero = models.BooleanField(default=False)
    direccion = models.TextField(blank=True, null=True)
    departamento = models.CharField(max_length=120, blank=True, null=True)
    municipio = models.CharField(max_length=120, blank=True, null=True)
    zona_residencial = models.CharField(max_length=20, choices=ZONA_RESIDENCIAL_CHOICES, default="urbana")
    codigo_postal = models.CharField(max_length=20, blank=True, null=True)
    barrio = models.CharField(max_length=120, blank=True, null=True)
    pais = models.CharField(max_length=120, default="Honduras")
    lugar_nacimiento = models.CharField(max_length=160, blank=True, null=True)
    ocupacion = models.CharField(max_length=160, blank=True, null=True)
    otra_ocupacion = models.CharField(max_length=160, blank=True, null=True)
    comentarios = models.TextField(blank=True, null=True)
    acompanante_nombre = models.CharField(max_length=180, blank=True, null=True)
    acompanante_relacion = models.CharField(max_length=20, choices=RELACION_CHOICES, default="no_indicada")
    acompanante_telefono = models.CharField(max_length=30, blank=True, null=True)
    acompanante_celular = models.CharField(max_length=30, blank=True, null=True)
    acompanante_email = models.EmailField(blank=True, null=True)
    responsable_nombre = models.CharField(max_length=180, blank=True, null=True)
    responsable_telefono = models.CharField(max_length=30, blank=True, null=True)
    responsable_relacion = models.CharField(max_length=20, choices=RELACION_CHOICES, default="no_indicada")
    escolaridad = models.CharField(max_length=20, choices=ESCOLARIDAD_CHOICES, default="no_indicada")
    pertenencia_etnica = models.CharField(max_length=20, choices=ETNIA_CHOICES, default="no_indicada")
    procedencia = models.CharField(max_length=160, blank=True, null=True)
    nacionalidad = models.CharField(max_length=120, default="Honduras")
    contacto_emergencia = models.CharField(max_length=180, blank=True, null=True)
    telefono_emergencia = models.CharField(max_length=30, blank=True, null=True)
    es_alergico = models.BooleanField(default=False)
    alergias = models.TextField(blank=True, null=True)
    antecedentes_medicos = models.TextField(blank=True, null=True)
    medicamentos_actuales = models.TextField(blank=True, null=True)
    notas_privadas = models.TextField(blank=True, null=True)
    acepta_promociones = models.BooleanField(default=False)
    activo = models.BooleanField(default=True)
    creado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="pacientes_creados")
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("empresa", "expediente_codigo")
        ordering = ["nombre"]
        verbose_name = "Paciente"
        verbose_name_plural = "Pacientes"

    def __str__(self):
        return f"{self.expediente_codigo} - {self.nombre}"

    def clean(self):
        super().clean()
        if (
            self._state.adding
            and self.empresa_id
            and self.empresa.slug in EMPRESAS_IDENTIDAD_PACIENTE_OBLIGATORIA
            and not (self.identidad or "").strip()
        ):
            raise ValidationError({
                "identidad": "La identidad es obligatoria para crear pacientes en esta empresa."
            })

    def save(self, *args, **kwargs):
        partes_nombre = [
            self.primer_nombre,
            self.segundo_nombre,
            self.primer_apellido,
            self.segundo_apellido,
        ]
        nombre_compuesto = " ".join(parte.strip() for parte in partes_nombre if parte and parte.strip())
        if nombre_compuesto:
            self.nombre = nombre_compuesto
        super().save(*args, **kwargs)

    @property
    def edad(self):
        if not self.fecha_nacimiento:
            return None
        hoy = timezone.localdate()
        return hoy.year - self.fecha_nacimiento.year - (
            (hoy.month, hoy.day) < (self.fecha_nacimiento.month, self.fecha_nacimiento.day)
        )


class PacienteFotoEvolucion(models.Model):
    TIPO_CHOICES = [
        ("ingreso", "Ingreso"),
        ("preoperatorio", "Preoperatorio"),
        ("procedimiento", "Procedimiento"),
        ("postoperatorio", "Postoperatorio"),
        ("control", "Control"),
        ("evolucion", "Evolucion"),
        ("otro", "Otro"),
    ]

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="fotos_evolucion_pacientes")
    paciente = models.ForeignKey(Paciente, on_delete=models.CASCADE, related_name="fotos_evolucion")
    imagen = models.ImageField(upload_to="clinica/pacientes/evolucion/", blank=True, null=True)
    video = models.FileField(upload_to="clinica/pacientes/evolucion/videos/", blank=True, null=True)
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES, default="ingreso")
    titulo = models.CharField(max_length=160, default="Foto de ingreso")
    descripcion = models.TextField(blank=True, null=True)
    fecha = models.DateTimeField(default=timezone.now)
    creado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-fecha", "-id"]
        verbose_name = "Foto de evolucion del paciente"
        verbose_name_plural = "Fotos de evolucion del paciente"

    def __str__(self):
        return f"{self.paciente.nombre} - {self.titulo}"


class ServicioClinico(models.Model):
    CATEGORIA_CHOICES = [
        ("consulta", "Consulta"),
        ("procedimiento", "Procedimiento"),
        ("cirugia", "Cirugia"),
        ("tratamiento", "Tratamiento"),
        ("spa", "Faciales, masajes, hidrataciones, tratamientos esteticos no medicos"),
        ("control", "Control"),
        ("laboratorio", "Laboratorio"),
        ("imagen", "Imagen"),
    ]

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="servicios_clinicos")
    nombre = models.CharField(max_length=180)
    categoria = models.CharField(max_length=30, choices=CATEGORIA_CHOICES, default="consulta")
    duracion_minutos = models.PositiveIntegerField(default=60)
    precio_referencia = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    requiere_consentimiento = models.BooleanField(default=False)
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ["categoria", "nombre"]
        verbose_name = "Servicio clinico"
        verbose_name_plural = "Servicios clinicos"

    def __str__(self):
        return self.nombre


class CitaClinica(models.Model):
    ESTADO_CHOICES = [
        ("solicitada", "Solicitada"),
        ("confirmada", "Confirmada"),
        ("en_atencion", "En atencion"),
        ("completada", "Completada"),
        ("cancelada", "Cancelada"),
        ("no_asistio", "No asistio"),
    ]
    CANAL_CHOICES = [
        ("recepcion", "Recepcion"),
        ("whatsapp", "WhatsApp"),
        ("instagram", "Instagram"),
        ("telefono", "Telefono"),
        ("referido", "Referido"),
        ("otro", "Otro"),
    ]

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="citas_clinicas")
    paciente = models.ForeignKey(Paciente, on_delete=models.CASCADE, related_name="citas")
    profesional = models.ForeignKey(ProfesionalSalud, on_delete=models.SET_NULL, null=True, blank=True, related_name="citas_clinicas")
    servicio = models.ForeignKey(ServicioClinico, on_delete=models.SET_NULL, null=True, blank=True, related_name="citas")
    fecha_hora = models.DateTimeField()
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default="solicitada")
    canal = models.CharField(max_length=20, choices=CANAL_CHOICES, default="recepcion")
    motivo = models.CharField(max_length=220)
    pagada = models.BooleanField(default=False)
    es_recordatorio_tratamiento = models.BooleanField(default=False)
    tratamiento_recordatorio = models.CharField(max_length=180, blank=True, null=True)
    sala = models.CharField(max_length=80, blank=True, null=True)
    observaciones = models.TextField(blank=True, null=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["fecha_hora"]
        verbose_name = "Cita clinica"
        verbose_name_plural = "Citas clinicas"

    def __str__(self):
        return f"{self.paciente.nombre} - {self.fecha_hora:%d/%m/%Y %H:%M}"

    @property
    def agenda_color(self):
        if self.es_recordatorio_tratamiento:
            return "recordatorio"
        servicio = _normalizar_texto(self.servicio.nombre if self.servicio_id else self.motivo)
        categoria = _normalizar_texto(self.servicio.categoria if self.servicio_id else "")
        if "terapia" in servicio or "camara" in servicio or "hiperbar" in servicio:
            return "terapias"
        if categoria == "consulta" or "consulta" in servicio or "evaluacion" in servicio or "valoracion" in servicio:
            return "consulta"
        if categoria == "spa" or any(
            palabra in servicio
            for palabra in ["facial", "masaje", "hidratacion", "spa", "estetico no medico"]
        ):
            return "spa"
        if categoria == "cirugia" or "cirug" in servicio:
            return "cirugias"
        if categoria in {"tratamiento", "procedimiento"} or "tratamiento" in servicio:
            return "tratamientos"
        if categoria == "control" or "control" in servicio or "seguimiento" in servicio:
            return "control"
        if categoria == "laboratorio" or "laboratorio" in servicio or "lab" in servicio:
            return "laboratorio"
        if categoria == "imagen" or "ultrasonido" in servicio or "imagen" in servicio:
            return "imagen"
        return "general"

    @property
    def agenda_color_label(self):
        etiquetas = {
            "consulta": "Consulta",
            "terapias": "Terapias / camaras hiperbaricas",
            "tratamientos": "Tratamientos",
            "cirugias": "Cirugias",
            "spa": "Spa",
            "control": "Control / seguimiento",
            "laboratorio": "Laboratorio",
            "imagen": "Imagen",
            "general": "General",
            "recordatorio": "Recordatorio de tratamiento",
        }
        return etiquetas.get(self.agenda_color, "General")

    @property
    def agenda_profesional_color(self):
        profesional = self.profesional
        combinado = _normalizar_texto(
            f"{profesional.nombre if profesional else ''} {profesional.especialidad if profesional else ''}"
        )
        if "luis" in combinado:
            return "doctor-luis"
        if "candy" in combinado or "luque" in combinado:
            return "dra-candy"
        if "licenciada" in combinado and "enfermer" in combinado:
            return "lic-enfermeria"
        if "enfermer" in combinado:
            return "enfermera"
        if "doctor" in combinado or "dr " in combinado or "dra " in combinado:
            return "medico"
        return "profesional"

    @property
    def agenda_profesional_color_label(self):
        etiquetas = {
            "doctor-luis": "Dr Luis",
            "dra-candy": "Dra Candy",
            "lic-enfermeria": "Licenciada en enfermeria",
            "enfermera": "Enfermera",
            "medico": "Medico",
            "profesional": "Profesional",
        }
        return etiquetas.get(self.agenda_profesional_color, "Profesional")


class TratamientoPaciente(models.Model):
    ESTADO_CHOICES = [
        ("planificado", "Planificado"),
        ("en_proceso", "En proceso"),
        ("completado", "Completado"),
        ("pausado", "Pausado"),
        ("cancelado", "Cancelado"),
    ]

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="tratamientos_pacientes")
    paciente = models.ForeignKey(Paciente, on_delete=models.CASCADE, related_name="tratamientos")
    servicio = models.ForeignKey(ServicioClinico, on_delete=models.SET_NULL, null=True, blank=True)
    profesional = models.ForeignKey(ProfesionalSalud, on_delete=models.SET_NULL, null=True, blank=True)
    nombre = models.CharField(max_length=180)
    fecha_inicio = models.DateField(default=timezone.localdate)
    fecha_fin_estimada = models.DateField(blank=True, null=True)
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default="planificado")
    objetivo = models.TextField(blank=True, null=True)
    plan_clinico = models.TextField(blank=True, null=True)
    monto_estimado = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    class Meta:
        ordering = ["-fecha_inicio", "nombre"]
        verbose_name = "Tratamiento de paciente"
        verbose_name_plural = "Tratamientos de pacientes"

    def __str__(self):
        return f"{self.paciente.nombre} - {self.nombre}"


class ExpedienteEvento(models.Model):
    TIPO_CHOICES = [
        ("consulta", "Consulta"),
        ("evaluacion", "Evaluacion"),
        ("evolucion", "Evolucion"),
        ("tratamiento", "Tratamiento"),
        ("procedimiento", "Procedimiento"),
        ("medicamento", "Medicamento"),
        ("seguimiento", "Seguimiento"),
        ("nota", "Nota interna"),
    ]

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="eventos_expediente")
    paciente = models.ForeignKey(Paciente, on_delete=models.CASCADE, related_name="eventos_expediente")
    cita = models.ForeignKey(CitaClinica, on_delete=models.SET_NULL, null=True, blank=True, related_name="eventos_expediente")
    tratamiento = models.ForeignKey(TratamientoPaciente, on_delete=models.SET_NULL, null=True, blank=True, related_name="eventos_expediente")
    profesional = models.ForeignKey(ProfesionalSalud, on_delete=models.SET_NULL, null=True, blank=True)
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES, default="consulta")
    fecha = models.DateTimeField(default=timezone.now)
    titulo = models.CharField(max_length=180)
    descripcion = models.TextField()
    diagnostico = models.TextField(blank=True, null=True)
    plan = models.TextField(blank=True, null=True)
    signos_vitales = models.TextField(blank=True, null=True)
    creado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ["-fecha"]
        verbose_name = "Evento de expediente"
        verbose_name_plural = "Eventos de expediente"

    def __str__(self):
        return f"{self.paciente.nombre} - {self.titulo}"


class HistoriaClinicaEspecialidad(models.Model):
    TIPO_CHOICES = [
        ("capilar", "Capilar"),
        ("cirugia_plastica", "Cirugia plastica y reconstructiva"),
        ("medicina_estetica", "Tratamiento Estetico / Piel"),
        ("enfermeria", "Enfermeria"),
        ("terapias", "Terapias"),
        ("camara_hiperbarica", "Camara hiperbarica"),
    ]
    ESTADO_CHOICES = [
        ("borrador", "Borrador"),
        ("finalizada", "Finalizada"),
    ]

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="historias_clinicas_especialidad")
    paciente = models.ForeignKey(Paciente, on_delete=models.CASCADE, related_name="historias_especialidad")
    profesional = models.ForeignKey(
        ProfesionalSalud,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="historias_clinicas_especialidad",
    )
    tipo = models.CharField(max_length=30, choices=TIPO_CHOICES)
    fecha_atencion = models.DateTimeField(default=timezone.now)
    motivo_consulta = models.TextField(blank=True)
    antecedentes = models.TextField(blank=True)
    historia_enfermedad_actual = models.TextField(blank=True)
    signos_vitales = models.TextField(blank=True)
    examen_fisico = models.TextField(blank=True)
    evaluacion_clinica = models.TextField(blank=True)
    diagnostico = models.TextField(blank=True)
    analisis_clinico = models.TextField(blank=True)
    procedimiento = models.TextField(blank=True)
    conducta = models.TextField(blank=True)
    plan_tratamiento = models.TextField(blank=True)
    indicaciones = models.TextField(blank=True)
    observaciones = models.TextField(blank=True)
    notas_privadas_doctor = models.TextField(blank=True)
    datos_especialidad = models.JSONField(default=dict, blank=True)
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default="borrador")
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="historias_clinicas_creadas",
    )
    actualizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="historias_clinicas_actualizadas",
    )
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-fecha_atencion", "-id"]
        indexes = [
            models.Index(fields=["empresa", "paciente", "tipo"]),
            models.Index(fields=["empresa", "fecha_atencion"]),
        ]
        verbose_name = "Historia clinica por especialidad"
        verbose_name_plural = "Historias clinicas por especialidad"

    def __str__(self):
        return f"{self.paciente.nombre} - {self.get_tipo_display()} - {self.fecha_atencion:%d/%m/%Y}"

    @property
    def bloqueada(self):
        return self.tipo == "enfermeria" and self.estado == "finalizada"

    def clean(self):
        super().clean()
        if not self.pk:
            return
        original = HistoriaClinicaEspecialidad.objects.filter(pk=self.pk).values("tipo", "estado").first()
        if original and original["tipo"] == "enfermeria" and original["estado"] == "finalizada":
            raise ValidationError("La nota de enfermeria finalizada esta bloqueada y no puede modificarse.")

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class PreconsultaClinica(models.Model):
    TIPO_CHOICES = [
        ("general", "General"),
        *HistoriaClinicaEspecialidad.TIPO_CHOICES,
    ]
    ESTADO_CHOICES = [
        ("pendiente", "Pendiente"),
        ("completada", "Completada"),
        ("revocada", "Revocada"),
    ]
    REVISION_CHOICES = [
        ("no_aplica", "No aplica"),
        ("normal", "Normal"),
        ("alterada", "Alterada"),
    ]

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="preconsultas_clinicas")
    paciente = models.ForeignKey(Paciente, on_delete=models.CASCADE, related_name="preconsultas")
    tipo = models.CharField(max_length=30, choices=TIPO_CHOICES, default="general")
    token_hash = models.CharField(max_length=64, unique=True, db_index=True)
    token_preview = models.CharField(max_length=16)
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default="pendiente")
    fecha_expiracion = models.DateTimeField()
    fecha_completada = models.DateTimeField(blank=True, null=True)
    datos_generales = models.JSONField(default=dict, blank=True)
    motivo_consulta = models.TextField(blank=True)
    funciones_organicas = models.CharField(max_length=20, choices=REVISION_CHOICES, blank=True)
    funciones_detalle = models.TextField(blank=True)
    revision_sistemas = models.CharField(max_length=20, choices=REVISION_CHOICES, blank=True)
    revision_sistemas_detalle = models.TextField(blank=True)
    antecedentes_hospitalarios = models.BooleanField(default=False)
    antecedentes_hospitalarios_detalle = models.TextField(blank=True)
    antecedentes_personales = models.JSONField(default=list, blank=True)
    antecedentes_personales_detalle = models.TextField(blank=True)
    medicamentos_habituales = models.JSONField(default=list, blank=True)
    medicamentos_habituales_detalle = models.TextField(blank=True)
    antecedentes_familiares = models.JSONField(default=list, blank=True)
    antecedentes_familiares_detalle = models.TextField(blank=True)
    dieta = models.TextField(blank=True)
    ejercicio = models.TextField(blank=True)
    habitos = models.TextField(blank=True)
    alergias = models.TextField(blank=True)
    antecedentes_infecciosos = models.TextField(blank=True)
    consentimiento_datos = models.BooleanField(default=False)
    ip_completada = models.GenericIPAddressField(blank=True, null=True)
    creada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="preconsultas_clinicas_creadas",
    )
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-fecha_creacion", "-id"]
        indexes = [
            models.Index(fields=["empresa", "paciente", "estado"]),
            models.Index(fields=["empresa", "paciente", "tipo"]),
            models.Index(fields=["empresa", "fecha_creacion"]),
        ]
        verbose_name = "Preconsulta clinica"
        verbose_name_plural = "Preconsultas clinicas"

    @property
    def vigente(self):
        return self.estado == "pendiente" and self.fecha_expiracion > timezone.now()

    def __str__(self):
        return f"Preconsulta {self.get_tipo_display()} - {self.paciente.nombre} - {self.get_estado_display()}"


class InvitacionRegistroPaciente(models.Model):
    ESTADO_CHOICES = [
        ("pendiente", "Pendiente"),
        ("completada", "Completada"),
        ("revocada", "Revocada"),
    ]

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="invitaciones_registro_paciente")
    token_hash = models.CharField(max_length=64, unique=True, db_index=True)
    token_preview = models.CharField(max_length=16)
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default="pendiente")
    fecha_expiracion = models.DateTimeField()
    fecha_completada = models.DateTimeField(blank=True, null=True)
    paciente = models.ForeignKey(
        Paciente,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invitaciones_registro",
    )
    preconsulta = models.OneToOneField(
        PreconsultaClinica,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invitacion_registro",
    )
    creada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invitaciones_registro_paciente_creadas",
    )
    ip_completada = models.GenericIPAddressField(blank=True, null=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-fecha_creacion", "-id"]
        indexes = [
            models.Index(fields=["empresa", "estado", "fecha_expiracion"]),
        ]

    @property
    def vigente(self):
        return self.estado == "pendiente" and self.fecha_expiracion > timezone.now()

    def __str__(self):
        return f"Registro nuevo paciente {self.token_preview} - {self.get_estado_display()}"


class MedicamentoPrescrito(models.Model):
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="medicamentos_prescritos")
    paciente = models.ForeignKey(Paciente, on_delete=models.CASCADE, related_name="medicamentos_prescritos")
    evento = models.ForeignKey(ExpedienteEvento, on_delete=models.SET_NULL, null=True, blank=True, related_name="medicamentos")
    tratamiento = models.ForeignKey(TratamientoPaciente, on_delete=models.SET_NULL, null=True, blank=True, related_name="medicamentos")
    medicamento = models.CharField(max_length=180)
    dosis = models.CharField(max_length=120)
    frecuencia = models.CharField(max_length=120)
    duracion = models.CharField(max_length=120, blank=True, null=True)
    indicaciones = models.TextField(blank=True, null=True)
    activo = models.BooleanField(default=True)
    fecha_prescripcion = models.DateField(default=timezone.localdate)

    class Meta:
        ordering = ["-fecha_prescripcion", "medicamento"]
        verbose_name = "Medicamento prescrito"
        verbose_name_plural = "Medicamentos prescritos"

    def __str__(self):
        return f"{self.medicamento} - {self.paciente.nombre}"


class ConsentimientoClinico(models.Model):
    ESTADO_CHOICES = [
        ("pendiente", "Pendiente"),
        ("firmado", "Firmado"),
        ("revocado", "Revocado"),
    ]

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="consentimientos_clinicos")
    paciente = models.ForeignKey(Paciente, on_delete=models.CASCADE, related_name="consentimientos")
    tratamiento = models.ForeignKey(TratamientoPaciente, on_delete=models.SET_NULL, null=True, blank=True, related_name="consentimientos")
    cita = models.ForeignKey(CitaClinica, on_delete=models.SET_NULL, null=True, blank=True, related_name="consentimientos")
    titulo = models.CharField(max_length=180)
    version = models.CharField(max_length=40, default="1.0")
    contenido = models.TextField()
    firmado_por = models.CharField(max_length=160, blank=True, null=True)
    fecha_firma = models.DateTimeField(blank=True, null=True)
    archivo = models.FileField(upload_to="clinica/consentimientos/", blank=True, null=True)
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default="pendiente")
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-fecha_creacion"]
        verbose_name = "Consentimiento clinico"
        verbose_name_plural = "Consentimientos clinicos"

    def __str__(self):
        return f"{self.titulo} - {self.paciente.nombre}"


class ExamenPaciente(models.Model):
    TIPO_CHOICES = [
        ("laboratorio", "Laboratorio"),
        ("imagen", "Imagen / radiologia"),
        ("preoperatorio", "Preoperatorio"),
        ("otro", "Otro"),
    ]

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="examenes_pacientes")
    paciente = models.ForeignKey(Paciente, on_delete=models.CASCADE, related_name="examenes")
    titulo = models.CharField(max_length=180)
    tipo = models.CharField(max_length=30, choices=TIPO_CHOICES, default="laboratorio")
    fecha_examen = models.DateField(default=timezone.localdate)
    laboratorio = models.CharField(max_length=180, blank=True, null=True)
    descripcion = models.TextField(blank=True, null=True)
    archivo = models.FileField(upload_to="clinica/examenes/")
    subido_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-fecha_examen", "-fecha_creacion"]
        verbose_name = "Examen de paciente"
        verbose_name_plural = "Examenes de pacientes"

    def __str__(self):
        return f"{self.titulo} - {self.paciente.nombre}"

    @property
    def es_imagen(self):
        nombre = (self.archivo.name or "").lower()
        return nombre.endswith((".jpg", ".jpeg", ".png", ".webp"))


class RecetaMedica(models.Model):
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="recetas_medicas")
    paciente = models.ForeignKey(Paciente, on_delete=models.CASCADE, related_name="recetas")
    fecha = models.DateField(default=timezone.localdate)
    diagnostico = models.CharField(max_length=240, blank=True, null=True)
    indicaciones = models.TextField(help_text="Detalle libre de medicamentos, dosis, frecuencia, duracion e indicaciones.")
    productos = models.ManyToManyField(Producto, blank=True, related_name="recetas_clinicas")
    profesional = models.ForeignKey(ProfesionalSalud, on_delete=models.SET_NULL, null=True, blank=True, related_name="recetas_emitidas")
    observaciones = models.TextField(blank=True, null=True)
    creada_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-fecha", "-fecha_creacion"]
        verbose_name = "Receta medica"
        verbose_name_plural = "Recetas medicas"

    def __str__(self):
        return f"Receta {self.fecha:%d/%m/%Y} - {self.paciente.nombre}"


class SeguimientoPostOperatorio(models.Model):
    ESTADO_CHOICES = [
        ("pendiente", "Pendiente"),
        ("contactado", "Contactado"),
        ("requiere_revision", "Requiere revision"),
        ("cerrado", "Cerrado"),
    ]

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="seguimientos_postoperatorios")
    paciente = models.ForeignKey(Paciente, on_delete=models.CASCADE, related_name="seguimientos_postoperatorios")
    tratamiento = models.ForeignKey(TratamientoPaciente, on_delete=models.CASCADE, related_name="seguimientos_postoperatorios")
    fecha_programada = models.DateField()
    tipo = models.CharField(max_length=120, default="Control postoperatorio")
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default="pendiente")
    notas = models.TextField(blank=True, null=True)
    signos_alarma = models.TextField(blank=True, null=True)
    proxima_revision = models.DateField(blank=True, null=True)

    class Meta:
        ordering = ["fecha_programada"]
        verbose_name = "Seguimiento postoperatorio"
        verbose_name_plural = "Seguimientos postoperatorios"

    def __str__(self):
        return f"{self.paciente.nombre} - {self.fecha_programada:%d/%m/%Y}"
