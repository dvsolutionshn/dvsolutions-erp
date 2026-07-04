from django.urls import path

from . import views


urlpatterns = [
    path("nuevo/<str:token>/", views.registro_paciente_publico, name="clinica_registro_paciente_publico"),
    path("<str:token>/", views.preconsulta_publica, name="clinica_preconsulta_publica"),
]
