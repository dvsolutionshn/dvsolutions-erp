"""El salto desde el balance destaca una línea del mayor sin cambiar sus saldos."""

from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from core.models import Empresa, Modulo, PlanComercial, PlanModulo, Usuario
from .models import AsientoContable, CuentaContable, LineaAsientoContable
from .reportes import datos_mayor


class MayorNavegacionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        modulo, _ = Modulo.objects.get_or_create(
            codigo="contabilidad", defaults={"nombre": "Contabilidad", "es_comercial": True},
        )
        plan = PlanComercial.objects.create(
            nombre="Plan navegación mayor", codigo="plan-navegacion-mayor", precio_mensual="99.00",
        )
        PlanModulo.objects.create(plan=plan, modulo=modulo, activo=True)
        cls.empresa = Empresa.objects.create(
            nombre="Empresa navegación mayor", slug="navegacion-mayor", rtn="08011999188241",
            estado_licencia="activa", plan_comercial=plan,
        )
        cls.otra_empresa = Empresa.objects.create(
            nombre="Otra empresa navegación", slug="otra-navegacion-mayor", rtn="08011999188242",
            estado_licencia="activa", plan_comercial=plan,
        )
        cls.usuario = Usuario.objects.create_user(
            username="contador-navegacion-mayor", empresa=cls.empresa, is_superuser=True, is_staff=True,
        )
        cls.banco = CuentaContable.objects.create(
            empresa=cls.empresa, codigo="1.1", nombre="Banco", tipo="activo",
        )
        cls.caja = CuentaContable.objects.create(
            empresa=cls.empresa, codigo="1.2", nombre="Caja", tipo="activo",
        )
        cls.capital = CuentaContable.objects.create(
            empresa=cls.empresa, codigo="3.1", nombre="Capital", tipo="patrimonio",
        )
        cls.banco_ajeno = CuentaContable.objects.create(
            empresa=cls.otra_empresa, codigo="1.1", nombre="Banco privado", tipo="activo",
        )
        cls.capital_ajeno = CuentaContable.objects.create(
            empresa=cls.otra_empresa, codigo="3.1", nombre="Capital privado", tipo="patrimonio",
        )
        cls.anterior = cls.movimiento(cls.banco, cls.capital, date(2026, 9, 30), "100.10")
        cls.seleccionada = cls.movimiento(cls.banco, cls.capital, date(2026, 10, 5), "200.20")
        cls.posterior = cls.movimiento(cls.banco, cls.capital, date(2026, 11, 1), "300.30")
        cls.otra_cuenta = cls.movimiento(cls.caja, cls.capital, date(2026, 10, 6), "400.40")
        cls.ajena = cls.movimiento(cls.banco_ajeno, cls.capital_ajeno, date(2026, 10, 5), "900.90")
        cls.borrador = cls.movimiento(cls.banco, cls.capital, date(2026, 10, 8), "500.50", estado="borrador")

    @classmethod
    def movimiento(cls, cuenta, contrapartida, fecha, importe, estado="contabilizado"):
        asiento = AsientoContable.objects.create(
            empresa=cuenta.empresa, fecha=fecha, descripcion="Aporte para navegación", estado=estado,
        )
        linea = LineaAsientoContable.objects.create(
            asiento=asiento, cuenta=cuenta, detalle="Detalle de la línea", debe=Decimal(importe),
        )
        LineaAsientoContable.objects.create(
            asiento=asiento, cuenta=contrapartida, haber=Decimal(importe),
        )
        return linea

    def setUp(self):
        self.client.force_login(self.usuario)
        self.url = reverse("mayor_cuenta", args=[self.empresa.slug])

    def consultar(self, movimiento=None, **filtros):
        parametros = {"cuenta": self.banco.pk, "fecha_fin": "2026-10-31", **filtros}
        if movimiento is not None:
            parametros["movimiento"] = movimiento
        return self.client.get(self.url, parametros)

    def test_seleccion_destaca_linea_valida_con_ancla_y_regreso_al_mismo_balance(self):
        respuesta = self.consultar(self.seleccionada.pk)
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.context["movimiento_seleccionado"], self.seleccionada.pk)
        self.assertContains(respuesta, f'id="movimiento-{self.seleccionada.pk}" class="mayor-movimiento mayor-movimiento--seleccionado" aria-current="true"')
        self.assertContains(respuesta, "Movimiento seleccionado", count=1)
        self.assertContains(respuesta, f'href="{reverse("balance_general", args=[self.empresa.slug])}?fecha_corte=2026-10-31"')
        self.assertNotContains(respuesta, "Banco privado")

    def test_seleccion_no_altera_movimientos_apertura_totales_ni_saldo_decimal(self):
        respuesta = self.consultar(self.seleccionada.pk, fecha_inicio="2026-10-01")
        bloque = datos_mayor(self.empresa, "2026-10-01", "2026-10-31", cuenta=self.banco)[0]
        self.assertEqual(respuesta.context["movimientos"], bloque["movimientos"])
        self.assertEqual(respuesta.context["resumen"], {
            "debe_total": bloque["debe"], "haber_total": bloque["haber"],
            "saldo": bloque["saldo"], "saldo_inicial": bloque["saldo_inicial"],
            "movimientos": len(bloque["movimientos"]),
        })
        self.assertEqual(respuesta.context["resumen"]["saldo"], Decimal("300.30"))
        self.assertEqual(respuesta.context["resumen"]["saldo_inicial"], Decimal("100.10"))

    def test_ids_ajenos_fuera_del_periodo_o_borradores_no_generan_seleccion(self):
        for linea in (self.anterior, self.posterior, self.otra_cuenta, self.ajena, self.borrador):
            with self.subTest(linea=linea.pk):
                respuesta = self.consultar(linea.pk, fecha_inicio="2026-10-01")
                self.assertEqual(respuesta.status_code, 200)
                self.assertIsNone(respuesta.context["movimiento_seleccionado"])
                self.assertNotContains(respuesta, "mayor-movimiento--seleccionado")
                self.assertNotContains(respuesta, "Movimiento seleccionado")
                self.assertNotContains(respuesta, "Volver al Balance")

    def test_sin_id_o_id_no_numerico_no_cambian_consulta(self):
        for valor in (None, "", "inexistente", "-1", "999999999999999999999999999999", "<script>alert(1)</script>"):
            with self.subTest(valor=valor):
                respuesta = self.consultar(valor)
                self.assertEqual(respuesta.status_code, 200)
                self.assertIsNone(respuesta.context["movimiento_seleccionado"])
                self.assertNotContains(respuesta, "Movimiento seleccionado")
                self.assertNotContains(respuesta, "Volver al Balance")

    def test_no_permite_consultar_cuenta_de_otra_empresa(self):
        respuesta = self.consultar(self.ajena.pk, cuenta=self.banco_ajeno.pk)
        self.assertEqual(respuesta.status_code, 404)

    def test_sin_cuenta_no_puede_resaltar_una_linea(self):
        respuesta = self.consultar(self.seleccionada.pk, cuenta="")
        self.assertEqual(respuesta.status_code, 200)
        self.assertIsNone(respuesta.context["movimiento_seleccionado"])
        self.assertEqual(respuesta.context["movimientos"], [])
        self.assertNotContains(respuesta, "Movimiento seleccionado")

    def test_regreso_sin_corte_conserva_balance_sin_filtro(self):
        respuesta = self.consultar(self.seleccionada.pk, fecha_fin="")
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, f'href="{reverse("balance_general", args=[self.empresa.slug])}?fecha_corte="')
