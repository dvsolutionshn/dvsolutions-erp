from django.urls import path

from . import views


urlpatterns = [
    path("<str:token>/", views.preconsulta_publica, name="clinica_preconsulta_publica"),
]
