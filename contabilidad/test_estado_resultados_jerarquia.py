"""La presentación jerárquica no modifica los resultados ni los movimientos."""

from datetime import date
from decimal import Decimal

from django.test import TestCase

from core.models import Empresa
from .models import AsientoContable, CuentaContable, LineaAsientoContable
from .reportes import datos_balance_general, datos_estado_resultados, saldos_financieros


class EstadoResultadosJerarquiaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            nombre="Resultados jerárquicos", slug="resultados-jerarquia", rtn="08011999188331",
        )
        cls.otra = Empresa.objects.create(
            nombre="Resultados privados", slug="otros-resultados", rtn="08011999188332",
        )
        cls.caja = cls.cuenta("1.1", "Caja", "activo")
        cls.capital = cls.cuenta("3.1", "Capital", "patrimonio")
        cls.ingresos = cls.cuenta("4", "Ingresos", "ingreso", acepta_movimientos=False)
        cls.ventas = cls.cuenta("4.1", "Ventas", "ingreso", cuenta_padre=cls.ingresos)
        cls.servicios = cls.cuenta("4.1.1", "Servicios", "ingreso", cuenta_padre=cls.ventas)
        cls.productos = cls.cuenta("4.1.2", "Productos", "ingreso", cuenta_padre=cls.ventas)
        cls.digital = cls.cuenta("DIGITAL", "Servicios digitales", "ingreso", cuenta_padre=cls.servicios)
        cls.costos = cls.cuenta("5", "Costos", "costo", acepta_movimientos=False)
        cls.costo_ventas = cls.cuenta("5.1", "Costo de ventas", "costo", cuenta_padre=cls.costos)
        cls.gastos = cls.cuenta("6", "Gastos", "gasto", acepta_movimientos=False)
        cls.administracion = cls.cuenta("6.1", "Administración", "gasto", cuenta_padre=cls.gastos)
        cls.alquiler = cls.cuenta("6.1.1", "Alquiler", "gasto", cuenta_padre=cls.administracion)
        cls.energia = cls.cuenta("6.1.2", "Energía", "gasto", cuenta_padre=cls.administracion)
        cls.sin_movimientos = cls.cuenta("6.9", "Sin movimientos", "gasto", cuenta_padre=cls.gastos)

    @classmethod
    def cuenta(cls, codigo, nombre, tipo, empresa=None, **kwargs):
        return CuentaContable.objects.create(
            empresa=empresa or cls.empresa, codigo=codigo, nombre=nombre, tipo=tipo, **kwargs,
        )

    def asiento(self, partidas, fecha=date(2026, 10, 1), empresa=None, estado="contabilizado", evento=None):
        asiento = AsientoContable.objects.create(
            empresa=empresa or self.empresa, fecha=fecha, descripcion="Prueba de resultados jerárquicos",
            estado=estado, evento=evento,
        )
        for cuenta, debe, haber in partidas:
            LineaAsientoContable.objects.create(
                asiento=asiento, cuenta=cuenta, debe=Decimal(str(debe)), haber=Decimal(str(haber)),
            )
        return asiento

    def filas(self, datos, grupo="ingresos"):
        return {fila["cuenta"].pk: fila for fila in datos[f"{grupo}_jerarquia"]}

    def test_principales_sin_movimiento_acumulan_tres_niveles_y_totales_una_vez(self):
        self.asiento([
            (self.caja, 150000, 0), (self.servicios, 0, 75000),
            (self.digital, 0, 25000), (self.productos, 0, 50000),
        ])
        self.asiento([(self.costo_ventas, 20000, 0), (self.caja, 0, 20000)])
        self.asiento([(self.alquiler, 5000, 0), (self.energia, 3000, 0), (self.caja, 0, 8000)])
        datos = datos_estado_resultados(self.empresa)
        ingresos = self.filas(datos)
        self.assertEqual(ingresos[self.ingresos.pk]["saldo"], Decimal("150000.00"))
        self.assertEqual(ingresos[self.ventas.pk]["saldo"], Decimal("150000.00"))
        self.assertEqual(ingresos[self.servicios.pk]["saldo"], Decimal("100000.00"))
        self.assertEqual(ingresos[self.ventas.pk]["saldo_directo"], Decimal("0.00"))
        self.assertEqual(
            [(fila["cuenta"].pk, fila["nivel"]) for fila in datos["ingresos_jerarquia"]],
            [(self.ingresos.pk, 0), (self.ventas.pk, 1), (self.servicios.pk, 2),
             (self.digital.pk, 3), (self.productos.pk, 2)],
        )
        self.assertTrue(ingresos[self.servicios.pk]["es_agrupadora"])
        self.assertFalse(ingresos[self.digital.pk]["es_agrupadora"])
        self.assertEqual(ingresos[self.digital.pk]["sangria_px"], 54)
        self.assertEqual(self.filas(datos, "costos")[self.costos.pk]["saldo"], Decimal("20000.00"))
        self.assertEqual(self.filas(datos, "gastos")[self.gastos.pk]["saldo"], Decimal("8000.00"))
        self.assertEqual(self.filas(datos, "gastos")[self.administracion.pk]["saldo"], Decimal("8000.00"))
        self.assertNotIn(self.sin_movimientos.pk, self.filas(datos, "gastos"))
        self.assertEqual(datos["resumen"], {
            "total_ingresos": Decimal("150000.00"), "total_costos": Decimal("20000.00"),
            "total_gastos": Decimal("8000.00"), "utilidad_bruta": Decimal("130000.00"),
            "utilidad": Decimal("122000.00"), "resultado_positivo": True,
            "cuentas_ingreso": 3, "cuentas_costo": 1, "cuentas_gasto": 2,
        })
        directas = saldos_financieros(self.empresa, ("ingreso", "costo", "gasto"), excluir_cierres=True)
        for grupo, tipo in (("ingresos", "ingreso"), ("costos", "costo"), ("gastos", "gasto")):
            self.assertEqual(datos[grupo], [fila for fila in directas if fila["cuenta"].tipo == tipo])
            self.assertEqual(set(datos[grupo][0]), {"cuenta", "debe", "haber", "saldo"})
        balance = datos_balance_general(self.empresa)["resumen"]
        self.assertEqual(balance["total_activos"], Decimal("122000.00"))
        self.assertEqual(balance["total_pasivo_patrimonio"], Decimal("122000.00"))
        self.assertTrue(balance["cuadrado"])

    def test_movimiento_directo_mas_descendientes_y_contracuenta_conservan_decimales(self):
        devolucion = self.cuenta("4.1.3", "Devoluciones", "ingreso", cuenta_padre=self.ventas)
        self.asiento([
            (self.caja, "201.00", 0), (self.ventas, 0, "10.10"),
            (self.servicios, 0, "100.20"), (self.digital, 0, "50.30"), (self.productos, 0, "40.40"),
        ])
        self.asiento([(devolucion, "5.25", 0), (self.caja, 0, "5.25")])
        datos = datos_estado_resultados(self.empresa)
        filas = self.filas(datos)
        self.assertEqual(filas[self.ventas.pk]["saldo_directo"], Decimal("10.10"))
        self.assertEqual(filas[self.servicios.pk]["saldo"], Decimal("150.50"))
        self.assertEqual(filas[devolucion.pk]["saldo"], Decimal("-5.25"))
        self.assertEqual(filas[self.ventas.pk]["saldo"], Decimal("195.75"))
        self.assertEqual(filas[self.ingresos.pk]["saldo"], Decimal("195.75"))
        self.assertEqual(datos["resumen"]["total_ingresos"], Decimal("195.75"))
        self.assertEqual(datos["resumen"]["utilidad"], Decimal("195.75"))
        self.assertTrue(datos_balance_general(self.empresa)["resumen"]["cuadrado"])

    def test_periodos_cierres_cuentas_inactivas_y_estados_conservan_el_resultado(self):
        self.asiento([(self.caja, 500, 0), (self.servicios, 0, 500)], fecha=date(2025, 12, 10))
        self.asiento([(self.alquiler, 200, 0), (self.caja, 0, 200)], fecha=date(2025, 12, 10))
        self.asiento([
            (self.servicios, 500, 0), (self.alquiler, 0, 200), (self.capital, 0, 300),
        ], fecha=date(2025, 12, 31), evento="cierre_anual")
        self.asiento([(self.caja, 50, 0), (self.productos, 0, 50)], fecha=date(2026, 1, 2))
        self.asiento([(self.caja, 999, 0), (self.productos, 0, 999)], estado="borrador")
        self.asiento([(self.caja, 999, 0), (self.productos, 0, 999)], estado="anulado")
        CuentaContable.objects.filter(pk__in=[self.ingresos.pk, self.servicios.pk]).update(activa=False)
        datos = datos_estado_resultados(self.empresa, "2025-01-01", "2025-12-31")
        self.assertEqual(self.filas(datos)[self.ingresos.pk]["saldo"], Decimal("500.00"))
        self.assertEqual(self.filas(datos, "gastos")[self.gastos.pk]["saldo"], Decimal("200.00"))
        self.assertNotIn(self.productos.pk, self.filas(datos))
        self.assertEqual(datos["resumen"]["utilidad"], Decimal("300.00"))
        despues_cierre = datos_estado_resultados(self.empresa, "2025-01-01", "2025-12-31", excluir_cierres=False)
        self.assertEqual(despues_cierre["resumen"]["utilidad"], Decimal("0.00"))
        self.assertEqual(self.filas(despues_cierre)[self.ingresos.pk]["saldo"], Decimal("0.00"))
        enero = datos_estado_resultados(self.empresa, "2026-01-01", "2026-01-31")
        self.assertEqual(enero["resumen"]["utilidad"], Decimal("50.00"))
        self.assertEqual(self.filas(enero)[self.ventas.pk]["saldo"], Decimal("50.00"))
        balance_cerrado = datos_balance_general(self.empresa, "2025-12-31")["resumen"]
        self.assertEqual(balance_cerrado["resultado_no_traspasado"], Decimal("0.00"))
        self.assertEqual(balance_cerrado["total_patrimonio"], Decimal("300.00"))
        self.assertTrue(balance_cerrado["cuadrado"])
        self.assertTrue(datos_balance_general(self.empresa, "2026-01-31")["resumen"]["cuadrado"])

    def test_aisla_empresa_y_no_modifica_cuentas_asientos_ni_lineas(self):
        ingreso_ajeno = self.cuenta("4", "Ingreso privado", "ingreso", empresa=self.otra)
        detalle_ajeno = self.cuenta(
            "4.1", "Detalle privado", "ingreso", empresa=self.otra, cuenta_padre=ingreso_ajeno,
        )
        caja_ajena = self.cuenta("1", "Caja privada", "activo", empresa=self.otra)
        self.asiento([(caja_ajena, 900, 0), (detalle_ajeno, 0, 900)], empresa=self.otra)
        self.asiento([(self.caja, 100, 0), (self.servicios, 0, 100)])
        # Simula una relación antigua inválida sin cambiarla al consultar.
        CuentaContable.objects.filter(pk=self.ventas.pk).update(cuenta_padre=ingreso_ajeno)
        anteriores = {
            modelo: list(modelo.objects.order_by("pk").values())
            for modelo in (CuentaContable, AsientoContable, LineaAsientoContable)
        }
        datos = datos_estado_resultados(self.empresa)
        filas = self.filas(datos)
        self.assertNotIn(ingreso_ajeno.pk, filas)
        self.assertNotIn(detalle_ajeno.pk, filas)
        self.assertEqual(filas[self.ventas.pk]["nivel"], 0)
        self.assertEqual(filas[self.ventas.pk]["saldo"], Decimal("100.00"))
        self.assertEqual(datos["resumen"]["total_ingresos"], Decimal("100.00"))
        self.assertTrue(datos_balance_general(self.empresa)["resumen"]["cuadrado"])
        for modelo, filas_anteriores in anteriores.items():
            self.assertEqual(list(modelo.objects.order_by("pk").values()), filas_anteriores)
