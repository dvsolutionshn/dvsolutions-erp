from datetime import date
from decimal import Decimal

from django.db import connection
from django.template.loader import render_to_string
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from core.models import Empresa
from .dashboard import datos_dashboard_contable
from .models import (
    AsientoContable,
    ConfiguracionContableEmpresa,
    CuentaContable,
    CuentaFinanciera,
    LineaAsientoContable,
    MovimientoBancario,
    PeriodoContable,
)


class DatosDashboardContableTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(nombre="Empresa panel", slug="empresa-panel", rtn="08011999177031")
        cls.otra = Empresa.objects.create(nombre="Otra empresa", slug="otra-panel", rtn="08011999177032")
        cls.caja = CuentaContable.objects.create(
            empresa=cls.empresa, codigo="1101", nombre="Caja", tipo="activo",
        )
        cls.ingreso = CuentaContable.objects.create(
            empresa=cls.empresa, codigo="4101", nombre="Ingreso", tipo="ingreso",
        )
        cls.caja_otra = CuentaContable.objects.create(
            empresa=cls.otra, codigo="1101", nombre="Caja ajena", tipo="activo",
        )

    def asiento(self, *, fecha=date(2026, 10, 14), estado="borrador", importe="42.25", empresa=None):
        empresa = empresa or self.empresa
        asiento = AsientoContable.objects.create(
            empresa=empresa, fecha=fecha, descripcion="Movimiento de prueba", estado=estado,
        )
        cuenta = self.caja if empresa == self.empresa else self.caja_otra
        contrapartida = self.ingreso if empresa == self.empresa else self.caja_otra
        LineaAsientoContable.objects.create(asiento=asiento, cuenta=cuenta, debe=Decimal(importe))
        LineaAsientoContable.objects.create(asiento=asiento, cuenta=contrapartida, haber=Decimal(importe))
        return asiento

    def test_mes_actual_separa_estados_limites_y_empresas(self):
        self.asiento(fecha=date(2026, 10, 1), estado="contabilizado")
        self.asiento(fecha=date(2026, 10, 31), estado="contabilizado")
        self.asiento(estado="borrador")
        self.asiento(estado="anulado")
        self.asiento(fecha=date(2026, 9, 30), estado="contabilizado")
        self.asiento(fecha=date(2026, 11, 1), estado="borrador")
        ajeno = self.asiento(estado="contabilizado", empresa=self.otra, importe="900.00")

        datos = datos_dashboard_contable(self.empresa, hoy=date(2026, 10, 14))

        self.assertEqual(datos["resumen"]["asientos"], 6)
        self.assertEqual(datos["resumen"]["contabilizados"], 3)
        self.assertEqual(datos["resumen"]["borradores"], 2)
        self.assertEqual(datos["resumen"]["anulados"], 1)
        self.assertEqual(datos["resumen"]["cuentas"], 2)
        self.assertEqual(datos["actividad_mes"]["total"], 4)
        self.assertEqual(datos["actividad_mes"]["contabilizados"], 2)
        self.assertEqual(datos["actividad_mes"]["borradores"], 1)
        self.assertEqual(datos["actividad_mes"]["anulados"], 1)
        self.assertEqual(datos["actividad_mes"]["porcentaje_contabilizado"], 50)
        self.assertEqual(datos["actividad_mes"]["etiqueta"], "Octubre de 2026")
        self.assertNotIn(ajeno.pk, [asiento.pk for asiento in datos["ultimos_asientos"]])

    def test_importes_recientes_suman_todas_las_lineas_sin_consultas_por_fila(self):
        asiento = self.asiento(importe="20.35")
        LineaAsientoContable.objects.create(asiento=asiento, cuenta=self.caja, debe=Decimal("4.90"))
        LineaAsientoContable.objects.create(asiento=asiento, cuenta=self.ingreso, haber=Decimal("4.90"))
        vacio = AsientoContable.objects.create(
            empresa=self.empresa, fecha=date(2026, 10, 15), descripcion="Sin líneas todavía",
        )
        self.asiento(empresa=self.otra, importe="800.00")

        with self.assertNumQueries(6):
            datos = datos_dashboard_contable(self.empresa, hoy=date(2026, 10, 14))
        with self.assertNumQueries(0):
            por_id = {asiento.pk: (asiento.importe_debe, asiento.importe_haber) for asiento in datos["ultimos_asientos"]}
        self.assertEqual(por_id[asiento.pk], (Decimal("25.25"), Decimal("25.25")))
        self.assertEqual(por_id[vacio.pk], (Decimal("0.00"), Decimal("0.00")))

    def test_panel_vacio_no_inventa_actividad_ni_crea_configuracion_o_periodo(self):
        with CaptureQueriesContext(connection) as consultas:
            datos = datos_dashboard_contable(self.empresa, hoy=date(2026, 10, 14))
        self.assertEqual(datos["actividad_mes"]["total"], 0)
        self.assertEqual(datos["actividad_mes"]["porcentaje_contabilizado"], 0)
        self.assertTrue(all(estado["porcentaje"] == 0 for estado in datos["actividad_mes"]["estados"]))
        self.assertEqual(datos["ultimos_asientos"], [])
        self.assertIsNone(datos["periodo_actual"])
        self.assertEqual(datos["estado_periodo"], "Sin período registrado")
        self.assertFalse(PeriodoContable.objects.filter(empresa=self.empresa).exists())
        self.assertFalse(ConfiguracionContableEmpresa.objects.filter(empresa=self.empresa).exists())
        self.assertTrue(all(consulta["sql"].lstrip().upper().startswith("SELECT") for consulta in consultas))

    def test_cuentas_activas_excluyen_agrupadoras_de_las_cuentas_de_movimiento(self):
        CuentaContable.objects.create(
            empresa=self.empresa, codigo="1", nombre="Activo", tipo="activo", acepta_movimientos=False,
        )
        CuentaContable.objects.create(
            empresa=self.empresa, codigo="1102", nombre="Banco inactivo", tipo="activo", activa=False,
        )
        datos = datos_dashboard_contable(self.empresa, hoy=date(2026, 10, 14))
        self.assertEqual(datos["resumen"]["cuentas"], 4)
        self.assertEqual(datos["resumen"]["cuentas_activas"], 3)
        self.assertEqual(datos["resumen"]["cuentas_movimiento"], 2)
        self.assertEqual({tipo["tipo"]: tipo["total"] for tipo in datos["cuentas_por_tipo"]}, {"activo": 3, "ingreso": 1})

    def test_periodo_muestra_solo_el_mes_y_la_empresa_consultados(self):
        PeriodoContable.objects.create(empresa=self.otra, anio=2026, mes=10, estado="cerrado")
        PeriodoContable.objects.create(empresa=self.empresa, anio=2026, mes=9, estado="cerrado")
        actual = PeriodoContable.objects.create(empresa=self.empresa, anio=2026, mes=10)
        datos = datos_dashboard_contable(self.empresa, hoy=date(2026, 10, 14))
        self.assertEqual(datos["periodo_actual"], actual)
        self.assertEqual(datos["estado_periodo"], "Abierto")
        actual.cambiar_estado("cerrado")
        datos = datos_dashboard_contable(self.empresa, hoy=date(2026, 10, 14))
        self.assertEqual(datos["estado_periodo"], "Cerrado")

    def test_bancos_y_pendientes_incluyen_clasificados_sin_confundir_cajas(self):
        banco = CuentaFinanciera.objects.create(empresa=self.empresa, nombre="Banco operativo", cuenta_contable=self.caja)
        CuentaFinanciera.objects.create(empresa=self.empresa, nombre="Caja", tipo="caja", cuenta_contable=self.caja)
        CuentaFinanciera.objects.create(empresa=self.empresa, nombre="Banco cerrado", activa=False, cuenta_contable=self.caja)
        banco_ajeno = CuentaFinanciera.objects.create(empresa=self.otra, nombre="Banco ajeno", cuenta_contable=self.caja_otra)
        for estado in ("pendiente", "clasificado", "contabilizado"):
            MovimientoBancario.objects.create(
                empresa=self.empresa, cuenta_financiera=banco, fecha=date(2026, 10, 14),
                descripcion="Movimiento del banco", debito=Decimal("10.00"), estado=estado,
            )
        MovimientoBancario.objects.create(
            empresa=self.otra, cuenta_financiera=banco_ajeno, fecha=date(2026, 10, 14),
            descripcion="Movimiento ajeno", debito=Decimal("90.00"),
        )
        datos = datos_dashboard_contable(self.empresa, hoy=date(2026, 10, 14))
        self.assertEqual(datos["bancos_resumen"]["cuentas_activas"], 2)
        self.assertEqual(datos["bancos_resumen"]["bancos_activos"], 1)
        self.assertEqual(datos["bancos_resumen"]["movimientos_pendientes"], 2)
        self.assertEqual(datos["bancos_resumen"]["movimientos_sin_clasificar"], 1)

    def test_actividad_de_diciembre_no_cuenta_enero_y_recientes_se_limitan_a_ocho(self):
        for dia in range(1, 10):
            self.asiento(fecha=date(2026, 12, dia))
        enero = self.asiento(fecha=date(2027, 1, 1))
        datos = datos_dashboard_contable(self.empresa, hoy=date(2026, 12, 31))
        self.assertEqual(datos["actividad_mes"]["total"], 9)
        self.assertEqual(datos["actividad_mes"]["etiqueta"], "Diciembre de 2026")
        self.assertEqual(len(datos["ultimos_asientos"]), 8)
        self.assertEqual(datos["ultimos_asientos"][0].pk, enero.pk)
        self.assertEqual([asiento.fecha for asiento in datos["ultimos_asientos"]], sorted(
            (asiento.fecha for asiento in datos["ultimos_asientos"]), reverse=True,
        ))

    def renderizar(self, **acceso):
        return render_to_string("contabilidad/dashboard_contabilidad.html", {
            "empresa": self.empresa,
            "erp_access": {"contabilidad": True, **acceso},
            **datos_dashboard_contable(self.empresa, hoy=date(2026, 10, 14)),
        })

    def test_panel_consulta_oculta_acciones_de_creacion_catalogo_y_reportes_sin_permiso(self):
        contenido = self.renderizar(
            asientos_contables=False, catalogo_cuentas=False, reportes_contables=False,
        )
        for destino in ("crear_asiento_contable", "crear_cuenta_contable", "catalogo_cuentas", "reportes_contables", "estado_resultados"):
            with self.subTest(destino=destino):
                self.assertNotIn(f'href="{reverse(destino, args=[self.empresa.slug])}"', contenido)
        for destino in ("asientos_contables", "bancos_dashboard", "periodos_contables"):
            self.assertIn(f'href="{reverse(destino, args=[self.empresa.slug])}"', contenido)

    def test_panel_habilita_acciones_autorizadas_y_escapa_descripcion(self):
        asiento = self.asiento(importe="21.35")
        asiento.descripcion = '<script>alert("dato")</script>'
        asiento.save(update_fields=["descripcion"])
        contenido = self.renderizar(
            asientos_contables=True, catalogo_cuentas=True, reportes_contables=True,
        )
        for destino in ("crear_asiento_contable", "catalogo_cuentas", "reportes_contables", "estado_resultados"):
            with self.subTest(destino=destino):
                self.assertIn(f'href="{reverse(destino, args=[self.empresa.slug])}"', contenido)
        self.assertIn(reverse("ver_asiento_contable", args=[self.empresa.slug, asiento.pk]), contenido)
        self.assertNotIn('<script>alert("dato")</script>', contenido)
        self.assertIn("&lt;script&gt;", contenido)
        self.assertIn("14/10/2026", contenido)
        self.assertIn("L 21.35", contenido)

    def test_panel_sin_asientos_muestra_mes_espanol_y_anillo_sin_progreso_inventado(self):
        contenido = self.renderizar(asientos_contables=False)
        self.assertIn("Octubre de 2026", contenido)
        self.assertIn("Aún no hay asientos este mes", contenido)
        self.assertIn("Sin período registrado", contenido)
        self.assertNotIn("stroke-dasharray", contenido)
        self.assertNotIn("Crear primer asiento", contenido)
