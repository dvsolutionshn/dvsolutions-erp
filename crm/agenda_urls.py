from django.urls import path

from . import views


urlpatterns = [
    path("", views.agenda_citas, name="agenda_citas"),
    path("<int:cita_id>/estado/", views.actualizar_estado_cita, name="agenda_cita_estado"),
    path("<int:cita_id>/eliminar/", views.eliminar_cita, name="agenda_cita_eliminar"),
]
