from django.urls import path

from . import views


urlpatterns = [
    path("", views.agenda_citas, name="agenda_citas"),
]
