from django.urls import path

from . import views


urlpatterns = [
    path("manuales/<str:token>/descargar/", views.descargar_manual_pdf_publico, name="clinica_descargar_manual_pdf_publico"),
    path("nuevo/<str:token>/actividad/", views.registro_paciente_actividad_publica, name="clinica_registro_paciente_actividad"),
    path("nuevo/<str:token>/", views.registro_paciente_publico, name="clinica_registro_paciente_publico"),
    path("<str:token>/", views.preconsulta_publica, name="clinica_preconsulta_publica"),
]
