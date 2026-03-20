from django.urls import path
from .views import empresa_login, dashboard, cerrar_sesion

urlpatterns = [
    path('<slug:slug>/', empresa_login, name='empresa_login'),
    path('<slug:slug>/dashboard/', dashboard, name='dashboard'),
    path('<slug:slug>/logout/', cerrar_sesion, name='cerrar_sesion'),
]