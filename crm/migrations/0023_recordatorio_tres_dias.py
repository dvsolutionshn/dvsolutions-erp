from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("crm", "0022_remove_sesionterapiapostquirurgica_crm_terapia_post_programa_sesion_uniq_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="citacliente",
            name="recordatorio_tres_dias_whatsapp",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="configuracioncrm",
            name="mensaje_cita_recordatorio_3_dias",
            field=models.TextField(default="recordatorio: faltan tres dias"),
        ),
        migrations.AlterField(
            model_name="notificacioncitawhatsapp",
            name="tipo",
            field=models.CharField(
                choices=[
                    ("confirmacion", "Confirmación al crear"),
                    ("semana", "Recordatorio 5 días antes"),
                    ("tres_dias", "Recordatorio 3 días antes"),
                    ("dia", "Recordatorio 1 día antes"),
                ],
                max_length=20,
            ),
        ),
    ]
