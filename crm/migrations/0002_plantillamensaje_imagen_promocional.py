from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("crm", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="plantillamensaje",
            name="imagen_promocional",
            field=models.ImageField(blank=True, null=True, upload_to="crm/promociones/"),
        ),
    ]
