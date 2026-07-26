from django.db import migrations, models
from django.db.models import Q


def activar_modo_clinico_simple(apps, schema_editor):
    Empresa = apps.get_model("core", "Empresa")
    EmpresaModulo = apps.get_model("core", "EmpresaModulo")
    Modulo = apps.get_model("core", "Modulo")
    RolSistema = apps.get_model("core", "RolSistema")
    Usuario = apps.get_model("core", "Usuario")
    UsuarioEmpresaPermiso = apps.get_model("core", "UsuarioEmpresaPermiso")

    rol, _ = RolSistema.objects.update_or_create(
        codigo="doctora-clinica-facturacion",
        defaults={
            "nombre": "Doctora - Clinica y Facturacion",
            "descripcion": "Acceso simplificado para doctoras: expediente clinico, pacientes y facturacion operativa.",
            "activo": True,
            "puede_facturas": True,
            "puede_ver_facturas": True,
            "puede_clientes": True,
            "puede_recibos": True,
            "puede_crear_facturas": True,
            "puede_registrar_pagos_clientes": True,
            "puede_crear_clientes": True,
            "puede_editar_clientes": True,
            "puede_clinica": True,
            "puede_pacientes": True,
            "puede_expediente_clinico": True,
            "puede_tratamientos_clinicos": True,
        },
    )

    empresas_objetivo = list(
        Empresa.objects.filter(
            Q(slug="hospital_mia")
            | Q(nombre__icontains="Hospital Mia")
            | (Q(nombre__icontains="Demo") & Q(nombre__icontains="Hospital"))
            | Q(slug__icontains="demo_hospital")
            | Q(slug__icontains="demo-hospital")
        ).distinct()
    )

    modulos_base = list(
        Modulo.objects.filter(codigo__in=["facturacion", "clinica_medica"])
    )
    for empresa in empresas_objetivo:
        for modulo in modulos_base:
            EmpresaModulo.objects.update_or_create(
                empresa=empresa,
                modulo=modulo,
                defaults={"activo": True},
            )

    usuarios_candy = Usuario.objects.filter(
        Q(username__icontains="candy")
        | Q(email__icontains="candy")
        | Q(first_name__icontains="candy")
        | Q(last_name__icontains="luque")
    )
    usuarios_demo = Usuario.objects.filter(
        Q(empresa__in=empresas_objetivo),
        Q(username__icontains="demo")
        | Q(email__icontains="demo")
        | Q(first_name__icontains="demo")
        | Q(last_name__icontains="demo"),
    )

    usuarios = (usuarios_candy | usuarios_demo).distinct()
    for usuario in usuarios:
        usuario.modo_clinico_simple = True
        usuario.rol_sistema = rol
        if not usuario.empresa_id and empresas_objetivo:
            usuario.empresa = empresas_objetivo[0]
        usuario.save(update_fields=["modo_clinico_simple", "rol_sistema", "empresa"])
        for empresa in empresas_objetivo:
            usuario.empresas_acceso.add(empresa)
            UsuarioEmpresaPermiso.objects.update_or_create(
                usuario=usuario,
                empresa=empresa,
                defaults={"rol_sistema": rol, "activo": True},
            )


def desactivar_modo_clinico_simple(apps, schema_editor):
    Usuario = apps.get_model("core", "Usuario")
    Usuario.objects.filter(modo_clinico_simple=True).update(modo_clinico_simple=False)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0038_rolsistema_puede_ver_facturas_usuarioempresapermiso"),
    ]

    operations = [
        migrations.AddField(
            model_name="usuario",
            name="modo_clinico_simple",
            field=models.BooleanField(
                default=False,
                help_text="Muestra una experiencia simplificada para doctores: solo Clinica y Facturacion.",
            ),
        ),
        migrations.RunPython(activar_modo_clinico_simple, desactivar_modo_clinico_simple),
    ]
