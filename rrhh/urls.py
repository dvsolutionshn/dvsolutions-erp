from django.urls import path

from . import views


urlpatterns = [
    path("", views.rrhh_dashboard, name="rrhh_dashboard"),
    path("configuracion/", views.configuracion_rrhh_view, name="configuracion_rrhh"),
    path("empleados/", views.empleados_rrhh, name="empleados_rrhh"),
    path("empleados/crear/", views.crear_empleado, name="crear_empleado_rrhh"),
    path("empleados/<int:empleado_id>/", views.ver_empleado, name="ver_empleado"),
    path("empleados/<int:empleado_id>/editar/", views.editar_empleado, name="editar_empleado_rrhh"),
    path("planillas/", views.planillas_rrhh, name="planillas_rrhh"),
    path("planillas/crear/", views.crear_periodo_planilla, name="crear_periodo_planilla"),
    path("planillas/<int:periodo_id>/", views.ver_planilla, name="ver_planilla"),
    path("planillas/<int:periodo_id>/generar/", views.generar_planilla_view, name="generar_planilla"),
    path("planillas/detalle/<int:detalle_id>/editar/", views.editar_detalle_planilla, name="editar_detalle_planilla"),
    path("movimientos/", views.movimientos_planilla, name="movimientos_planilla"),
    path("vacaciones/", views.vacaciones_rrhh, name="vacaciones_rrhh"),
    path("voucher/<int:detalle_id>/pdf/", views.voucher_planilla_pdf, name="voucher_planilla_pdf"),
    path("voucher/<int:detalle_id>/whatsapp-api/", views.enviar_voucher_whatsapp_api, name="enviar_voucher_whatsapp_api"),
    path("voucher/<int:detalle_id>/email/", views.enviar_voucher_email, name="enviar_voucher_email"),
]
