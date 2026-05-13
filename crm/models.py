from urllib.parse import quote

from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import Empresa
from facturacion.models import Cliente, Producto


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
    remitente_correo = models.EmailField(blank=True, null=True)
    recordatorio_cumpleanos_activo = models.BooleanField(default=True)
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
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name="citas")
    producto = models.ForeignKey(Producto, on_delete=models.SET_NULL, null=True, blank=True, related_name="citas")
    titulo = models.CharField(max_length=180)
    fecha_hora = models.DateTimeField()
    responsable = models.CharField(max_length=120, blank=True, null=True)
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default="pendiente")
    observacion = models.TextField(blank=True, null=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["fecha_hora"]

    def __str__(self):
        return f"{self.titulo} - {self.cliente}"

    @property
    def whatsapp_url(self):
        telefono = "".join(ch for ch in (self.cliente.telefono_whatsapp or self.cliente.telefono or "") if ch.isdigit())
        if telefono and not telefono.startswith("504") and len(telefono) == 8:
            telefono = f"504{telefono}"
        mensaje = (
            f"Hola {self.cliente.nombre}, le recordamos su cita {self.titulo} "
            f"para el {timezone.localtime(self.fecha_hora).strftime('%d/%m/%Y %I:%M %p')}."
        )
        return f"https://wa.me/{telefono}?text={quote(mensaje)}" if telefono else ""
