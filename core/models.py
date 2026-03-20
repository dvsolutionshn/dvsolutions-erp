from django.db import models
from django.contrib.auth.models import AbstractUser


from django.db import models


from django.db import models


class Empresa(models.Model):

    nombre = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)

    rtn = models.CharField(max_length=20, unique=True)

    logo = models.ImageField(upload_to='logos/', blank=True, null=True)

    # =========================
    # DATOS EMPRESARIALES
    # =========================

    direccion = models.TextField(blank=True, null=True)
    ciudad = models.CharField(max_length=100, blank=True, null=True)
    departamento = models.CharField(max_length=100, blank=True, null=True)
    pais = models.CharField(max_length=100, default="Honduras")

    telefono = models.CharField(max_length=30, blank=True, null=True)
    correo = models.EmailField(blank=True, null=True)
    sitio_web = models.CharField(max_length=200, blank=True, null=True)

    slogan = models.CharField(max_length=200, blank=True, null=True)

    # =========================
    # CONFIGURACIONES
    # =========================

    condiciones_pago = models.CharField(
        max_length=200,
        blank=True,
        null=True,
        default="Pago inmediato"
    )

    activa = models.BooleanField(default=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['nombre']
        verbose_name = "Empresa"
        verbose_name_plural = "Empresas"

    def __str__(self):
        return f"{self.nombre} ({self.rtn})"


class Usuario(AbstractUser):
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, null=True, blank=True)
    es_administrador_empresa = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.username} - {self.empresa.nombre if self.empresa else 'Sin empresa'}"
    
class Modulo(models.Model):
    nombre = models.CharField(max_length=100)
    codigo = models.CharField(max_length=50, unique=True)

    def __str__(self):
        return self.nombre


class EmpresaModulo(models.Model):
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE)
    modulo = models.ForeignKey(Modulo, on_delete=models.CASCADE)
    activo = models.BooleanField(default=True)

    class Meta:
        unique_together = ('empresa', 'modulo')

    def __str__(self):
        return f"{self.empresa.nombre} - {self.modulo.nombre}"    
    
# ============================================
# PAGOS - CUENTAS POR COBRAR
# ============================================

class Pago(models.Model):

    factura = models.ForeignKey(
        'facturacion.Factura',
        on_delete=models.CASCADE,
        related_name='pagos_factura'
    )

    fecha_pago = models.DateField()

    monto = models.DecimalField(
        max_digits=14,
        decimal_places=2
    )

    metodo = models.CharField(
        max_length=50,
        choices=[
            ('efectivo', 'Efectivo'),
            ('transferencia', 'Transferencia'),
            ('cheque', 'Cheque'),
            ('tarjeta', 'Tarjeta'),
        ]
    )

    referencia = models.CharField(
        max_length=100,
        blank=True,
        null=True
    )

    creado = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Pago {self.factura.numero_factura} - {self.monto}"