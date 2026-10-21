from django.contrib import admin

from .models import LineaPedidoMero, PedidoMero, ProductoMero, PuestoMero


admin.site.register(PuestoMero)
admin.site.register(ProductoMero)
admin.site.register(PedidoMero)
admin.site.register(LineaPedidoMero)
