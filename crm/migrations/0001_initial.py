from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("core", "0013_crm_permissions_and_module"),
        ("facturacion", "0030_cliente_crm_fields_producto_fecha_fields"),
    ]

    operations = [
        migrations.CreateModel(
            name="ConfiguracionCRM",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("whatsapp_activo", models.BooleanField(default=False)),
                ("whatsapp_phone_number_id", models.CharField(blank=True, max_length=120, null=True)),
                ("whatsapp_business_account_id", models.CharField(blank=True, max_length=120, null=True)),
                ("whatsapp_token", models.TextField(blank=True, null=True)),
                ("remitente_correo", models.EmailField(blank=True, max_length=254, null=True)),
                ("recordatorio_cumpleanos_activo", models.BooleanField(default=True)),
                ("recordatorio_citas_activo", models.BooleanField(default=True)),
                ("dias_alerta_producto", models.PositiveIntegerField(default=7)),
                ("empresa", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="configuracion_crm", to="core.empresa")),
            ],
            options={"verbose_name": "Configuracion CRM", "verbose_name_plural": "Configuraciones CRM"},
        ),
        migrations.CreateModel(
            name="PlantillaMensaje",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("nombre", models.CharField(max_length=150)),
                ("tipo", models.CharField(choices=[("promocion", "Promocion"), ("cumpleanos", "Cumpleanos"), ("cita", "Cita"), ("general", "General")], default="general", max_length=20)),
                ("canal", models.CharField(choices=[("whatsapp", "WhatsApp"), ("correo", "Correo"), ("ambos", "WhatsApp y correo")], default="whatsapp", max_length=20)),
                ("asunto", models.CharField(blank=True, max_length=180, null=True)),
                ("mensaje", models.TextField(help_text="Puedes usar: {{cliente}}, {{empresa}}, {{fecha}}, {{producto}}.")),
                ("activa", models.BooleanField(default=True)),
                ("fecha_creacion", models.DateTimeField(auto_now_add=True)),
                ("empresa", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="plantillas_crm", to="core.empresa")),
            ],
            options={"ordering": ["nombre"]},
        ),
        migrations.CreateModel(
            name="CampaniaMarketing",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("nombre", models.CharField(max_length=180)),
                ("audiencia", models.CharField(choices=[("todos", "Todos los clientes activos"), ("promociones", "Clientes que aceptan promociones"), ("cumpleanos", "Clientes con cumpleanos proximos")], default="promociones", max_length=20)),
                ("fecha_programada", models.DateTimeField(blank=True, null=True)),
                ("estado", models.CharField(choices=[("borrador", "Borrador"), ("programada", "Programada"), ("enviada", "Enviada"), ("cancelada", "Cancelada")], default="borrador", max_length=20)),
                ("fecha_creacion", models.DateTimeField(auto_now_add=True)),
                ("creado_por", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ("empresa", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="campanias_marketing", to="core.empresa")),
                ("plantilla", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to="crm.plantillamensaje")),
            ],
            options={"ordering": ["-fecha_creacion"]},
        ),
        migrations.CreateModel(
            name="CitaCliente",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("titulo", models.CharField(max_length=180)),
                ("fecha_hora", models.DateTimeField()),
                ("responsable", models.CharField(blank=True, max_length=120, null=True)),
                ("estado", models.CharField(choices=[("pendiente", "Pendiente"), ("confirmada", "Confirmada"), ("realizada", "Realizada"), ("cancelada", "Cancelada")], default="pendiente", max_length=20)),
                ("observacion", models.TextField(blank=True, null=True)),
                ("fecha_creacion", models.DateTimeField(auto_now_add=True)),
                ("cliente", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="citas", to="facturacion.cliente")),
                ("empresa", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="citas_clientes", to="core.empresa")),
                ("producto", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="citas", to="facturacion.producto")),
            ],
            options={"ordering": ["fecha_hora"]},
        ),
        migrations.CreateModel(
            name="EnvioCampania",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("canal", models.CharField(default="whatsapp", max_length=20)),
                ("mensaje", models.TextField()),
                ("estado", models.CharField(choices=[("pendiente", "Pendiente"), ("preparado", "Preparado"), ("enviado", "Enviado"), ("error", "Error")], default="pendiente", max_length=20)),
                ("respuesta", models.TextField(blank=True, null=True)),
                ("fecha_envio", models.DateTimeField(blank=True, null=True)),
                ("campania", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="envios", to="crm.campaniamarketing")),
                ("cliente", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="envios_crm", to="facturacion.cliente")),
            ],
            options={"ordering": ["cliente__nombre"], "unique_together": {("campania", "cliente", "canal")}},
        ),
    ]
