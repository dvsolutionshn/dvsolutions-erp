from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from core.models import Empresa


class ConfiguracionTecnicentro(models.Model):
    empresa = models.OneToOneField(Empresa, on_delete=models.CASCADE, related_name="configuracion_tecnicentro")
    nombre_comercial = models.CharField(max_length=160, blank=True)
    tiempo_diagnostico_minutos = models.PositiveIntegerField(default=30)
    tiempo_recepcion_minutos = models.PositiveIntegerField(default=15)
    mensaje_recepcion = models.CharField(max_length=300, default="Tu vehiculo fue recibido correctamente.")
    mensaje_listo = models.CharField(max_length=300, default="Tu vehiculo esta listo para ser retirado.")
    notificar_whatsapp = models.BooleanField(default=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.nombre_comercial or f"Tecnicentro - {self.empresa.nombre}"


class BahiaServicio(models.Model):
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="bahias_tecnicentro")
    codigo = models.CharField(max_length=20)
    nombre = models.CharField(max_length=100)
    especialidad = models.CharField(max_length=120, blank=True)
    activa = models.BooleanField(default=True)

    class Meta:
        ordering = ["codigo"]
        constraints = [models.UniqueConstraint(fields=["empresa", "codigo"], name="unique_bahia_codigo_empresa")]

    def __str__(self):
        return f"{self.codigo} · {self.nombre}"


class Vehiculo(models.Model):
    TIPO_CHOICES = [
        ("turismo", "Turismo"), ("pickup", "Pickup"), ("suv", "SUV"),
        ("camion", "Camion"), ("moto", "Motocicleta"), ("otro", "Otro"),
    ]
    COMBUSTIBLE_CHOICES = [
        ("gasolina", "Gasolina"), ("diesel", "Diesel"), ("hibrido", "Hibrido"),
        ("electrico", "Electrico"), ("gas", "Gas"),
    ]

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="vehiculos_tecnicentro")
    cliente = models.ForeignKey("facturacion.Cliente", on_delete=models.PROTECT, related_name="vehiculos")
    placa = models.CharField(max_length=20)
    vin = models.CharField(max_length=40, blank=True)
    marca = models.CharField(max_length=80)
    modelo = models.CharField(max_length=80)
    anio = models.PositiveIntegerField(null=True, blank=True)
    color = models.CharField(max_length=50, blank=True)
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES, default="turismo")
    combustible = models.CharField(max_length=20, choices=COMBUSTIBLE_CHOICES, default="gasolina")
    kilometraje_actual = models.PositiveIntegerField(default=0)
    activo = models.BooleanField(default=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["placa"]
        constraints = [models.UniqueConstraint(fields=["empresa", "placa"], name="unique_vehiculo_placa_empresa")]

    def clean(self):
        self.placa = (self.placa or "").strip().upper()
        if self.cliente_id and self.empresa_id and self.cliente.empresa_id != self.empresa_id:
            raise ValidationError("El cliente del vehiculo debe pertenecer a la misma empresa.")

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.placa} · {self.marca} {self.modelo}"


class OrdenServicio(models.Model):
    ESTADO_CHOICES = [
        ("espera", "En cola"), ("recepcion", "Recepcion tecnica"),
        ("diagnostico", "Diagnostico"), ("cotizacion", "Cotizacion"),
        ("aprobacion", "Esperando aprobacion"), ("reparacion", "En reparacion"),
        ("pruebas", "Pruebas de calidad"), ("listo", "Listo para entregar"),
        ("entregado", "Entregado"), ("cancelado", "Cancelado"),
    ]
    PRIORIDAD_CHOICES = [("normal", "Normal"), ("alta", "Alta"), ("urgente", "Urgente")]
    NIVEL_COMBUSTIBLE = [("reserva", "Reserva"), ("cuarto", "1/4"), ("medio", "1/2"), ("tres_cuartos", "3/4"), ("lleno", "Lleno")]

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="ordenes_tecnicentro")
    numero = models.CharField(max_length=30, blank=True)
    cliente = models.ForeignKey("facturacion.Cliente", on_delete=models.PROTECT, related_name="ordenes_taller")
    vehiculo = models.ForeignKey(Vehiculo, on_delete=models.PROTECT, related_name="ordenes")
    asesor_recepcion = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="ordenes_taller_recibidas")
    tecnico_asignado = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="ordenes_taller_asignadas")
    bahia = models.ForeignKey(BahiaServicio, on_delete=models.SET_NULL, null=True, blank=True, related_name="ordenes")
    factura = models.ForeignKey("facturacion.Factura", on_delete=models.SET_NULL, null=True, blank=True, related_name="ordenes_taller")
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default="espera", db_index=True)
    prioridad = models.CharField(max_length=15, choices=PRIORIDAD_CHOICES, default="normal")
    motivo_ingreso = models.TextField()
    observaciones_recepcion = models.TextField(blank=True)
    kilometraje_entrada = models.PositiveIntegerField(default=0)
    nivel_combustible = models.CharField(max_length=20, choices=NIVEL_COMBUSTIBLE, default="medio")
    deja_vehiculo = models.BooleanField(default=True)
    autoriza_whatsapp = models.BooleanField(default=True)
    tiempo_espera_estimado_min = models.PositiveIntegerField(default=30)
    tiempo_reparacion_estimado_min = models.PositiveIntegerField(default=60)
    fecha_recepcion = models.DateTimeField(default=timezone.now, db_index=True)
    fecha_ingreso_taller = models.DateTimeField(null=True, blank=True)
    fecha_estimada_finalizacion = models.DateTimeField(null=True, blank=True)
    fecha_listo = models.DateTimeField(null=True, blank=True)
    fecha_entrega = models.DateTimeField(null=True, blank=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-fecha_recepcion", "-id"]
        constraints = [models.UniqueConstraint(fields=["empresa", "numero"], name="unique_orden_taller_numero_empresa")]

    def generar_numero(self):
        if self.numero:
            return self.numero
        with transaction.atomic():
            ultimo = OrdenServicio.objects.select_for_update().filter(empresa=self.empresa).order_by("-id").first()
            consecutivo = (ultimo.id + 1) if ultimo else 1
            self.numero = f"OT-{timezone.localdate():%Y}-{consecutivo:06d}"
        return self.numero

    def save(self, *args, **kwargs):
        self.generar_numero()
        self.full_clean()
        return super().save(*args, **kwargs)

    @property
    def minutos_en_taller(self):
        fin = self.fecha_entrega or timezone.now()
        return max(0, int((fin - self.fecha_recepcion).total_seconds() // 60))

    @property
    def progreso(self):
        mapa = {"espera": 8, "recepcion": 16, "diagnostico": 28, "cotizacion": 40, "aprobacion": 50, "reparacion": 68, "pruebas": 82, "listo": 94, "entregado": 100, "cancelado": 0}
        return mapa.get(self.estado, 0)

    def __str__(self):
        return f"{self.numero} · {self.vehiculo.placa}"


class CitaTaller(models.Model):
    ESTADO_CHOICES = [
        ("programada", "Programada"), ("confirmada", "Confirmada"),
        ("atendida", "Atendida"), ("no_asistio", "No asistió"),
        ("cancelada", "Cancelada"),
    ]

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="citas_tecnicentro")
    cliente = models.ForeignKey("facturacion.Cliente", on_delete=models.PROTECT, related_name="citas_taller")
    vehiculo = models.ForeignKey(Vehiculo, on_delete=models.SET_NULL, null=True, blank=True, related_name="citas")
    orden = models.OneToOneField(OrdenServicio, on_delete=models.SET_NULL, null=True, blank=True, related_name="cita_origen")
    fecha_hora = models.DateTimeField(db_index=True)
    servicio_solicitado = models.CharField(max_length=220)
    duracion_estimada_min = models.PositiveIntegerField(default=60)
    estado = models.CharField(max_length=15, choices=ESTADO_CHOICES, default="programada", db_index=True)
    observaciones = models.TextField(blank=True)
    creado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["fecha_hora", "id"]

    def clean(self):
        if self.cliente_id and self.empresa_id and self.cliente.empresa_id != self.empresa_id:
            raise ValidationError("El cliente de la cita debe pertenecer a la misma empresa.")
        if self.vehiculo_id and self.vehiculo.empresa_id != self.empresa_id:
            raise ValidationError("El vehículo de la cita debe pertenecer a la misma empresa.")

    def __str__(self):
        return f"{self.fecha_hora:%d/%m/%Y %H:%M} · {self.cliente.nombre}"


class InspeccionRecepcion(models.Model):
    orden = models.OneToOneField(OrdenServicio, on_delete=models.CASCADE, related_name="inspeccion_recepcion")
    carroceria = models.CharField(max_length=20, choices=[("buena", "Sin daños visibles"), ("observaciones", "Con observaciones"), ("danada", "Daño evidente")], default="buena")
    llantas = models.CharField(max_length=20, choices=[("buenas", "Buen estado"), ("desgaste", "Desgaste visible"), ("danadas", "Daño o presión baja")], default="buenas")
    parabrisas = models.CharField(max_length=20, choices=[("bueno", "Sin daños"), ("marcas", "Marcas menores"), ("quebrado", "Quebrado o fisurado")], default="bueno")
    luces_tablero_activas = models.BooleanField(default=False)
    porta_documentos = models.BooleanField(default=False)
    llanta_repuesto = models.BooleanField(default=False)
    herramientas = models.BooleanField(default=False)
    radio_pantalla = models.BooleanField(default=False)
    objetos_valor = models.TextField(blank=True)
    danos_existentes = models.TextField(blank=True)
    observaciones = models.TextField(blank=True)
    aceptacion_cliente = models.BooleanField(default=False)
    nombre_aceptante = models.CharField(max_length=160, blank=True)
    inspeccionado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    fecha = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Inspección de recepción · {self.orden.numero}"


class HistorialEstadoOrden(models.Model):
    orden = models.ForeignKey(OrdenServicio, on_delete=models.CASCADE, related_name="historial_estados")
    estado = models.CharField(max_length=20, choices=OrdenServicio.ESTADO_CHOICES)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    nota = models.CharField(max_length=300, blank=True)
    fecha = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-fecha", "-id"]


class DiagnosticoVehicular(models.Model):
    ESTADO_CHOICES = [("borrador", "Borrador"), ("completado", "Completado")]
    orden = models.OneToOneField(OrdenServicio, on_delete=models.CASCADE, related_name="diagnostico")
    tecnico = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    sintomas_reportados = models.TextField(blank=True)
    hallazgos = models.TextField()
    causa_probable = models.TextField(blank=True)
    recomendaciones = models.TextField(blank=True)
    requiere_prueba_ruta = models.BooleanField(default=False)
    estado = models.CharField(max_length=15, choices=ESTADO_CHOICES, default="borrador")
    fecha_inicio = models.DateTimeField(default=timezone.now)
    fecha_finalizacion = models.DateTimeField(null=True, blank=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Diagnostico {self.orden.numero}"


class EvidenciaOrden(models.Model):
    ETAPA_CHOICES = [("recepcion", "Recepcion"), ("diagnostico", "Diagnostico"), ("reparacion", "Reparacion"), ("resultado", "Resultado final")]
    orden = models.ForeignKey(OrdenServicio, on_delete=models.CASCADE, related_name="evidencias")
    etapa = models.CharField(max_length=20, choices=ETAPA_CHOICES)
    imagen = models.ImageField(upload_to="tecnicentro/evidencias/%Y/%m/")
    descripcion = models.CharField(max_length=240, blank=True)
    subido_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    fecha = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-fecha", "-id"]


class CotizacionTaller(models.Model):
    ESTADO_CHOICES = [("borrador", "Borrador"), ("enviada", "Enviada"), ("aprobada", "Aprobada"), ("rechazada", "Rechazada")]
    orden = models.OneToOneField(OrdenServicio, on_delete=models.CASCADE, related_name="cotizacion")
    estado = models.CharField(max_length=15, choices=ESTADO_CHOICES, default="borrador")
    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    impuesto = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    notas = models.TextField(blank=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_aprobacion = models.DateTimeField(null=True, blank=True)

    def recalcular(self):
        self.subtotal = sum((linea.subtotal for linea in self.lineas.all()), Decimal("0.00"))
        self.impuesto = sum((linea.impuesto_monto for linea in self.lineas.all()), Decimal("0.00"))
        self.total = self.subtotal + self.impuesto
        self.save(update_fields=["subtotal", "impuesto", "total"])


class LineaCotizacionTaller(models.Model):
    TIPO_CHOICES = [("repuesto", "Repuesto"), ("mano_obra", "Mano de obra"), ("servicio", "Servicio externo")]
    cotizacion = models.ForeignKey(CotizacionTaller, on_delete=models.CASCADE, related_name="lineas")
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    producto = models.ForeignKey("facturacion.Producto", on_delete=models.PROTECT, null=True, blank=True, related_name="lineas_cotizacion_taller")
    descripcion = models.CharField(max_length=220)
    cantidad = models.DecimalField(max_digits=10, decimal_places=2, default=1)
    precio_unitario = models.DecimalField(max_digits=14, decimal_places=2)
    porcentaje_impuesto = models.DecimalField(max_digits=6, decimal_places=2, default=0)

    @property
    def subtotal(self):
        return (self.cantidad * self.precio_unitario).quantize(Decimal("0.01"))

    @property
    def impuesto_monto(self):
        return (self.subtotal * self.porcentaje_impuesto / Decimal("100")).quantize(Decimal("0.01"))

    def clean(self):
        if self.producto_id and self.producto.empresa_id != self.cotizacion.orden.empresa_id:
            raise ValidationError("El repuesto debe pertenecer a la misma empresa.")
