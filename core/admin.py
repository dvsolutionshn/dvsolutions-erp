from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import Empresa, Usuario


@admin.register(Empresa)
class EmpresaAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'rtn', 'activa', 'fecha_creacion')


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