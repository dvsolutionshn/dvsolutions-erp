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
    grupo_atencion = models.UUIDField(blank=True, null=True, db_index=True)
    opcion_servicio = models.CharField(max_length=180, blank=True, null=True)
    fase_servicio = models.PositiveSmallIntegerField(blank=True, null=True)
    sesion_servicio = models.PositiveSmallIntegerField(blank=True, null=True)
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
        base = self.servicio_clinico.nombre if self.servicio_clinico_id else (self.producto.nombre if self.producto_id else "Sin tipo de consulta")
        if self.fase_servicio and self.sesion_servicio:
            return f"{base} · Fase {self.fase_servicio} · Sesión {self.sesion_servicio}"
        if self.sesion_servicio:
            return f"{base} · Sesión {self.sesion_servicio}"
        if self.opcion_servicio:
            return f"{base} · {self.opcion_servicio}"
        return base

    @property
    def display_responsable(self):
        return self.profesional_salud.nombre if self.profesional_salud_id else (self.responsable or "Sin responsable")

    @property
    def agenda_color(self):
        if self.cita_clinica_id and getattr(self.cita_clinica, "es_recordatorio_tratamiento", False):
            return "recordatorio"
        if self.servicio_clinico_id and self.servicio_clinico.color_calendario:
            return f"servicio-{self.servicio_clinico_id}"
        servicio = _normalizar_texto(self.display_servicio)
        categoria = _normalizar_texto(
            self.servicio_clinico.categoria if self.servicio_clinico_id else ""
        )
        if "camara" in servicio or "hiperbar" in servicio:
            return "camara_hiperbarica"
        if "terapia" in servicio:
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
        if self.servicio_clinico_id and self.servicio_clinico.color_calendario:
            return self.servicio_clinico.nombre
        etiquetas = {
            "consulta": "Consulta",
            "terapias": "Terapias",
            "camara_hiperbarica": "Camara hiperbarica",
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
    def agenda_color_personalizado(self):
        if self.servicio_clinico_id:
            return self.servicio_clinico.color_calendario or ""
        return ""

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


class ProgramaCamaraHiperbarica(models.Model):
    PROGRAMA_CHOICES = [
        ("10x90", "10 sesiones de 90 minutos"),
        ("20x45", "20 sesiones de 45 minutos"),
        ("otro", "Otro programa"),
    ]

    empresa = models.ForeignKey(
        Empresa,
        on_delete=models.CASCADE,
        related_name="programas_camara_hiperbarica",
    )
    paciente = models.ForeignKey(
        "clinica.Paciente",
        on_delete=models.CASCADE,
        related_name="programas_camara_hiperbarica",
    )
    cirugia = models.CharField(max_length=220, blank=True)
    fecha_cirugia = models.DateField(blank=True, null=True)
    indicacion = models.TextField(blank=True)
    programa = models.CharField(max_length=20, choices=PROGRAMA_CHOICES, blank=True)
    programa_otro = models.CharField(max_length=180, blank=True)
    orden_medica = models.TextField(blank=True)
    activo = models.BooleanField(default=True)
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="programas_camara_hiperbarica_creados",
    )
    actualizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="programas_camara_hiperbarica_actualizados",
    )
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-activo", "-fecha_creacion", "-id"]
        indexes = [models.Index(fields=["empresa", "paciente", "activo"])]
        verbose_name = "Programa de cámara hiperbárica"
        verbose_name_plural = "Programas de cámara hiperbárica"

    def __str__(self):
        return f"{self.paciente.nombre} · {self.get_programa_display() or 'Programa por definir'}"

    def clean(self):
        super().clean()
        if self.paciente_id and self.empresa_id and self.paciente.empresa_id != self.empresa_id:
            from django.core.exceptions import ValidationError

            raise ValidationError("El paciente no pertenece a la empresa del programa hiperbárico.")
        if self.programa == "otro" and not self.programa_otro.strip():
            from django.core.exceptions import ValidationError

            raise ValidationError({"programa_otro": "Especifique el programa indicado."})


class SesionCamaraHiperbarica(models.Model):
    RESPUESTA_CHOICES = [("si", "Sí"), ("no", "No")]
    TOLERANCIA_CHOICES = [
        ("buena", "Buena"),
        ("regular", "Regular"),
        ("mala", "Mala"),
    ]
    ESTADO_CHOICES = [("borrador", "Borrador"), ("finalizada", "Sesión finalizada")]

    programa = models.ForeignKey(
        ProgramaCamaraHiperbarica,
        on_delete=models.CASCADE,
        related_name="sesiones",
    )
    empresa = models.ForeignKey(
        Empresa,
        on_delete=models.CASCADE,
        related_name="sesiones_camara_hiperbarica",
    )
    paciente = models.ForeignKey(
        "clinica.Paciente",
        on_delete=models.CASCADE,
        related_name="sesiones_camara_hiperbarica",
    )
    cita = models.OneToOneField(
        CitaCliente,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="control_camara_hiperbarica",
    )
    numero_sesion = models.PositiveSmallIntegerField()
    fecha_sesion = models.DateTimeField(default=timezone.now, editable=False)

    estado_general_estable = models.CharField(max_length=2, choices=RESPUESTA_CHOICES, blank=True)
    sin_fiebre = models.CharField(max_length=2, choices=RESPUESTA_CHOICES, blank=True)
    sin_dificultad_respiratoria = models.CharField(max_length=2, choices=RESPUESTA_CHOICES, blank=True)
    sin_dolor_toracico = models.CharField(max_length=2, choices=RESPUESTA_CHOICES, blank=True)
    sin_sintomas_neurologicos = models.CharField(max_length=2, choices=RESPUESTA_CHOICES, blank=True)
    sin_dolor_oido = models.CharField(max_length=2, choices=RESPUESTA_CHOICES, blank=True)
    compensa_ambos_oidos = models.CharField(max_length=2, choices=RESPUESTA_CHOICES, blank=True)
    area_quirurgica_revisada = models.CharField(max_length=2, choices=RESPUESTA_CHOICES, blank=True)
    seguridad_camara_verificada = models.CharField(max_length=2, choices=RESPUESTA_CHOICES, blank=True)
    apto_para_sesion = models.CharField(max_length=2, choices=RESPUESTA_CHOICES, blank=True)
    observaciones_previas = models.TextField(blank=True)
    firma_control_previo = models.CharField(max_length=180, blank=True)

    presion_arterial_antes = models.CharField(max_length=30, blank=True)
    saturacion_oxigeno_antes = models.CharField(max_length=20, blank=True)
    presion_camara = models.CharField(max_length=30, blank=True)
    tiempo_minutos = models.PositiveSmallIntegerField(blank=True, null=True)
    compensacion_oidos = models.CharField(max_length=180, blank=True)
    tolerancia = models.CharField(max_length=12, choices=TOLERANCIA_CHOICES, blank=True)
    presion_arterial_despues = models.CharField(max_length=30, blank=True)
    saturacion_oxigeno_despues = models.CharField(max_length=20, blank=True)
    evolucion_evento_adverso = models.TextField(blank=True)
    firma_parametros = models.CharField(max_length=180, blank=True)

    nota_enfermeria = models.TextField(blank=True)
    firma_enfermeria = models.CharField(max_length=180, blank=True)
    estado = models.CharField(max_length=15, choices=ESTADO_CHOICES, default="borrador")
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sesiones_camara_hiperbarica_creadas",
    )
    actualizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sesiones_camara_hiperbarica_actualizadas",
    )
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["numero_sesion", "fecha_sesion", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["programa", "numero_sesion"],
                name="crm_camara_programa_numero_sesion_uniq",
            ),
            models.CheckConstraint(
                condition=models.Q(numero_sesion__gte=1, numero_sesion__lte=22),
                name="crm_camara_numero_sesion_1_22",
            ),
        ]
        indexes = [models.Index(fields=["empresa", "paciente", "estado"])]
        verbose_name = "Sesión de cámara hiperbárica"
        verbose_name_plural = "Sesiones de cámara hiperbárica"

    def __str__(self):
        return f"{self.paciente.nombre} · Sesión {self.numero_sesion}"

    @property
    def bloqueada(self):
        return self.estado == "finalizada"

    def clean(self):
        from django.core.exceptions import ValidationError

        super().clean()
        errores = {}
        if not 1 <= (self.numero_sesion or 0) <= 22:
            errores["numero_sesion"] = "La sesión debe estar comprendida entre 1 y 22."
        if self.programa_id:
            if self.programa.empresa_id != self.empresa_id or self.programa.paciente_id != self.paciente_id:
                errores["programa"] = "El programa no corresponde a esta empresa y paciente."
        if self.cita_id:
            if self.cita.empresa_id != self.empresa_id or self.cita.paciente_id != self.paciente_id:
                errores["cita"] = "La cita no corresponde a esta empresa y paciente."
        if self.pk:
            original = SesionCamaraHiperbarica.objects.filter(pk=self.pk).values("estado").first()
            if original and original["estado"] == "finalizada":
                raise ValidationError("La sesión finalizada está bloqueada y no puede modificarse.")
        if errores:
            raise ValidationError(errores)


class ProgramaTerapiaPostQuirurgica(models.Model):
    empresa = models.ForeignKey(
        Empresa,
        on_delete=models.CASCADE,
        related_name="programas_terapia_postquirurgica",
    )
    paciente = models.ForeignKey(
        "clinica.Paciente",
        on_delete=models.CASCADE,
        related_name="programas_terapia_postquirurgica",
    )
    cirugia = models.CharField(max_length=220, blank=True)
    fecha_cirugia = models.DateField(blank=True, null=True)
    activo = models.BooleanField(default=True)
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="programas_terapia_postquirurgica_creados",
    )
    actualizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="programas_terapia_postquirurgica_actualizados",
    )
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-activo", "-fecha_creacion", "-id"]
        indexes = [models.Index(fields=["empresa", "paciente", "activo"])]
        verbose_name = "Programa de terapias post quirúrgicas"
        verbose_name_plural = "Programas de terapias post quirúrgicas"

    def __str__(self):
        return f"{self.paciente.nombre} · {self.cirugia or 'Terapias post quirúrgicas'}"

    def clean(self):
        from django.core.exceptions import ValidationError

        super().clean()
        if self.paciente_id and self.empresa_id and self.paciente.empresa_id != self.empresa_id:
            raise ValidationError("El paciente no pertenece a la empresa del programa post quirúrgico.")


class SesionTerapiaPostQuirurgica(models.Model):
    ESTADO_CHOICES = [("borrador", "Borrador"), ("finalizada", "Sesión finalizada")]
    ESTADO_PACIENTE_CHOICES = [
        ("bueno", "Bueno"), ("regular", "Regular"), ("malo", "Malo"),
        ("edema", "Edema"), ("equimosis", "Equimosis"), ("induracion", "Induración"),
        ("fibrosis", "Fibrosis"), ("seroma", "Seroma"), ("eritema", "Eritema"),
        ("herida_alterada", "Herida alterada"),
    ]
    EQUIPO_CHOICES = [
        ("usg", "USG"), ("tens", "TENS"), ("vibrata", "Vibrata"),
        ("vacuum", "Vacuum"), ("faja_ajuste", "Faja / ajuste"), ("presoterapia", "Presoterapia"),
        ("frio_calor", "Frío / Calor"), ("laser_corporal", "Láser corporal"),
        ("radiofrecuencia", "Radiofrecuencia"), ("exilis", "Exilis"),
        ("emsculpt", "Emsculpt"),
    ]
    CUIDADO_CHOICES = [
        ("drenaje_linfatico", "Drenaje linfático"),
        ("curacion_heridas", "Curación de heridas"),
        ("cups", "Cups"),
        ("otro", "Otro"),
    ]

    programa = models.ForeignKey(
        ProgramaTerapiaPostQuirurgica,
        on_delete=models.CASCADE,
        related_name="sesiones",
    )
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="sesiones_terapia_postquirurgica")
    paciente = models.ForeignKey(
        "clinica.Paciente", on_delete=models.CASCADE, related_name="sesiones_terapia_postquirurgica"
    )
    cita = models.OneToOneField(
        CitaCliente,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="control_terapia_postquirurgica",
    )
    numero_sesion = models.PositiveSmallIntegerField()
    fecha_sesion = models.DateTimeField(default=timezone.now, editable=False)
    hora_inicio = models.TimeField(blank=True, null=True)
    hora_finalizacion = models.TimeField(blank=True, null=True)
    presion_arterial = models.CharField(max_length=30, blank=True)
    frecuencia_cardiaca = models.CharField(max_length=20, blank=True)
    frecuencia_respiratoria = models.CharField(max_length=20, blank=True)
    saturacion_oxigeno = models.CharField(max_length=20, blank=True)
    temperatura = models.CharField(max_length=20, blank=True)
    escala_dolor = models.PositiveSmallIntegerField(blank=True, null=True)
    estado_paciente = models.JSONField(default=list, blank=True)
    equipos_utilizados = models.JSONField(default=list, blank=True)
    minutos_area = models.CharField(max_length=180, blank=True)
    cuidados_realizados = models.JSONField(default=list, blank=True)
    cuidado_otro = models.CharField(max_length=220, blank=True)
    nota_enfermeria = models.TextField(blank=True)
    enfermera_nombre = models.CharField(max_length=180, blank=True)
    firma_enfermeria = models.CharField(max_length=180, blank=True)
    estado = models.CharField(max_length=15, choices=ESTADO_CHOICES, default="borrador")
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sesiones_terapia_postquirurgica_creadas",
    )
    actualizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sesiones_terapia_postquirurgica_actualizadas",
    )
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["numero_sesion", "fecha_sesion", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["programa", "numero_sesion"], name="crm_terapia_post_programa_sesion_uniq"
            ),
            models.CheckConstraint(
                condition=models.Q(numero_sesion__gte=1, numero_sesion__lte=12),
                name="crm_terapia_post_sesion_1_12",
            ),
        ]
        indexes = [models.Index(fields=["empresa", "paciente", "estado"])]
        verbose_name = "Sesión de terapia post quirúrgica"
        verbose_name_plural = "Sesiones de terapia post quirúrgica"

    def __str__(self):
        return f"{self.paciente.nombre} · Terapia post quirúrgica {self.numero_sesion}"

    @property
    def bloqueada(self):
        return self.estado == "finalizada"

    @staticmethod
    def _etiquetas_seleccionadas(valores, opciones):
        etiquetas = dict(opciones)
        return [etiquetas.get(valor, valor) for valor in (valores or [])]

    @property
    def estado_paciente_display(self):
        return self._etiquetas_seleccionadas(self.estado_paciente, self.ESTADO_PACIENTE_CHOICES)

    @property
    def equipos_utilizados_display(self):
        return self._etiquetas_seleccionadas(self.equipos_utilizados, self.EQUIPO_CHOICES)

    @property
    def cuidados_realizados_display(self):
        return self._etiquetas_seleccionadas(self.cuidados_realizados, self.CUIDADO_CHOICES)

    def clean(self):
        from django.core.exceptions import ValidationError

        super().clean()
        errores = {}
        if not 1 <= (self.numero_sesion or 0) <= 12:
            errores["numero_sesion"] = "La sesión debe estar comprendida entre 1 y 12."
        if self.escala_dolor is not None and self.escala_dolor > 10:
            errores["escala_dolor"] = "La escala de dolor debe estar entre 0 y 10."
        if self.programa_id:
            if self.empresa_id != self.programa.empresa_id or self.paciente_id != self.programa.paciente_id:
                errores["programa"] = "El programa no corresponde a esta empresa y paciente."
        if self.cita_id:
            if self.cita.empresa_id != self.empresa_id or self.cita.paciente_id != self.paciente_id:
                errores["cita"] = "La cita no corresponde a esta empresa y paciente."
        if self.pk:
            original = SesionTerapiaPostQuirurgica.objects.filter(pk=self.pk).values("estado").first()
            if original and original["estado"] == "finalizada":
                raise ValidationError("La sesión finalizada está bloqueada y no puede modificarse.")
        if errores:
            raise ValidationError(errores)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class OpcionServicioAgenda(models.Model):
    CATEGORIA_CHOICES = [
        ("tratamientos", "Tratamientos"),
        ("spa", "Spa"),
    ]

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="opciones_servicio_agenda")
    categoria = models.CharField(max_length=30, choices=CATEGORIA_CHOICES)
    nombre = models.CharField(max_length=180)
    activo = models.BooleanField(default=True)
    orden = models.PositiveSmallIntegerField(default=0)
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="opciones_servicio_agenda_creadas",
    )
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["categoria", "orden", "nombre"]
        constraints = [
            models.UniqueConstraint(
                fields=["empresa", "categoria", "nombre"],
                name="crm_opcion_agenda_empresa_categoria_nombre_uniq",
            )
        ]

    def __str__(self):
        return self.nombre


class CitaCirugiaFoto(models.Model):
    cita = models.ForeignKey(CitaCliente, on_delete=models.CASCADE, related_name="fotos_cirugia")
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="fotos_cirugia_citas")
    imagen = models.FileField(upload_to="crm/citas/cirugias/")
    descripcion = models.CharField(max_length=180, blank=True, null=True)
    creado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-fecha_creacion", "-id"]
        verbose_name = "Foto de cirugia agendada"
        verbose_name_plural = "Fotos de cirugias agendadas"

    def __str__(self):
        return f"{self.cita.display_cliente} - foto cirugia"

    @property
    def es_video(self):
        nombre = (self.imagen.name or "").lower()
        return nombre.endswith((".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"))


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
