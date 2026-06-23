from django.urls import path

from . import views


urlpatterns = [
    path("citas/<str:token>/", views.cita_respuesta_publica, name="crm_cita_respuesta_publica"),
]
