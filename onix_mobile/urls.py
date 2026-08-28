from django.urls import path

from . import views


app_name = "onix_mobile"

urlpatterns = [
    path("login/", views.login, name="login"),
    path("bootstrap/", views.bootstrap, name="bootstrap"),
    path("connections/", views.connections, name="connections"),
    path("connections/profile/", views.personal_profile, name="personal_profile"),
    path("connections/google/start/", views.google_connection_start, name="google_connection_start"),
    path("connections/google/callback/", views.google_connection_callback, name="google_connection_callback"),
    path("connections/<slug:proveedor>/disconnect/", views.disconnect_connection, name="disconnect_connection"),
    path("history/", views.history, name="history"),
    path("chat/", views.chat, name="chat"),
    path("actions/<uuid:accion_id>/", views.action, name="action"),
    path("invoices/<int:factura_id>/pdf/", views.invoice_pdf, name="invoice_pdf"),
    path("logout/", views.logout, name="logout"),
]
