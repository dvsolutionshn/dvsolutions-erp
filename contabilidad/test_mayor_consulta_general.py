"""La consulta del Mayor conserva los cálculos y limita las cuentas visibles."""

from datetime import date
from decimal import Decimal
from io import BytesIO

from django.test import TestCase
from django.urls import reverse
from openpyxl import load_workbook

from core.models import Empresa, Modulo, PlanComercial, PlanModulo, RolSistema, Usuario
from .models import AsientoContable, CuentaContable, LineaAsientoContable
from .reportes import datos_mayor


class MayorConsultaGeneralTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        modulo, _ = Modulo.objects.get_or_create(codigo="contabilidad", defaults={"nombre": "Contabilidad"})
        plan = PlanComercial.objects.create(nombre="Plan Mayor", codigo="plan-mayor-consulta", precio_mensual="99.00")
        PlanModulo.objects.create(plan=plan, modulo=modulo, activo=True)
        cls.empresa = Empresa.objects.create(nombre="Empresa Mayor", slug="mayor-consulta", rtn="08011999420101", estado_licencia="activa", plan_comercial=plan)
        cls.otra = Empresa.objects.create(nombre="Empresa privada Mayor", slug="mayor-consulta-privada", rtn="08011999420102", estado_licencia="activa", plan_comercial=plan)
        rol = RolSistema.objects.create(nombre="Consulta Mayor", codigo="consulta-mayor", puede_contabilidad=True, puede_reportes_contables=True)
        cls.usuario = Usuario.objects.create_user(username="consulta-mayor", empresa=cls.empresa, rol_sistema=rol)
        cls.ajeno = Usuario.objects.create_user(username="consulta-mayor-ajeno", empresa=cls.otra, rol_sistema=rol)
        cls.sin_permiso = Usuario.objects.create_user(username="sin-consulta-mayor", empresa=cls.empresa)

        def cuenta(codigo, nombre, tipo="activo", **datos):
            return CuentaContable.objects.create(empresa=cls.empresa, codigo=codigo, nombre=nombre, tipo=tipo, **datos)

        cls.caja = cuenta("110101", "Caja pequeña")
        cls.banco = cuenta("110201", "Banco Atlántida", activa=False)
        cls.capital = cuenta("310101", "Capital social", "patrimonio")
        cls.ingreso = cuenta("410101", "Ingresos por préstamos", "ingreso")
        cls.gasto = cuenta("610101", "Servicios públicos", "gasto")
        cls.solo_apertura = cuenta("120101", "Equipo sin actividad")
        cls.sin_historia = cuenta("130101", "Cuenta sin historia")
        cls.registro(date(2025, 12, 31), [(cls.caja, "1000.00", 0), (cls.solo_apertura, "500.00", 0), (cls.capital, 0, "1500.00")])
        cls.registro(date(2026, 9, 5), [(cls.caja, "1250000.50", 0), (cls.ingreso, 0, "1250000.50")])
        cls.registro(date(2026, 9, 10), [(cls.gasto, "1250.00", 0), (cls.caja, 0, "1250.00")])
        cls.registro(date(2026, 9, 15), [(cls.banco, "2500.00", 0), (cls.caja, 0, "2500.00")])
        cls.registro(date(2026, 9, 20), [(cls.caja, "2500.00", 0), (cls.banco, 0, "2500.00")])
        cls.registro(date(2026, 10, 1), [(cls.caja, "999999.99", 0), (cls.ingreso, 0, "999999.99")])
        for estado in ("borrador", "anulado"):
            cls.registro(date(2026, 9, 25), [(cls.caja, "888888.88", 0), (cls.ingreso, 0, "888888.88")], estado=estado)
        privada = CuentaContable.objects.create(empresa=cls.otra, codigo="110101", nombre="Cuenta confidencial Mayor", tipo="activo")
        ingreso_privado = CuentaContable.objects.create(empresa=cls.otra, codigo="410101", nombre="Ingreso confidencial Mayor", tipo="ingreso")
        cls.registro(date(2026, 9, 5), [(privada, "10000000.00", 0), (ingreso_privado, 0, "10000000.00")], empresa=cls.otra)

    @classmethod
    def registro(cls, fecha, partidas, *, estado="contabilizado", empresa=None):
        asiento = AsientoContable.objects.create(empresa=empresa or cls.empresa, fecha=fecha, descripcion="Movimiento de prueba del Mayor", estado=estado)
        for cuenta, debe, haber in partidas:
            LineaAsientoContable.objects.create(asiento=asiento, cuenta=cuenta, debe=Decimal(str(debe)), haber=Decimal(str(haber)))

    def setUp(self):
        self.client.force_login(self.usuario)
        self.filtros = {"fecha_inicio": "2026-09-01", "fecha_fin": "2026-09-30"}

    def consultar(self, reporte="mayor_cuenta", **filtros):
        respuesta = self.client.get(reverse(reporte, args=[self.empresa.slug]), {**self.filtros, **filtros})
        self.assertEqual(respuesta.status_code, 200)
        return respuesta

    def test_sin_busqueda_muestra_todas_con_movimiento_incluso_saldo_cero_e_inactivas(self):
        esperadas = {self.caja.pk, self.banco.pk, self.ingreso.pk, self.gasto.pk}
        for reporte in ("mayor_cuenta", "libro_mayor_general"):
            with self.subTest(reporte=reporte):
                respuesta = self.consultar(reporte)
                bloques = respuesta.context["cuentas_mayor"]
                self.assertEqual({bloque["cuenta"].pk for bloque in bloques}, esperadas)
                self.assertTrue(all(bloque["movimientos"] for bloque in bloques))
                self.assertEqual(next(b for b in bloques if b["cuenta"].pk == self.banco.pk)["saldo"], Decimal("0.00"))
                self.assertEqual(respuesta.context["resumen"]["debe_total"], Decimal("1256250.50"))
                self.assertEqual(respuesta.context["resumen"]["haber_total"], Decimal("1256250.50"))
                self.assertEqual(respuesta.context["resumen"]["movimientos"], 8)

    def test_busca_codigo_nombre_sin_tildes_y_sin_resultados(self):
        for reporte in ("mayor_cuenta", "libro_mayor_general"):
            for consulta, esperadas in (("110201", {self.banco.pk}), (" ATLANTIDA ", {self.banco.pk}), ("publicos", {self.gasto.pk}), ("no existe", set())):
                with self.subTest(reporte=reporte, consulta=consulta):
                    respuesta = self.consultar(reporte, q=consulta)
                    self.assertEqual({b["cuenta"].pk for b in respuesta.context["cuentas_mayor"]}, esperadas)
                    self.assertTrue(respuesta.context["resumen"]["vista_parcial"])
        respuesta = self.consultar(q="  ")
        self.assertEqual(respuesta.context["resumen"]["cuentas"], 4)
        self.assertFalse(respuesta.context["resumen"]["vista_parcial"])

    def test_conserva_saldos_movimientos_y_apertura_del_calculo_actual(self):
        actuales = {b["cuenta"].pk: b for b in datos_mayor(self.empresa, **self.filtros)}
        visibles = self.consultar().context["cuentas_mayor"]
        for bloque in visibles:
            self.assertEqual(bloque, actuales[bloque["cuenta"].pk])
        self.assertEqual(actuales[self.solo_apertura.pk]["saldo"], Decimal("500.00"))
        self.assertNotIn(self.solo_apertura.pk, {b["cuenta"].pk for b in visibles})
        individual = self.consultar(cuenta=self.solo_apertura.pk, q="otra búsqueda")
        self.assertFalse(individual.context["modo_general"])
        self.assertEqual(individual.context["resumen"]["saldo_inicial"], Decimal("500.00"))
        self.assertEqual(individual.context["movimientos"], [])

    def test_periodo_sin_movimientos_deja_general_vacio_sin_borrar_apertura(self):
        respuesta = self.consultar(fecha_inicio="2026-08-01", fecha_fin="2026-08-31")
        self.assertEqual(respuesta.context["cuentas_mayor"], [])
        self.assertEqual(respuesta.context["resumen"]["debe_total"], Decimal("0.00"))
        self.assertTrue(datos_mayor(self.empresa, "2026-08-01", "2026-08-31"))

    def test_excel_exporta_mismo_alcance_numerico_con_miles_y_millones(self):
        for reporte in ("exportar_mayor_cuenta_excel", "exportar_libro_mayor_general_excel"):
            for consulta in ("", "atlantida"):
                with self.subTest(reporte=reporte, consulta=consulta):
                    respuesta = self.consultar(reporte, q=consulta)
                    hoja = load_workbook(BytesIO(respuesta.content)).active
                    filas = [fila for fila in hoja if fila[3].value == "Movimiento de prueba del Mayor"]
                    esperadas = {b["cuenta"].pk: b for b in self.consultar(q=consulta).context["cuentas_mayor"]}
                    self.assertEqual(len(filas), sum(len(b["movimientos"]) for b in esperadas.values()))
                    for fila in filas:
                        self.assertNotIn("Equipo sin actividad", str(fila[2].value))
                        for celda in fila[5:8]:
                            self.assertEqual(celda.data_type, "n")
                            self.assertIn("#,##0.00", celda.number_format)
                    self.assertEqual(sum((Decimal(str(fila[5].value)) for fila in filas), Decimal("0.00")), sum((b["debe"] for b in esperadas.values()), Decimal("0.00")))
                    if not consulta:
                        self.assertTrue(any(Decimal(str(fila[5].value)) == Decimal("1250000.50") for fila in filas))

    def test_consulta_y_exportacion_respetan_empresa_y_permisos(self):
        for usuario in (self.sin_permiso, self.ajeno):
            self.client.force_login(usuario)
            for reporte in ("mayor_cuenta", "libro_mayor_general", "exportar_mayor_cuenta_excel", "exportar_libro_mayor_general_excel"):
                respuesta = self.client.get(reverse(reporte, args=[self.empresa.slug]), self.filtros)
                self.assertIn(respuesta.status_code, (302, 403))
        self.client.force_login(self.usuario)
        respuesta = self.consultar(empresa=self.otra.pk)
        self.assertNotContains(respuesta, "Cuenta confidencial Mayor")
        self.assertNotContains(respuesta, "Ingreso confidencial Mayor")
        self.assertTrue(all(b["cuenta"].empresa_id == self.empresa.pk for b in respuesta.context["cuentas_mayor"]))

    def test_consultas_y_excel_no_modifican_asientos_partidas_o_catalogo(self):
        originales = {modelo: list(modelo.objects.order_by("pk").values()) for modelo in (CuentaContable, AsientoContable, LineaAsientoContable)}
        for reporte in ("mayor_cuenta", "libro_mayor_general", "exportar_mayor_cuenta_excel", "exportar_libro_mayor_general_excel"):
            self.consultar(reporte)
        for modelo, valores in originales.items():
            self.assertEqual(list(modelo.objects.order_by("pk").values()), valores)
