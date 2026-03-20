from django.db import models
from django.utils import timezone
from django.db.models import F
from decimal import Decimal
from num2words import num2words

from core.models import Empresa, Usuario


# ==========================
# CAI
# ==========================

class CAI(models.Model):
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE)

    numero_cai = models.CharField(max_length=50)
    establecimiento = models.CharField(max_length=3)
    punto_emision = models.CharField(max_length=3)
    tipo_documento = models.CharField(max_length=2)

    rango_inicial = models.IntegerField()
    rango_final = models.IntegerField()
    correlativo_actual = models.IntegerField(default=0)

    fecha_limite = models.DateField()
    activo = models.BooleanField(default=True)

    fecha_creacion = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.numero_cai} - {self.empresa.nombre}"


# ==========================
# CLIENTE
# ==========================

class Cliente(models.Model):
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE)

    nombre = models.CharField(max_length=200)
    rtn = models.CharField(max_length=20, blank=True, null=True)
    direccion = models.TextField(blank=True, null=True)
    ciudad = models.CharField(max_length=100, blank=True, null=True)

    activo = models.BooleanField(default=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.nombre


# ==========================
# TIPO IMPUESTO
# ==========================

class TipoImpuesto(models.Model):
    nombre = models.CharField(max_length=50)
    porcentaje = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    activo = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.nombre} ({self.porcentaje}%)"


# ==========================
# PRODUCTO
# ==========================

class Producto(models.Model):
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE)

    nombre = models.CharField(max_length=200)
    descripcion = models.TextField(blank=True, null=True)
    precio = models.DecimalField(max_digits=12, decimal_places=2)

    activo = models.BooleanField(default=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.nombre


# ==========================
# FACTURA
# ==========================

class Factura(models.Model):

    ESTADOS = (
        ('borrador', 'Borrador'),
        ('emitida', 'Emitida'),
        ('anulada', 'Anulada'),
    )

    ESTADO_PAGO = (
        ('pendiente', 'Pendiente'),
        ('parcial', 'Parcial'),
        ('pagado', 'Pagado'),
    )

    MONEDAS = (
        ('HNL', 'Lempiras'),
        ('USD', 'Dólares'),
    )

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE)
    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT)

    # NUEVO: vendedor
    vendedor = models.ForeignKey(
        Usuario,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='facturas_vendidas'
    )

    moneda = models.CharField(max_length=3, choices=MONEDAS, default='HNL')
    tipo_cambio = models.DecimalField(max_digits=10, decimal_places=4, default=1)

    fecha_emision = models.DateField(default=timezone.now)
    fecha_vencimiento = models.DateField(blank=True, null=True)

    # CAMPOS FISCALES
    orden_compra_exenta = models.CharField(max_length=100, blank=True, null=True)
    constancia_exonerado = models.CharField(max_length=100, blank=True, null=True)
    registro_sag = models.CharField(max_length=100, blank=True, null=True)

    cai = models.ForeignKey(CAI, on_delete=models.PROTECT, null=True, blank=True)
    numero_factura = models.CharField(max_length=20, blank=True, null=True)

    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    impuesto = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_lempiras = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    estado = models.CharField(max_length=10, choices=ESTADOS, default='borrador')
    estado_pago = models.CharField(max_length=10, choices=ESTADO_PAGO, default='pendiente')

    fecha_creacion = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):

        is_new = self.pk is None

        if is_new and self.estado == 'emitida':
            self._generar_cai()

        if not is_new:
            original = Factura.objects.get(pk=self.pk)
            if original.estado == 'borrador' and self.estado == 'emitida' and not self.numero_factura:
                self._generar_cai()

        super().save(*args, **kwargs)

    def _generar_cai(self):

        hoy = timezone.now().date()

        cai = CAI.objects.filter(
            empresa=self.empresa,
            activo=True,
            fecha_limite__gte=hoy,
            correlativo_actual__lt=F('rango_final')
        ).order_by('-fecha_creacion').first()

        if not cai:
            raise ValueError("No existe CAI vigente disponible.")

        siguiente = cai.correlativo_actual + 1

        self.numero_factura = (
            f"{cai.establecimiento}-"
            f"{cai.punto_emision}-"
            f"{cai.tipo_documento}-"
            f"{str(siguiente).zfill(8)}"
        )

        self.cai = cai
        cai.correlativo_actual = siguiente
        cai.save()

    def calcular_totales(self):

        subtotal_general = 0
        impuesto_general = 0

        for linea in self.lineas.all():
            subtotal_general += linea.subtotal
            impuesto_general += linea.impuesto_monto

        self.subtotal = subtotal_general
        self.impuesto = impuesto_general
        self.total = subtotal_general + impuesto_general

        if self.moneda == 'USD':
            self.total_lempiras = self.total * self.tipo_cambio
        else:
            self.total_lempiras = self.total

    def actualizar_estado_pago(self):

        total_pagado = self.total_pagado

        if total_pagado <= 0:
            self.estado_pago = 'pendiente'
        elif total_pagado < self.total:
            self.estado_pago = 'parcial'
        else:
            self.estado_pago = 'pagado'

        self.save()

    @property
    def total_pagado(self):
        return sum((p.monto for p in self.pagos_facturacion.all()), Decimal('0.00'))

    @property
    def saldo_pendiente(self):
        return self.total - self.total_pagado

    def resumen_fiscal(self):

        resumen = {
            "base_15": 0,
            "base_18": 0,
            "base_exento": 0,
            "base_exonerado": 0,
            "isv_15": 0,
            "isv_18": 0,
        }

        for linea in self.lineas.all():

            nombre = linea.impuesto.nombre.lower()
            porcentaje = float(linea.impuesto.porcentaje)

            if porcentaje == 15:
                resumen["base_15"] += linea.subtotal
                resumen["isv_15"] += linea.impuesto_monto

            elif porcentaje == 18:
                resumen["base_18"] += linea.subtotal
                resumen["isv_18"] += linea.impuesto_monto

            elif porcentaje == 0:
                if "exoner" in nombre:
                    resumen["base_exonerado"] += linea.subtotal
                else:
                    resumen["base_exento"] += linea.subtotal

        return resumen

    def total_en_letras(self):

        total_entero = int(self.total)
        moneda_texto = "LEMPIRAS" if self.moneda == "HNL" else "DÓLARES"
        letras = num2words(total_entero, lang='es').upper()

        return f"SON: {letras} {moneda_texto} EXACTOS"

    def __str__(self):
        return self.numero_factura or "Factura sin número"


# ==========================
# LINEA FACTURA
# ==========================

class LineaFactura(models.Model):

    factura = models.ForeignKey(
        Factura,
        on_delete=models.CASCADE,
        related_name="lineas"
    )

    producto = models.ForeignKey(Producto, on_delete=models.PROTECT)
    cantidad = models.DecimalField(max_digits=10, decimal_places=2)
    precio_unitario = models.DecimalField(max_digits=12, decimal_places=2)

    # NUEVO: comentario por línea
    comentario = models.TextField(blank=True, null=True)

    subtotal = models.DecimalField(max_digits=12, decimal_places=2)
    impuesto = models.ForeignKey(TipoImpuesto, on_delete=models.PROTECT)
    impuesto_monto = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    def save(self, *args, **kwargs):

        self.subtotal = self.cantidad * self.precio_unitario
        self.impuesto_monto = self.subtotal * (self.impuesto.porcentaje / 100)

        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.producto.nombre}"


# ==========================
# PAGOS DE FACTURA
# ==========================

class PagoFactura(models.Model):

    METODOS = (
        ('efectivo', 'Efectivo'),
        ('transferencia', 'Transferencia'),
        ('tarjeta', 'Tarjeta'),
    )

    factura = models.ForeignKey(
        Factura,
        on_delete=models.CASCADE,
        related_name='pagos_facturacion'
    )

    fecha = models.DateField(default=timezone.now)
    monto = models.DecimalField(max_digits=12, decimal_places=2)

    metodo = models.CharField(
        max_length=20,
        choices=METODOS,
        default='efectivo'
    )

    referencia = models.CharField(max_length=100, blank=True, null=True)

    fecha_creacion = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self.factura.actualizar_estado_pago()

    def __str__(self):
        return f"Pago {self.monto} - Factura {self.factura.numero_factura}"