from django.urls import path

from . import views


urlpatterns = [
    path("", views.dashboard, name="tecnicentro_dashboard"),
    path("recepcion/", views.recepcion, name="tecnicentro_recepcion"),
    path("ordenes/", views.ordenes, name="tecnicentro_ordenes"),
    path("configuracion/", views.configuracion, name="tecnicentro_configuracion"),
    path("ordenes/<int:orden_id>/", views.detalle_orden, name="tecnicentro_detalle_orden"),
    path("ordenes/<int:orden_id>/estado/", views.cambiar_estado, name="tecnicentro_cambiar_estado"),
    path("ordenes/<int:orden_id>/asignar/", views.asignar_orden, name="tecnicentro_asignar_orden"),
    path("diagnosticos/<int:orden_id>/", views.diagnostico, name="tecnicentro_diagnostico"),
    path("diagnosticos/<int:orden_id>/evidencia/", views.agregar_evidencia, name="tecnicentro_agregar_evidencia"),
]
