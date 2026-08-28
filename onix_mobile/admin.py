from django.contrib import admin

from .models import ConexionOnixExterna, PerfilOnixPersonal, SesionOnixMovil, SolicitudOAuthOnix


@admin.register(SesionOnixMovil)
class SesionOnixMovilAdmin(admin.ModelAdmin):
    list_display = (
        "usuario",
        "empresa",
        "dispositivo",
        "ultima_actividad",
        "expira_en",
        "revocada_en",
    )
    list_filter = ("empresa", "revocada_en")
    search_fields = ("usuario__username", "usuario__email", "dispositivo")
    readonly_fields = (
        "token_hash",
        "creada_en",
        "ultima_actividad",
        "expira_en",
        "revocada_en",
    )


@admin.register(PerfilOnixPersonal)
class PerfilOnixPersonalAdmin(admin.ModelAdmin):
    list_display = ("usuario", "telefono_whatsapp", "whatsapp_verificado_en", "canal_recordatorio")
    search_fields = ("usuario__email", "usuario__username", "telefono_whatsapp")
    list_filter = ("acepta_notificaciones_whatsapp", "canal_recordatorio")


@admin.register(ConexionOnixExterna)
class ConexionOnixExternaAdmin(admin.ModelAdmin):
    list_display = ("proveedor", "usuario", "empresa", "estado", "cuenta_externa", "fecha_actualizacion")
    list_filter = ("proveedor", "estado", "empresa", "sincronizacion_activa")
    search_fields = ("usuario__email", "usuario__username", "empresa__nombre", "cuenta_externa")
    readonly_fields = (
        "token_acceso_cifrado",
        "token_refresco_cifrado",
        "fecha_creacion",
        "fecha_actualizacion",
    )


@admin.register(SolicitudOAuthOnix)
class SolicitudOAuthOnixAdmin(admin.ModelAdmin):
    list_display = ("proveedor", "usuario", "empresa", "fecha_creacion", "expira_en", "consumida_en")
    list_filter = ("proveedor", "empresa")
    search_fields = ("usuario__email", "empresa__nombre")
    readonly_fields = ("estado_hash", "fecha_creacion", "expira_en", "consumida_en")
