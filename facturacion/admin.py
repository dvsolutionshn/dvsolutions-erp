from django.contrib import admin
from .models import CAI, Factura, Cliente, Producto, LineaFactura, TipoImpuesto, PagoFactura


# ==========================
# TIPO IMPUESTO
# ==========================
@admin.register(TipoImpuesto)
class TipoImpuestoAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'porcentaje', 'activo')


# ==========================
# CAI
# ==========================
@admin.register(CAI)
class CAIAdmin(admin.ModelAdmin):
    list_display = (
        'empresa',
        'numero_cai',
        'rango_inicial',
        'rango_final',
        'correlativo_actual',
        'fecha_limite',
        'activo'
    )


# ==========================
# CLIENTE
# ==========================
@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'empresa', 'rtn', 'activo')


# ==========================
# PRODUCTO
# ==========================
@admin.register(Producto)
class ProductoAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'empresa', 'precio', 'activo')


# ==========================
# LINEAS FACTURA
# ==========================
class LineaFacturaInline(admin.TabularInline):
    model = LineaFactura
    extra = 1


# ==========================
# PAGOS INLINE 🔥 NUEVO
# ==========================
class PagoFacturaInline(admin.TabularInline):
    model = PagoFactura
    extra = 0


# ==========================
# FACTURA
# ==========================
@admin.register(Factura)
class FacturaAdmin(admin.ModelAdmin):
    list_display = (
        'numero_factura',
        'empresa',
        'cliente',
        'subtotal',
        'impuesto',
        'total',
        'estado',
        'estado_pago'  # 🔥 agregado
    )

    exclude = ('cai',)
    readonly_fields = ('numero_factura', 'subtotal', 'impuesto', 'total')

    inlines = [LineaFacturaInline, PagoFacturaInline]  # 🔥 agregado


# ==========================
# PAGO FACTURA 🔥 NUEVO
# ==========================
@admin.register(PagoFactura)
class PagoFacturaAdmin(admin.ModelAdmin):
    list_display = ('factura', 'monto', 'metodo', 'fecha')