"""El detalle diferido comparte el alcance y los importes del Mayor."""

from datetime import date
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

from django.core.exceptions import PermissionDenied
from django.db import connection
from django.test import RequestFactory, TestCase
from django.urls import reverse

from core.models import Empresa, Modulo, PlanComercial, PlanModulo, RolSistema, Usuario
from .balance_movimientos_views import movimientos_balance_general
from .models import AsientoContable, CuentaContable, LineaAsientoContable
from .reportes import datos_balance_general, datos_mayor


class BalanceMovimientosTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        modulo, _ = Modulo.objects.get_or_create(codigo="contabilidad", defaults={"nombre": "Contabilidad", "es_comercial": True})
        plan = PlanComercial.objects.create(nombre="Plan detalle balance", codigo="plan-detalle-balance", precio_mensual="99.00")
        PlanModulo.objects.create(plan=plan, modulo=modulo, activo=True)
        cls.empresa = Empresa.objects.create(nombre="Balance interactivo", slug="balance-interactivo", rtn="08011999211221", estado_licencia="activa", plan_comercial=plan)
        cls.otra = Empresa.objects.create(nombre="Balance privado", slug="balance-privado", rtn="08011999211222", estado_licencia="activa", plan_comercial=plan)
        cls.usuario = Usuario.objects.create_user(username="contador-detalle", empresa=cls.empresa, is_superuser=True, is_staff=True)
        cls.principal = CuentaContable.objects.create(empresa=cls.empresa, codigo="1", nombre="Activos", tipo="activo", acepta_movimientos=False)
        cls.banco = CuentaContable.objects.create(empresa=cls.empresa, codigo="1.01", nombre="Banco de prueba", tipo="activo", cuenta_padre=cls.principal)
        cls.capital = CuentaContable.objects.create(empresa=cls.empresa, codigo="3", nombre="Capital", tipo="patrimonio")
        cls.ajena = CuentaContable.objects.create(empresa=cls.otra, codigo="1.01", nombre="Cuenta confidencial", tipo="activo")
        cls.capital_ajeno = CuentaContable.objects.create(empresa=cls.otra, codigo="3", nombre="Capital privado", tipo="patrimonio")

    def setUp(self):
        self.client.force_login(self.usuario)

    def url(self, cuenta=None, empresa=None):
        return reverse("movimientos_balance_general", args=[(empresa or self.empresa).slug, (cuenta or self.banco).pk])

    def asiento(self, importe, *, fecha=date(2026, 10, 5), estado="contabilizado", empresa=None, cuenta=None, descripcion="Depósito de prueba", detalle=None, credito=False):
        empresa = empresa or self.empresa
        cuenta = cuenta or self.banco
        capital = self.capital_ajeno if empresa == self.otra else self.capital
        asiento = AsientoContable.objects.create(empresa=empresa, fecha=fecha, estado=estado, descripcion=descripcion, referencia="DOC-PRUEBA")
        monto = Decimal(str(importe))
        linea = LineaAsientoContable.objects.create(asiento=asiento, cuenta=cuenta, detalle=detalle, debe=0 if credito else monto, haber=monto if credito else 0)
        LineaAsientoContable.objects.create(asiento=asiento, cuenta=capital, debe=monto if credito else 0, haber=0 if credito else monto)
        return linea

    def test_detalle_reutiliza_mayor_con_decimales_y_balance_sin_mutaciones(self):
        self.asiento("1000000.10", detalle="Primer depósito")
        self.asiento("0.20", descripcion="Segundo depósito")
        self.asiento("25.05", fecha=date(2026, 10, 6), credito=True)
        antes = datos_balance_general(self.empresa, "2026-10-06")
        registros = list(LineaAsientoContable.objects.order_by("pk").values())
        respuesta = self.client.get(self.url(), {"fecha_corte": "2026-10-06"})
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta["X-Balance-Movimientos"], "1")
        self.assertIn("no-store", respuesta["Cache-Control"])
        self.assertEqual(respuesta.context["bloque"]["saldo"], Decimal("999975.25"))
        mayor = datos_mayor(self.empresa, fecha_fin="2026-10-06", cuenta=self.banco)[0]
        self.assertEqual([m["saldo"] for m in respuesta.context["pagina"]], [m["saldo"] for m in mayor["movimientos"]])
        self.assertContains(respuesta, "Primer depósito")
        self.assertContains(respuesta, "Segundo depósito")
        self.assertContains(respuesta, "DOC-PRUEBA")
        self.assertEqual(antes, datos_balance_general(self.empresa, "2026-10-06"))
        self.assertEqual(registros, list(LineaAsientoContable.objects.order_by("pk").values()))
        self.assertTrue(antes["resumen"]["cuadrado"])

    def test_corte_incluye_historial_no_solo_mes_y_excluye_futuro_borradores_y_anulados(self):
        anterior = self.asiento(100, fecha=date(2026, 9, 1), descripcion="Historial anterior")
        actual = self.asiento(50, fecha=date(2026, 10, 5))
        self.asiento(10, fecha=date(2026, 10, 6), descripcion="Movimiento futuro")
        self.asiento(999, estado="borrador", descripcion="Borrador oculto")
        self.asiento(999, estado="anulado", descripcion="Anulado oculto")
        respuesta = self.client.get(self.url(), {"fecha_corte": "2026-10-05"})
        self.assertEqual([m["linea"].pk for m in respuesta.context["pagina"]], [anterior.pk, actual.pk])
        self.assertEqual(respuesta.context["bloque"]["saldo"], Decimal("150.00"))
        for texto in ("Movimiento futuro", "Borrador oculto", "Anulado oculto"):
            self.assertNotContains(respuesta, texto)

    def test_sin_corte_incluye_todo_y_conserva_cuenta_inactiva(self):
        self.asiento("10.10", fecha=date(2026, 12, 31))
        CuentaContable.objects.filter(pk=self.banco.pk).update(activa=False)
        respuesta = self.client.get(self.url())
        self.assertContains(respuesta, "Todo el historial contabilizado")
        self.assertEqual(respuesta.context["bloque"]["saldo"], Decimal("10.10"))

    def test_no_expone_cuentas_ajenas_o_tipos_fuera_del_balance(self):
        self.asiento(99, empresa=self.otra, cuenta=self.ajena, descripcion="Concepto privado")
        self.assertEqual(self.client.get(self.url(self.ajena)).status_code, 404)
        ingreso = CuentaContable.objects.create(empresa=self.empresa, codigo="4", nombre="Ingresos", tipo="ingreso")
        self.assertEqual(self.client.get(self.url(ingreso)).status_code, 404)
        self.assertNotContains(self.client.get(self.url()), "Concepto privado")

    def test_relacion_historica_ajena_no_filtra_movimientos(self):
        linea = self.asiento(999, empresa=self.otra, cuenta=self.ajena, descripcion="Asiento privado enlazado incorrectamente")
        # Simula una relación histórica inválida creada fuera de las validaciones.
        with connection.cursor() as cursor:
            cursor.execute("UPDATE contabilidad_lineaasientocontable SET cuenta_id = %s WHERE id = %s", [self.banco.pk, linea.pk])
        respuesta = self.client.get(self.url())
        self.assertEqual(respuesta.context["pagina"].paginator.count, 0)
        self.assertNotContains(respuesta, "Asiento privado")

    def test_links_mayor_preservan_empresa_cuenta_corte_y_movimiento(self):
        linea = self.asiento("1250.50")
        respuesta = self.client.get(self.url(), {"fecha_corte": "2026-10-20"})
        destino = respuesta.context["pagina"][0]["url_mayor"]
        partes = urlsplit(destino)
        self.assertEqual(partes.path, reverse("mayor_cuenta", args=[self.empresa.slug]))
        self.assertEqual(parse_qs(partes.query), {"cuenta": [str(self.banco.pk)], "fecha_fin": ["2026-10-20"], "movimiento": [str(linea.pk)]})
        self.assertEqual(partes.fragment, f"movimiento-{linea.pk}")
        self.assertNotIn("fecha_inicio", parse_qs(partes.query))

    def test_paginacion_mantiene_saldo_acumulado_y_corte(self):
        for _ in range(51):
            self.asiento("0.10")
        primera = self.client.get(self.url(), {"fecha_corte": "2026-10-20"})
        segunda = self.client.get(self.url(), {"fecha_corte": "2026-10-20", "pagina": 2})
        self.assertEqual(len(primera.context["pagina"]), 50)
        self.assertEqual(primera.context["pagina"][-1]["saldo"], Decimal("5.00"))
        self.assertEqual(len(segunda.context["pagina"]), 1)
        self.assertEqual(segunda.context["pagina"][0]["saldo"], Decimal("5.10"))
        self.assertContains(primera, "data-balance-pagina")
        self.assertEqual(parse_qs(urlsplit(primera.context["pagina_siguiente_url"]).query), {"fecha_corte": ["2026-10-20"], "pagina": ["2"]})

    def test_fecha_invalida_rechazada_sin_consultar_mayor(self):
        for fecha in ("texto", "2026-02-30", "20261001", "2026-1-1"):
            with self.subTest(fecha=fecha):
                respuesta = self.client.get(self.url(), {"fecha_corte": fecha})
                self.assertEqual(respuesta.status_code, 400)
                self.assertNotIn("X-Balance-Movimientos", respuesta)

    def test_solo_get_y_sesion_requerida(self):
        self.assertEqual(self.client.post(self.url()).status_code, 405)
        self.client.logout()
        respuesta = self.client.get(self.url())
        self.assertEqual(respuesta.status_code, 302)
        self.assertNotIn("X-Balance-Movimientos", respuesta)

    def test_permiso_explicito_tambien_sin_middleware(self):
        rol = RolSistema.objects.create(nombre="Contador sin reportes", codigo="contador-sin-detalle", puede_contabilidad=True, puede_reportes_contables=False)
        usuario = Usuario.objects.create_user(username="sin-detalle", empresa=self.empresa, rol_sistema=rol)
        solicitud = RequestFactory().get(self.url())
        solicitud.user = usuario
        with self.assertRaises(PermissionDenied):
            movimientos_balance_general(solicitud, self.empresa.slug, self.banco.pk)
        usuario.es_administrador_empresa = True
        with self.assertRaises(PermissionDenied):
            movimientos_balance_general(solicitud, self.otra.slug, self.ajena.pk)

    def test_cuenta_acreedora_comparte_saldo_natural_del_mayor(self):
        self.asiento("1250.50")
        respuesta = self.client.get(self.url(self.capital))
        self.assertEqual(respuesta.context["pagina"][0]["saldo"], Decimal("1250.50"))

    def test_balance_inicial_no_carga_detalle_y_los_totales_no_cambian(self):
        self.asiento(100, detalle="Detalle exclusivo diferido")
        respuesta = self.client.get(reverse("balance_general", args=[self.empresa.slug]))
        self.assertNotContains(respuesta, "Detalle exclusivo diferido")
        self.assertNotContains(respuesta, "data-balance-fragmento")
        self.assertTrue(respuesta.context["resumen"]["cuadrado"])
        self.assertEqual(respuesta.context["resumen"]["total_activos"], Decimal("100.00"))
