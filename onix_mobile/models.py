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

