from django.urls import path
from . import views

urlpatterns = [

    path('', views.facturacion_dashboard, name='facturacion_dashboard'),

    path('crear/', views.crear_factura, name='crear_factura'),

    path('<int:factura_id>/', views.ver_factura, name='ver_factura'),

    path('<int:factura_id>/editar/', views.editar_factura, name='editar_factura'),

    path('<int:factura_id>/pdf/', views.descargar_factura_pdf, name='descargar_factura_pdf'),

    # 🔥 NUEVO
    path('<int:factura_id>/pago/', views.registrar_pago, name='registrar_pago'),

    path('reportes/', views.reportes_facturacion, name='reportes_facturacion'),

    path('cxc/', views.reporte_cxc, name='reporte_cxc'),

    path('reportes/excel/', views.exportar_excel_reportes, name='exportar_excel'),
]