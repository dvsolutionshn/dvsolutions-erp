"""Read-only production preflight; never emit credentials or patient records."""
import json
import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()
from django.conf import settings
from django.db import connection
from django.db.migrations.recorder import MigrationRecorder
from crm.models import ConfiguracionCRM, NotificacionCitaWhatsApp

config = ConfiguracionCRM.objects.filter(empresa__slug="hospital_mia").values(
    "empresa_id", "whatsapp_activo", "recordatorio_citas_activo", "recordatorio_cumpleanos_activo",
    "whatsapp_phone_number_id", "whatsapp_business_account_id", "whatsapp_numero_conectado",
    "whatsapp_plantilla_cita", "whatsapp_idioma_cita",
).first()
if config:
    phone = config.pop("whatsapp_numero_conectado") or ""
    digits = "".join(c for c in phone if c.isdigit())
    config["numero_terminacion"] = digits[-4:]
    config["parece_numero_prueba_meta"] = digits.startswith("1555")
    config["phone_id_presente"] = bool(config.pop("whatsapp_phone_number_id"))
    config["cuenta_id_presente"] = bool(config.pop("whatsapp_business_account_id"))
    config["token_presente"] = ConfiguracionCRM.objects.filter(empresa_id=config["empresa_id"]).exclude(
        whatsapp_token__isnull=True).exclude(whatsapp_token="").exists()
print(json.dumps({
    "database_engine": connection.vendor,
    "app_id": settings.META_WHATSAPP_APP_ID,
    "config_id": settings.META_WHATSAPP_CONFIG_ID,
    "app_secret_present": bool(settings.META_WHATSAPP_APP_SECRET),
    "verify_token_present": bool(os.environ.get("META_WHATSAPP_VERIFY_TOKEN", "")),
    "graph_version": settings.META_WHATSAPP_GRAPH_VERSION,
    "media_root": str(settings.MEDIA_ROOT), "static_root": str(settings.STATIC_ROOT),
    "crm_migrations": list(MigrationRecorder.Migration.objects.filter(app="crm").values_list("name", flat=True)),
    "hospital_mia": config,
    "hospital_pending_notifications": NotificacionCitaWhatsApp.objects.filter(
        cita__empresa__slug="hospital_mia", estado__in=["pendiente", "error"]).count(),
}, ensure_ascii=True))
