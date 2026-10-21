from decimal import Decimal

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [
        migrations.CreateModel(
            name="PuestoMero",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("nombre", models.CharField(max_length=120)),
                ("slug", models.SlugField(unique=True)),
                ("descripcion", models.CharField(blank=True, max_length=240)),
                ("activo", models.BooleanField(default=True)),
                ("fecha_creacion", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["nombre"]},
        ),
        migrations.CreateModel(
            name="ProductoMero",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("nombre", models.CharField(max_length=80)),
                ("emoji", models.CharField(blank=True, max_length=12)),
                ("precio", models.DecimalField(decimal_places=2, max_digits=10)),
                ("activo", models.BooleanField(default=True)),
                ("orden", models.PositiveSmallIntegerField(default=0)),
                ("puesto", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="productos", to="mero.puestomero")),
            ],
            options={"ordering": ["orden", "nombre"]},
        ),
        migrations.CreateModel(
            name="PedidoMero",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("numero", models.PositiveIntegerField()),
                ("tipo_atencion", models.CharField(choices=[("carro", "Carro"), ("fila", "Persona en fila")], default="carro", max_length=12)),
                ("referencia_vehiculo", models.CharField(blank=True, max_length=80)),
                ("nota", models.CharField(blank=True, max_length=240)),
                ("estado", models.CharField(choices=[("nuevo", "Nuevo"), ("preparando", "Preparando"), ("listo", "Listo"), ("entregado", "Entregado"), ("cancelado", "Cancelado")], default="nuevo", max_length=16)),
                ("metodo_pago", models.CharField(choices=[("pendiente", "Pendiente"), ("efectivo", "Efectivo"), ("transferencia", "Transferencia"), ("tarjeta", "Tarjeta / QR")], default="pendiente", max_length=16)),
                ("total", models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=10)),
                ("fecha_creacion", models.DateTimeField(auto_now_add=True)),
                ("fecha_actualizacion", models.DateTimeField(auto_now=True)),
                ("puesto", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="pedidos", to="mero.puestomero")),
            ],
            options={"ordering": ["-fecha_creacion"]},
        ),
        migrations.CreateModel(
            name="LineaPedidoMero",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("cantidad", models.PositiveIntegerField(default=1)),
                ("precio_unitario", models.DecimalField(decimal_places=2, max_digits=10)),
                ("pedido", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="lineas", to="mero.pedidomero")),
                ("producto", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="mero.productomero")),
            ],
        ),
        migrations.AddIndex(
            model_name="pedidomero",
            index=models.Index(fields=["puesto", "fecha_creacion"], name="mero_pedido_puesto__e6985c_idx"),
        ),
    ]
