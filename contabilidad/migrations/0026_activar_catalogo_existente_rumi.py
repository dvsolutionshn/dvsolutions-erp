from django.db import migrations


def activar_catalogo_existente_rumi(apps, schema_editor):
    """Activa únicamente las cuentas que RUMI ya tenga al aplicar la migración."""
    CuentaContable = apps.get_model("contabilidad", "CuentaContable")
    CuentaContable.objects.using(schema_editor.connection.alias).filter(
        empresa__slug="rumi", activa=False,
    ).update(activa=True)


class Migration(migrations.Migration):
    dependencies = [("contabilidad", "0025_catalogo_operativo_rumi")]

    operations = [
        # No se revierte: desconocemos qué cuentas estaban activas antes de
        # aplicarla y desactivarlas supondría alterar decisiones legítimas.
        migrations.RunPython(activar_catalogo_existente_rumi, migrations.RunPython.noop),
    ]
