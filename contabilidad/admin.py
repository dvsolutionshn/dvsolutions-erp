from django.contrib import admin

from .models import AsientoContable, ConfiguracionContableEmpresa, CuentaContable, LineaAsientoContable


class LineaAsientoInline(admin.TabularInline):
    model = LineaAsientoContable
    extra = 0


@admin.register(CuentaContable)
class CuentaContableAdmin(admin.ModelAdmin):
    list_display = ("empresa", "codigo", "nombre", "cuenta_padre", "tipo", "acepta_movimientos", "activa")
    list_filter = ("empresa", "tipo", "acepta_movimientos", "activa")
    search_fields = ("codigo", "nombre", "cuenta_padre__codigo", "cuenta_padre__nombre")


@admin.register(AsientoContable)
class AsientoContableAdmin(admin.ModelAdmin):
    list_display = ("empresa", "numero", "fecha", "descripcion", "estado")
    list_filter = ("empresa", "estado", "fecha")
    search_fields = ("numero", "descripcion", "referencia")
    inlines = [LineaAsientoInline]


@admin.register(ConfiguracionContableEmpresa)
class ConfiguracionContableEmpresaAdmin(admin.ModelAdmin):
    list_display = ("empresa", "cuenta_clientes", "cuenta_ventas", "cuenta_proveedores", "fecha_actualizacion")
    list_filter = ("empresa",)
    search_fields = ("empresa__nombre",)
