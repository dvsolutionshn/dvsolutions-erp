from django.urls import path

from . import views


urlpatterns = [
    path("", views.agenda_citas, name="agenda_citas"),
    path("app/", views.agenda_mobile, name="agenda_mobile"),
    path("app/manifest.webmanifest", views.agenda_mobile_manifest, name="agenda_mobile_manifest"),
    path("app/service-worker.js", views.agenda_mobile_service_worker, name="agenda_mobile_service_worker"),
    path("pacientes/crear-rapido/", views.crear_paciente_rapido_cita, name="agenda_crear_paciente_rapido"),
    path("pacientes/buscar/", views.buscar_pacientes_cita, name="agenda_buscar_pacientes"),
    path("clientes/buscar/", views.buscar_clientes_cita, name="agenda_buscar_clientes"),
    path("<int:cita_id>/cancelar-whatsapp/", views.cancelar_cita_whatsapp, name="agenda_cita_cancelar_whatsapp"),
    path("<int:cita_id>/reagendar-whatsapp/", views.reagendar_cita_whatsapp, name="agenda_cita_reagendar_whatsapp"),
    path("<int:cita_id>/estado/", views.actualizar_estado_cita, name="agenda_cita_estado"),
    path("<int:cita_id>/eliminar/", views.eliminar_cita, name="agenda_cita_eliminar"),
]
