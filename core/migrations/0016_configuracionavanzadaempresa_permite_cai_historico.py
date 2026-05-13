from django.db import migrations, models


def habilitar_cai_historico_amkt_digital(apps, schema_editor):
    Empresa = apps.get_model("core", "Empresa")
    ConfiguracionAvanzadaEmpresa = apps.get_model("core", "ConfiguracionAvanzadaEmpresa")

    empresa = Empresa.objects.filter(slug="amkt-digital").first()
    if not empresa:
        return

    configuracion, _ = ConfiguracionAvanzadaEmpresa.objects.get_or_create(empresa=empresa)
    if not configuracion.permite_cai_historico:
        configuracion.permite_cai_historico = True
        configuracion.save(update_fields=["permite_cai_historico"])


def deshacer_cai_historico_amkt_digital(apps, schema_editor):
    Empresa = apps.get_model("core", "Empresa")
    ConfiguracionAvanzadaEmpresa = apps.get_model("core", "ConfiguracionAvanzadaEmpresa")

    empresa = Empresa.objects.filter(slug="amkt-digital").first()
    if not empresa:
        return

    configuracion = ConfiguracionAvanzadaEmpresa.objects.filter(empresa=empresa).first()
    if configuracion and configuracion.permite_cai_historico:
        configuracion.permite_cai_historico = False
        configuracion.save(update_fields=["permite_cai_historico"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0015_configuracionavanzadaempresa"),
    ]

    operations = [
        migrations.AddField(
            model_name="configuracionavanzadaempresa",
            name="permite_cai_historico",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(
            habilitar_cai_historico_amkt_digital,
            deshacer_cai_historico_amkt_digital,
        ),
    ]
