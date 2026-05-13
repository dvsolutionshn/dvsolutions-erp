from django.urls import path

from . import views


urlpatterns = [
    path("", views.crm_dashboard, name="crm_dashboard"),
    path("configuracion/", views.configuracion_crm, name="crm_configuracion"),
    path("configuracion/enviar-prueba-whatsapp/", views.enviar_prueba_whatsapp, name="crm_enviar_prueba_whatsapp"),
    path("plantillas/", views.plantillas, name="crm_plantillas"),
    path("campanias/", views.campanias, name="crm_campanias"),
    path("campanias/crear/", views.crear_campania, name="crm_crear_campania"),
    path("campanias/<int:campania_id>/", views.ver_campania, name="crm_ver_campania"),
    path("campanias/<int:campania_id>/preparar-envios/", views.preparar_envios_campania, name="crm_preparar_envios_campania"),
    path("campanias/<int:campania_id>/enviar-prueba-masiva/", views.enviar_campania_plantilla_prueba, name="crm_enviar_campania_plantilla_prueba"),
    path("campanias/<int:campania_id>/enviar-whatsapp-api/", views.enviar_campania_whatsapp_api, name="crm_enviar_campania_whatsapp_api"),
    path("citas/", views.citas, name="crm_citas"),
]
