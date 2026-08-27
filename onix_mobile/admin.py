from django.contrib import admin

from .models import SesionOnixMovil


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

