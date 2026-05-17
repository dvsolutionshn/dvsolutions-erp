from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0019_solicitudcomercial"),
    ]

    operations = [
        migrations.AddField(
            model_name="solicitudcomercial",
            name="rtn_empresa",
            field=models.CharField(blank=True, max_length=20, null=True),
        ),
    ]
