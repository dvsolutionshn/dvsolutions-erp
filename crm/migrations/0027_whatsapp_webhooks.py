from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("crm", "0026_bloqueo_disponibilidad_fecha_fin"),
    ]

    operations = [
        migrations.AddField(
            model_name="configuracioncrm", name="whatsapp_pendiente_validacion",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="configuracioncrm", name="whatsapp_prueba_message_id",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="configuracioncrm", name="whatsapp_validado_en",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="MensajeWhatsAppWebhook",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("message_id", models.CharField(max_length=255)),
                ("direccion", models.CharField(choices=[("entrante", "Entrante"), ("saliente", "Saliente")], max_length=10)),
                ("telefono_cliente", models.CharField(blank=True, max_length=40)),
                ("phone_number_id", models.CharField(max_length=120)),
                ("cuenta_id", models.CharField(max_length=120)),
                ("tipo", models.CharField(blank=True, max_length=30)),
                ("texto", models.TextField(blank=True)),
                ("estado", models.CharField(choices=[("received", "Recibido"), ("sent", "Enviado a WhatsApp"), ("delivered", "Entregado"), ("read", "Leído"), ("failed", "No entregado")], max_length=15)),
                ("error_codigo", models.CharField(blank=True, max_length=80)),
                ("fecha_meta", models.DateTimeField(blank=True, null=True)),
                ("estado_actualizado_en", models.DateTimeField(blank=True, null=True)),
                ("fecha_creacion", models.DateTimeField(auto_now_add=True)),
                ("empresa", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="mensajes_whatsapp_webhook", to="core.empresa")),
            ],
            options={
                "ordering": ["-fecha_meta", "-id"],
                "indexes": [models.Index(fields=["empresa", "direccion", "fecha_creacion"], name="crm_wa_empresa_inbox_idx")],
                "constraints": [models.UniqueConstraint(fields=("empresa", "message_id"), name="crm_wa_message_empresa_uniq")],
            },
        ),
    ]
