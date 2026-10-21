"""El detalle de Resultados conserva período, cierres y empresa del reporte."""

from datetime import date
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

from django.core.exceptions import PermissionDenied
from django.db import connection
from django.test import RequestFactory, TestCase
from django.urls import reverse

from core.models import Empresa, Modulo, PlanComercial, PlanModulo, RolSistema, Usuario
from .balance_movimientos_views import movimientos_estado_resultados
from .models import AsientoContable, CuentaContable, LineaAsientoContable
from .reportes import EVENTOS_CIERRE_RESULTADOS, datos_estado_resultados, datos_mayor


class ResultadosMovimientosTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        modulo, _ = Modulo.objects.get_or_create(
            codigo="contabilidad", defaults={"nombre": "Contabilidad", "es_comercial": True},
        )
        plan = PlanComercial.objects.create(
            nombre="Plan detalle resultados", codigo="plan-detalle-resultados", precio_mensual="99.00",
        )
        PlanModulo.objects.create(plan=plan, modulo=modulo, activo=True)
        cls.empresa = Empresa.objects.create(
            nombre="Resultados interactivos", slug="resultados-interactivos", rtn="08011999211321",
            estado_licencia="activa", plan_comercial=plan,
        )
        cls.otra = Empresa.objects.create(
            nombre="Resultados privados", slug="resultados-privados", rtn="08011999211322",
            estado_licencia="activa", plan_comercial=plan,
        )
        cls.usuario = Usuario.objects.create_user(
            username="contador-detalle-resultados", empresa=cls.empresa, is_superuser=True, is_staff=True,
        )
        cls.banco = cls.cuenta("1", "Banco", "activo")
        cls.principal = cls.cuenta("4", "Ingresos", "ingreso", acepta_movimientos=False)
        cls.ingreso = cls.cuenta("4.1", "Intereses", "ingreso", cuenta_padre=cls.principal)
        cls.costo = cls.cuenta("5.1", "Costo financiero", "costo")
        cls.gasto = cls.cuenta("6.1", "Servicios", "gasto")
        cls.ingreso_ajeno = cls.cuenta("4.1", "Ingreso confidencial", "ingreso", empresa=cls.otra)
        cls.banco_ajeno = cls.cuenta("1", "Banco privado", "activo", empresa=cls.otra)

    @classmethod
    def cuenta(cls, codigo, nombre, tipo, empresa=None, **kwargs):
        return CuentaContable.objects.create(
            empresa=empresa or cls.empresa, codigo=codigo, nombre=nombre, tipo=tipo, **kwargs,
        )

    def setUp(self):
        self.client.force_login(self.usuario)

    def url(self, cuenta=None, empresa=None):
        return reverse("movimientos_estado_resultados", args=[
            (empresa or self.empresa).slug, (cuenta or self.ingreso).pk,
        ])

    def movimiento(self, importe, *, cuenta=None, fecha=date(2026, 10, 5), estado="contabilizado", evento=None, reverso=False, detalle="", descripcion="Movimiento de resultados"):
        cuenta = cuenta or self.ingreso
        banco = self.banco_ajeno if cuenta.empresa_id == self.otra.pk else self.banco
        asiento = AsientoContable.objects.create(
            empresa=cuenta.empresa, fecha=fecha, estado=estado, evento=evento,
            descripcion=descripcion, referencia="REF-RESULTADOS",
        )
        monto = Decimal(str(importe))
        es_debito = (cuenta.tipo in ("costo", "gasto")) != reverso
        debe, haber = (monto, Decimal("0.00")) if es_debito else (Decimal("0.00"), monto)
        linea = LineaAsientoContable.objects.create(
            asiento=asiento, cuenta=cuenta, debe=debe, haber=haber, detalle=detalle,
        )
        LineaAsientoContable.objects.create(asiento=asiento, cuenta=banco, debe=haber, haber=debe)
        return linea

    def consultar(self, cuenta=None, **filtros):
        return self.client.get(self.url(cuenta), {
            "fecha_inicio": "2026-10-01", "fecha_fin": "2026-10-31", **filtros,
        })

    def test_importes_decimales_y_resultado_se_conservan_sin_modificar_registros(self):
        self.movimiento("1000000.10", detalle="Intereses del préstamo")
        self.movimiento("0.20", descripcion="Ingreso con centavos")
        self.movimiento("25.05", reverso=True)
        antes = datos_estado_resultados(self.empresa, "2026-10-01", "2026-10-31")
        lineas = list(LineaAsientoContable.objects.order_by("pk").values())
        asientos = list(AsientoContable.objects.order_by("pk").values())
        respuesta = self.consultar()
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta["X-Balance-Movimientos"], "1")
        self.assertIn("no-store", respuesta["Cache-Control"])
        importes = [m["importe"] for m in respuesta.context["pagina"]]
        self.assertEqual(importes, [Decimal("1000000.10"), Decimal("0.20"), Decimal("-25.05")])
        self.assertEqual(sum(importes), antes["resumen"]["total_ingresos"])
        self.assertContains(respuesta, "Intereses del préstamo")
        self.assertContains(respuesta, "Ingreso con centavos")
        self.assertContains(respuesta, "REF-RESULTADOS")
        self.assertContains(respuesta, '<th scope="col" class="bgm-amount">Importe</th>', html=True)
        self.assertEqual(antes, datos_estado_resultados(self.empresa, "2026-10-01", "2026-10-31"))
        self.assertEqual(lineas, list(LineaAsientoContable.objects.order_by("pk").values()))
        self.assertEqual(asientos, list(AsientoContable.objects.order_by("pk").values()))

    def test_ingreso_costo_gasto_comparten_naturaleza_contable(self):
        for cuenta in (self.ingreso, self.costo, self.gasto):
            with self.subTest(tipo=cuenta.tipo):
                self.movimiento("1250.50", cuenta=cuenta)
                self.movimiento("10.25", cuenta=cuenta, reverso=True)
                respuesta = self.consultar(cuenta)
                self.assertEqual([m["importe"] for m in respuesta.context["pagina"]], [Decimal("1250.50"), Decimal("-10.25")])

    def test_periodo_inclusivo_sin_apertura_futuros_borradores_ni_anulados(self):
        self.movimiento(900, fecha=date(2026, 9, 30), descripcion="Apertura anterior")
        inicio = self.movimiento(100, fecha=date(2026, 10, 1))
        fin = self.movimiento(50, fecha=date(2026, 10, 31))
        self.movimiento(999, fecha=date(2026, 11, 1), descripcion="Registro futuro")
        self.movimiento(999, estado="borrador", descripcion="Registro borrador")
        self.movimiento(999, estado="anulado", descripcion="Registro anulado")
        respuesta = self.consultar()
        self.assertEqual([m["linea"].pk for m in respuesta.context["pagina"]], [inicio.pk, fin.pk])
        self.assertEqual(sum(m["importe"] for m in respuesta.context["pagina"]), Decimal("150.00"))
        self.assertEqual(respuesta.context["bloque"]["saldo_inicial"], Decimal("900.00"))
        for texto in ("Apertura anterior", "Registro futuro", "Registro borrador", "Registro anulado"):
            self.assertNotContains(respuesta, texto)
        self.assertContains(respuesta, "Desde el 01/10/2026 hasta el 31/10/2026")

    def test_excluye_los_tres_eventos_de_cierre_igual_que_resultados(self):
        normal = self.movimiento(100)
        for evento in EVENTOS_CIERRE_RESULTADOS:
            self.movimiento(10, evento=evento, reverso=True, descripcion=f"Cierre {evento}")
        respuesta = self.consultar()
        self.assertEqual([m["linea"].pk for m in respuesta.context["pagina"]], [normal.pk])
        self.assertEqual(datos_estado_resultados(self.empresa, "2026-10-01", "2026-10-31")["resumen"]["total_ingresos"], Decimal("100.00"))
        self.assertEqual(len(datos_mayor(self.empresa, "2026-10-01", "2026-10-31", cuenta=self.ingreso)[0]["movimientos"]), 4)
        for evento in EVENTOS_CIERRE_RESULTADOS:
            self.assertNotContains(respuesta, f"Cierre {evento}")

    def test_fechas_abiertas_y_cuentas_inactivas_conservan_historial(self):
        anterior = self.movimiento(10, fecha=date(2026, 9, 30))
        posterior = self.movimiento(20, fecha=date(2026, 11, 1))
        CuentaContable.objects.filter(pk=self.ingreso.pk).update(activa=False)
        respuesta = self.client.get(self.url())
        self.assertEqual([m["linea"].pk for m in respuesta.context["pagina"]], [anterior.pk, posterior.pk])
        self.assertContains(respuesta, "Desde el inicio del historial")
        self.assertContains(respuesta, "Sin fecha final")
        self.assertEqual(self.consultar(fecha_inicio="").context["pagina"].paginator.count, 1)
        self.assertEqual(self.consultar(fecha_fin="").context["pagina"].paginator.count, 1)

    def test_no_expone_otra_empresa_ni_cuentas_fuera_de_resultados(self):
        self.movimiento(999, cuenta=self.ingreso_ajeno, descripcion="Concepto confidencial")
        self.assertEqual(self.client.get(self.url(self.ingreso_ajeno)).status_code, 404)
        self.assertEqual(self.client.get(self.url(self.banco)).status_code, 404)
        self.assertNotContains(self.consultar(), "Concepto confidencial")

    def test_relacion_historica_contaminada_no_expone_asientos_ajenos(self):
        linea = self.movimiento(999, cuenta=self.ingreso_ajeno, descripcion="Asiento confidencial")
        with connection.cursor() as cursor:
            cursor.execute("UPDATE contabilidad_lineaasientocontable SET cuenta_id = %s WHERE id = %s", [self.ingreso.pk, linea.pk])
        respuesta = self.consultar()
        self.assertEqual(respuesta.context["pagina"].paginator.count, 0)
        self.assertNotContains(respuesta, "Asiento confidencial")

    def test_links_mayor_preservan_empresa_cuenta_fechas_origen_y_seleccion(self):
        linea = self.movimiento(100)
        respuesta = self.consultar()
        partes = urlsplit(respuesta.context["pagina"][0]["url_mayor"])
        self.assertEqual(partes.path, reverse("mayor_cuenta", args=[self.empresa.slug]))
        self.assertEqual(parse_qs(partes.query), {
            "cuenta": [str(self.ingreso.pk)], "fecha_inicio": ["2026-10-01"], "fecha_fin": ["2026-10-31"],
            "origen": ["estado_resultados"], "movimiento": [str(linea.pk)],
        })
        self.assertEqual(partes.fragment, f"movimiento-{linea.pk}")
        self.assertEqual(parse_qs(urlsplit(respuesta.context["mayor_url"]).query)["origen"], ["estado_resultados"])

    def test_paginacion_conserva_fechas_e_importes_individuales(self):
        for _ in range(51):
            self.movimiento("0.10")
        primera = self.consultar()
        segunda = self.consultar(pagina=2)
        self.assertEqual(len(primera.context["pagina"]), 50)
        self.assertEqual(len(segunda.context["pagina"]), 1)
        self.assertEqual(segunda.context["pagina"][0]["importe"], Decimal("0.10"))
        self.assertEqual(parse_qs(urlsplit(primera.context["pagina_siguiente_url"]).query), {
            "fecha_inicio": ["2026-10-01"], "fecha_fin": ["2026-10-31"], "pagina": ["2"],
        })
        self.assertContains(primera, "data-balance-pagina")

    def test_fechas_invalidas_y_rango_invertido_rechazados(self):
        for filtros in (
            {"fecha_inicio": "texto"}, {"fecha_fin": "2026-02-30"},
            {"fecha_inicio": "20261001"}, {"fecha_fin": "2026-1-1"},
            {"fecha_inicio": "2026-11-01", "fecha_fin": "2026-10-01"},
        ):
            with self.subTest(filtros=filtros):
                respuesta = self.consultar(**filtros)
                self.assertEqual(respuesta.status_code, 400)
                self.assertNotIn("X-Balance-Movimientos", respuesta)

    def test_solo_get_y_sesion_requerida(self):
        self.assertEqual(self.client.post(self.url()).status_code, 405)
        self.client.logout()
        respuesta = self.client.get(self.url())
        self.assertEqual(respuesta.status_code, 302)
        self.assertNotIn("X-Balance-Movimientos", respuesta)

    def test_permiso_y_empresa_se_verifican_tambien_sin_middleware(self):
        rol = RolSistema.objects.create(
            nombre="Sin consulta resultados", codigo="sin-consulta-resultados", puede_contabilidad=True,
            puede_reportes_contables=False,
        )
        usuario = Usuario.objects.create_user(username="sin-detalle-resultados", empresa=self.empresa, rol_sistema=rol)
        solicitud = RequestFactory().get(self.url())
        solicitud.user = usuario
        with self.assertRaises(PermissionDenied):
            movimientos_estado_resultados(solicitud, self.empresa.slug, self.ingreso.pk)
        usuario.es_administrador_empresa = True
        with self.assertRaises(PermissionDenied):
            movimientos_estado_resultados(solicitud, self.otra.slug, self.ingreso_ajeno.pk)

    def test_pantalla_inicial_no_precarga_movimientos_ni_cambia_resultado(self):
        self.movimiento(100, detalle="Detalle reservado para desplegar")
        respuesta = self.client.get(reverse("estado_resultados", args=[self.empresa.slug]), {
            "fecha_inicio": "2026-10-01", "fecha_fin": "2026-10-31",
        })
        self.assertNotContains(respuesta, "Detalle reservado para desplegar")
        self.assertNotContains(respuesta, "data-balance-fragmento")
        self.assertEqual(respuesta.context["resumen"]["utilidad"], Decimal("100.00"))
