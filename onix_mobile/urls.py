from django.urls import path

from . import views


app_name = "onix_mobile"

urlpatterns = [
    path("login/", views.login, name="login"),
    path("bootstrap/", views.bootstrap, name="bootstrap"),
    path("history/", views.history, name="history"),
    path("chat/", views.chat, name="chat"),
    path("actions/<uuid:accion_id>/", views.action, name="action"),
    path("invoices/<int:factura_id>/pdf/", views.invoice_pdf, name="invoice_pdf"),
    path("logout/", views.logout, name="logout"),
]
