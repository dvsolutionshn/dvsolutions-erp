from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = []

if settings.ENABLE_DJANGO_ADMIN:
    urlpatterns.append(path(settings.DJANGO_ADMIN_PATH, admin.site.urls))

urlpatterns += [
    # Facturación por empresa
    path('<slug:empresa_slug>/dashboard/facturacion/', include('facturacion.urls')),
    path('<slug:empresa_slug>/dashboard/contabilidad/', include('contabilidad.urls')),
    path('<slug:empresa_slug>/dashboard/rrhh/', include('rrhh.urls')),
    path('<slug:empresa_slug>/dashboard/crm/', include('crm.urls')),
    path('<slug:empresa_slug>/dashboard/citas/', include('crm.agenda_urls')),

    # Core (login, home, etc)
    path('', include('core.urls')),
]

# Servir media en desarrollo
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
