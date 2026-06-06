from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import Empresa
from facturacion.models import Cliente


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


class Paciente(models.Model):
    SEXO_CHOICES = [
        ("femenino", "Femenino"),
        ("masculino", "Masculino"),
        ("otro", "Otro"),
        ("no_indicado", "No indicado"),
    ]

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="pacientes")
    cliente = models.ForeignKey(Cliente, on_delete=models.SET_NULL, null=True, blank=True, related_name="pacientes_clinicos")
    expediente_codigo = models.CharField(max_length=40)
    identidad = models.CharField(max_length=30, blank=True, null=True)
    nombre = models.CharField(max_length=160)
    fecha_nacimiento = models.DateField(blank=True, null=True)
    sexo = models.CharField(max_length=20, choices=SEXO_CHOICES, default="no_indicado")
    telefono = models.CharField(max_length=30, blank=True, null=True)
    whatsapp = models.CharField(max_length=30, blank=True, null=True)
    correo = models.EmailField(blank=True, null=True)
    direccion = models.TextField(blank=True, null=True)
    contacto_emergencia = models.CharField(max_length=180, blank=True, null=True)
    telefono_emergencia = models.CharField(max_length=30, blank=True, null=True)
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

    @property
    def edad(self):
        if not self.fecha_nacimiento:
            return None
        hoy = timezone.localdate()
        return hoy.year - self.fecha_nacimiento.year - (
            (hoy.month, hoy.day) < (self.fecha_nacimiento.month, self.fecha_nacimiento.day)
        )


class ServicioClinico(models.Model):
    CATEGORIA_CHOICES = [
        ("consulta", "Consulta"),
        ("procedimiento", "Procedimiento"),
        ("cirugia", "Cirugia"),
        ("tratamiento", "Tratamiento"),
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
    sala = models.CharField(max_length=80, blank=True, null=True)
    observaciones = models.TextField(blank=True, null=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["fecha_hora"]
        verbose_name = "Cita clinica"
        verbose_name_plural = "Citas clinicas"

    def __str__(self):
        return f"{self.paciente.nombre} - {self.fecha_hora:%d/%m/%Y %H:%M}"


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
