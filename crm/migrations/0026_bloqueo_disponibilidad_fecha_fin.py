from django.db import migrations, models


def completar_fechas_finales(apps, schema_editor):
    Bloqueo = apps.get_model("crm", "BloqueoDisponibilidadMedica")
    Bloqueo.objects.filter(fecha_fin__isnull=True).update(fecha_fin=models.F("fecha"))


class Migration(migrations.Migration):

    dependencies = [
        ("crm", "0025_bloqueodisponibilidadmedica"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="bloqueodisponibilidadmedica",
            name="crm_bloqueo_horario_valido",
        ),
        migrations.AddField(
            model_name="bloqueodisponibilidadmedica",
            name="fecha_fin",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.RunPython(completar_fechas_finales, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="bloqueodisponibilidadmedica",
            name="fecha_fin",
            field=models.DateField(),
        ),
        migrations.AddIndex(
            model_name="bloqueodisponibilidadmedica",
            index=models.Index(fields=["empresa", "fecha_fin"], name="crm_bloq_empresa_fin"),
        ),
        migrations.AddConstraint(
            model_name="bloqueodisponibilidadmedica",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(dia_completo=True, fecha_fin__gte=models.F("fecha"))
                    | (
                        models.Q(dia_completo=False)
                        & models.Q(hora_inicio__isnull=False)
                        & models.Q(hora_fin__isnull=False)
                        & (
                            models.Q(fecha_fin__gt=models.F("fecha"))
                            | (
                                models.Q(fecha_fin=models.F("fecha"))
                                & models.Q(hora_fin__gt=models.F("hora_inicio"))
                            )
                        )
                    )
                ),
                name="crm_bloqueo_horario_valido",
            ),
        ),
    ]
