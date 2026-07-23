import unicodedata
from urllib.parse import quote

from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import Empresa
from facturacion.models import Cliente, Producto


def _normalizar_texto(valor):
    texto = unicodedata.normalize("NFKD", valor or "")
    return "".join(ch for ch in texto if not unicodedata.combining(ch)).lower()


class ConfiguracionCRM(models.Model):
    empresa = models.OneToOneField(Empresa, on_delete=models.CASCADE, related_name="configuracion_crm")
    whatsapp_activo = models.BooleanField(default=False)
    whatsapp_api_version = models.CharField(max_length=20, default="v25.0")
    whatsapp_phone_number_id = models.CharField(max_length=120, blank=True, null=True)
    whatsapp_business_account_id = models.CharField(max_length=120, blank=True, null=True)
    whatsapp_token = models.TextField(blank=True, null=True)
    whatsapp_numero_prueba = models.CharField(max_length=30, blank=True, null=True)
    whatsapp_plantilla_prueba = models.CharField(max_length=80, default="hello_world")
    whatsapp_idioma_plantilla = models.CharField(max_length=12, default="en_US")
    whatsapp_plantilla_marketing = models.CharField(max_length=80, default="promo_general_imagen")
    whatsapp_idioma_marketing = models.CharField(max_length=12, default="es")
    whatsapp_plantilla_cita = models.CharField(max_length=80, default="recordatorio_cita")
    whatsapp_idioma_cita = models.CharField(max_length=12, default="es")
    whatsapp_cita_incluir_enlace = models.BooleanField(default=False)
    mensaje_cita_confirmacion = models.TextField(default="confirmacion de cita")
    mensaje_cita_recordatorio_7_dias = models.TextField(default="recordatorio: falta una semana")
    mensaje_cita_recordatorio_1_dia = models.TextField(default="recordatorio: su cita es manana")
    mensaje_cita_cancelada = models.TextField(default="cita cancelada")
    mensaje_cita_reagendada = models.TextField(default="cita reagendada")
    whatsapp_plantilla_preconsulta = models.CharField(max_length=80, default="preconsulta_paciente")
    whatsapp_idioma_preconsulta = models.CharField(max_length=12, default="es")
    remitente_correo = models.EmailField(blank=True, null=True)
    recordatorio_cumpleanos_activo = models.BooleanField(default=True)
    cumpleanos_recordatorio_1_dia = models.BooleanField(default=True)
    cumpleanos_recordatorio_7_dias = models.BooleanField(default=False)
    recordatorio_citas_activo = models.BooleanField(default=True)
    dias_alerta_producto = models.PositiveIntegerField(default=7)

    class Meta:
        verbose_name = "Configuracion CRM"
        verbose_name_plural = "Configuraciones CRM"

    def __str__(self):
        return f"CRM - {self.empresa.nombre}"


class PlantillaMensaje(models.Model):
    CANAL_CHOICES = [
        ("whatsapp", "WhatsApp"),
        ("correo", "Correo"),
        ("ambos", "WhatsApp y correo"),
    ]
    TIPO_CHOICES = [
        ("promocion", "Promocion"),
        ("cumpleanos", "Cumpleanos"),
        ("cita", "Cita"),
        ("general", "General"),
    ]

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="plantillas_crm")
    nombre = models.CharField(max_length=150)
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES, default="general")
    canal = models.CharField(max_length=20, choices=CANAL_CHOICES, default="whatsapp")
    asunto = models.CharField(max_length=180, blank=True, null=True)
    mensaje = models.TextField(help_text="Puedes usar: {{cliente}}, {{empresa}}, {{fecha}}, {{producto}}.")
    imagen_promocional = models.ImageField(upload_to="crm/promociones/", blank=True, null=True)
    activa = models.BooleanField(default=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre

    def render(self, cliente=None, cita=None, producto=None):
        texto = self.mensaje
        replacements = {
            "{{cliente}}": cliente.nombre if cliente else "",
            "{{empresa}}": self.empresa.nombre,
            "{{fecha}}": cita.fecha_hora.strftime("%d/%m/%Y %I:%M %p") if cita else "",
            "{{producto}}": producto.nombre if producto else "",
        }
        for key, value in replacements.items():
            texto = texto.replace(key, value)
        return texto


class CampaniaMarketing(models.Model):
    ESTADO_CHOICES = [
        ("borrador", "Borrador"),
        ("programada", "Programada"),
        ("enviada", "Enviada"),
        ("cancelada", "Cancelada"),
    ]
    AUDIENCIA_CHOICES = [
        ("todos", "Todos los clientes activos"),
        ("promociones", "Clientes que aceptan promociones"),
        ("cumpleanos", "Clientes con cumpleanos proximos"),
    ]

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="campanias_marketing")
    nombre = models.CharField(max_length=180)
    plantilla = models.ForeignKey(PlantillaMensaje, on_delete=models.SET_NULL, null=True, blank=True)
    audiencia = models.CharField(max_length=20, choices=AUDIENCIA_CHOICES, default="promociones")
    fecha_programada = models.DateTimeField(blank=True, null=True)
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default="borrador")
    creado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-fecha_creacion"]

    def __str__(self):
        return self.nombre

    def clientes_objetivo(self):
        clientes = Cliente.objects.filter(empresa=self.empresa, activo=True)
        if self.audiencia == "promociones":
            clientes = clientes.filter(acepta_promociones=True)
        elif self.audiencia == "cumpleanos":
            manana = timezone.localdate() + timezone.timedelta(days=1)
            clientes = clientes.filter(fecha_nacimiento__month=manana.month, fecha_nacimiento__day=manana.day)
        return clientes.order_by("nombre")


class EnvioCampania(models.Model):
    ESTADO_CHOICES = [
        ("pendiente", "Pendiente"),
        ("preparado", "Preparado"),
        ("enviado", "Enviado"),
        ("error", "Error"),
    ]

    campania = models.ForeignKey(CampaniaMarketing, on_delete=models.CASCADE, related_name="envios")
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name="envios_crm")
    canal = models.CharField(max_length=20, default="whatsapp")
    mensaje = models.TextField()
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default="pendiente")
    respuesta = models.TextField(blank=True, null=True)
    fecha_envio = models.DateTimeField(blank=True, null=True)

    class Meta:
        unique_together = ("campania", "cliente", "canal")
        ordering = ["cliente__nombre"]

    def __str__(self):
        return f"{self.campania} - {self.cliente}"

    @property
    def whatsapp_url(self):
        telefono = "".join(ch for ch in (self.cliente.telefono_whatsapp or self.cliente.telefono or "") if ch.isdigit())
        if telefono and not telefono.startswith("504") and len(telefono) == 8:
            telefono = f"504{telefono}"
        return f"https://wa.me/{telefono}?text={quote(self.mensaje)}" if telefono else ""


class CitaCliente(models.Model):
    ESTADO_CHOICES = [
        ("pendiente", "Pendiente"),
        ("confirmada", "Confirmada"),
        ("realizada", "Realizada"),
        ("cancelada", "Cancelada"),
    ]

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="citas_clientes")
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, null=True, blank=True, related_name="citas")
    producto = models.ForeignKey(Producto, on_delete=models.SET_NULL, null=True, blank=True, related_name="citas")
    paciente = models.ForeignKey("clinica.Paciente", on_delete=models.CASCADE, null=True, blank=True, related_name="citas_agenda")
    servicio_clinico = models.ForeignKey("clinica.ServicioClinico", on_delete=models.SET_NULL, null=True, blank=True, related_name="citas_agenda")
    profesional_salud = models.ForeignKey("clinica.ProfesionalSalud", on_delete=models.SET_NULL, null=True, blank=True, related_name="citas_agenda")
    cita_clinica = models.OneToOneField("clinica.CitaClinica", on_delete=models.SET_NULL, null=True, blank=True, related_name="cita_agenda")
    titulo = models.CharField(max_length=180)
    fecha_hora = models.DateTimeField()
    duracion_minutos = models.PositiveIntegerField(default=60)
    responsable = models.CharField(max_length=120, blank=True, null=True)
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default="pendiente")
    pagada = models.BooleanField(default=False)
    observacion = models.TextField(blank=True, null=True)
    cirugia_detalle = models.TextField(blank=True, null=True)
    cirugia_fin_estimada = models.DateTimeField(blank=True, null=True)
    enviar_confirmacion_whatsapp = models.BooleanField(default=False)
    recordatorio_semana_whatsapp = models.BooleanField(default=True)
    recordatorio_dia_whatsapp = models.BooleanField(default=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["fecha_hora"]

    def __str__(self):
        return f"{self.titulo} - {self.display_cliente}"

    @property
    def display_cliente(self):
        return self.paciente.nombre if self.paciente_id else (self.cliente.nombre if self.cliente_id else "Sin paciente")

    @property
    def display_servicio(self):
        return self.servicio_clinico.nombre if self.servicio_clinico_id else (self.producto.nombre if self.producto_id else "Sin tipo de consulta")

    @property
    def display_responsable(self):
        return self.profesional_salud.nombre if self.profesional_salud_id else (self.responsable or "Sin responsable")

    @property
    def agenda_color(self):
        if self.cita_clinica_id and getattr(self.cita_clinica, "es_recordatorio_tratamiento", False):
            return "recordatorio"
        servicio = _normalizar_texto(self.display_servicio)
        categoria = _normalizar_texto(
            self.servicio_clinico.categoria if self.servicio_clinico_id else ""
        )
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
        responsable = _normalizar_texto(self.display_responsable)
        especialidad = _normalizar_texto(
            self.profesional_salud.especialidad if self.profesional_salud_id else ""
        )
        combinado = f"{responsable} {especialidad}"
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

    @property
    def whatsapp_url(self):
        if self.paciente_id:
            contacto = self.paciente.whatsapp or self.paciente.telefono or ""
        elif self.cliente_id:
            contacto = self.cliente.telefono_whatsapp or self.cliente.telefono or ""
        else:
            contacto = ""
        telefono = "".join(ch for ch in contacto if ch.isdigit())
        if telefono and not telefono.startswith("504") and len(telefono) == 8:
            telefono = f"504{telefono}"
        mensaje = (
            f"Hola {self.display_cliente}, le recordamos su cita {self.titulo} "
            f"para el {timezone.localtime(self.fecha_hora).strftime('%d/%m/%Y %I:%M %p')}."
        )
        return f"https://wa.me/{telefono}?text={quote(mensaje)}" if telefono else ""


class CitaCirugiaFoto(models.Model):
    cita = models.ForeignKey(CitaCliente, on_delete=models.CASCADE, related_name="fotos_cirugia")
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="fotos_cirugia_citas")
    imagen = models.ImageField(upload_to="crm/citas/cirugias/")
    descripcion = models.CharField(max_length=180, blank=True, null=True)
    creado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-fecha_creacion", "-id"]
        verbose_name = "Foto de cirugia agendada"
        verbose_name_plural = "Fotos de cirugias agendadas"

    def __str__(self):
        return f"{self.cita.display_cliente} - foto cirugia"


class NotificacionCitaWhatsApp(models.Model):
    TIPO_CONFIRMACION = "confirmacion"
    TIPO_SEMANA = "semana"
    TIPO_DIA = "dia"
    TIPO_CHOICES = [
        (TIPO_CONFIRMACION, "Confirmación al crear"),
        (TIPO_SEMANA, "Recordatorio 7 días antes"),
        (TIPO_DIA, "Recordatorio 1 día antes"),
    ]
    ESTADO_CHOICES = [
        ("pendiente", "Pendiente"), ("enviado", "Enviado"),
        ("error", "Error"), ("omitido", "Omitido"),
    ]

    cita = models.ForeignKey(CitaCliente, on_delete=models.CASCADE, related_name="notificaciones_whatsapp")
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    programada_para = models.DateTimeField(db_index=True)
    estado = models.CharField(max_length=15, choices=ESTADO_CHOICES, default="pendiente", db_index=True)
    intentos = models.PositiveIntegerField(default=0)
    ultimo_error = models.TextField(blank=True)
    respuesta = models.JSONField(default=dict, blank=True)
    enviada_en = models.DateTimeField(null=True, blank=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["programada_para", "id"]
        constraints = [
            models.UniqueConstraint(fields=["cita", "tipo"], name="unique_notificacion_tipo_por_cita")
        ]

    def __str__(self):
        return f"{self.cita} · {self.get_tipo_display()}"

class NotificacionCumpleanosWhatsApp(models.Model):
    ESTADO_CHOICES = [
        ("pendiente", "Pendiente"), ("enviado", "Enviado"),
        ("error", "Error"), ("omitido", "Omitido"),
    ]

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="notificaciones_cumpleanos_whatsapp")
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name="notificaciones_cumpleanos_whatsapp")
    plantilla = models.ForeignKey(PlantillaMensaje, on_delete=models.SET_NULL, null=True, blank=True)
    dias_antes = models.PositiveSmallIntegerField(default=1)
    cumpleanos_fecha = models.DateField(db_index=True)
    programada_para = models.DateTimeField(db_index=True)
    mensaje = models.TextField(blank=True)
    estado = models.CharField(max_length=15, choices=ESTADO_CHOICES, default="pendiente", db_index=True)
    intentos = models.PositiveIntegerField(default=0)
    ultimo_error = models.TextField(blank=True)
    respuesta = models.JSONField(default=dict, blank=True)
    enviada_en = models.DateTimeField(null=True, blank=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["programada_para", "cliente__nombre"]
        constraints = [
            models.UniqueConstraint(
                fields=["empresa", "cliente", "dias_antes", "cumpleanos_fecha"],
                name="unique_notificacion_cumpleanos_cliente_fecha",
            )
        ]

    def __str__(self):
        return f"{self.cliente} - cumpleanos {self.cumpleanos_fecha:%d/%m/%Y}"
