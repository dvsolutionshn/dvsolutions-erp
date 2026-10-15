from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("contabilidad", "0023_revision_conversion_asiento")]
    operations = [migrations.AlterField(
        model_name="cuentacontable", name="tipo",
        field=models.CharField(max_length=20, choices=[
            ("activo", "Activo"), ("pasivo", "Pasivo"), ("patrimonio", "Patrimonio"),
            ("ingreso", "Ingreso"), ("costo", "Costo"), ("gasto", "Gasto"),
            ("orden", "Control extracontable"),
        ]),
    )]
