import re
from urllib.parse import urlsplit

from django.contrib import admin
from django.urls import path, include, re_path
from django.conf import settings
from django.conf.urls.static import static

from clinica.media_privada import PREFIJOS_MEDIA_CLINICA, archivo_media_clinica

urlpatterns = []

if settings.ENABLE_DJANGO_ADMIN:
    urlpatterns.append(path(settings.DJANGO_ADMIN_PATH, admin.site.urls))

prefijo_media = re.escape(urlsplit(settings.MEDIA_URL).path.lstrip("/"))
rutas_media_clinica = "|".join(map(re.escape, PREFIJOS_MEDIA_CLINICA))
urlpatterns.append(re_path(
    rf"^{prefijo_media}(?P<nombre>(?:{rutas_media_clinica}).+)$",
    archivo_media_clinica,
    name="archivo_media_clinica",
))

urlpatterns += [
    path('mero/', include('mero.urls')),
    path('api/onix/mobile/v1/', include('onix_mobile.urls')),
    path('formularios/clinica/', include('clinica.public_urls')),
    path('confirmacion/', include('crm.public_urls')),
    path('<slug:empresa_slug>/tecnicentro/', include('tecnicentro.public_urls')),
    # Facturación por empresa
    path('<slug:empresa_slug>/dashboard/facturacion/', include('facturacion.urls')),
    path('<slug:empresa_slug>/dashboard/contabilidad/', include('contabilidad.urls')),
    path('<slug:empresa_slug>/dashboard/rrhh/', include('rrhh.urls')),
    path('<slug:empresa_slug>/dashboard/crm/', include('crm.urls')),
    path('<slug:empresa_slug>/dashboard/citas/', include('crm.agenda_urls')),
    path('<slug:empresa_slug>/dashboard/clinica/', include('clinica.urls')),
    path('<slug:empresa_slug>/dashboard/tecnicentro/', include('tecnicentro.urls')),

    # Core (login, home, etc)
    path('', include('core.urls')),
]

# Servir media en desarrollo
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
