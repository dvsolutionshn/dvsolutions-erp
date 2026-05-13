from calendar import monthrange
from datetime import date
from decimal import Decimal
from urllib.parse import quote

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Sum
from django.utils import timezone

from core.models import Empresa


class ConfiguracionRRHHEmpresa(models.Model):
    empresa = models.OneToOneField(Empresa, on_delete=models.CASCADE, related_name="configuracion_rrhh")
    ihss_trabajador_porcentaje = models.DecimalField(max_digits=6, decimal_places=4, default=Decimal("0.0250"))
    ihss_techo_mensual = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("11903.13"))
    rap_trabajador_porcentaje = models.DecimalField(max_digits=6, decimal_places=4, default=Decimal("0.0150"))
    aplicar_rap = models.BooleanField(default=True)
    isr_porcentaje_base = models.DecimalField(max_digits=6, decimal_places=4, default=Decimal("0.0000"))
    moneda = models.CharField(max_length=10, default="HNL")
    hora_extra_diurna_factor = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("1.25"))
    hora_extra_nocturna_factor = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("1.50"))
    hora_extra_feriado_factor = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("2.00"))
    dias_base_mes = models.PositiveIntegerField(default=30)
    activa = models.BooleanField(default=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Configuracion de RRHH"
        verbose_name_plural = "Configuraciones de RRHH"

    def __str__(self):
        return f"Configuracion RRHH - {self.empresa.nombre}"


class Empleado(models.Model):
    ESTADO_CHOICES = [
        ("activo", "Activo"),
        ("suspendido", "Suspendido"),
        ("retirado", "Retirado"),
    ]
    TIPO_SALARIO_CHOICES = [
        ("mensual", "Mensual"),
        ("quincenal", "Quincenal"),
        ("semanal", "Semanal"),
        ("diario", "Diario"),
    ]

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="empleados")
    codigo = models.CharField(max_length=30, blank=True, null=True)
    nombres = models.CharField(max_length=120)
    apellidos = models.CharField(max_length=120)
    identidad = models.CharField(max_length=25, blank=True, null=True)
    rtn = models.CharField(max_length=25, blank=True, null=True)
    foto = models.ImageField(upload_to="rrhh/empleados/", blank=True, null=True)
    fecha_nacimiento = models.DateField(blank=True, null=True)
    fecha_ingreso = models.DateField()
    fecha_salida = models.DateField(blank=True, null=True)
    puesto = models.CharField(max_length=120, blank=True, null=True)
    departamento = models.CharField(max_length=120, blank=True, null=True)
    correo = models.EmailField(blank=True, null=True)
    telefono = models.CharField(max_length=30, blank=True, null=True)
    direccion = models.TextField(blank=True, null=True)
    salario_mensual = models.DecimalField(max_digits=12, decimal_places=2)
    tipo_salario = models.CharField(max_length=20, choices=TIPO_SALARIO_CHOICES, default="mensual")
    cuenta_bancaria = models.CharField(max_length=80, blank=True, null=True)
    banco = models.CharField(max_length=120, blank=True, null=True)
    aplica_ihss = models.BooleanField(default=True)
    aplica_rap = models.BooleanField(default=True)
    aplica_isr = models.BooleanField(default=True)
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default="activo")
    observacion = models.TextField(blank=True, null=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["nombres", "apellidos"]
        unique_together = ("empresa", "codigo")

    def __str__(self):
        return self.nombre_completo

    @property
    def nombre_completo(self):
        return f"{self.nombres} {self.apellidos}".strip()

    @property
    def salario_diario(self):
        return (self.salario_mensual / Decimal("30.00")).quantize(Decimal("0.01"))

    @property
    def salario_hora(self):
        return (self.salario_diario / Decimal("8.00")).quantize(Decimal("0.01"))

    def anios_antiguedad(self, al=None):
        al = al or timezone.localdate()
        if al < self.fecha_ingreso:
            return 0
        years = al.year - self.fecha_ingreso.year
        if (al.month, al.day) < (self.fecha_ingreso.month, self.fecha_ingreso.day):
            years -= 1
        return max(years, 0)

    def dias_vacaciones_anuales(self, al=None):
        anios = self.anios_antiguedad(al)
        if anios < 1:
            return 0
        if anios == 1:
            return 10
        if anios == 2:
            return 12
        if anios == 3:
            return 15
        return 20

    def dias_vacaciones_disponibles(self, al=None):
        ganados = self.dias_vacaciones_anuales(al)
        usados = self.vacaciones.filter(estado="aprobada").aggregate(total=Sum("dias"))["total"] or Decimal("0.00")
        return Decimal(ganados) - usados


class PeriodoPlanilla(models.Model):
    ESTADO_CHOICES = [
        ("borrador", "Borrador"),
        ("calculada", "Calculada"),
        ("cerrada", "Cerrada"),
        ("pagada", "Pagada"),
    ]
    FRECUENCIA_CHOICES = [
        ("mensual", "Mensual"),
        ("quincenal", "Quincenal"),
        ("semanal", "Semanal"),
        ("especial", "Especial"),
    ]

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="periodos_planilla")
    nombre = models.CharField(max_length=160)
    frecuencia = models.CharField(max_length=20, choices=FRECUENCIA_CHOICES, default="mensual")
    fecha_inicio = models.DateField()
    fecha_fin = models.DateField()
    fecha_pago = models.DateField(default=timezone.localdate)
    incluir_14avo = models.BooleanField(default=False)
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default="borrador")
    creado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-fecha_inicio", "-id"]

    def clean(self):
        if self.fecha_fin and self.fecha_inicio and self.fecha_fin < self.fecha_inicio:
            raise ValidationError("La fecha final no puede ser menor que la fecha inicial.")

    @property
    def total_devengado(self):
        return self.detalles.aggregate(total=Sum("total_devengado"))["total"] or Decimal("0.00")

    @property
    def total_deducciones(self):
        return self.detalles.aggregate(total=Sum("total_deducciones"))["total"] or Decimal("0.00")

    @property
    def total_neto(self):
        return self.detalles.aggregate(total=Sum("neto_pagar"))["total"] or Decimal("0.00")

    def __str__(self):
        return self.nombre


class DetallePlanilla(models.Model):
    periodo = models.ForeignKey(PeriodoPlanilla, on_delete=models.CASCADE, related_name="detalles")
    empleado = models.ForeignKey(Empleado, on_delete=models.PROTECT, related_name="detalles_planilla")
    dias_pagados = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("30.00"))
    salario_base = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    horas_extra_diurnas = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    horas_extra_nocturnas = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    horas_extra_feriado = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    monto_horas_extra = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    bonos = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    comisiones = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    decimo_cuarto = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_devengado = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    ihss = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    rap = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    isr = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    prestamos = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    otras_deducciones = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_deducciones = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    neto_pagar = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    observacion = models.TextField(blank=True, null=True)

    class Meta:
        unique_together = ("periodo", "empleado")
        ordering = ["empleado__nombres", "empleado__apellidos"]

    def __str__(self):
        return f"{self.periodo} - {self.empleado}"

    def telefono_whatsapp_normalizado(self):
        telefono = "".join(ch for ch in (self.empleado.telefono or "") if ch.isdigit())
        if telefono and not telefono.startswith("504") and len(telefono) == 8:
            telefono = f"504{telefono}"
        return telefono

    def resumen_voucher_texto(self):
        return (
            f"Hola {self.empleado.nombres}, te compartimos el resumen de tu voucher de pago.\n\n"
            f"Empresa: {self.periodo.empresa.nombre}\n"
            f"Periodo: {self.periodo.nombre}\n"
            f"Empleado: {self.empleado.nombre_completo}\n"
            f"Fecha de pago: {self.periodo.fecha_pago.strftime('%d/%m/%Y')}\n\n"
            f"Acreditacion:\n"
            f"- Banco/cuenta: {self.empleado.banco or 'No definido'}\n"
            f"- Numero de cuenta: {self.empleado.cuenta_bancaria or 'No definido'}\n\n"
            f"Devengados:\n"
            f"- Salario base: L. {self.salario_base}\n"
            f"- Horas extra: L. {self.monto_horas_extra}\n"
            f"- Bonos: L. {self.bonos}\n"
            f"- Comisiones: L. {self.comisiones}\n"
            f"- 14avo: L. {self.decimo_cuarto}\n"
            f"- Total devengado: L. {self.total_devengado}\n\n"
            f"Deducciones:\n"
            f"- IHSS: L. {self.ihss}\n"
            f"- RAP: L. {self.rap}\n"
            f"- ISR: L. {self.isr}\n"
            f"- Prestamos: L. {self.prestamos}\n"
            f"- Otras deducciones: L. {self.otras_deducciones}\n"
            f"- Total deducciones: L. {self.total_deducciones}\n\n"
            f"Neto a pagar: L. {self.neto_pagar}\n\n"
            f"Nota: el PDF oficial queda disponible para descarga o envio por correo desde el sistema."
        )

    @property
    def whatsapp_url(self):
        telefono = self.telefono_whatsapp_normalizado()
        return f"https://wa.me/{telefono}?text={quote(self.resumen_voucher_texto())}" if telefono else ""


class MovimientoPlanilla(models.Model):
    TIPO_CHOICES = [
        ("bono", "Bono"),
        ("comision", "Comision"),
        ("deduccion", "Deduccion"),
        ("prestamo", "Prestamo"),
    ]
    empleado = models.ForeignKey(Empleado, on_delete=models.CASCADE, related_name="movimientos_planilla")
    periodo = models.ForeignKey(PeriodoPlanilla, on_delete=models.CASCADE, related_name="movimientos", null=True, blank=True)
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    descripcion = models.CharField(max_length=160)
    monto = models.DecimalField(max_digits=12, decimal_places=2)
    fecha = models.DateField(default=timezone.localdate)
    aplicado = models.BooleanField(default=False)

    class Meta:
        ordering = ["-fecha", "-id"]


class VacacionEmpleado(models.Model):
    ESTADO_CHOICES = [
        ("solicitada", "Solicitada"),
        ("aprobada", "Aprobada"),
        ("rechazada", "Rechazada"),
    ]
    empleado = models.ForeignKey(Empleado, on_delete=models.CASCADE, related_name="vacaciones")
    fecha_inicio = models.DateField()
    fecha_fin = models.DateField()
    dias = models.DecimalField(max_digits=6, decimal_places=2)
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default="solicitada")
    observacion = models.TextField(blank=True, null=True)
    aprobado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ["-fecha_inicio"]

    def clean(self):
        if self.fecha_fin < self.fecha_inicio:
            raise ValidationError("La fecha final no puede ser menor que la fecha inicial.")

# Create your models here.
