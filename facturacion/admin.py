from django.contrib import admin
from .models import CAI, BodegaInventario, CategoriaProductoFarmaceutico, CierreCaja, ExistenciaLoteBodega, Factura, Cliente, LoteInventario, MovimientoLoteBodega, PerfilFarmaceuticoProducto, Producto, LineaFactura, TipoImpuesto, PagoFactura


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
    list_display = ('factura', 'monto', 'metodo', 'fecha', 'cajero')


@admin.register(CierreCaja)
class CierreCajaAdmin(admin.ModelAdmin):
    list_display = ('empresa', 'fecha', 'cajero', 'turno', 'total_sistema', 'total_reportado', 'diferencia', 'estado')
    list_filter = ('empresa', 'fecha', 'turno', 'estado')
    search_fields = ('empresa__nombre', 'cajero__username', 'observacion')


@admin.register(BodegaInventario)
class BodegaInventarioAdmin(admin.ModelAdmin):
    list_display = ('empresa', 'nombre', 'tipo', 'activa')
    list_filter = ('empresa', 'tipo', 'activa')
    search_fields = ('nombre', 'empresa__nombre')


@admin.register(LoteInventario)
class LoteInventarioAdmin(admin.ModelAdmin):
    list_display = ('empresa', 'producto', 'numero_lote', 'fecha_vencimiento', 'activo')
    list_filter = ('empresa', 'activo', 'fecha_vencimiento')
    search_fields = ('numero_lote', 'producto__nombre')


@admin.register(ExistenciaLoteBodega)
class ExistenciaLoteBodegaAdmin(admin.ModelAdmin):
    list_display = ('empresa', 'bodega', 'lote', 'cantidad')
    list_filter = ('empresa', 'bodega')
    search_fields = ('lote__numero_lote', 'lote__producto__nombre', 'bodega__nombre')


@admin.register(MovimientoLoteBodega)
class MovimientoLoteBodegaAdmin(admin.ModelAdmin):
    list_display = ('empresa', 'fecha', 'bodega', 'lote', 'tipo', 'cantidad', 'referencia')
    list_filter = ('empresa', 'tipo', 'bodega')
    search_fields = ('lote__numero_lote', 'lote__producto__nombre', 'referencia')


@admin.register(CategoriaProductoFarmaceutico)
class CategoriaProductoFarmaceuticoAdmin(admin.ModelAdmin):
    list_display = ('empresa', 'nombre', 'requiere_receta_default', 'requiere_refrigeracion_default', 'producto_controlado_default', 'activa')
    list_filter = ('empresa', 'activa', 'requiere_receta_default', 'requiere_refrigeracion_default', 'producto_controlado_default')
    search_fields = ('nombre', 'descripcion')


@admin.register(PerfilFarmaceuticoProducto)
class PerfilFarmaceuticoProductoAdmin(admin.ModelAdmin):
    list_display = ('empresa', 'producto', 'categoria', 'principio_activo', 'laboratorio', 'producto_controlado', 'requiere_refrigeracion')
    list_filter = ('empresa', 'categoria', 'producto_controlado', 'requiere_refrigeracion', 'requiere_receta')
    search_fields = ('producto__nombre', 'principio_activo', 'laboratorio', 'registro_sanitario')
