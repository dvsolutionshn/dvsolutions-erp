from django.contrib import admin

from .models import ConfiguracionRRHHEmpresa, DetallePlanilla, Empleado, MovimientoPlanilla, PeriodoPlanilla, VacacionEmpleado


@admin.register(Empleado)
class EmpleadoAdmin(admin.ModelAdmin):
    list_display = ("nombre_completo", "empresa", "puesto", "departamento", "salario_mensual", "estado")
    list_filter = ("empresa", "estado", "departamento", "aplica_ihss", "aplica_rap")
    search_fields = ("nombres", "apellidos", "identidad", "rtn", "codigo")


@admin.register(PeriodoPlanilla)
class PeriodoPlanillaAdmin(admin.ModelAdmin):
    list_display = ("nombre", "empresa", "fecha_inicio", "fecha_fin", "fecha_pago", "estado", "total_neto")
    list_filter = ("empresa", "estado", "frecuencia", "incluir_14avo")


@admin.register(DetallePlanilla)
class DetallePlanillaAdmin(admin.ModelAdmin):
    list_display = ("periodo", "empleado", "total_devengado", "total_deducciones", "neto_pagar")
    list_filter = ("periodo__empresa", "periodo")
    search_fields = ("empleado__nombres", "empleado__apellidos")


admin.site.register(ConfiguracionRRHHEmpresa)
admin.site.register(MovimientoPlanilla)
admin.site.register(VacacionEmpleado)

# Register your models here.
