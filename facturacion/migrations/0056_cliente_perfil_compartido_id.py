import uuid

from django.db import migrations, models


def asignar_perfiles_individuales(apps, schema_editor):
    Cliente = apps.get_model('facturacion', 'Cliente')
    for cliente in Cliente.objects.filter(perfil_compartido_id__isnull=True).iterator():
        cliente.perfil_compartido_id = uuid.uuid4()
        cliente.save(update_fields=['perfil_compartido_id'])


class Migration(migrations.Migration):

    dependencies = [
        ('facturacion', '0055_historialcostorealproducto'),
    ]

    operations = [
        migrations.AddField(
            model_name='cliente',
            name='perfil_compartido_id',
            field=models.UUIDField(db_index=True, editable=False, null=True),
        ),
        migrations.RunPython(
            asignar_perfiles_individuales,
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name='cliente',
            name='perfil_compartido_id',
            field=models.UUIDField(db_index=True, default=uuid.uuid4, editable=False),
        ),
    ]
