from datetime import timedelta
import re
from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import F
from django.utils import timezone
from num2words import num2words

from core.models import ConfiguracionAvanzadaEmpresa, Empresa, Usuario

DOS_DECIMALES = Decimal("0.01")


def _monto_en_letras_con_centavos(total, moneda_codigo):
    total = Decimal(total or 0).quantize(DOS_DECIMALES)
    entero = int(total)
    centavos = int((total - Decimal(entero)) * 100)
    moneda_texto = "LEMPIRAS" if moneda_codigo == "HNL" else "DOLARES"
    letras = num2words(entero, lang='es').upper()
    return f"SON: {letras} {moneda_texto} CON {centavos:02d}/100"


class ConfiguracionFacturacionEmpresa(models.Model):
    PLANTILLAS_FACTURA = (
        ('normal', 'Factura clasica'),
        ('alternativa', 'Factura alternativa'),
        ('notas_extensas', 'Factura notas extensas'),
        ('independiente', 'Factura independiente'),
    )

    empresa = models.OneToOneField(Empresa, on_delete=models.CASCADE, related_name='configuracion_facturacion')
    plantilla_factura_pdf = models.CharField(max_length=20, choices=PLANTILLAS_FACTURA, default='normal')
    nombre_comercial_documentos = models.CharField(max_length=200, blank=True, null=True)
    color_primario = models.CharField(max_length=20, default='#334155')
    color_secundario = models.CharField(max_length=20, default='#94a3b8')
    logo_ancho_pdf = models.PositiveIntegerField(
        default=110,
        validators=[MinValueValidator(40), MaxValueValidator(260)],
    )
    logo_alto_pdf = models.PositiveIntegerField(
        default=60,
        validators=[MinValueValidator(30), MaxValueValidator(160)],
    )
    mostrar_vendedor = models.BooleanField(default=False)
    mostrar_descuentos = models.BooleanField(default=True)
    mostrar_notas_linea = models.BooleanField(default=True)
    leyenda_factura = models.CharField(max_length=255, blank=True, null=True)
    pie_factura = models.TextField(blank=True, null=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Configuracion de facturacion por empresa'
        verbose_name_plural = 'Configuraciones de facturacion por empresa'

    def __str__(self):
        return f"Configuracion Facturacion - {self.empresa.nombre}"


# ==========================
# CAI
# ==========================

class CAI(models.Model):
    USOS_DOCUMENTO = (
        ('factura', 'Factura'),
        ('nota_credito', 'Nota de Credito'),
    )

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE)

    numero_cai = models.CharField(max_length=50)
    uso_documento = models.CharField(max_length=20, choices=USOS_DOCUMENTO, default='factura')
    establecimiento = models.CharField(max_length=3)
    punto_emision = models.CharField(max_length=3)
    tipo_documento = models.CharField(max_length=2)

    rango_inicial = models.IntegerField()
    rango_final = models.IntegerField()
    correlativo_actual = models.IntegerField(default=0)

    fecha_activacion = models.DateField(default=timezone.localdate)
    fecha_limite = models.DateField()
    activo = models.BooleanField(default=True)

    fecha_creacion = models.DateTimeField(auto_now_add=True)

    def clean(self):
        super().clean()
        if self.fecha_activacion and self.fecha_limite and self.fecha_activacion > self.fecha_limite:
            raise ValidationError("La fecha de activacion no puede ser mayor que la fecha de vencimiento del CAI.")
        if not self.pk:
            return

        original = CAI.objects.get(pk=self.pk)
        campos_bloqueados = [
            'numero_cai',
            'uso_documento',
            'establecimiento',
            'punto_emision',
            'tipo_documento',
            'rango_inicial',
            'rango_final',
            'fecha_activacion',
            'fecha_limite',
            'empresa_id',
        ]

        cai_ya_usado = (
            self.factura_set.filter(estado='emitida').exists() or
            self.notacredito_set.filter(estado='emitida').exists()
        )

        config_avanzada = ConfiguracionAvanzadaEmpresa.para_empresa(self.empresa)

        if cai_ya_usado and not config_avanzada.permite_gestion_fiscal_historica:
            cambios = [
                campo for campo in campos_bloqueados
                if getattr(original, campo) != getattr(self, campo)
            ]
            if cambios:
                raise ValidationError(
                    "No se pueden cambiar los datos fiscales de un CAI que ya fue usado en documentos emitidos."
                )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.numero_cai} - {self.empresa.nombre}"


# ==========================
# CLIENTE
# ==========================

class Cliente(models.Model):
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE)

    nombre = models.CharField(max_length=200)
    rtn = models.CharField(max_length=20, blank=True, null=True)
    correo = models.EmailField(blank=True, null=True)
    telefono = models.CharField(max_length=30, blank=True, null=True)
    telefono_whatsapp = models.CharField(max_length=30, blank=True, null=True)
    fecha_nacimiento = models.DateField(blank=True, null=True)
    acepta_promociones = models.BooleanField(default=False)
    canal_preferido = models.CharField(
        max_length=20,
        choices=[
            ("whatsapp", "WhatsApp"),
            ("correo", "Correo"),
            ("telefono", "Telefono"),
        ],
        default="whatsapp",
    )
    direccion = models.TextField(blank=True, null=True)
    ciudad = models.CharField(max_length=100, blank=True, null=True)

    activo = models.BooleanField(default=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    def clean(self):
        super().clean()
        if self.empresa_id and self.nombre:
            nombre_normalizado = self.nombre.strip()
            existe_nombre = Cliente.objects.filter(
                empresa=self.empresa,
                nombre__iexact=nombre_normalizado,
            ).exclude(pk=self.pk)
            if existe_nombre.exists():
                raise ValidationError({
                    "nombre": "Ya existe un cliente con este nombre en la empresa."
                })

        if self.empresa_id and self.rtn:
            rtn_normalizado = self.rtn.strip()
            existe_rtn = Cliente.objects.filter(
                empresa=self.empresa,
                rtn__iexact=rtn_normalizado,
            ).exclude(pk=self.pk)
            if existe_rtn.exists():
                raise ValidationError({
                    "rtn": "Ya existe un cliente con este RTN en la empresa."
                })
            self.rtn = rtn_normalizado

        if self.nombre:
            self.nombre = self.nombre.strip()

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

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
    TIPOS_ITEM = (
        ('producto', 'Producto'),
        ('servicio', 'Servicio'),
    )

    UNIDADES_MEDIDA = (
        ('unidad', 'Unidad'),
        ('caja', 'Caja'),
        ('paquete', 'Paquete'),
        ('libra', 'Libra'),
        ('kilogramo', 'Kilogramo'),
        ('litro', 'Litro'),
        ('galon', 'Galon'),
        ('hora', 'Hora'),
        ('dia', 'Dia'),
        ('mes', 'Mes'),
        ('servicio', 'Servicio'),
    )

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE)

    nombre = models.CharField(max_length=200)
    codigo = models.CharField(max_length=50, blank=True, null=True)
    tipo_item = models.CharField(max_length=15, choices=TIPOS_ITEM, default='producto')
    unidad_medida = models.CharField(max_length=20, choices=UNIDADES_MEDIDA, default='unidad')
    descripcion = models.TextField(blank=True, null=True)
    precio = models.DecimalField(max_digits=12, decimal_places=2)
    fecha_referencia = models.DateField(blank=True, null=True)
    fecha_alerta = models.DateField(blank=True, null=True)
    nota_fecha = models.CharField(max_length=200, blank=True, null=True)
    controla_inventario = models.BooleanField(default=True)
    impuesto_predeterminado = models.ForeignKey(
        TipoImpuesto,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='productos_predeterminados'
    )

    activo = models.BooleanField(default=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    def clean(self):
        super().clean()

        if self.codigo and self.empresa_id:
            existe_codigo = Producto.objects.filter(
                empresa=self.empresa,
                codigo__iexact=self.codigo
            ).exclude(pk=self.pk)
            if existe_codigo.exists():
                raise ValidationError({
                    'codigo': 'Ya existe un producto o servicio con este codigo en la empresa.'
                })

        if self.tipo_item == 'servicio' and self.controla_inventario:
            raise ValidationError({
                'controla_inventario': 'Un servicio no debe controlar inventario.'
            })

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.nombre

    @property
    def stock_actual(self):
        if not self.controla_inventario:
            return Decimal('0.00')
        inventario = getattr(self, 'inventario', None)
        return inventario.existencias if inventario else Decimal('0.00')


class CategoriaProductoFarmaceutico(models.Model):
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name='categorias_farmaceuticas')
    nombre = models.CharField(max_length=120)
    descripcion = models.TextField(blank=True, null=True)
    requiere_receta_default = models.BooleanField(default=False)
    requiere_refrigeracion_default = models.BooleanField(default=False)
    producto_controlado_default = models.BooleanField(default=False)
    activa = models.BooleanField(default=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['nombre']
        unique_together = ('empresa', 'nombre')

    def __str__(self):
        return self.nombre


class PerfilFarmaceuticoProducto(models.Model):
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name='perfiles_farmaceuticos_producto')
    producto = models.OneToOneField(Producto, on_delete=models.CASCADE, related_name='perfil_farmaceutico')
    categoria = models.ForeignKey(
        CategoriaProductoFarmaceutico,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='productos',
    )
    principio_activo = models.CharField(max_length=160, blank=True, null=True)
    presentacion = models.CharField(max_length=160, blank=True, null=True)
    concentracion = models.CharField(max_length=120, blank=True, null=True)
    laboratorio = models.CharField(max_length=160, blank=True, null=True)
    registro_sanitario = models.CharField(max_length=120, blank=True, null=True)
    requiere_receta = models.BooleanField(default=False)
    requiere_refrigeracion = models.BooleanField(default=False)
    producto_controlado = models.BooleanField(default=False)
    alerta_vencimiento_dias = models.PositiveIntegerField(default=60)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['producto__nombre']

    def clean(self):
        super().clean()
        if self.producto_id and self.producto.empresa_id != self.empresa_id:
            raise ValidationError("El producto farmaceutico debe pertenecer a la misma empresa.")
        if self.categoria_id and self.categoria.empresa_id != self.empresa_id:
            raise ValidationError("La categoria farmaceutica debe pertenecer a la misma empresa.")

    def __str__(self):
        return f"Perfil farmaceutico - {self.producto.nombre}"


class Proveedor(models.Model):
    CONDICIONES_PAGO = (
        ('contado', 'Contado'),
        ('credito', 'Credito'),
    )

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE)

    nombre = models.CharField(max_length=200)
    rtn = models.CharField(max_length=20, blank=True, null=True)
    contacto = models.CharField(max_length=150, blank=True, null=True)
    telefono = models.CharField(max_length=50, blank=True, null=True)
    correo = models.EmailField(blank=True, null=True)
    direccion = models.TextField(blank=True, null=True)
    ciudad = models.CharField(max_length=100, blank=True, null=True)
    condicion_pago = models.CharField(max_length=20, choices=CONDICIONES_PAGO, default='contado')
    dias_credito = models.PositiveIntegerField(default=0)

    activo = models.BooleanField(default=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    def clean(self):
        super().clean()
        if self.condicion_pago == 'contado':
            self.dias_credito = 0

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.nombre


class InventarioProducto(models.Model):
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE)
    producto = models.OneToOneField(
        Producto,
        on_delete=models.CASCADE,
        related_name='inventario'
    )
    existencias = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    stock_minimo = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Inventario {self.producto.nombre}"


class BodegaInventario(models.Model):
    TIPOS = (
        ('principal', 'Bodega principal'),
        ('provisional', 'Bodega provisional'),
        ('vitrina', 'Vitrina'),
        ('otra', 'Otra'),
    )

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name='bodegas_inventario')
    nombre = models.CharField(max_length=120)
    tipo = models.CharField(max_length=20, choices=TIPOS, default='otra')
    activa = models.BooleanField(default=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['tipo', 'nombre']
        unique_together = ('empresa', 'nombre')

    def __str__(self):
        return self.nombre


class LoteInventario(models.Model):
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name='lotes_inventario')
    producto = models.ForeignKey(Producto, on_delete=models.CASCADE, related_name='lotes_inventario')
    numero_lote = models.CharField(max_length=80)
    fecha_vencimiento = models.DateField(blank=True, null=True)
    proveedor = models.ForeignKey(Proveedor, on_delete=models.SET_NULL, blank=True, null=True, related_name='lotes_inventario')
    activo = models.BooleanField(default=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['fecha_vencimiento', 'producto__nombre', 'numero_lote']
        unique_together = ('empresa', 'producto', 'numero_lote')

    @property
    def dias_para_vencer(self):
        if not self.fecha_vencimiento:
            return None
        return (self.fecha_vencimiento - timezone.localdate()).days

    @property
    def vencido(self):
        dias = self.dias_para_vencer
        return dias is not None and dias < 0

    @property
    def por_vencer(self):
        dias = self.dias_para_vencer
        return dias is not None and 0 <= dias <= 60

    def __str__(self):
        return f"{self.producto.nombre} - {self.numero_lote}"


class ExistenciaLoteBodega(models.Model):
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name='existencias_lote_bodega')
    bodega = models.ForeignKey(BodegaInventario, on_delete=models.CASCADE, related_name='existencias')
    lote = models.ForeignKey(LoteInventario, on_delete=models.CASCADE, related_name='existencias_bodega')
    cantidad = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['bodega__nombre', 'lote__producto__nombre', 'lote__fecha_vencimiento']
        unique_together = ('bodega', 'lote')

    def clean(self):
        super().clean()
        if self.bodega_id and self.lote_id and self.bodega.empresa_id != self.lote.empresa_id:
            raise ValidationError("La bodega y el lote deben pertenecer a la misma empresa.")

    def __str__(self):
        return f"{self.bodega} / {self.lote}: {self.cantidad}"


class MovimientoLoteBodega(models.Model):
    TIPOS = (
        ('entrada', 'Entrada'),
        ('salida_factura', 'Salida por factura'),
        ('traslado_salida', 'Traslado salida'),
        ('traslado_entrada', 'Traslado entrada'),
        ('ajuste', 'Ajuste'),
        ('reversion', 'Reversion'),
    )

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name='movimientos_lote_bodega')
    bodega = models.ForeignKey(BodegaInventario, on_delete=models.PROTECT, related_name='movimientos')
    lote = models.ForeignKey(LoteInventario, on_delete=models.PROTECT, related_name='movimientos_bodega')
    tipo = models.CharField(max_length=30, choices=TIPOS)
    cantidad = models.DecimalField(max_digits=12, decimal_places=2)
    existencia_anterior = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    existencia_resultante = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    referencia = models.CharField(max_length=140, blank=True, null=True)
    observacion = models.TextField(blank=True, null=True)
    factura = models.ForeignKey('Factura', on_delete=models.SET_NULL, null=True, blank=True, related_name='movimientos_lotes')
    fecha = models.DateTimeField(default=timezone.now)
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha', '-id']

    def __str__(self):
        return f"{self.get_tipo_display()} - {self.lote} - {self.cantidad}"

class MovimientoInventario(models.Model):
    TIPOS = (
        ('entrada', 'Entrada'),
        ('entrada_compra', 'Entrada por Compra'),
        ('salida_factura', 'Salida por Factura'),
        ('devolucion_nota_credito', 'Entrada por Nota de Credito'),
        ('ajuste_entrada', 'Ajuste Positivo'),
        ('ajuste_salida', 'Ajuste Negativo'),
        ('reversion_factura', 'Reversion de Factura'),
        ('reversion_nota_credito', 'Reversion de Nota de Credito'),
        ('reversion_compra', 'Reversion de Compra'),
    )

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE)
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT, related_name='movimientos_inventario')
    tipo = models.CharField(max_length=30, choices=TIPOS)
    cantidad = models.DecimalField(max_digits=12, decimal_places=2)
    existencia_anterior = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    existencia_resultante = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    referencia = models.CharField(max_length=120, blank=True, null=True)
    observacion = models.TextField(blank=True, null=True)
    factura = models.ForeignKey('Factura', on_delete=models.SET_NULL, null=True, blank=True)
    nota_credito = models.ForeignKey('NotaCredito', on_delete=models.SET_NULL, null=True, blank=True)
    entrada_documento = models.ForeignKey('EntradaInventarioDocumento', on_delete=models.SET_NULL, null=True, blank=True)
    compra_documento = models.ForeignKey('CompraInventario', on_delete=models.SET_NULL, null=True, blank=True)
    fecha = models.DateTimeField(default=timezone.now)
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha', '-id']

    def __str__(self):
        return f"{self.get_tipo_display()} - {self.producto.nombre} - {self.cantidad}"


class EntradaInventarioDocumento(models.Model):
    ESTADOS = (
        ('borrador', 'Borrador'),
        ('aplicada', 'Aplicada'),
    )

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE)
    referencia = models.CharField(max_length=120)
    fecha_documento = models.DateField(default=timezone.now)
    observacion = models.TextField(blank=True, null=True)
    estado = models.CharField(max_length=15, choices=ESTADOS, default='borrador')
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha_documento', '-id']

    def __str__(self):
        return self.referencia

    @property
    def total_unidades(self):
        return sum((linea.cantidad for linea in self.lineas.all()), Decimal('0.00'))


class LineaEntradaInventario(models.Model):
    entrada = models.ForeignKey(
        EntradaInventarioDocumento,
        on_delete=models.CASCADE,
        related_name='lineas'
    )
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT)
    cantidad = models.DecimalField(max_digits=12, decimal_places=2)
    comentario = models.CharField(max_length=150, blank=True, null=True)

    def __str__(self):
        return f"{self.producto.nombre} - {self.cantidad}"


class CompraInventario(models.Model):
    CONDICIONES_PAGO = Proveedor.CONDICIONES_PAGO

    ESTADOS = (
        ('borrador', 'Borrador'),
        ('aplicada', 'Aplicada'),
        ('anulada', 'Anulada'),
    )

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE)
    numero_compra = models.CharField(max_length=20, blank=True, null=True, unique=True)
    proveedor = models.ForeignKey('Proveedor', on_delete=models.PROTECT, null=True, blank=True)
    proveedor_nombre = models.CharField(max_length=200)
    referencia_documento = models.CharField(max_length=120, blank=True, null=True)
    fecha_documento = models.DateField(default=timezone.now)
    condicion_pago = models.CharField(max_length=20, choices=CONDICIONES_PAGO, default='contado')
    dias_credito = models.PositiveIntegerField(default=0)
    fecha_vencimiento = models.DateField(blank=True, null=True)
    observacion = models.TextField(blank=True, null=True)
    estado = models.CharField(max_length=15, choices=ESTADOS, default='borrador')
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha_documento', '-id']

    def clean(self):
        super().clean()
        if self.condicion_pago == 'contado':
            self.dias_credito = 0
            if self.fecha_documento:
                self.fecha_vencimiento = self.fecha_documento
        elif self.fecha_documento and self.fecha_vencimiento is None:
            self.fecha_vencimiento = self.fecha_documento + timedelta(days=self.dias_credito or 0)

    def save(self, *args, **kwargs):
        if not self.numero_compra:
            ultima = CompraInventario.objects.filter(empresa=self.empresa).order_by('-id').first()
            siguiente = 1 if not ultima else ultima.id + 1
            self.numero_compra = f"COM-{str(siguiente).zfill(8)}"
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.numero_compra or f"Compra {self.pk}"

    @property
    def total_unidades(self):
        return sum((linea.cantidad for linea in self.lineas.all()), Decimal('0.00'))

    @property
    def total_documento(self):
        return sum((linea.total_linea for linea in self.lineas.all()), Decimal('0.00'))

    @property
    def total_pagado(self):
        return sum((pago.monto for pago in self.pagos_compra.all()), Decimal('0.00'))

    @property
    def saldo_pendiente(self):
        saldo = self.total_documento - self.total_pagado
        return saldo if saldo > 0 else Decimal('0.00')

    @property
    def estado_pago(self):
        total_documento = self.total_documento
        if total_documento <= 0:
            return 'pagado'
        if self.total_pagado <= 0:
            return 'pendiente'
        if self.total_pagado < total_documento:
            return 'parcial'
        return 'pagado'

    @property
    def puede_registrar_pago(self):
        return self.estado == 'aplicada' and self.saldo_pendiente > 0

    @property
    def motivo_bloqueo_pago(self):
        if self.estado == 'borrador':
            return 'La compra aun esta en borrador y no debe pagarse hasta aplicarla.'
        if self.estado == 'anulada':
            return 'La compra fue anulada y ya no admite pagos.'
        if self.saldo_pendiente <= 0:
            return 'La compra ya fue pagada en su totalidad.'
        return ''

    @property
    def fecha_control_cxp(self):
        return self.fecha_vencimiento or self.fecha_documento

    @property
    def esta_vencida(self):
        return self.saldo_pendiente > 0 and self.fecha_control_cxp < timezone.now().date()


class RegistroCompraFiscal(models.Model):
    ESTADOS = (
        ('registrada', 'Registrada'),
        ('anulada', 'Anulada'),
    )

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE)
    proveedor = models.ForeignKey('Proveedor', on_delete=models.SET_NULL, null=True, blank=True)
    proveedor_nombre = models.CharField(max_length=200)
    proveedor_rtn = models.CharField(max_length=20, blank=True, null=True)
    numero_factura = models.CharField(max_length=120)
    cai = models.CharField(max_length=80, blank=True, null=True)
    clasificacion_contable = models.ForeignKey(
        'contabilidad.ClasificacionCompraFiscal',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='compras_fiscales',
    )
    fecha_documento = models.DateField()
    periodo_anio = models.PositiveIntegerField()
    periodo_mes = models.PositiveIntegerField()
    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    base_15 = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    isv_15 = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    base_18 = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    isv_18 = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    exento = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    exonerado = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    estado = models.CharField(max_length=15, choices=ESTADOS, default='registrada')
    origen_importacion = models.CharField(max_length=150, blank=True, null=True)
    observacion = models.TextField(blank=True, null=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha_documento', '-id']
        indexes = [
            models.Index(fields=['empresa', 'numero_factura']),
            models.Index(fields=['empresa', 'periodo_anio', 'periodo_mes']),
        ]

    def clean(self):
        super().clean()
        if self.fecha_documento:
            self.periodo_anio = self.periodo_anio or self.fecha_documento.year
            self.periodo_mes = self.periodo_mes or self.fecha_documento.month
        if self.periodo_mes and (self.periodo_mes < 1 or self.periodo_mes > 12):
            raise ValidationError({'periodo_mes': 'El mes debe estar entre 1 y 12.'})
        duplicada = self.buscar_duplicada()
        if duplicada:
            raise ValidationError(
                f"Esta factura ya existe en el libro de compras: {duplicada.numero_factura} "
                f"del proveedor {duplicada.proveedor_nombre} en {duplicada.periodo_mes}/{duplicada.periodo_anio}."
            )

    def save(self, *args, **kwargs):
        if self.fecha_documento:
            self.periodo_anio = self.periodo_anio or self.fecha_documento.year
            self.periodo_mes = self.periodo_mes or self.fecha_documento.month
        self.full_clean()
        super().save(*args, **kwargs)

    def buscar_duplicada(self):
        if not self.empresa_id or not self.numero_factura:
            return None
        queryset = RegistroCompraFiscal.objects.filter(
            empresa=self.empresa,
            numero_factura__iexact=self.numero_factura.strip(),
        ).exclude(estado='anulada')
        if self.pk:
            queryset = queryset.exclude(pk=self.pk)
        if self.proveedor_rtn:
            queryset = queryset.filter(proveedor_rtn__iexact=self.proveedor_rtn.strip())
        elif self.proveedor_nombre:
            queryset = queryset.filter(proveedor_nombre__iexact=self.proveedor_nombre.strip())
        return queryset.order_by('periodo_anio', 'periodo_mes', 'id').first()

    @property
    def impuesto_total(self):
        return (self.isv_15 or Decimal('0.00')) + (self.isv_18 or Decimal('0.00'))

    def __str__(self):
        return f"{self.numero_factura} - {self.proveedor_nombre}"


class LineaCompraInventario(models.Model):
    compra = models.ForeignKey(
        CompraInventario,
        on_delete=models.CASCADE,
        related_name='lineas'
    )
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT)
    cantidad = models.DecimalField(max_digits=12, decimal_places=2)
    costo_unitario = models.DecimalField(max_digits=12, decimal_places=2)
    comentario = models.CharField(max_length=150, blank=True, null=True)

    @property
    def total_linea(self):
        return self.cantidad * self.costo_unitario

    def __str__(self):
        return f"{self.producto.nombre} - {self.cantidad}"


class PagoCompra(models.Model):
    METODOS = (
        ('efectivo', 'Efectivo'),
        ('transferencia', 'Transferencia'),
        ('tarjeta', 'Tarjeta'),
    )

    compra = models.ForeignKey(
        CompraInventario,
        on_delete=models.CASCADE,
        related_name='pagos_compra'
    )
    fecha = models.DateField(default=timezone.now)
    monto = models.DecimalField(max_digits=12, decimal_places=2)
    metodo = models.CharField(max_length=20, choices=METODOS, default='efectivo')
    referencia = models.CharField(max_length=100, blank=True, null=True)
    cuenta_financiera = models.ForeignKey(
        'contabilidad.CuentaFinanciera',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='pagos_compras',
    )
    observacion = models.TextField(blank=True, null=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha', '-id']

    def clean(self):
        super().clean()
        if self.monto is None:
            return

        if self.monto <= 0:
            raise ValidationError({'monto': 'El pago debe ser mayor que cero.'})

        if self.compra.estado != 'aplicada':
            raise ValidationError('Solo se pueden registrar pagos en compras aplicadas.')

        total_pagado = self.compra.pagos_compra.exclude(pk=self.pk).aggregate(
            total=models.Sum('monto')
        )['total'] or Decimal('0.00')
        saldo_disponible = self.compra.total_documento - total_pagado

        if self.monto > saldo_disponible:
            raise ValidationError({'monto': 'El pago no puede ser mayor que el saldo pendiente de la compra.'})
        if self.cuenta_financiera_id and self.cuenta_financiera.empresa_id != self.compra.empresa_id:
            raise ValidationError({'cuenta_financiera': 'La cuenta financiera debe pertenecer a la misma empresa de la compra.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)
        ComprobanteEgresoCompra.objects.get_or_create(
            pago=self,
            defaults={
                'empresa': self.compra.empresa,
                'compra': self.compra,
                'proveedor': self.compra.proveedor,
                'proveedor_nombre': self.compra.proveedor_nombre,
                'fecha': self.fecha,
                'monto': self.monto,
                'metodo': self.metodo,
                'referencia': self.referencia,
                'observacion': self.observacion,
            }
        )

    def __str__(self):
        return f"Pago {self.monto} - {self.compra.numero_compra}"


class ComprobanteEgresoCompra(models.Model):
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE)
    compra = models.ForeignKey(CompraInventario, on_delete=models.PROTECT, related_name='comprobantes_egreso')
    proveedor = models.ForeignKey(Proveedor, on_delete=models.PROTECT, null=True, blank=True)
    pago = models.OneToOneField(PagoCompra, on_delete=models.CASCADE, related_name='comprobante')

    numero_comprobante = models.CharField(max_length=20, blank=True, null=True, unique=True)
    proveedor_nombre = models.CharField(max_length=200)
    fecha = models.DateField(default=timezone.now)
    monto = models.DecimalField(max_digits=12, decimal_places=2)
    metodo = models.CharField(max_length=20, choices=PagoCompra.METODOS, default='efectivo')
    referencia = models.CharField(max_length=100, blank=True, null=True)
    observacion = models.TextField(blank=True, null=True)

    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha_creacion', '-id']

    def save(self, *args, **kwargs):
        if not self.numero_comprobante:
            ultimo = ComprobanteEgresoCompra.objects.filter(empresa=self.empresa).order_by('-id').first()
            siguiente = 1 if not ultimo else ultimo.id + 1
            self.numero_comprobante = f"EGR-{str(siguiente).zfill(8)}"
        super().save(*args, **kwargs)

    def __str__(self):
        return self.numero_comprobante or f"Egreso {self.pk}"


# ==========================
# FACTURA
# ==========================

class Factura(models.Model):
    NUMERO_FACTURA_REGEX = re.compile(r"^(?P<establecimiento>\d{3})-(?P<punto>\d{3})-(?P<tipo>\d{2})-(?P<correlativo>\d{8})$")

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

    # 🔥 CAMPOS FISCALES (CORREGIDOS)
    orden_compra_exenta = models.CharField(max_length=100, blank=True, null=True)
    registro_exonerado = models.CharField(max_length=100, blank=True, null=True)
    registro_sag = models.CharField(max_length=100, blank=True, null=True)

    cai = models.ForeignKey(CAI, on_delete=models.PROTECT, null=True, blank=True)
    numero_factura = models.CharField(max_length=20, blank=True, null=True)
    cai_numero = models.CharField(max_length=50, blank=True, null=True)
    cai_establecimiento = models.CharField(max_length=3, blank=True, null=True)
    cai_punto_emision = models.CharField(max_length=3, blank=True, null=True)
    cai_tipo_documento = models.CharField(max_length=2, blank=True, null=True)
    cai_rango_inicial = models.IntegerField(blank=True, null=True)
    cai_rango_final = models.IntegerField(blank=True, null=True)
    cai_fecha_limite = models.DateField(blank=True, null=True)

    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    impuesto = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_lempiras = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    estado = models.CharField(max_length=10, choices=ESTADOS, default='borrador')
    estado_pago = models.CharField(max_length=10, choices=ESTADO_PAGO, default='pendiente')

    fecha_creacion = models.DateTimeField(auto_now_add=True)

    def clean(self):
        super().clean()
        self._validar_numero_factura_manual()

    def save(self, *args, **kwargs):

        self.full_clean()

        is_new = self.pk is None
        debe_generar_cai = False
        debe_aplicar_numero_manual = False
        original = None
        config_avanzada = ConfiguracionAvanzadaEmpresa.para_empresa(self.empresa) if self.empresa_id else None

        if is_new and self.estado == 'emitida' and not self.numero_factura:
            debe_generar_cai = True
        elif is_new and self.estado == 'emitida' and self.numero_factura and config_avanzada and config_avanzada.permite_gestion_fiscal_historica:
            debe_aplicar_numero_manual = True

        if not is_new:
            original = Factura.objects.get(pk=self.pk)
            if original.estado == 'borrador' and self.estado == 'emitida' and not self.numero_factura:
                debe_generar_cai = True
            elif (
                self.estado == 'emitida'
                and self.numero_factura
                and config_avanzada
                and config_avanzada.permite_gestion_fiscal_historica
                and (
                    original.estado != self.estado
                    or original.numero_factura != self.numero_factura
                    or original.fecha_emision != self.fecha_emision
                )
            ):
                debe_aplicar_numero_manual = True

        if debe_generar_cai:
            with transaction.atomic():
                self._generar_cai()
                if kwargs.get('update_fields'):
                    kwargs = kwargs.copy()
                    kwargs['update_fields'] = set(kwargs['update_fields']) | {
                        'cai_numero',
                        'cai_establecimiento',
                        'cai_punto_emision',
                        'cai_tipo_documento',
                        'cai_rango_inicial',
                        'cai_rango_final',
                        'cai_fecha_limite',
                        'cai',
                        'numero_factura',
                    }
                super().save(*args, **kwargs)
        elif debe_aplicar_numero_manual:
            with transaction.atomic():
                self._aplicar_numero_factura_manual()
                if kwargs.get('update_fields'):
                    kwargs = kwargs.copy()
                    kwargs['update_fields'] = set(kwargs['update_fields']) | {
                        'cai_numero',
                        'cai_establecimiento',
                        'cai_punto_emision',
                        'cai_tipo_documento',
                        'cai_rango_inicial',
                        'cai_rango_final',
                        'cai_fecha_limite',
                        'cai',
                        'numero_factura',
                    }
                super().save(*args, **kwargs)
        else:
            super().save(*args, **kwargs)

    def _obtener_fecha_referencia_cai(self):
        config_avanzada = ConfiguracionAvanzadaEmpresa.para_empresa(self.empresa)
        fecha_referencia = self.fecha_emision or timezone.now().date()
        if not config_avanzada.permite_cai_historico:
            fecha_referencia = timezone.now().date()
        return fecha_referencia

    def _obtener_queryset_cai_factura(self, usar_bloqueo=False, fecha_referencia=None):
        queryset = CAI.objects
        if usar_bloqueo:
            queryset = queryset.select_for_update()
        fecha_referencia = fecha_referencia or self._obtener_fecha_referencia_cai()
        return queryset.filter(
            empresa=self.empresa,
            uso_documento='factura',
            activo=True,
            fecha_activacion__lte=fecha_referencia,
            fecha_limite__gte=fecha_referencia,
        ).order_by('fecha_activacion', 'fecha_limite', 'fecha_creacion')

    def _descomponer_numero_factura(self):
        numero = (self.numero_factura or '').strip()
        coincidencia = self.NUMERO_FACTURA_REGEX.match(numero)
        if not coincidencia:
            raise ValidationError(
                "El numero manual debe tener el formato 000-000-00-00000000."
            )
        datos = coincidencia.groupdict()
        datos["correlativo"] = int(datos["correlativo"])
        return datos

    def _obtener_cai_para_numero_manual(self, usar_bloqueo=False):
        datos = self._descomponer_numero_factura()
        fecha_referencia = self.fecha_emision or timezone.now().date()
        cai = self._obtener_queryset_cai_factura(
            usar_bloqueo=usar_bloqueo,
            fecha_referencia=fecha_referencia,
        ).filter(
            establecimiento=datos["establecimiento"],
            punto_emision=datos["punto"],
            tipo_documento=datos["tipo"],
            rango_inicial__lte=datos["correlativo"],
            rango_final__gte=datos["correlativo"],
        ).first()
        return cai, datos["correlativo"]

    def _validar_numero_factura_manual(self):
        if not self.numero_factura or not self.empresa_id:
            return

        factura_duplicada = Factura.objects.filter(
            empresa=self.empresa,
            numero_factura__iexact=self.numero_factura.strip(),
        )
        if self.pk:
            factura_duplicada = factura_duplicada.exclude(pk=self.pk)
        if factura_duplicada.exists():
            raise ValidationError(
                {"numero_factura": "Ya existe una factura con este numero en esta empresa."}
            )

        cai, correlativo = self._obtener_cai_para_numero_manual()
        if not cai:
            raise ValidationError(
                {"numero_factura": "No existe un CAI que cubra este numero para la fecha de la factura."}
            )

        if correlativo > cai.rango_final:
            raise ValidationError(
                {"numero_factura": "El correlativo manual excede el rango final del CAI seleccionado."}
            )

    def _generar_cai(self):
        fecha_referencia = self._obtener_fecha_referencia_cai()

        cai = self._obtener_queryset_cai_factura(
            usar_bloqueo=True,
            fecha_referencia=fecha_referencia,
        ).filter(
            correlativo_actual__lt=F('rango_final')
        ).first()

        if not cai:
            raise ValueError("No existe un CAI disponible para la fecha seleccionada.")

        siguiente = cai.correlativo_actual + 1
        correlativos_usados = set()
        facturas_con_numero = Factura.objects.filter(
            empresa=self.empresa,
            cai=cai,
        ).exclude(numero_factura__isnull=True).exclude(numero_factura__exact="")
        if self.pk:
            facturas_con_numero = facturas_con_numero.exclude(pk=self.pk)

        for numero in facturas_con_numero.values_list("numero_factura", flat=True):
            coincidencia = self.NUMERO_FACTURA_REGEX.match((numero or "").strip())
            if coincidencia:
                correlativos_usados.add(int(coincidencia.group("correlativo")))

        while siguiente in correlativos_usados and siguiente <= cai.rango_final:
            siguiente += 1

        if siguiente > cai.rango_final:
            raise ValueError("No existe un correlativo libre dentro del rango del CAI seleccionado.")

        self.numero_factura = (
            f"{cai.establecimiento}-"
            f"{cai.punto_emision}-"
            f"{cai.tipo_documento}-"
            f"{str(siguiente).zfill(8)}"
        )

        self.cai = cai
        self._guardar_snapshot_cai(cai)
        cai.correlativo_actual = siguiente
        cai.save(update_fields=['correlativo_actual'])

    def _aplicar_numero_factura_manual(self):
        cai, _correlativo = self._obtener_cai_para_numero_manual(usar_bloqueo=True)
        if not cai:
            raise ValueError("No existe un CAI que cubra este numero para la fecha de la factura.")

        self.numero_factura = self.numero_factura.strip()
        self.cai = cai
        self._guardar_snapshot_cai(cai)

    def _guardar_snapshot_cai(self, cai):
        self.cai_numero = cai.numero_cai
        self.cai_establecimiento = cai.establecimiento
        self.cai_punto_emision = cai.punto_emision
        self.cai_tipo_documento = cai.tipo_documento
        self.cai_rango_inicial = cai.rango_inicial
        self.cai_rango_final = cai.rango_final
        self.cai_fecha_limite = cai.fecha_limite

    def emitir(self):
        with transaction.atomic():
            factura = Factura.objects.select_for_update().get(pk=self.pk)
            factura.estado = 'emitida'
            factura.save(update_fields=['estado', 'numero_factura', 'cai'])
            self.estado = factura.estado
            self.numero_factura = factura.numero_factura
            self.cai = factura.cai

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
            self.total_lempiras = (self.total * self.tipo_cambio).quantize(DOS_DECIMALES)
        else:
            self.total_lempiras = Decimal(self.total).quantize(DOS_DECIMALES)

    def actualizar_estado_pago(self):

        total_pagado = self.total_pagado
        total_documento = self.total_documento_ajustado

        if total_documento <= 0:
            self.estado_pago = 'pagado'
        elif total_pagado <= 0:
            self.estado_pago = 'pendiente'
        elif total_pagado < total_documento:
            self.estado_pago = 'parcial'
        else:
            self.estado_pago = 'pagado'

        self.save()

    @property
    def total_pagado(self):
        return sum((p.monto for p in self.pagos_facturacion.all()), Decimal('0.00'))

    @property
    def tiene_pagos_registrados(self):
        return self.pagos_facturacion.exists()

    @property
    def saldo_pendiente(self):
        return self.total_documento_ajustado - self.total_pagado

    @property
    def total_notas_credito(self):
        return sum(
            (n.total for n in self.notas_credito.filter(estado='emitida')),
            Decimal('0.00')
        )

    @property
    def total_documento_ajustado(self):
        ajustado = self.total - self.total_notas_credito
        return ajustado if ajustado > 0 else Decimal('0.00')

    @property
    def tiene_notas_credito_activas(self):
        return self.notas_credito.exclude(estado='anulada').exists()

    @property
    def puede_editar_emitida(self):
        if self.estado != 'emitida':
            return True
        return not self.tiene_pagos_registrados and not self.tiene_notas_credito_activas

    @property
    def motivo_bloqueo_edicion(self):
        if self.estado != 'emitida':
            return ""
        if self.tiene_pagos_registrados:
            return "La factura ya tiene pagos registrados."
        if self.tiene_notas_credito_activas:
            return "La factura ya tiene notas de crédito relacionadas."
        return ""

    def resumen_fiscal(self):

        resumen = {
            "base_15": 0,
            "base_18": 0,
            "base_exento": 0,
            "base_exonerado": 0,
            "isv_15": 0,
            "isv_18": 0,
            "descuento_total": 0,  # 🔥 NUEVO
        }

        for linea in self.lineas.all():

            resumen["descuento_total"] += linea.descuento_monto  # 🔥

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
        return _monto_en_letras_con_centavos(self.total, self.moneda)

    def __str__(self):
        return self.numero_factura or "Factura sin número"


    @property
    def cai_numero_historico(self):
        return self.cai_numero or (self.cai.numero_cai if self.cai else None)

    @property
    def cai_establecimiento_historico(self):
        return self.cai_establecimiento or (self.cai.establecimiento if self.cai else None)

    @property
    def cai_punto_emision_historico(self):
        return self.cai_punto_emision or (self.cai.punto_emision if self.cai else None)

    @property
    def cai_tipo_documento_historico(self):
        return self.cai_tipo_documento or (self.cai.tipo_documento if self.cai else None)

    @property
    def cai_rango_inicial_historico(self):
        return self.cai_rango_inicial if self.cai_rango_inicial is not None else (self.cai.rango_inicial if self.cai else None)

    @property
    def cai_rango_final_historico(self):
        return self.cai_rango_final if self.cai_rango_final is not None else (self.cai.rango_final if self.cai else None)

    @property
    def cai_fecha_limite_historico(self):
        return self.cai_fecha_limite or (self.cai.fecha_limite if self.cai else None)


# ==========================
# NOTA DE CREDITO
# ==========================

class NotaCredito(models.Model):

    ESTADOS = (
        ('borrador', 'Borrador'),
        ('emitida', 'Emitida'),
        ('anulada', 'Anulada'),
    )

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE)
    factura_origen = models.ForeignKey(
        Factura,
        on_delete=models.PROTECT,
        related_name='notas_credito'
    )
    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT)
    vendedor = models.ForeignKey(
        Usuario,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='notas_credito_emitidas'
    )

    moneda = models.CharField(max_length=3, choices=Factura.MONEDAS, default='HNL')
    tipo_cambio = models.DecimalField(max_digits=10, decimal_places=4, default=1)

    fecha_emision = models.DateField(default=timezone.now)
    motivo = models.TextField(blank=True, null=True)

    cai = models.ForeignKey(CAI, on_delete=models.PROTECT, null=True, blank=True)
    numero_nota = models.CharField(max_length=20, blank=True, null=True)
    cai_numero = models.CharField(max_length=50, blank=True, null=True)
    cai_establecimiento = models.CharField(max_length=3, blank=True, null=True)
    cai_punto_emision = models.CharField(max_length=3, blank=True, null=True)
    cai_tipo_documento = models.CharField(max_length=2, blank=True, null=True)
    cai_rango_inicial = models.IntegerField(blank=True, null=True)
    cai_rango_final = models.IntegerField(blank=True, null=True)
    cai_fecha_limite = models.DateField(blank=True, null=True)

    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    impuesto = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_lempiras = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    estado = models.CharField(max_length=10, choices=ESTADOS, default='borrador')
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    def clean(self):
        super().clean()

        if self.factura_origen_id and self.empresa_id and self.factura_origen.empresa_id != self.empresa_id:
            raise ValidationError("La factura origen no pertenece a la empresa seleccionada.")

        if self.estado == 'emitida' and self.factura_origen_id:
            if self.factura_origen.estado != 'emitida':
                raise ValidationError("Solo se pueden emitir notas de credito sobre facturas emitidas.")

            total_otras_notas = self.factura_origen.notas_credito.filter(
                estado='emitida'
            ).exclude(pk=self.pk).aggregate(
                total=models.Sum('total')
            )['total'] or Decimal('0.00')

            disponible = self.factura_origen.total - total_otras_notas

            if self.total <= 0:
                raise ValidationError("La nota de credito emitida debe tener un total mayor que cero.")

            if self.total > disponible:
                raise ValidationError("La nota de credito no puede exceder el saldo disponible de la factura.")

    def save(self, *args, **kwargs):

        self.full_clean()

        is_new = self.pk is None
        debe_generar_cai = False

        if is_new and self.estado == 'emitida':
            debe_generar_cai = True

        if not is_new:
            original = NotaCredito.objects.get(pk=self.pk)
            if original.estado == 'borrador' and self.estado == 'emitida' and not self.numero_nota:
                debe_generar_cai = True

        if debe_generar_cai:
            with transaction.atomic():
                self._generar_cai()
                if kwargs.get('update_fields'):
                    kwargs = kwargs.copy()
                    kwargs['update_fields'] = set(kwargs['update_fields']) | {
                        'cai_numero',
                        'cai_establecimiento',
                        'cai_punto_emision',
                        'cai_tipo_documento',
                        'cai_rango_inicial',
                        'cai_rango_final',
                        'cai_fecha_limite',
                        'cai',
                        'numero_nota',
                    }
                super().save(*args, **kwargs)
        else:
            super().save(*args, **kwargs)

    def _generar_cai(self):
        config_avanzada = ConfiguracionAvanzadaEmpresa.para_empresa(self.empresa)
        fecha_referencia = self.fecha_emision or timezone.now().date()
        if not config_avanzada.permite_cai_historico:
            fecha_referencia = timezone.now().date()

        cai = CAI.objects.select_for_update().filter(
            empresa=self.empresa,
            uso_documento='nota_credito',
            activo=True,
            fecha_activacion__lte=fecha_referencia,
            fecha_limite__gte=fecha_referencia,
            correlativo_actual__lt=F('rango_final')
        ).order_by('fecha_activacion', 'fecha_limite', 'fecha_creacion').first()

        if not cai:
            raise ValueError("No existe un CAI disponible para la fecha seleccionada en la nota de credito.")

        siguiente = cai.correlativo_actual + 1

        self.numero_nota = (
            f"{cai.establecimiento}-"
            f"{cai.punto_emision}-"
            f"{cai.tipo_documento}-"
            f"{str(siguiente).zfill(8)}"
        )

        self.cai = cai
        self._guardar_snapshot_cai(cai)
        cai.correlativo_actual = siguiente
        cai.save(update_fields=['correlativo_actual'])

    def _guardar_snapshot_cai(self, cai):
        self.cai_numero = cai.numero_cai
        self.cai_establecimiento = cai.establecimiento
        self.cai_punto_emision = cai.punto_emision
        self.cai_tipo_documento = cai.tipo_documento
        self.cai_rango_inicial = cai.rango_inicial
        self.cai_rango_final = cai.rango_final
        self.cai_fecha_limite = cai.fecha_limite

    def calcular_totales(self):

        subtotal_general = Decimal('0.00')
        impuesto_general = Decimal('0.00')

        for linea in self.lineas.all():
            subtotal_general += linea.subtotal
            impuesto_general += linea.impuesto_monto

        self.subtotal = subtotal_general
        self.impuesto = impuesto_general
        self.total = subtotal_general + impuesto_general

        if self.moneda == 'USD':
            self.total_lempiras = (self.total * self.tipo_cambio).quantize(DOS_DECIMALES)
        else:
            self.total_lempiras = Decimal(self.total).quantize(DOS_DECIMALES)

    def resumen_fiscal(self):
        resumen = {
            "base_15": 0,
            "base_18": 0,
            "base_exento": 0,
            "base_exonerado": 0,
            "isv_15": 0,
            "isv_18": 0,
            "descuento_total": 0,
        }

        for linea in self.lineas.all():
            resumen["descuento_total"] += linea.descuento_monto

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
        return _monto_en_letras_con_centavos(self.total, self.moneda)

    @property
    def cai_numero_historico(self):
        return self.cai_numero or (self.cai.numero_cai if self.cai else None)

    @property
    def cai_establecimiento_historico(self):
        return self.cai_establecimiento or (self.cai.establecimiento if self.cai else None)

    @property
    def cai_punto_emision_historico(self):
        return self.cai_punto_emision or (self.cai.punto_emision if self.cai else None)

    @property
    def cai_tipo_documento_historico(self):
        return self.cai_tipo_documento or (self.cai.tipo_documento if self.cai else None)

    @property
    def cai_rango_inicial_historico(self):
        return self.cai_rango_inicial if self.cai_rango_inicial is not None else (self.cai.rango_inicial if self.cai else None)

    @property
    def cai_rango_final_historico(self):
        return self.cai_rango_final if self.cai_rango_final is not None else (self.cai.rango_final if self.cai else None)

    @property
    def cai_fecha_limite_historico(self):
        return self.cai_fecha_limite or (self.cai.fecha_limite if self.cai else None)

    def __str__(self):
        return self.numero_nota or f"NC borrador {self.id}"


# ==========================
# LINEA FACTURA
# ==========================

class LineaFactura(models.Model):

    factura = models.ForeignKey(
        Factura,
        on_delete=models.CASCADE,
        related_name="lineas"
    )

    producto = models.ForeignKey(Producto, on_delete=models.PROTECT, null=True, blank=True)
    descripcion_manual = models.CharField(max_length=255, blank=True, null=True)
    cantidad = models.DecimalField(max_digits=10, decimal_places=2)
    precio_unitario = models.DecimalField(max_digits=12, decimal_places=2)

    comentario = models.TextField(blank=True, null=True)

    # 🔥 NUEVO DESCUENTO
    descuento_porcentaje = models.DecimalField(max_digits=5, decimal_places=2, default=0, blank=True, null=True)
    descuento_monto = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    impuesto = models.ForeignKey(TipoImpuesto, on_delete=models.PROTECT)
    impuesto_monto = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    def clean(self):
        super().clean()
        if self.descripcion_manual:
            self.descripcion_manual = self.descripcion_manual.strip()
        if not self.producto and not self.descripcion_manual:
            raise ValidationError({
                "producto": "Selecciona un producto o escribe una descripcion manual para la linea."
            })

    def save(self, *args, **kwargs):
        self.full_clean()

        subtotal_base = self.cantidad * self.precio_unitario

        # descuento
        descuento = self.descuento_porcentaje or Decimal('0')

        self.descuento_monto = (subtotal_base * (descuento / Decimal('100'))).quantize(DOS_DECIMALES)

        subtotal_final = (subtotal_base - self.descuento_monto).quantize(DOS_DECIMALES)

        # impuesto sobre subtotal con descuento
        self.impuesto_monto = (subtotal_final * (self.impuesto.porcentaje / Decimal ('100'))).quantize(DOS_DECIMALES)

        self.subtotal = subtotal_final

        super().save(*args, **kwargs)

    @property
    def total_linea(self):
        return ((self.subtotal or Decimal('0.00')) + (self.impuesto_monto or Decimal('0.00'))).quantize(DOS_DECIMALES)

    @property
    def descripcion_visual(self):
        if self.producto_id and self.producto:
            return self.producto.nombre
        return self.descripcion_manual or "Linea sin descripcion"

    def __str__(self):
        return self.descripcion_visual


class LineaNotaCredito(models.Model):

    nota_credito = models.ForeignKey(
        NotaCredito,
        on_delete=models.CASCADE,
        related_name="lineas"
    )

    producto = models.ForeignKey(Producto, on_delete=models.PROTECT, null=True, blank=True)
    descripcion_manual = models.CharField(max_length=255, blank=True, null=True)
    cantidad = models.DecimalField(max_digits=10, decimal_places=2)
    precio_unitario = models.DecimalField(max_digits=12, decimal_places=2)
    comentario = models.TextField(blank=True, null=True)
    descuento_porcentaje = models.DecimalField(max_digits=5, decimal_places=2, default=0, blank=True, null=True)
    descuento_monto = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    impuesto = models.ForeignKey(TipoImpuesto, on_delete=models.PROTECT)
    impuesto_monto = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    def clean(self):
        super().clean()
        if self.descripcion_manual:
            self.descripcion_manual = self.descripcion_manual.strip()
        if not self.producto and not self.descripcion_manual:
            raise ValidationError({
                "producto": "Selecciona un producto o escribe una descripcion manual para la linea."
            })

    def save(self, *args, **kwargs):
        self.full_clean()

        subtotal_base = self.cantidad * self.precio_unitario
        descuento = self.descuento_porcentaje or Decimal('0')
        self.descuento_monto = (subtotal_base * (descuento / Decimal('100'))).quantize(DOS_DECIMALES)
        subtotal_final = (subtotal_base - self.descuento_monto).quantize(DOS_DECIMALES)
        self.impuesto_monto = (subtotal_final * (self.impuesto.porcentaje / Decimal('100'))).quantize(DOS_DECIMALES)
        self.subtotal = subtotal_final

        super().save(*args, **kwargs)

    @property
    def total_linea(self):
        return ((self.subtotal or Decimal('0.00')) + (self.impuesto_monto or Decimal('0.00'))).quantize(DOS_DECIMALES)

    @property
    def descripcion_visual(self):
        if self.producto_id and self.producto:
            return self.producto.nombre
        return self.descripcion_manual or "Linea sin descripcion"

    def __str__(self):
        return self.descripcion_visual


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
    cuenta_financiera = models.ForeignKey(
        'contabilidad.CuentaFinanciera',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='pagos_facturas',
    )
    cajero = models.ForeignKey(
        Usuario,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='pagos_facturas_registrados',
    )

    fecha_creacion = models.DateTimeField(auto_now_add=True)

    def clean(self):
        super().clean()
        if self.monto is None:
            return

        if self.monto <= 0:
            raise ValidationError({'monto': 'El monto del pago debe ser mayor que cero.'})

        total_pagado = self.factura.pagos_facturacion.exclude(pk=self.pk).aggregate(
            total=models.Sum('monto')
        )['total'] or Decimal('0.00')

        saldo_disponible = self.factura.total_documento_ajustado - total_pagado
        if self.monto > saldo_disponible:
            raise ValidationError({'monto': 'El pago no puede ser mayor que el saldo pendiente.'})
        if self.cuenta_financiera_id and self.cuenta_financiera.empresa_id != self.factura.empresa_id:
            raise ValidationError({'cuenta_financiera': 'La cuenta financiera debe pertenecer a la misma empresa de la factura.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)
        ReciboPago.objects.get_or_create(
            pago=self,
            defaults={
                'empresa': self.factura.empresa,
                'factura': self.factura,
                'cliente': self.factura.cliente,
                'fecha': self.fecha,
                'monto': self.monto,
                'metodo': self.metodo,
                'referencia': self.referencia,
            }
        )
        self.factura.actualizar_estado_pago()

    def __str__(self):
        return f"Pago {self.monto} - Factura {self.factura.numero_factura}"


class CierreCaja(models.Model):
    TURNOS = (
        ('general', 'General'),
        ('manana', 'Manana'),
        ('tarde', 'Tarde'),
        ('noche', 'Noche'),
    )
    ESTADOS = (
        ('cerrado', 'Cerrado'),
        ('revisado', 'Revisado'),
        ('anulado', 'Anulado'),
    )

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name='cierres_caja')
    cajero = models.ForeignKey(Usuario, on_delete=models.PROTECT, related_name='cierres_caja')
    fecha = models.DateField(default=timezone.now)
    turno = models.CharField(max_length=20, choices=TURNOS, default='general')
    efectivo_sistema = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tarjeta_sistema = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    transferencia_sistema = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    efectivo_reportado = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tarjeta_reportado = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    transferencia_reportado = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    observacion = models.TextField(blank=True, null=True)
    estado = models.CharField(max_length=20, choices=ESTADOS, default='cerrado')
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha', '-creado_en']
        unique_together = ('empresa', 'cajero', 'fecha', 'turno')

    @property
    def total_sistema(self):
        return self.efectivo_sistema + self.tarjeta_sistema + self.transferencia_sistema

    @property
    def total_reportado(self):
        return self.efectivo_reportado + self.tarjeta_reportado + self.transferencia_reportado

    @property
    def diferencia(self):
        return self.total_reportado - self.total_sistema

    def __str__(self):
        return f"Cierre {self.fecha} - {self.cajero}"


class ReciboPago(models.Model):

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE)
    factura = models.ForeignKey(Factura, on_delete=models.PROTECT, related_name='recibos_pago')
    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT)
    pago = models.OneToOneField(PagoFactura, on_delete=models.CASCADE, related_name='recibo')

    numero_recibo = models.CharField(max_length=20, blank=True, null=True, unique=True)
    fecha = models.DateField(default=timezone.now)
    monto = models.DecimalField(max_digits=12, decimal_places=2)
    metodo = models.CharField(max_length=20, choices=PagoFactura.METODOS, default='efectivo')
    referencia = models.CharField(max_length=100, blank=True, null=True)

    fecha_creacion = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if not self.numero_recibo:
            ultimo = ReciboPago.objects.filter(empresa=self.empresa).order_by('-id').first()
            siguiente = 1 if not ultimo else ultimo.id + 1
            self.numero_recibo = f"REC-{str(siguiente).zfill(8)}"
        super().save(*args, **kwargs)

    def __str__(self):
        return self.numero_recibo or f"Recibo {self.pk}"
