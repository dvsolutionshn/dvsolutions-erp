from calendar import monthrange
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction

from core.models import Empresa


class CuentaContable(models.Model):
    TIPO_CHOICES = [
        ("activo", "Activo"),
        ("pasivo", "Pasivo"),
        ("patrimonio", "Patrimonio"),
        ("ingreso", "Ingreso"),
        ("costo", "Costo"),
        ("gasto", "Gasto"),
    ]

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="cuentas_contables")
    cuenta_padre = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="subcuentas",
    )
    codigo = models.CharField(max_length=30)
    nombre = models.CharField(max_length=200)
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    descripcion = models.TextField(blank=True, null=True)
    acepta_movimientos = models.BooleanField(default=True)
    activa = models.BooleanField(default=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["codigo", "nombre"]
        unique_together = ("empresa", "codigo")
        verbose_name = "Cuenta contable"
        verbose_name_plural = "Cuentas contables"

    def __str__(self):
        return f"{self.codigo} - {self.nombre}"

    @property
    def nivel(self):
        nivel = 0
        cuenta = self.cuenta_padre
        while cuenta:
            nivel += 1
            cuenta = cuenta.cuenta_padre
        return nivel

    @property
    def nombre_jerarquico(self):
        return f"{'-- ' * self.nivel}{self.codigo} - {self.nombre}"

    def clean(self):
        if self.cuenta_padre_id:
            if self.pk and self.cuenta_padre_id == self.pk:
                raise ValidationError("Una cuenta no puede ser padre de si misma.")
            if self.cuenta_padre.empresa_id != self.empresa_id:
                raise ValidationError("La cuenta padre debe pertenecer a la misma empresa.")

            cuenta = self.cuenta_padre
            while cuenta:
                if self.pk and cuenta.id == self.pk:
                    raise ValidationError("La jerarquia de cuentas no puede tener ciclos.")
                cuenta = cuenta.cuenta_padre


class ConfiguracionContableEmpresa(models.Model):
    empresa = models.OneToOneField(
        Empresa,
        on_delete=models.CASCADE,
        related_name="configuracion_contable",
    )
    cuenta_caja = models.ForeignKey(
        CuentaContable,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="configuraciones_caja",
    )
    cuenta_bancos = models.ForeignKey(
        CuentaContable,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="configuraciones_bancos",
    )
    cuenta_clientes = models.ForeignKey(
        CuentaContable,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="configuraciones_clientes",
    )
    cuenta_inventario = models.ForeignKey(
        CuentaContable,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="configuraciones_inventario",
    )
    cuenta_isv_por_pagar = models.ForeignKey(
        CuentaContable,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="configuraciones_isv_por_pagar",
    )
    cuenta_proveedores = models.ForeignKey(
        CuentaContable,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="configuraciones_proveedores",
    )
    cuenta_ventas = models.ForeignKey(
        CuentaContable,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="configuraciones_ventas",
    )
    cuenta_devoluciones_ventas = models.ForeignKey(
        CuentaContable,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="configuraciones_devoluciones_ventas",
    )
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Configuracion contable por empresa"
        verbose_name_plural = "Configuraciones contables por empresa"

    def __str__(self):
        return f"Configuracion contable - {self.empresa.nombre}"


class PeriodoContable(models.Model):
    ESTADO_CHOICES = [
        ("abierto", "Abierto"),
        ("cerrado", "Cerrado"),
    ]

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="periodos_contables")
    anio = models.PositiveIntegerField()
    mes = models.PositiveIntegerField()
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default="abierto")
    observacion = models.TextField(blank=True, null=True)
    cerrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="periodos_contables_cerrados",
    )
    fecha_cierre = models.DateTimeField(null=True, blank=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-anio", "-mes"]
        unique_together = ("empresa", "anio", "mes")
        verbose_name = "Periodo contable"
        verbose_name_plural = "Periodos contables"

    def clean(self):
        if self.mes < 1 or self.mes > 12:
            raise ValidationError("El mes debe estar entre 1 y 12.")

    @property
    def fecha_inicio(self):
        from datetime import date

        return date(self.anio, self.mes, 1)

    @property
    def fecha_fin(self):
        from datetime import date

        return date(self.anio, self.mes, monthrange(self.anio, self.mes)[1])

    @property
    def esta_cerrado(self):
        return self.estado == "cerrado"

    def __str__(self):
        return f"{self.mes:02d}/{self.anio} - {self.empresa.nombre}"

    @classmethod
    def para_fecha(cls, empresa, fecha):
        return cls.objects.filter(empresa=empresa, anio=fecha.year, mes=fecha.month).first()

    @classmethod
    def fecha_bloqueada(cls, empresa, fecha):
        periodo = cls.para_fecha(empresa, fecha)
        return bool(periodo and periodo.esta_cerrado)


class ClasificacionCompraFiscal(models.Model):
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="clasificaciones_compras_fiscales")
    nombre = models.CharField(max_length=120)
    cuenta_contable = models.ForeignKey(
        CuentaContable,
        on_delete=models.PROTECT,
        related_name="clasificaciones_compras_fiscales",
    )
    descripcion = models.TextField(blank=True, null=True)
    activa = models.BooleanField(default=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["nombre"]
        unique_together = ("empresa", "nombre")
        verbose_name = "Clasificacion de compra fiscal"
        verbose_name_plural = "Clasificaciones de compras fiscales"

    def clean(self):
        super().clean()
        if self.cuenta_contable_id and self.cuenta_contable.empresa_id != self.empresa_id:
            raise ValidationError("La cuenta contable debe pertenecer a la misma empresa.")
        if self.cuenta_contable_id and not self.cuenta_contable.acepta_movimientos:
            raise ValidationError("La cuenta contable debe aceptar movimientos.")

    def __str__(self):
        return f"{self.nombre} -> {self.cuenta_contable}"


class AsientoContable(models.Model):
    ESTADO_CHOICES = [
        ("borrador", "Borrador"),
        ("contabilizado", "Contabilizado"),
        ("anulado", "Anulado"),
    ]

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="asientos_contables")
    numero = models.CharField(max_length=30, blank=True, null=True)
    fecha = models.DateField()
    descripcion = models.CharField(max_length=255)
    referencia = models.CharField(max_length=120, blank=True, null=True)
    origen_modulo = models.CharField(max_length=80, blank=True, null=True)
    documento_tipo = models.CharField(max_length=80, blank=True, null=True)
    documento_id = models.PositiveIntegerField(blank=True, null=True)
    evento = models.CharField(max_length=80, blank=True, null=True)
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default="borrador")
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="asientos_creados",
    )
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-fecha", "-id"]
        verbose_name = "Asiento contable"
        verbose_name_plural = "Asientos contables"
        indexes = [
            models.Index(fields=["empresa", "documento_tipo", "documento_id", "evento"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["empresa", "numero"],
                condition=models.Q(numero__isnull=False) & ~models.Q(numero__exact=""),
                name="unique_asiento_numero_por_empresa",
            ),
        ]

    def __str__(self):
        return self.numero or f"Asiento {self.id}"

    @property
    def total_debe(self):
        return sum((linea.debe for linea in self.lineas.all()), Decimal("0.00"))

    @property
    def total_haber(self):
        return sum((linea.haber for linea in self.lineas.all()), Decimal("0.00"))

    @property
    def esta_balanceado(self):
        return self.total_debe == self.total_haber and self.total_debe > 0

    def generar_numero(self):
        if self.numero:
            return self.numero
        with transaction.atomic():
            ultimo = (
                AsientoContable.objects.select_for_update()
                .filter(empresa=self.empresa)
                .exclude(numero__isnull=True)
                .exclude(numero__exact="")
                .order_by("-id")
                .first()
            )
            consecutivo = 1
            if ultimo and ultimo.numero:
                try:
                    consecutivo = int(ultimo.numero.split("-")[-1]) + 1
                except (TypeError, ValueError):
                    consecutivo = (
                        AsientoContable.objects.filter(empresa=self.empresa)
                        .exclude(numero__isnull=True)
                        .exclude(numero__exact="")
                        .count()
                        + 1
                    )
        self.numero = f"ASI-{consecutivo:08d}"
        return self.numero

    def clean(self):
        super().clean()
        if self.empresa_id and self.fecha and PeriodoContable.fecha_bloqueada(self.empresa, self.fecha):
            raise ValidationError("No se puede registrar un asiento en un periodo contable cerrado.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class LineaAsientoContable(models.Model):
    asiento = models.ForeignKey(AsientoContable, on_delete=models.CASCADE, related_name="lineas")
    cuenta = models.ForeignKey(CuentaContable, on_delete=models.PROTECT, related_name="lineas_asiento")
    detalle = models.CharField(max_length=200, blank=True, null=True)
    debe = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    haber = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    class Meta:
        verbose_name = "Linea de asiento"
        verbose_name_plural = "Lineas de asiento"

    def clean(self):
        if self.debe < 0 or self.haber < 0:
            raise ValidationError("Los valores contables no pueden ser negativos.")
        if self.debe == 0 and self.haber == 0:
            raise ValidationError("Cada linea debe tener valor en debe o haber.")
        if self.debe > 0 and self.haber > 0:
            raise ValidationError("Una misma linea no puede tener debe y haber al mismo tiempo.")
        if self.asiento_id and self.cuenta_id and self.asiento.empresa_id != self.cuenta.empresa_id:
            raise ValidationError("La cuenta contable debe pertenecer a la misma empresa del asiento.")

    def __str__(self):
        return f"{self.cuenta} - D {self.debe} / H {self.haber}"


class CuentaFinanciera(models.Model):
    TIPO_CHOICES = [
        ("caja", "Caja"),
        ("banco", "Banco"),
        ("tarjeta_credito", "Tarjeta de credito"),
    ]

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="cuentas_financieras")
    nombre = models.CharField(max_length=160)
    tipo = models.CharField(max_length=30, choices=TIPO_CHOICES, default="banco")
    institucion = models.CharField(max_length=160, blank=True, null=True)
    numero = models.CharField(max_length=80, blank=True, null=True)
    cuenta_contable = models.ForeignKey(
        CuentaContable,
        on_delete=models.PROTECT,
        related_name="cuentas_financieras",
    )
    activa = models.BooleanField(default=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["nombre"]
        unique_together = ("empresa", "nombre")

    def clean(self):
        super().clean()
        if self.cuenta_contable_id and self.cuenta_contable.empresa_id != self.empresa_id:
            raise ValidationError("La cuenta contable debe pertenecer a la misma empresa.")
        if self.cuenta_contable_id and not self.cuenta_contable.acepta_movimientos:
            raise ValidationError("La cuenta contable debe aceptar movimientos.")

    def __str__(self):
        return self.nombre


class ClasificacionMovimientoBanco(models.Model):
    TIPO_CHOICES = [
        ("ingreso", "Ingreso"),
        ("egreso", "Egreso"),
        ("transferencia", "Transferencia"),
    ]

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="clasificaciones_movimientos_banco")
    nombre = models.CharField(max_length=120)
    tipo = models.CharField(max_length=30, choices=TIPO_CHOICES, default="egreso")
    cuenta_contable = models.ForeignKey(
        CuentaContable,
        on_delete=models.PROTECT,
        related_name="clasificaciones_movimientos_banco",
    )
    activa = models.BooleanField(default=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["nombre"]
        unique_together = ("empresa", "nombre")

    def clean(self):
        super().clean()
        if self.cuenta_contable_id and self.cuenta_contable.empresa_id != self.empresa_id:
            raise ValidationError("La cuenta contable debe pertenecer a la misma empresa.")
        if self.cuenta_contable_id and not self.cuenta_contable.acepta_movimientos:
            raise ValidationError("La cuenta contable debe aceptar movimientos.")

    def __str__(self):
        return f"{self.nombre} -> {self.cuenta_contable}"


class ReglaClasificacionBanco(models.Model):
    TIPO_MOVIMIENTO_CHOICES = [
        ("todos", "Todos"),
        ("ingreso", "Solo ingresos"),
        ("egreso", "Solo egresos"),
    ]

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="reglas_clasificacion_banco")
    nombre = models.CharField(max_length=140)
    texto_busqueda = models.CharField(max_length=160)
    tipo_movimiento = models.CharField(max_length=20, choices=TIPO_MOVIMIENTO_CHOICES, default="todos")
    clasificacion = models.ForeignKey(
        ClasificacionMovimientoBanco,
        on_delete=models.PROTECT,
        related_name="reglas_bancarias",
    )
    prioridad = models.PositiveIntegerField(default=100)
    activa = models.BooleanField(default=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["prioridad", "nombre"]
        unique_together = ("empresa", "nombre")

    def clean(self):
        super().clean()
        if self.clasificacion_id and self.clasificacion.empresa_id != self.empresa_id:
            raise ValidationError("La clasificacion debe pertenecer a la misma empresa.")

    def aplica_a(self, movimiento):
        if not self.activa:
            return False
        if movimiento.estado == "contabilizado" or movimiento.clasificacion_id:
            return False
        if self.tipo_movimiento == "ingreso" and not movimiento.es_ingreso:
            return False
        if self.tipo_movimiento == "egreso" and movimiento.es_ingreso:
            return False
        return self.texto_busqueda.lower() in (movimiento.descripcion or "").lower()

    def __str__(self):
        return f"{self.nombre}: {self.texto_busqueda} -> {self.clasificacion}"


class MovimientoBancario(models.Model):
    ESTADO_CHOICES = [
        ("pendiente", "Pendiente"),
        ("clasificado", "Clasificado"),
        ("contabilizado", "Contabilizado"),
    ]

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="movimientos_bancarios")
    cuenta_financiera = models.ForeignKey(CuentaFinanciera, on_delete=models.CASCADE, related_name="movimientos")
    fecha = models.DateField()
    descripcion = models.CharField(max_length=255)
    referencia = models.CharField(max_length=120, blank=True, null=True)
    debito = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    credito = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    saldo = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    clasificacion = models.ForeignKey(
        ClasificacionMovimientoBanco,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="movimientos",
    )
    pago_factura = models.OneToOneField(
        "facturacion.PagoFactura",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="movimiento_bancario",
    )
    pago_compra = models.OneToOneField(
        "facturacion.PagoCompra",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="movimiento_bancario",
    )
    asiento = models.ForeignKey(AsientoContable, on_delete=models.SET_NULL, null=True, blank=True, related_name="movimientos_bancarios")
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default="pendiente")
    conciliado = models.BooleanField(default=False)
    fecha_conciliacion = models.DateTimeField(null=True, blank=True)
    conciliado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="movimientos_bancarios_conciliados",
    )
    origen_importacion = models.CharField(max_length=160, blank=True, null=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-fecha", "-id"]
        indexes = [
            models.Index(fields=["empresa", "cuenta_financiera", "fecha"]),
        ]

    def clean(self):
        super().clean()
        if self.cuenta_financiera_id and self.cuenta_financiera.empresa_id != self.empresa_id:
            raise ValidationError("La cuenta financiera debe pertenecer a la misma empresa.")
        if self.clasificacion_id and self.clasificacion.empresa_id != self.empresa_id:
            raise ValidationError("La clasificacion debe pertenecer a la misma empresa.")
        if self.debito < 0 or self.credito < 0:
            raise ValidationError("Debito y credito no pueden ser negativos.")
        if self.debito > 0 and self.credito > 0:
            raise ValidationError("Un movimiento no puede tener debito y credito al mismo tiempo.")
        if self.debito == 0 and self.credito == 0:
            raise ValidationError("El movimiento debe tener debito o credito.")

    @property
    def monto(self):
        return self.credito if self.credito > 0 else self.debito

    @property
    def es_ingreso(self):
        return self.credito > 0

    def __str__(self):
        return f"{self.fecha} - {self.descripcion} - {self.monto}"
