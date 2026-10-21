from decimal import Decimal

from django.db import models
from django.utils import timezone


class PuestoMero(models.Model):
    nombre = models.CharField(max_length=120)
    slug = models.SlugField(unique=True)
    descripcion = models.CharField(max_length=240, blank=True)
    activo = models.BooleanField(default=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre


class ProductoMero(models.Model):
    puesto = models.ForeignKey(PuestoMero, on_delete=models.CASCADE, related_name="productos")
    nombre = models.CharField(max_length=80)
    emoji = models.CharField(max_length=12, blank=True)
    precio = models.DecimalField(max_digits=10, decimal_places=2)
    activo = models.BooleanField(default=True)
    orden = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["orden", "nombre"]

    def __str__(self):
        return f"{self.puesto}: {self.nombre}"


class PedidoMero(models.Model):
    TIPOS_ATENCION = [
        ("carro", "Carro"),
        ("fila", "Persona en fila"),
    ]
    ESTADOS = [
        ("nuevo", "Nuevo"),
        ("preparando", "Preparando"),
        ("listo", "Listo"),
        ("entregado", "Entregado"),
        ("cancelado", "Cancelado"),
    ]
    METODOS_PAGO = [
        ("pendiente", "Pendiente"),
        ("efectivo", "Efectivo"),
        ("transferencia", "Transferencia"),
        ("tarjeta", "Tarjeta / QR"),
    ]

    puesto = models.ForeignKey(PuestoMero, on_delete=models.CASCADE, related_name="pedidos")
    numero = models.PositiveIntegerField()
    tipo_atencion = models.CharField(max_length=12, choices=TIPOS_ATENCION, default="carro")
    referencia_vehiculo = models.CharField(max_length=80, blank=True)
    nota = models.CharField(max_length=240, blank=True)
    estado = models.CharField(max_length=16, choices=ESTADOS, default="nuevo")
    metodo_pago = models.CharField(max_length=16, choices=METODOS_PAGO, default="pendiente")
    total = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-fecha_creacion"]
        indexes = [
            models.Index(
                fields=["puesto", "fecha_creacion"],
                name="mero_pedido_puesto__e6985c_idx",
            )
        ]

    @property
    def color(self):
        colores = ["Coral", "Mango", "Lima", "Cielo", "Uva", "Rosa"]
        return colores[(self.numero - 1) % len(colores)]

    def __str__(self):
        return f"Pedido {self.numero} - {self.puesto}"


class LineaPedidoMero(models.Model):
    pedido = models.ForeignKey(PedidoMero, on_delete=models.CASCADE, related_name="lineas")
    producto = models.ForeignKey(ProductoMero, on_delete=models.PROTECT)
    cantidad = models.PositiveIntegerField(default=1)
    precio_unitario = models.DecimalField(max_digits=10, decimal_places=2)

    @property
    def subtotal(self):
        return self.precio_unitario * self.cantidad

    def __str__(self):
        return f"{self.cantidad} x {self.producto.nombre}"
