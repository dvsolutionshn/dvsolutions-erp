from django.urls import path

from . import public_views


urlpatterns = [
    path("", public_views.login_tecnicentro, name="tecnicentro_login"),
    path("salir/", public_views.logout_tecnicentro, name="tecnicentro_logout"),
]
