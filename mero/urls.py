from django.urls import path

from . import views


app_name = "mero"

urlpatterns = [
    path("demo/", views.demo, name="demo"),
    path("demo/estado/", views.estado_demo, name="estado"),
    path("demo/pedidos/", views.crear_pedido, name="crear_pedido"),
    path("demo/pedidos/<int:pedido_id>/", views.actualizar_pedido, name="actualizar_pedido"),
    path("manifest.webmanifest", views.manifest, name="manifest"),
    path("sw.js", views.service_worker, name="service_worker"),
]
