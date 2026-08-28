import hashlib
import hmac
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import Empresa


class SesionOnixMovil(models.Model):
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="sesiones_onix_movil",
    )
    empresa = models.ForeignKey(
        Empresa,
        on_delete=models.CASCADE,
        related_name="sesiones_onix_movil",
    )
    token_hash = models.CharField(max_length=64, unique=True, editable=False)
    dispositivo = models.CharField(max_length=160, blank=True)
    direccion_ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True)
    creada_en = models.DateTimeField(auto_now_add=True)
    ultima_actividad = models.DateTimeField(auto_now=True)
    expira_en = models.DateTimeField(db_index=True)
    revocada_en = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ["-ultima_actividad", "-id"]
        indexes = [
            models.Index(
                fields=["usuario", "empresa", "revocada_en"],
                name="onix_mob_sesion_activa_idx",
            ),
        ]
        verbose_name = "Sesion de Onix Mobile"
        verbose_name_plural = "Sesiones de Onix Mobile"

    def __str__(self):
        return f"{self.usuario} · {self.empresa.nombre} · {self.dispositivo or 'Dispositivo'}"

    @staticmethod
    def calcular_hash(token):
        return hmac.new(
            settings.SECRET_KEY.encode("utf-8"),
            token.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    @classmethod
    def emitir(cls, *, usuario, empresa, dispositivo="", direccion_ip=None, user_agent=""):
        token = f"onx_{secrets.token_urlsafe(48)}"
        dias = max(1, int(getattr(settings, "ONIX_MOBILE_TOKEN_DAYS", 30)))
        max_sesiones = max(1, int(getattr(settings, "ONIX_MOBILE_MAX_SESSIONS_PER_USER", 5)))
        ahora = timezone.now()

        activas = cls.objects.filter(
            usuario=usuario,
            empresa=empresa,
            revocada_en__isnull=True,
            expira_en__gt=ahora,
        ).order_by("-ultima_actividad", "-id")
        ids_a_revocar = list(activas.values_list("id", flat=True)[max_sesiones - 1 :])
        if ids_a_revocar:
            cls.objects.filter(id__in=ids_a_revocar).update(revocada_en=ahora)

        sesion = cls.objects.create(
            usuario=usuario,
            empresa=empresa,
            token_hash=cls.calcular_hash(token),
            dispositivo=(dispositivo or "")[:160],
            direccion_ip=direccion_ip,
            user_agent=(user_agent or "")[:300],
            expira_en=ahora + timedelta(days=dias),
        )
        return token, sesion

    @property
    def activa(self):
        return self.revocada_en is None and self.expira_en > timezone.now()

    def revocar(self):
        if self.revocada_en is None:
            self.revocada_en = timezone.now()
            self.save(update_fields=["revocada_en", "ultima_actividad"])


class PerfilOnixPersonal(models.Model):
    CANAL_APP = "app"
    CANAL_CORREO = "correo"
    CANAL_WHATSAPP = "whatsapp"
    CANALES = (
        (CANAL_APP, "Aplicacion"),
        (CANAL_CORREO, "Correo"),
        (CANAL_WHATSAPP, "WhatsApp"),
    )

    usuario = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="perfil_onix_personal",
    )
    telefono_whatsapp = models.CharField(max_length=20, blank=True)
    whatsapp_verificado_en = models.DateTimeField(null=True, blank=True)
    acepta_notificaciones_whatsapp = models.BooleanField(default=False)
    zona_horaria = models.CharField(max_length=64, default="America/Tegucigalpa")
    canal_recordatorio = models.CharField(max_length=12, choices=CANALES, default=CANAL_APP)
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Perfil personal de Onix"
        verbose_name_plural = "Perfiles personales de Onix"

    def __str__(self):
        return self.usuario.email or self.usuario.username


class ConexionOnixExterna(models.Model):
    GOOGLE_CALENDAR = "google_calendar"
    APPLE_CALENDAR = "apple_calendar"
    GMAIL = "gmail"
    WHATSAPP = "whatsapp"
    PROVEEDORES = (
        (GOOGLE_CALENDAR, "Google Calendar"),
        (APPLE_CALENDAR, "Calendario de iPhone"),
        (GMAIL, "Gmail"),
        (WHATSAPP, "WhatsApp"),
    )

    PENDIENTE = "pendiente"
    CONECTADA = "conectada"
    ERROR = "error"
    REVOCADA = "revocada"
    ESTADOS = (
        (PENDIENTE, "Pendiente"),
        (CONECTADA, "Conectada"),
        (ERROR, "Error"),
        (REVOCADA, "Revocada"),
    )

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="conexiones_onix",
    )
    empresa = models.ForeignKey(
        Empresa,
        on_delete=models.CASCADE,
        related_name="conexiones_onix",
    )
    proveedor = models.CharField(max_length=32, choices=PROVEEDORES)
    estado = models.CharField(max_length=12, choices=ESTADOS, default=PENDIENTE, db_index=True)
    cuenta_externa = models.CharField(max_length=254, blank=True)
    nombre_cuenta = models.CharField(max_length=180, blank=True)
    permisos = models.JSONField(default=list, blank=True)
    token_acceso_cifrado = models.TextField(blank=True, editable=False)
    token_refresco_cifrado = models.TextField(blank=True, editable=False)
    token_expira_en = models.DateTimeField(null=True, blank=True)
    sincronizacion_activa = models.BooleanField(default=True)
    ultima_sincronizacion = models.DateTimeField(null=True, blank=True)
    ultimo_error = models.CharField(max_length=500, blank=True)
    metadatos = models.JSONField(default=dict, blank=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["usuario", "empresa", "proveedor"],
                name="onix_conexion_unica_usuario_empresa",
            ),
        ]
        indexes = [
            models.Index(fields=["empresa", "proveedor", "estado"], name="onix_conexion_estado_idx"),
        ]
        verbose_name = "Conexion externa de Onix"
        verbose_name_plural = "Conexiones externas de Onix"

    def __str__(self):
        return f"{self.get_proveedor_display()} · {self.usuario} · {self.empresa}"

    def guardar_tokens(self, *, acceso="", refresco=""):
        from .security import cifrar_secreto

        if acceso:
            self.token_acceso_cifrado = cifrar_secreto(acceso)
        if refresco:
            self.token_refresco_cifrado = cifrar_secreto(refresco)

    def token_acceso(self):
        from .security import descifrar_secreto

        return descifrar_secreto(self.token_acceso_cifrado)

    def token_refresco(self):
        from .security import descifrar_secreto

        return descifrar_secreto(self.token_refresco_cifrado)

    def revocar(self):
        self.estado = self.REVOCADA
        self.sincronizacion_activa = False
        self.token_acceso_cifrado = ""
        self.token_refresco_cifrado = ""
        self.token_expira_en = None
        self.ultimo_error = ""
        self.save()


class SolicitudOAuthOnix(models.Model):
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="solicitudes_oauth_onix",
    )
    empresa = models.ForeignKey(
        Empresa,
        on_delete=models.CASCADE,
        related_name="solicitudes_oauth_onix",
    )
    proveedor = models.CharField(max_length=32, choices=ConexionOnixExterna.PROVEEDORES)
    estado_hash = models.CharField(max_length=64, unique=True, editable=False)
    expira_en = models.DateTimeField(db_index=True)
    consumida_en = models.DateTimeField(null=True, blank=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-fecha_creacion"]
        verbose_name = "Solicitud OAuth de Onix"
        verbose_name_plural = "Solicitudes OAuth de Onix"

    @staticmethod
    def calcular_hash(estado):
        return hmac.new(
            settings.SECRET_KEY.encode("utf-8"),
            estado.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    @classmethod
    def emitir(cls, *, usuario, empresa, proveedor):
        estado = secrets.token_urlsafe(48)
        solicitud = cls.objects.create(
            usuario=usuario,
            empresa=empresa,
            proveedor=proveedor,
            estado_hash=cls.calcular_hash(estado),
            expira_en=timezone.now() + timedelta(minutes=10),
        )
        return estado, solicitud

    @classmethod
    def consumir(cls, estado, proveedor):
        solicitud = cls.objects.filter(
            estado_hash=cls.calcular_hash(estado),
            proveedor=proveedor,
            consumida_en__isnull=True,
            expira_en__gt=timezone.now(),
        ).first()
        if not solicitud:
            return None
        solicitud.consumida_en = timezone.now()
        solicitud.save(update_fields=["consumida_en"])
        return solicitud
