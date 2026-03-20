from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),

    # Facturación por empresa
    path('<slug:empresa_slug>/dashboard/facturacion/', include('facturacion.urls')),

    # Core (login, home, etc)
    path('', include('core.urls')),
]

# Servir media en desarrollo
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)