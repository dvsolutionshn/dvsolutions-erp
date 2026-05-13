from django.contrib import admin

from .models import CampaniaMarketing, CitaCliente, ConfiguracionCRM, EnvioCampania, PlantillaMensaje


@admin.register(ConfiguracionCRM)
class ConfiguracionCRMAdmin(admin.ModelAdmin):
    list_display = ("empresa", "whatsapp_activo", "whatsapp_phone_number_id", "whatsapp_business_account_id", "recordatorio_cumpleanos_activo", "recordatorio_citas_activo")
    search_fields = ("empresa__nombre",)


@admin.register(PlantillaMensaje)
class PlantillaMensajeAdmin(admin.ModelAdmin):
    list_display = ("nombre", "empresa", "tipo", "canal", "imagen_promocional", "activa")
    list_filter = ("tipo", "canal", "activa")
    search_fields = ("nombre", "empresa__nombre")


@admin.register(CampaniaMarketing)
class CampaniaMarketingAdmin(admin.ModelAdmin):
    list_display = ("nombre", "empresa", "audiencia", "estado", "fecha_programada")
    list_filter = ("audiencia", "estado")
    search_fields = ("nombre", "empresa__nombre")


@admin.register(EnvioCampania)
class EnvioCampaniaAdmin(admin.ModelAdmin):
    list_display = ("campania", "cliente", "canal", "estado", "fecha_envio")
    list_filter = ("canal", "estado")
    search_fields = ("campania__nombre", "cliente__nombre")


@admin.register(CitaCliente)
class CitaClienteAdmin(admin.ModelAdmin):
    list_display = ("titulo", "empresa", "cliente", "fecha_hora", "estado", "responsable")
    list_filter = ("estado",)
    search_fields = ("titulo", "cliente__nombre", "empresa__nombre")
