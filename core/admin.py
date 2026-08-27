from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import (
    AccionOnix,
    ConfiguracionAvanzadaEmpresa,
    ConfiguracionOnix,
    ConsumoOnix,
    ConversacionOnix,
    Empresa,
    MensajeOnix,
    PlanComercial,
    PlanModulo,
    RolSistema,
    Usuario,
    UsuarioEmpresaPermiso,
)


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
        'usa_control_lotes_fefo',
        'usa_bodegas_internas',
        'modulos_clinicos_visibles',
    )

    def modulos_clinicos_visibles(self, obj):
        return ", ".join(obj.modulos_adicionales_visibles_clinica.values_list("nombre", flat=True)) or "-"
    list_filter = (
        'usa_cierre_caja',
        'usa_pagos_mixtos',
        'usa_reporte_bancos',
        'usa_inventario_farmaceutico',
        'usa_control_lotes_fefo',
        'usa_bodegas_internas',
    )


@admin.register(Usuario)
class UsuarioAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (
        ('Empresa Info', {'fields': ('empresa', 'empresas_acceso', 'es_administrador_empresa')}),
    )
    filter_horizontal = UserAdmin.filter_horizontal + ('empresas_acceso',)

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
    list_display = ('nombre', 'codigo', 'activo', 'puede_punto_venta', 'puede_cierres_caja', 'puede_facturas', 'puede_ver_facturas', 'puede_inventario', 'puede_reportes')


@admin.register(UsuarioEmpresaPermiso)
class UsuarioEmpresaPermisoAdmin(admin.ModelAdmin):
    list_display = ("usuario", "empresa", "rol_sistema", "activo", "fecha_actualizacion")
    list_filter = ("activo", "empresa", "rol_sistema")
    search_fields = ("usuario__username", "usuario__email", "empresa__nombre", "rol_sistema__nombre")


@admin.register(ConfiguracionOnix)
class ConfiguracionOnixAdmin(admin.ModelAdmin):
    list_display = (
        "empresa",
        "activo",
        "modelo",
        "herramientas_consulta_activas",
        "herramientas_accion_activas",
        "limite_tokens_mensual",
        "voz_activa",
        "fecha_actualizacion",
    )
    list_filter = (
        "activo",
        "herramientas_consulta_activas",
        "herramientas_accion_activas",
        "voz_activa",
        "modelo",
    )
    search_fields = ("empresa__nombre", "empresa__slug")


@admin.register(ConversacionOnix)
class ConversacionOnixAdmin(admin.ModelAdmin):
    list_display = ("empresa", "usuario", "titulo", "activa", "fecha_actualizacion")
    list_filter = ("activa", "empresa")
    search_fields = ("empresa__nombre", "usuario__username", "usuario__email", "titulo")
    readonly_fields = ("fecha_creacion", "fecha_actualizacion")


@admin.register(MensajeOnix)
class MensajeOnixAdmin(admin.ModelAdmin):
    list_display = ("conversacion", "rol", "resumen", "fecha")
    list_filter = ("rol", "conversacion__empresa")
    search_fields = ("contenido", "conversacion__empresa__nombre", "conversacion__usuario__username")
    readonly_fields = ("conversacion", "rol", "contenido", "pagina", "metadatos", "fecha")

    @admin.display(description="Mensaje")
    def resumen(self, obj):
        return obj.contenido[:100]


@admin.register(ConsumoOnix)
class ConsumoOnixAdmin(admin.ModelAdmin):
    list_display = (
        "empresa",
        "usuario",
        "modelo",
        "tokens_total",
        "costo_estimado_usd",
        "llamadas_herramientas",
        "fecha",
    )
    list_filter = ("empresa", "modelo", "fecha")
    search_fields = ("empresa__nombre", "usuario__username", "respuesta_id")
    readonly_fields = (
        "empresa",
        "usuario",
        "conversacion",
        "modelo",
        "respuesta_id",
        "tokens_entrada",
        "tokens_entrada_cache",
        "tokens_salida",
        "tokens_total",
        "costo_estimado_usd",
        "llamadas_herramientas",
        "fecha",
    )


@admin.register(AccionOnix)
class AccionOnixAdmin(admin.ModelAdmin):
    list_display = (
        "tipo",
        "empresa",
        "usuario",
        "estado",
        "fecha_creacion",
        "fecha_confirmacion",
    )
    list_filter = ("tipo", "estado", "empresa", "fecha_creacion")
    search_fields = ("empresa__nombre", "usuario__username", "usuario__email")
    readonly_fields = (
        "id",
        "empresa",
        "usuario",
        "conversacion",
        "tipo",
        "estado",
        "datos",
        "vista_previa",
        "resultado",
        "detalle_error",
        "expira_en",
        "fecha_creacion",
        "fecha_confirmacion",
        "fecha_actualizacion",
    )
