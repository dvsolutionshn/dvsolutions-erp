from calendar import monthrange
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, router, transaction

from core.models import Empresa


def bloquear_empresas_contables(empresa_ids, using="default"):
    """Comparte con el cierre mensual el bloqueo por empresa, en orden estable."""
    ids = sorted({empresa_id for empresa_id in empresa_ids if empresa_id is not None})
    list(Empresa.objects.using(using).select_for_update().filter(pk__in=ids).order_by("pk"))


def validar_fecha_contable_abierta(empresa_id, fecha, using="default"):
    if empresa_id and fecha and PeriodoContable.objects.using(using).filter(
        empresa_id=empresa_id, anio=fecha.year, mes=fecha.month, estado="cerrado"
    ).exists():
        raise ValidationError(
            "El período contable está cerrado. No se pueden modificar sus registros; "
            "registre el ajuste en un período abierto o solicite una reapertura autorizada."
        )


class RegistrosContablesQuerySet(models.QuerySet):
    """Las escrituras en lote conservan las mismas reglas que la edición individual."""

    def _empresas(self):
        campo = "asiento__empresa_id" if self.model.__name__ == "LineaAsientoContable" else "empresa_id"
        return self.values_list(campo, flat=True)

    def update(self, **kwargs):
        if self.query.is_sliced:
            raise TypeError("No se puede actualizar una consulta después de limitar sus resultados.")
        if any(hasattr(valor, "resolve_expression") for valor in kwargs.values()):
            raise ValidationError("Actualice los registros contables con valores explícitos para validar el período.")
        with transaction.atomic(using=self.db):
            bloquear_empresas_contables(self._empresas(), using=self.db)
            registros = list(self.model.objects.using(self.db).filter(pk__in=self.values("pk")))
            for registro in registros:
                for campo, valor in kwargs.items():
                    setattr(registro, campo, valor)
                registro.save(using=self.db, update_fields=list(kwargs))
            self._result_cache = None
            return len(registros)

    def bulk_update(self, objs, fields, batch_size=None):
        objs = list(objs)
        if not fields:
            raise ValueError("La actualización debe indicar al menos un campo.")
        if any(obj.pk is None for obj in objs):
            raise ValueError("Todos los registros deben tener identificador para actualizarlos.")
        with transaction.atomic(using=self.db):
            originales = self.filter(pk__in=[obj.pk for obj in objs])
            bloquear_empresas_contables(originales._empresas(), using=self.db)
            existentes = set(originales.values_list("pk", flat=True))
            for obj in objs:
                if obj.pk in existentes:
                    obj.save(using=self.db, update_fields=fields)
            return len(existentes)

    def bulk_create(self, objs, batch_size=None, ignore_conflicts=False, update_conflicts=False,
                    update_fields=None, unique_fields=None):
        if update_conflicts:
            raise ValidationError(
                "No se pueden reemplazar registros contables por conflicto. Use la actualización validada del registro existente."
            )
        objs = list(objs)
        with transaction.atomic(using=self.db):
            if self.model.__name__ == "LineaAsientoContable":
                ids = AsientoContable.objects.using(self.db).filter(
                    pk__in=[obj.asiento_id for obj in objs]
                ).values_list("empresa_id", flat=True)
            else:
                ids = [obj.empresa_id for obj in objs]
            bloquear_empresas_contables(ids, using=self.db)
            for obj in objs:
                obj.full_clean()
            return super().bulk_create(
                objs, batch_size=batch_size, ignore_conflicts=ignore_conflicts,
                update_conflicts=update_conflicts, update_fields=update_fields, unique_fields=unique_fields,
            )

    def delete(self):
        with transaction.atomic(using=self.db):
            bloquear_empresas_contables(self._empresas(), using=self.db)
            for registro in self:
                registro.validar_eliminacion(using=self.db)
            return super().delete()


class RegistroFechaContableMixin:
    """Protege tanto la fecha guardada como la nueva, sin confiar en el formulario."""

    def _datos_guardados(self, using):
        if self.pk:
            return type(self).objects.using(using).filter(pk=self.pk).values("empresa_id", "fecha").first()
        return None

    def _validar_periodos(self, using):
        anterior = self._datos_guardados(using)
        if anterior:
            validar_fecha_contable_abierta(anterior["empresa_id"], anterior["fecha"], using)
            if anterior["empresa_id"] != self.empresa_id:
                raise ValidationError("No se puede trasladar un registro contable a otra empresa.")
        validar_fecha_contable_abierta(self.empresa_id, self.fecha, using)

    def save(self, *args, **kwargs):
        using = kwargs.get("using") or router.db_for_write(type(self), instance=self)
        with transaction.atomic(using=using):
            anterior = self._datos_guardados(using)
            bloquear_empresas_contables(
                [self.empresa_id, anterior["empresa_id"] if anterior else None], using=using
            )
            self._validar_periodos(using)
            self.full_clean()
            return super().save(*args, **kwargs)

    def validar_eliminacion(self, using=None):
        using = using or self._state.db or router.db_for_write(type(self), instance=self)
        anterior = self._datos_guardados(using)
        if anterior:
            bloquear_empresas_contables([anterior["empresa_id"]], using=using)
            anterior = self._datos_guardados(using)
            if anterior:
                validar_fecha_contable_abierta(anterior["empresa_id"], anterior["fecha"], using)

    def delete(self, using=None, keep_parents=False):
        using = using or router.db_for_write(type(self), instance=self)
        with transaction.atomic(using=using):
            self.validar_eliminacion(using=using)
            return super().delete(using=using, keep_parents=keep_parents)


class CuentaContable(models.Model):
    TIPO_CHOICES = [
        ("activo", "Activo"),
        ("pasivo", "Pasivo"),
        ("patrimonio", "Patrimonio"),
        ("ingreso", "Ingreso"),
        ("costo", "Costo"),
        ("gasto", "Gasto"),
        ("orden", "Control extracontable"),
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
    actividad_flujo = models.CharField(
        max_length=20, default="auto", blank=True,
        choices=[("auto", "Clasificación automática"), ("operacion", "Operación"),
                 ("inversion", "Inversión"), ("financiamiento", "Financiamiento")],
    )
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
        if self.tipo == "orden" and (self.acepta_movimientos or self.activa):
            raise ValidationError("Las cuentas de control extracontable se conservan inactivas y no admiten asientos financieros.")
        if self.cuenta_padre_id:
            if (self.tipo == "orden") != (self.cuenta_padre.tipo == "orden"):
                raise ValidationError("Las cuentas financieras y los controles extracontables deben conservar jerarquías separadas.")
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
    isv_compras_recuperable = models.BooleanField(
        default=False,
        verbose_name="ISV de compras recuperable",
        help_text="Active esta opción únicamente cuando esta empresa pueda recuperar el ISV de sus compras. "
        "El impuesto se registrará como crédito fiscal y se excluirá del costo del inventario.",
    )
    cuenta_isv_acreditable = models.ForeignKey(
        CuentaContable, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="configuraciones_isv_acreditable",
    )
    cuenta_entradas_inventario = models.ForeignKey(
        CuentaContable, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="configuraciones_entradas_inventario",
    )
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Configuracion contable por empresa"
        verbose_name_plural = "Configuraciones contables por empresa"

    def __str__(self):
        return f"Configuracion contable - {self.empresa.nombre}"

    def clean(self):
        super().clean()
        tipos = {
            'cuenta_caja': 'activo', 'cuenta_bancos': 'activo', 'cuenta_clientes': 'activo',
            'cuenta_inventario': 'activo', 'cuenta_isv_acreditable': 'activo',
            'cuenta_proveedores': 'pasivo', 'cuenta_isv_por_pagar': 'pasivo',
            'cuenta_ventas': 'ingreso', 'cuenta_devoluciones_ventas': 'ingreso',
        }
        for campo in self._meta.fields:
            if isinstance(campo, models.ForeignKey) and campo.related_model is CuentaContable:
                cuenta = getattr(self, campo.name, None)
                if cuenta and cuenta.empresa_id != self.empresa_id:
                    raise ValidationError({campo.name: "La cuenta debe pertenecer a esta empresa."})
                if cuenta and campo.name in tipos and cuenta.tipo != tipos[campo.name]:
                    raise ValidationError({campo.name: f"Seleccione una cuenta de tipo {tipos[campo.name]} para esta operación."})

    def save(self, *args, **kwargs):
        if self.pk:
            empresa_anterior = type(self).objects.filter(pk=self.pk).values_list("empresa_id", flat=True).first()
            if empresa_anterior and empresa_anterior != self.empresa_id:
                raise ValidationError("No se puede trasladar la configuración contable a otra empresa.")
        self.full_clean()
        return super().save(*args, **kwargs)


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
    objects = RegistrosContablesQuerySet.as_manager()

    class Meta:
        ordering = ["-anio", "-mes"]
        unique_together = ("empresa", "anio", "mes")
        verbose_name = "Periodo contable"
        verbose_name_plural = "Periodos contables"

    def clean(self):
        if not isinstance(self.anio, int) or not 1 <= self.anio <= 9999:
            raise ValidationError({"anio": "El año debe estar entre 1 y 9999."})
        if self.mes < 1 or self.mes > 12:
            raise ValidationError("El mes debe estar entre 1 y 12.")
        anterior = self._datos_guardados()
        if anterior and any(
            anterior[campo] != getattr(self, campo) for campo in ("empresa_id", "anio", "mes")
        ):
            raise ValidationError("La empresa, el año y el mes de un período existente no se pueden modificar.")

    def _datos_guardados(self, using=None):
        using = using or self._state.db or router.db_for_write(type(self), instance=self)
        if self.pk:
            return type(self).objects.using(using).filter(pk=self.pk).values(
                "empresa_id", "anio", "mes", "estado", "cerrado_por_id", "fecha_cierre"
            ).first()
        return None

    def save(self, *args, **kwargs):
        using = kwargs.get("using") or router.db_for_write(type(self), instance=self)
        with transaction.atomic(using=using):
            anterior = self._datos_guardados(using)
            bloquear_empresas_contables(
                [self.empresa_id, anterior["empresa_id"] if anterior else None], using=using
            )
            anterior = self._datos_guardados(using)
            if anterior and not getattr(self, "_transicion_estado_autorizada", False) and any(
                anterior[campo] != getattr(self, campo)
                for campo in ("estado", "cerrado_por_id", "fecha_cierre")
            ):
                raise ValidationError("Use la acción autorizada de cierre o reapertura para cambiar el estado del período.")
            self.full_clean()
            return super().save(*args, **kwargs)

    def cambiar_estado(self, estado, *, cerrado_por=None, fecha_cierre=None):
        """Persistencia de la transición; el servicio autorizado verifica los pendientes."""
        if estado not in dict(self.ESTADO_CHOICES):
            raise ValidationError("El estado del período no es válido.")
        if not self.pk:
            raise ValidationError("Guarde el período antes de cerrarlo o reabrirlo.")
        using = self._state.db or router.db_for_write(type(self), instance=self)
        with transaction.atomic(using=using):
            bloquear_empresas_contables([self.empresa_id], using=using)
            anterior = self._datos_guardados(using)
            if not anterior:
                raise ValidationError("El período contable ya no existe.")
            if anterior["estado"] == estado:
                self.estado = anterior["estado"]
                self.cerrado_por_id = anterior["cerrado_por_id"]
                self.fecha_cierre = anterior["fecha_cierre"]
                return self
            self.estado = estado
            self.cerrado_por = cerrado_por if estado == "cerrado" else None
            self.fecha_cierre = fecha_cierre if estado == "cerrado" else None
            self._transicion_estado_autorizada = True
            try:
                self.save(using=using, update_fields=["estado", "cerrado_por", "fecha_cierre"])
            finally:
                self._transicion_estado_autorizada = False
            return self

    def validar_eliminacion(self, using=None):
        using = using or self._state.db or router.db_for_write(type(self), instance=self)
        anterior = self._datos_guardados(using)
        if anterior:
            bloquear_empresas_contables([anterior["empresa_id"]], using=using)
            if self._datos_guardados(using)["estado"] == "cerrado":
                raise ValidationError("No se puede eliminar un período cerrado. Use la reapertura autorizada.")

    def delete(self, using=None, keep_parents=False):
        using = using or router.db_for_write(type(self), instance=self)
        with transaction.atomic(using=using):
            self.validar_eliminacion(using=using)
            return super().delete(using=using, keep_parents=keep_parents)

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


class AsientoContable(RegistroFechaContableMixin, models.Model):
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
    moneda_origen = models.CharField(max_length=3, choices=[("HNL", "Lempiras"), ("USD", "Dólares")], default="HNL")
    tipo_cambio = models.DecimalField(max_digits=14, decimal_places=6, default=1)
    conversion_verificada = models.BooleanField(default=True, editable=False)
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="asientos_creados",
    )
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    objects = RegistrosContablesQuerySet.as_manager()

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
            models.UniqueConstraint(
                fields=["empresa", "documento_tipo", "documento_id", "evento"],
                condition=(
                    models.Q(documento_id__isnull=False)
                    & ~models.Q(documento_tipo__exact="")
                    & ~models.Q(evento__exact="")
                ),
                name="unique_asiento_evento_documento_empresa",
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
        if self.pk and RegularizacionMonedaFactura.objects.filter(
            models.Q(asiento_origen_id=self.pk) | models.Q(asiento_ajuste_id=self.pk)
        ).exists():
            campos = ['empresa_id', 'fecha', 'estado', 'moneda_origen', 'tipo_cambio', 'documento_tipo', 'documento_id', 'evento']
            anterior = type(self).objects.filter(pk=self.pk).values(*campos).first()
            if anterior and any(anterior[campo] != getattr(self, campo) for campo in campos):
                raise ValidationError('El asiento respalda una regularización monetaria. Conserve su estado, fecha y documento original.')
        from .proteccion_conversion import validar_asiento_revisado
        validar_asiento_revisado(self, self._state.db or "default")
        from .moneda import validar_moneda_tipo_cambio
        self.moneda_origen, self.tipo_cambio = validar_moneda_tipo_cambio(self.moneda_origen, self.tipo_cambio)
        self._validar_periodos(self._state.db or router.db_for_write(type(self), instance=self))


class RegularizacionMonedaFactura(models.Model):
    empresa = models.ForeignKey(Empresa, on_delete=models.PROTECT)
    factura = models.OneToOneField('facturacion.Factura', on_delete=models.PROTECT, related_name='regularizacion_moneda')
    asiento_origen = models.OneToOneField(AsientoContable, on_delete=models.PROTECT, related_name='regularizacion_moneda')
    asiento_ajuste = models.OneToOneField(AsientoContable, on_delete=models.PROTECT, null=True, blank=True, related_name='revision_moneda_aplicada')
    cuenta_cliente = models.ForeignKey(CuentaContable, on_delete=models.PROTECT)
    fecha = models.DateField()
    tipo_cambio = models.DecimalField(max_digits=14, decimal_places=6)
    total_original = models.DecimalField(max_digits=14, decimal_places=2)
    total_hnl = models.DecimalField(max_digits=14, decimal_places=2)
    saldo_original = models.DecimalField(max_digits=14, decimal_places=2)
    saldo_hnl_anterior = models.DecimalField(max_digits=14, decimal_places=2)
    saldo_hnl_corregido = models.DecimalField(max_digits=14, decimal_places=2)
    aplicado_original = models.DecimalField(max_digits=14, decimal_places=2)
    aplicado_hnl_anterior = models.DecimalField(max_digits=14, decimal_places=2)
    aplicado_hnl_corregido = models.DecimalField(max_digits=14, decimal_places=2)
    notas_original = models.DecimalField(max_digits=14, decimal_places=2)
    notas_hnl_anterior = models.DecimalField(max_digits=14, decimal_places=2)
    notas_hnl_corregido = models.DecimalField(max_digits=14, decimal_places=2)
    motivo = models.TextField()
    creado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Regularización de moneda de factura'
        verbose_name_plural = 'Regularizaciones de moneda de facturas'

    def clean(self):
        super().clean()
        from .moneda import validar_moneda_tipo_cambio
        _moneda, self.tipo_cambio = validar_moneda_tipo_cambio('USD', self.tipo_cambio)
        validar_fecha_contable_abierta(self.empresa_id, self.fecha)
        if (self.factura.empresa_id != self.empresa_id or self.asiento_origen.empresa_id != self.empresa_id
                or self.cuenta_cliente.empresa_id != self.empresa_id):
            raise ValidationError('La factura, el asiento y la cuenta deben pertenecer a esta empresa.')
        if self.asiento_origen.documento_tipo != 'factura' or self.asiento_origen.documento_id != self.factura_id:
            raise ValidationError('La revisión debe corresponder al asiento original de esta factura.')
        if self.asiento_ajuste_id and self.asiento_ajuste.empresa_id != self.empresa_id:
            raise ValidationError('El asiento de ajuste debe pertenecer a esta empresa.')

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError('La regularización conserva sus datos originales. Registre un nuevo ajuste contable para corregirla.')
        self.full_clean()
        return super().save(*args, **kwargs)


class LineaAsientoContable(models.Model):
    asiento = models.ForeignKey(AsientoContable, on_delete=models.CASCADE, related_name="lineas")
    cuenta = models.ForeignKey(CuentaContable, on_delete=models.PROTECT, related_name="lineas_asiento")
    detalle = models.CharField(max_length=200, blank=True, null=True)
    debe = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    haber = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    moneda_origen = models.CharField(max_length=3, choices=[("HNL", "Lempiras"), ("USD", "Dólares")], default="HNL")
    tipo_cambio = models.DecimalField(max_digits=14, decimal_places=6, default=1)
    debe_origen = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    haber_origen = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    objects = RegistrosContablesQuerySet.as_manager()

    class Meta:
        verbose_name = "Linea de asiento"
        verbose_name_plural = "Lineas de asiento"

    def clean(self):
        if self.cuenta_id and self.cuenta.tipo == "orden":
            raise ValidationError("Las cuentas de control extracontable son de consulta y no admiten asientos financieros.")
        from .proteccion_conversion import validar_partida_revisada
        validar_partida_revisada(
            [self.asiento_id, self._asiento_anterior_id(self._state.db or "default")], self._state.db or "default",
        )
        if self.asiento_id and RegularizacionMonedaFactura.objects.filter(
            models.Q(asiento_origen_id=self.asiento_id) | models.Q(asiento_ajuste_id=self.asiento_id)
        ).exists():
            raise ValidationError('La partida respalda una regularización monetaria. Registre un ajuste nuevo para conservar el saldo revisado.')
        from .moneda import validar_moneda_tipo_cambio
        self.moneda_origen, self.tipo_cambio = validar_moneda_tipo_cambio(self.moneda_origen, self.tipo_cambio)
        if self.moneda_origen == "HNL":
            self.debe_origen, self.haber_origen = self.debe, self.haber
        if self.debe_origen < 0 or self.haber_origen < 0:
            raise ValidationError("Los importes de origen no pueden ser negativos.")
        if self.debe_origen > 0 and self.haber_origen > 0:
            raise ValidationError("El importe de origen debe estar en una sola columna.")
        self._validar_asientos(self._state.db or router.db_for_write(type(self), instance=self))
        if self.debe < 0 or self.haber < 0:
            raise ValidationError("Los valores contables no pueden ser negativos.")
        if self.debe == 0 and self.haber == 0:
            raise ValidationError("Cada linea debe tener valor en debe o haber.")
        if self.debe > 0 and self.haber > 0:
            raise ValidationError("Una misma linea no puede tener debe y haber al mismo tiempo.")
        if self.asiento_id and self.cuenta_id and self.asiento.empresa_id != self.cuenta.empresa_id:
            raise ValidationError("La cuenta contable debe pertenecer a la misma empresa del asiento.")

    def _asiento_anterior_id(self, using):
        if self.pk:
            return type(self).objects.using(using).filter(pk=self.pk).values_list("asiento_id", flat=True).first()
        return None

    def _asientos_guardados(self, using):
        return AsientoContable.objects.using(using).filter(
            pk__in=[pk for pk in (self.asiento_id, self._asiento_anterior_id(using)) if pk]
        ).values("empresa_id", "fecha")

    def _validar_asientos(self, using):
        asientos = list(self._asientos_guardados(using))
        if len({asiento["empresa_id"] for asiento in asientos}) > 1:
            raise ValidationError("No se puede trasladar una línea contable a otra empresa.")
        for asiento in asientos:
            validar_fecha_contable_abierta(asiento["empresa_id"], asiento["fecha"], using)

    def save(self, *args, **kwargs):
        using = kwargs.get("using") or router.db_for_write(type(self), instance=self)
        with transaction.atomic(using=using):
            bloquear_empresas_contables(
                [asiento["empresa_id"] for asiento in self._asientos_guardados(using)], using=using
            )
            self.full_clean()
            return super().save(*args, **kwargs)

    def validar_eliminacion(self, using=None):
        using = using or self._state.db or router.db_for_write(type(self), instance=self)
        from .proteccion_conversion import validar_partida_revisada
        validar_partida_revisada([self.asiento_id, self._asiento_anterior_id(using)], using)
        if self.asiento_id and RegularizacionMonedaFactura.objects.using(using).filter(
            models.Q(asiento_origen_id=self.asiento_id) | models.Q(asiento_ajuste_id=self.asiento_id)
        ).exists():
            raise ValidationError('La partida respalda una regularización monetaria y conserva sus importes originales.')
        asiento_id = self._asiento_anterior_id(using)
        asiento = AsientoContable.objects.using(using).filter(pk=asiento_id).values("empresa_id", "fecha").first()
        if asiento:
            bloquear_empresas_contables([asiento["empresa_id"]], using=using)
            asiento = AsientoContable.objects.using(using).filter(
                pk=self._asiento_anterior_id(using)
            ).values("empresa_id", "fecha").first()
            if asiento:
                validar_fecha_contable_abierta(asiento["empresa_id"], asiento["fecha"], using)

    def delete(self, using=None, keep_parents=False):
        using = using or router.db_for_write(type(self), instance=self)
        with transaction.atomic(using=using):
            self.validar_eliminacion(using=using)
            return super().delete(using=using, keep_parents=keep_parents)

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
        if self.cuenta_contable_id:
            tipo_requerido = "pasivo" if self.tipo == "tarjeta_credito" else "activo"
            if self.cuenta_contable.tipo != tipo_requerido:
                raise ValidationError({
                    "cuenta_contable": "Una tarjeta de crédito debe usar una cuenta de pasivo; caja y banco deben usar cuentas de activo."
                })

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


class MovimientoBancario(RegistroFechaContableMixin, models.Model):
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
    objects = RegistrosContablesQuerySet.as_manager()

    class Meta:
        ordering = ["-fecha", "-id"]
        indexes = [
            models.Index(fields=["empresa", "cuenta_financiera", "fecha"]),
        ]

    def clean(self):
        super().clean()
        self._validar_periodos(self._state.db or router.db_for_write(type(self), instance=self))
        if self.cuenta_financiera_id and self.cuenta_financiera.empresa_id != self.empresa_id:
            raise ValidationError("La cuenta financiera debe pertenecer a la misma empresa.")
        if self.clasificacion_id and self.clasificacion.empresa_id != self.empresa_id:
            raise ValidationError("La clasificacion debe pertenecer a la misma empresa.")
        if self.asiento_id and self.asiento.empresa_id != self.empresa_id:
            raise ValidationError("El asiento debe pertenecer a la misma empresa del movimiento bancario.")
        if self.pago_factura_id and self.pago_factura.factura.empresa_id != self.empresa_id:
            raise ValidationError("El cobro debe pertenecer a la misma empresa del movimiento bancario.")
        if self.pago_compra_id and self.pago_compra.compra.empresa_id != self.empresa_id:
            raise ValidationError("El pago debe pertenecer a la misma empresa del movimiento bancario.")
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


class RevisionesConversionQuerySet(models.QuerySet):
    """La evidencia confirmada no se reemplaza ni elimina mediante escrituras en lote."""

    def update(self, **kwargs):
        raise ValidationError("La revisión de conversión es inmutable. Registre un ajuste nuevo para corregirla.")

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValidationError("La revisión de conversión es inmutable. Registre un ajuste nuevo para corregirla.")

    def bulk_create(self, objs, **kwargs):
        raise ValidationError("Registre cada revisión de conversión mediante el servicio autorizado y su vista previa.")

    def delete(self):
        raise ValidationError("Conserve la revisión de conversión y su respaldo contable original.")


class RevisionConversionAsiento(models.Model):
    empresa = models.ForeignKey(Empresa, on_delete=models.PROTECT)
    origen = models.OneToOneField(AsientoContable, on_delete=models.PROTECT, related_name="revision_conversion")
    ajuste = models.OneToOneField(
        AsientoContable, on_delete=models.PROTECT, related_name="revision_conversion_aplicada",
        null=True, blank=True,
    )
    fecha = models.DateField()
    partidas = models.JSONField()
    firma_fuente = models.CharField(max_length=64)
    cuenta_contrapartida = models.ForeignKey(CuentaContable, on_delete=models.PROTECT, null=True, blank=True)
    motivo = models.TextField()
    creado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    objects = RevisionesConversionQuerySet.as_manager()

    class Meta:
        verbose_name = "Revisión integral de conversión histórica"
        verbose_name_plural = "Revisiones integrales de conversión histórica"

    def clean(self):
        super().clean()
        if not self.origen_id or not self.empresa_id or not self.fecha:
            raise ValidationError("Indique el asiento original, la empresa y la fecha de la revisión.")
        validar_fecha_contable_abierta(self.empresa_id, self.fecha)
        if self.origen.empresa_id != self.empresa_id:
            raise ValidationError("El asiento original debe pertenecer a la empresa de la revisión.")
        if self.cuenta_contrapartida_id and self.cuenta_contrapartida.empresa_id != self.empresa_id:
            raise ValidationError("La contrapartida debe pertenecer a la empresa de la revisión.")
        if self.ajuste_id and (
            self.ajuste.empresa_id != self.empresa_id or self.ajuste.estado != "contabilizado"
            or self.ajuste.fecha != self.fecha or self.ajuste.documento_tipo != "revision_conversion"
            or self.ajuste.documento_id != self.origen_id or self.ajuste.evento != "revision_integral"
        ):
            raise ValidationError("El ajuste debe corresponder a este asiento y estar contabilizado en la fecha y empresa autorizadas.")
        if not isinstance(self.partidas, list):
            raise ValidationError("La revisión debe conservar el detalle completo de sus partidas confirmadas.")
        if not 10 <= len((self.motivo or "").strip()) <= 2000:
            raise ValidationError("Describa el motivo y respaldo de la revisión entre 10 y 2,000 caracteres.")
        from .revision_conversion import vista_previa_revision

        previa = vista_previa_revision(self.origen, self.partidas, self.cuenta_contrapartida)
        if self.partidas != previa["partidas"] or self.firma_fuente != previa["firma_fuente"]:
            raise ValidationError("Las partidas confirmadas deben coincidir con el documento y su vista previa vigente.")
        if self.fecha < previa["fecha_minima"]:
            raise ValidationError("La revisión no puede anticipar la fecha del asiento original ni sus ajustes previos.")
        if previa["requiere_contrapartida"] and not self.cuenta_contrapartida_id:
            raise ValidationError("La revisión requiere una contrapartida autorizada para quedar balanceada.")
        if bool(previa["lineas_ajuste"]) != bool(self.ajuste_id):
            raise ValidationError("La revisión debe conservar el asiento que publica todas las diferencias confirmadas.")
        if self.ajuste_id:
            esperadas = {
                linea["cuenta_id"]: Decimal(linea["debe"]) - Decimal(linea["haber"])
                for linea in previa["lineas_ajuste"]
            }
            publicadas = {}
            for linea in self.ajuste.lineas.select_related("cuenta"):
                if linea.cuenta.empresa_id != self.empresa_id:
                    raise ValidationError("Todas las cuentas del ajuste deben pertenecer a esta empresa.")
                publicadas[linea.cuenta_id] = publicadas.get(linea.cuenta_id, Decimal("0")) + linea.debe - linea.haber
            if publicadas != esperadas or not self.ajuste.esta_balanceado:
                raise ValidationError("Las partidas publicadas del ajuste no coinciden con la revisión confirmada.")

    def save(self, *args, **kwargs):
        using = kwargs.get("using") or router.db_for_write(type(self), instance=self)
        with transaction.atomic(using=using):
            bloquear_empresas_contables([self.empresa_id], using=using)
            if self.pk and type(self).objects.using(using).filter(pk=self.pk).exists():
                raise ValidationError("La revisión de conversión es inmutable. Registre un ajuste nuevo para corregirla.")
            self.full_clean()
            return super().save(*args, **kwargs)

    def delete(self, using=None, keep_parents=False):
        raise ValidationError("Conserve la revisión de conversión y su respaldo contable original.")
