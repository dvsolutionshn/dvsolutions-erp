from django.db import migrations


def crear_clientes(apps, schema_editor):
    Empresa = apps.get_model('core', 'Empresa')
    Cliente = apps.get_model('facturacion', 'ClienteContable')
    alias = schema_editor.connection.alias
    empresa = Empresa.objects.using(alias).filter(slug='dubon_asociados').first()
    if empresa:
        for nombre in ('Nordic', 'Molac', 'Gecko'):
            Cliente.objects.using(alias).get_or_create(empresa_id=empresa.pk, nombre=nombre)


class Migration(migrations.Migration):
    dependencies = [('facturacion', '0074_clientes_contables')]
    operations = [migrations.RunPython(crear_clientes, migrations.RunPython.noop)]
