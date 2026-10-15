from datetime import date
from decimal import Decimal

from django.test import TestCase

from core.models import Empresa
from .models import (
    AsientoContable, ConfiguracionContableEmpresa, CuentaContable,
    CuentaFinanciera, LineaAsientoContable,
)
from .reportes import clasificar_contrapartida, cuentas_efectivo_ids, datos_flujo_efectivo


class ReportesCatalogoRumiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.rumi = Empresa.objects.create(nombre="RUMI", slug="rumi", rtn="08011999188001")
        cls.otra = Empresa.objects.create(nombre="RUMI sucursal", slug="rumi-otra", rtn="08011999188002")

    def cuenta(self, codigo, nombre, tipo="activo", empresa=None, **kwargs):
        return CuentaContable.objects.create(
            empresa=empresa or self.rumi, codigo=codigo, nombre=nombre, tipo=tipo, **kwargs,
        )

    def asiento(self, partidas):
        asiento = AsientoContable.objects.create(
            empresa=self.rumi, fecha=date(2026, 4, 1), estado="contabilizado",
            descripcion="Operación de RUMI para comprobar el flujo de efectivo",
        )
        for cuenta, debe, haber in partidas:
            LineaAsientoContable.objects.create(
                asiento=asiento, cuenta=cuenta, debe=Decimal(str(debe)), haber=Decimal(str(haber)),
            )
        return asiento

    def test_efectivo_rumi_no_incluye_cuentas_por_prefijos_honduras(self):
        clientes = self.cuenta("110101", "Derechos comerciales por cobrar")
        anticipos = self.cuenta("110201", "Anticipos operativos")
        caja = self.cuenta("101001", "Caja general")
        banco_historico = self.cuenta("HIST-002", "Banco histórico", activa=False)
        efectivo = self.cuenta("103001", "Efectivo para operaciones")
        caja_otra = self.cuenta("101001", "Caja de otra empresa", empresa=self.otra)

        ids = cuentas_efectivo_ids(self.rumi)

        self.assertEqual(ids, {caja.pk, banco_historico.pk, efectivo.pk})
        self.assertNotIn(clientes.pk, ids)
        self.assertNotIn(anticipos.pk, ids)
        self.assertNotIn(caja_otra.pk, ids)

    def test_otras_empresas_conservan_deteccion_por_prefijos_honduras(self):
        caja = self.cuenta("110101", "Disponibilidad principal", empresa=self.otra)
        banco = self.cuenta("110201", "Disponibilidad secundaria", empresa=self.otra)
        rumi_cliente = self.cuenta("110101", "Derechos comerciales por cobrar")

        self.assertEqual(cuentas_efectivo_ids(self.otra), {caja.pk, banco.pk})
        self.assertNotIn(rumi_cliente.pk, cuentas_efectivo_ids(self.rumi))
        identidad_alterada = Empresa.objects.get(pk=self.otra.pk)
        identidad_alterada.slug = "rumi"
        self.assertEqual(cuentas_efectivo_ids(identidad_alterada), {caja.pk, banco.pk})

    def test_efectivo_rumi_conserva_enlaces_explicitamente_configurados(self):
        caja = self.cuenta("CFG-001", "Disponibilidad de oficina")
        banco = self.cuenta("CFG-002", "Disponibilidad en institución")
        financiera = self.cuenta("FIN-001", "Depósitos disponibles", activa=False)
        ConfiguracionContableEmpresa.objects.create(
            empresa=self.rumi, cuenta_caja=caja, cuenta_bancos=banco,
        )
        CuentaFinanciera.objects.create(
            empresa=self.rumi, nombre="Cuenta de operación", tipo="banco",
            cuenta_contable=financiera, activa=False,
        )

        self.assertEqual(cuentas_efectivo_ids(self.rumi), {caja.pk, banco.pk, financiera.pk})

    def test_rumi_no_clasifica_contrapartidas_por_prefijos_honduras(self):
        anticipos = self.cuenta("120101", "Anticipos operativos")
        proveedores = self.cuenta("210401", "Obligaciones comerciales", "pasivo")
        nomina = self.cuenta("220101", "Obligaciones laborales", "pasivo")

        for cuenta in (anticipos, proveedores, nomina):
            with self.subTest(codigo=cuenta.codigo):
                self.assertEqual(clasificar_contrapartida(cuenta), "operacion")
                self.assertEqual(clasificar_contrapartida(cuenta, empresa=self.rumi), "operacion")

    def test_rumi_conserva_clasificacion_por_nombre_y_patrimonio(self):
        casos = (
            ("ACT-001", "Equipo de cómputo", "activo", "inversion"),
            ("ACT-002", "Inversión financiera", "activo", "inversion"),
            ("PAS-001", "Préstamo bancario", "pasivo", "financiamiento"),
            ("PAS-002", "Bonos por pagar", "pasivo", "financiamiento"),
            ("PAT-001", "Aportaciones de socios", "patrimonio", "financiamiento"),
        )
        for codigo, nombre, tipo, esperado in casos:
            with self.subTest(codigo=codigo):
                self.assertEqual(clasificar_contrapartida(self.cuenta(codigo, nombre, tipo)), esperado)

    def test_rumi_conserva_actividad_expresa_heredada_y_prioridad_de_la_cuenta(self):
        padre = self.cuenta("1201", "Cuenta agrupadora", actividad_flujo="financiamiento")
        hija = self.cuenta("120101", "Anticipos operativos", cuenta_padre=padre)
        self.assertEqual(clasificar_contrapartida(hija), "financiamiento")

        hija.actividad_flujo = "operacion"
        hija.save(update_fields=["actividad_flujo"])
        self.assertEqual(clasificar_contrapartida(hija), "operacion")

    def test_otras_empresas_conservan_clasificacion_por_prefijos_honduras(self):
        casos = (
            ("120101", "Anticipos operativos", "activo", "inversion"),
            ("210401", "Obligaciones comerciales", "pasivo", "financiamiento"),
            ("220101", "Obligaciones laborales", "pasivo", "financiamiento"),
        )
        for codigo, nombre, tipo, esperado in casos:
            with self.subTest(codigo=codigo):
                cuenta = self.cuenta(codigo, nombre, tipo, empresa=self.otra)
                self.assertEqual(clasificar_contrapartida(cuenta), esperado)
                identidad_alterada = Empresa.objects.get(pk=self.otra.pk)
                identidad_alterada.slug = "rumi"
                self.assertEqual(clasificar_contrapartida(cuenta, empresa=identidad_alterada), esperado)

    def test_flujo_rumi_cuadra_y_clasifica_cobro_anticipo_y_obligacion_comercial(self):
        caja = self.cuenta("101001", "Caja general")
        clientes = self.cuenta("110101", "Derechos comerciales por cobrar")
        anticipos = self.cuenta("120101", "Anticipos operativos")
        proveedores = self.cuenta("210401", "Obligaciones comerciales", "pasivo")
        self.asiento([(caja, 100, 0), (clientes, 0, 100)])
        self.asiento([(caja, 50, 0), (proveedores, 0, 50)])
        self.asiento([(anticipos, 25, 0), (caja, 0, 25)])

        datos = datos_flujo_efectivo(self.rumi, "2026-04-01", "2026-04-30")

        self.assertEqual(datos["flujo"]["operacion"]["entradas"], Decimal("150.00"))
        self.assertEqual(datos["flujo"]["operacion"]["salidas"], Decimal("25.00"))
        self.assertEqual(datos["flujo"]["operacion"]["neto"], Decimal("125.00"))
        self.assertEqual(datos["flujo"]["inversion"]["neto"], Decimal("0.00"))
        self.assertEqual(datos["flujo"]["financiamiento"]["neto"], Decimal("0.00"))
        self.assertEqual(datos["resumen"]["saldo_final"], Decimal("125.00"))
        self.assertTrue(datos["resumen"]["conciliado"])
