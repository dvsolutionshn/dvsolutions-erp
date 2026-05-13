from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from calendar import monthrange
from datetime import date


class PlanComercial(models.Model):
    nombre = models.CharField(max_length=120)
    codigo = models.SlugField(unique=True)
    descripcion = models.TextField(blank=True, null=True)
    precio_mensual = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ["nombre"]
        verbose_name = "Plan comercial"
        verbose_name_plural = "Planes comerciales"

    def __str__(self):
        return self.nombre


class RolSistema(models.Model):
    nombre = models.CharField(max_length=120)
    codigo = models.SlugField(unique=True)
    descripcion = models.TextField(blank=True, null=True)
    activo = models.BooleanField(default=True)
    puede_facturas = models.BooleanField(default=False)
    puede_clientes = models.BooleanField(default=False)
    puede_productos = models.BooleanField(default=False)
    puede_proveedores = models.BooleanField(default=False)
    puede_inventario = models.BooleanField(default=False)
    puede_compras = models.BooleanField(default=False)
    puede_cai = models.BooleanField(default=False)
    puede_impuestos = models.BooleanField(default=False)
    puede_notas_credito = models.BooleanField(default=False)
    puede_recibos = models.BooleanField(default=False)
    puede_egresos = models.BooleanField(default=False)
    puede_reportes = models.BooleanField(default=False)
    puede_cxc = models.BooleanField(default=False)
    puede_cxp = models.BooleanField(default=False)
    puede_contabilidad = models.BooleanField(default=False)
    puede_crear_facturas = models.BooleanField(default=False)
    puede_editar_facturas = models.BooleanField(default=False)
    puede_anular_facturas = models.BooleanField(default=False)
    puede_eliminar_borradores = models.BooleanField(default=False)
    puede_registrar_pagos_clientes = models.BooleanField(default=False)
    puede_crear_clientes = models.BooleanField(default=False)
    puede_editar_clientes = models.BooleanField(default=False)
    puede_crear_productos = models.BooleanField(default=False)
    puede_editar_productos = models.BooleanField(default=False)
    puede_crear_proveedores = models.BooleanField(default=False)
    puede_editar_proveedores = models.BooleanField(default=False)
    puede_ajustar_inventario = models.BooleanField(default=False)
    puede_crear_compras = models.BooleanField(default=False)
    puede_editar_compras = models.BooleanField(default=False)
    puede_aplicar_compras = models.BooleanField(default=False)
    puede_anular_compras = models.BooleanField(default=False)
    puede_registrar_pagos_proveedores = models.BooleanField(default=False)
    puede_crear_notas_credito = models.BooleanField(default=False)
    puede_editar_notas_credito = models.BooleanField(default=False)
    puede_anular_notas_credito = models.BooleanField(default=False)
    puede_exportar_reportes = models.BooleanField(default=False)
    puede_catalogo_cuentas = models.BooleanField(default=False)
    puede_crear_asientos = models.BooleanField(default=False)
    puede_contabilizar_asientos = models.BooleanField(default=False)
    puede_reportes_contables = models.BooleanField(default=False)
    puede_rrhh = models.BooleanField(default=False)
    puede_empleados = models.BooleanField(default=False)
    puede_planillas = models.BooleanField(default=False)
    puede_vacaciones = models.BooleanField(default=False)
    puede_configuracion_rrhh = models.BooleanField(default=False)
    puede_crm = models.BooleanField(default=False)
    puede_campanias = models.BooleanField(default=False)
    puede_citas = models.BooleanField(default=False)
    puede_configuracion_crm = models.BooleanField(default=False)

    class Meta:
        ordering = ["nombre"]
        verbose_name = "Rol del sistema"
        verbose_name_plural = "Roles del sistema"

    def __str__(self):
        return self.nombre

    @property
    def tiene_algun_acceso_facturacion(self):
        return any(
            getattr(self, permiso)
            for permiso in [
                "puede_facturas",
                "puede_clientes",
                "puede_productos",
                "puede_proveedores",
                "puede_inventario",
                "puede_compras",
                "puede_cai",
                "puede_impuestos",
                "puede_notas_credito",
                "puede_recibos",
                "puede_egresos",
                "puede_reportes",
                "puede_cxc",
                "puede_cxp",
            ]
        )

    @property
    def tiene_algun_acceso_contabilidad(self):
        return any(
            getattr(self, permiso)
            for permiso in [
                "puede_contabilidad",
                "puede_catalogo_cuentas",
                "puede_crear_asientos",
                "puede_contabilizar_asientos",
                "puede_reportes_contables",
            ]
        )

    @property
    def tiene_algun_acceso_rrhh(self):
        return any(
            getattr(self, permiso)
            for permiso in [
                "puede_rrhh",
                "puede_empleados",
                "puede_planillas",
                "puede_vacaciones",
                "puede_configuracion_rrhh",
            ]
        )

    @property
    def tiene_algun_acceso_crm(self):
        return any(
            getattr(self, permiso)
            for permiso in [
                "puede_crm",
                "puede_campanias",
                "puede_citas",
                "puede_configuracion_crm",
            ]
        )


class Empresa(models.Model):
    ESTADO_LICENCIA_CHOICES = [
        ("prueba", "Prueba"),
        ("activa", "Activa"),
        ("suspendida", "Suspendida"),
        ("vencida", "Vencida"),
    ]

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
    plan_comercial = models.ForeignKey(
        PlanComercial,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="empresas",
    )
    estado_licencia = models.CharField(
        max_length=20,
        choices=ESTADO_LICENCIA_CHOICES,
        default="prueba",
    )
    fecha_inicio_plan = models.DateField(blank=True, null=True)
    fecha_vencimiento_plan = models.DateField(blank=True, null=True)
    observaciones_comerciales = models.TextField(blank=True, null=True)

    activa = models.BooleanField(default=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['nombre']
        verbose_name = "Empresa"
        verbose_name_plural = "Empresas"

    def __str__(self):
        return f"{self.nombre} ({self.rtn})"

    def save(self, *args, **kwargs):
        es_nueva = self.pk is None
        if es_nueva and not self.fecha_inicio_plan:
            self.fecha_inicio_plan = timezone.localdate()
        if es_nueva and not self.fecha_vencimiento_plan:
            self.fecha_vencimiento_plan = self.fecha_inicio_plan + timezone.timedelta(days=7)
        super().save(*args, **kwargs)

    def modulos_habilitados(self):
        plan_qs = Modulo.objects.filter(
            planmodulo__plan=self.plan_comercial,
            planmodulo__activo=True,
        ) if self.plan_comercial_id else Modulo.objects.none()
        manual_qs = Modulo.objects.filter(
            empresamodulo__empresa=self,
            empresamodulo__activo=True,
        )
        return (plan_qs | manual_qs).distinct().order_by("nombre")

    def tiene_modulo_activo(self, codigo):
        return self.modulos_habilitados().filter(codigo=codigo).exists()

    @property
    def estado_licencia_actual(self):
        if (
            self.estado_licencia in {"prueba", "activa"}
            and self.fecha_vencimiento_plan
            and self.fecha_vencimiento_plan < timezone.localdate()
        ):
            return "vencida"
        return self.estado_licencia

    @property
    def licencia_operativa(self):
        return self.activa and self.estado_licencia_actual in {"prueba", "activa"}

    def marcar_prueba(self):
        hoy = timezone.localdate()
        self.estado_licencia = "prueba"
        self.fecha_inicio_plan = hoy
        self.fecha_vencimiento_plan = hoy + timezone.timedelta(days=7)

    def suspender_licencia(self):
        self.estado_licencia = "suspendida"
        self.save(update_fields=["estado_licencia"])

    def activar_licencia_manual(self):
        hoy = timezone.localdate()
        if self.fecha_vencimiento_plan and self.fecha_vencimiento_plan >= hoy:
            self.estado_licencia = "activa"
            self.activa = True
            self.save(update_fields=["estado_licencia", "activa"])
            return True
        return False

    def aplicar_pago_licencia(self, pago):
        hoy = pago.fecha_pago or timezone.localdate()
        base = self.fecha_vencimiento_plan if self.fecha_vencimiento_plan and self.fecha_vencimiento_plan >= hoy else hoy
        self.fecha_inicio_plan = hoy if not self.fecha_inicio_plan else self.fecha_inicio_plan
        self.fecha_vencimiento_plan = sumar_meses(base, pago.cantidad_meses)
        self.estado_licencia = "activa"
        self.activa = True
        self.save(update_fields=[
            "fecha_inicio_plan",
            "fecha_vencimiento_plan",
            "estado_licencia",
            "activa",
        ])


class ConfiguracionPowerBIEmpresa(models.Model):
    empresa = models.OneToOneField(
        Empresa,
        on_delete=models.CASCADE,
        related_name="configuracion_power_bi",
    )
    activo = models.BooleanField(default=False)
    mostrar_en_reportes = models.BooleanField(default=True)
    titulo_panel = models.CharField(max_length=160, default="Dashboard ejecutivo")
    descripcion_panel = models.TextField(blank=True, null=True)
    url_embed = models.URLField(blank=True, null=True)
    alto_iframe = models.PositiveIntegerField(default=760)
    usa_token_seguro = models.BooleanField(default=False)
    workspace_id = models.CharField(max_length=160, blank=True, null=True)
    report_id = models.CharField(max_length=160, blank=True, null=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["empresa__nombre"]
        verbose_name = "Configuracion Power BI por empresa"
        verbose_name_plural = "Configuraciones Power BI por empresa"

    def __str__(self):
        return f"Power BI - {self.empresa.nombre}"

    def clean(self):
        if self.alto_iframe and (self.alto_iframe < 420 or self.alto_iframe > 1800):
            raise ValidationError({
                "alto_iframe": "El alto del panel debe mantenerse entre 420 y 1800 pixeles."
            })
        if self.activo and not self.url_embed:
            raise ValidationError({
                "url_embed": "Debes indicar una URL embed para activar el dashboard de Power BI."
            })


class Usuario(AbstractUser):
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, null=True, blank=True)
    es_administrador_empresa = models.BooleanField(default=False)
    rol_sistema = models.ForeignKey(
        RolSistema,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="usuarios",
    )

    def __str__(self):
        return f"{self.username} - {self.empresa.nombre if self.empresa else 'Sin empresa'}"

    def tiene_permiso_erp(self, permiso):
        if self.is_superuser or self.es_administrador_empresa:
            return True
        return bool(
            self.rol_sistema_id
            and self.rol_sistema.activo
            and getattr(self.rol_sistema, permiso, False)
        )

    @property
    def tiene_alguna_permision_facturacion(self):
        if self.is_superuser or self.es_administrador_empresa:
            return True
        return bool(self.rol_sistema_id and self.rol_sistema.activo and self.rol_sistema.tiene_algun_acceso_facturacion)

    @property
    def tiene_alguna_permision_contabilidad(self):
        if self.is_superuser or self.es_administrador_empresa:
            return True
        return bool(self.rol_sistema_id and self.rol_sistema.activo and self.rol_sistema.tiene_algun_acceso_contabilidad)

    @property
    def tiene_alguna_permision_rrhh(self):
        if self.is_superuser or self.es_administrador_empresa:
            return True
        return bool(self.rol_sistema_id and self.rol_sistema.activo and self.rol_sistema.tiene_algun_acceso_rrhh)

    @property
    def tiene_alguna_permision_crm(self):
        if self.is_superuser or self.es_administrador_empresa:
            return True
        return bool(self.rol_sistema_id and self.rol_sistema.activo and self.rol_sistema.tiene_algun_acceso_crm)
    
class Modulo(models.Model):
    nombre = models.CharField(max_length=100)
    codigo = models.CharField(max_length=50, unique=True)
    es_comercial = models.BooleanField(default=True)

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


class ConfiguracionAvanzadaEmpresa(models.Model):
    empresa = models.OneToOneField(
        Empresa,
        on_delete=models.CASCADE,
        related_name="configuracion_avanzada",
    )
    usa_cierre_caja = models.BooleanField(default=False)
    usa_pagos_mixtos = models.BooleanField(default=False)
    usa_reporte_bancos = models.BooleanField(default=False)
    usa_inventario_farmaceutico = models.BooleanField(default=False)
    usa_bodegas_internas = models.BooleanField(default=False)
    ventas_solo_desde_vitrina = models.BooleanField(default=False)
    permite_cai_historico = models.BooleanField(default=False)
    bodega_venta_predeterminada = models.CharField(max_length=80, default="Vitrina")
    notas = models.TextField(blank=True, null=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Configuracion avanzada por empresa"
        verbose_name_plural = "Configuraciones avanzadas por empresa"

    def __str__(self):
        return f"Funciones avanzadas - {self.empresa.nombre}"

    @staticmethod
    def es_empresa_historica_especial(empresa):
        slug_normalizado = (empresa.slug or "").lower().replace("_", "-").strip()
        nombre_normalizado = (empresa.nombre or "").lower().replace("_", "-").strip()
        return (
            slug_normalizado.startswith("amkt")
            or nombre_normalizado.startswith("amkt")
            or "integrated-sales-and-services" in slug_normalizado
            or "integrated sales and services" in nombre_normalizado
            or "digital-planning" in slug_normalizado
            or "diggital-planning" in slug_normalizado
            or "digital planning" in nombre_normalizado
            or "diggital planning" in nombre_normalizado
        )

    @property
    def permite_gestion_fiscal_historica(self):
        return bool(self.permite_cai_historico)

    @classmethod
    def para_empresa(cls, empresa):
        config, _ = cls.objects.get_or_create(empresa=empresa)
        return config


class PlanModulo(models.Model):
    plan = models.ForeignKey(PlanComercial, on_delete=models.CASCADE)
    modulo = models.ForeignKey(Modulo, on_delete=models.CASCADE)
    activo = models.BooleanField(default=True)

    class Meta:
        unique_together = ("plan", "modulo")
        verbose_name = "Modulo por plan"
        verbose_name_plural = "Modulos por plan"

    def __str__(self):
        return f"{self.plan.nombre} - {self.modulo.nombre}"


def sumar_meses(fecha_base, meses):
    if not fecha_base:
        fecha_base = timezone.localdate()
    month_index = fecha_base.month - 1 + meses
    year = fecha_base.year + month_index // 12
    month = month_index % 12 + 1
    day = min(fecha_base.day, monthrange(year, month)[1])
    return date(year, month, day)


class PagoLicenciaEmpresa(models.Model):
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="pagos_licencia")
    plan_comercial = models.ForeignKey(
        PlanComercial,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pagos_licencia",
    )
    fecha_pago = models.DateField(default=timezone.localdate)
    cantidad_meses = models.PositiveIntegerField(default=1)
    monto = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    metodo = models.CharField(
        max_length=30,
        choices=[
            ("efectivo", "Efectivo"),
            ("transferencia", "Transferencia"),
            ("deposito", "Deposito"),
            ("tarjeta", "Tarjeta"),
        ],
        default="transferencia",
    )
    referencia = models.CharField(max_length=120, blank=True, null=True)
    observacion = models.TextField(blank=True, null=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-fecha_pago", "-id"]
        verbose_name = "Pago de licencia"
        verbose_name_plural = "Pagos de licencia"

    def __str__(self):
        return f"{self.empresa.nombre} - {self.cantidad_meses} mes(es)"
    
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
