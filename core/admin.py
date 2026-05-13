from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import ConfiguracionAvanzadaEmpresa, Empresa, PlanComercial, PlanModulo, RolSistema, Usuario


@admin.register(Empresa)
class EmpresaAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'rtn', 'activa', 'fecha_creacion')


@admin.register(ConfiguracionAvanzadaEmpresa)
class ConfiguracionAvanzadaEmpresaAdmin(admin.ModelAdmin):
    list_display = (
        'empresa',
        'usa_cierre_caja',
        'usa_pagos_mixtos',
        'usa_reporte_bancos',
        'usa_inventario_farmaceutico',
        'usa_bodegas_internas',
    )
    list_filter = (
        'usa_cierre_caja',
        'usa_pagos_mixtos',
        'usa_reporte_bancos',
        'usa_inventario_farmaceutico',
        'usa_bodegas_internas',
    )


@admin.register(Usuario)
class UsuarioAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (
        ('Empresa Info', {'fields': ('empresa', 'es_administrador_empresa')}),
    )

from .models import Modulo, EmpresaModulo

@admin.register(Modulo)
class ModuloAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'codigo')


@admin.register(EmpresaModulo)
class EmpresaModuloAdmin(admin.ModelAdmin):
    list_display = ('empresa', 'modulo', 'activo')


@admin.register(PlanComercial)
class PlanComercialAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'codigo', 'precio_mensual', 'activo')


@admin.register(PlanModulo)
class PlanModuloAdmin(admin.ModelAdmin):
    list_display = ('plan', 'modulo', 'activo')


@admin.register(RolSistema)
class RolSistemaAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'codigo', 'activo', 'puede_facturas', 'puede_inventario', 'puede_reportes')
