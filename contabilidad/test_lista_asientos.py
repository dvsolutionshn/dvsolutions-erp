"""El diario permite buscar y revisar sin mezclar datos entre empresas."""

from datetime import date
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

from django.core.exceptions import PermissionDenied
from django.db import connection
from django.http import QueryDict
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from core.models import Empresa, Modulo, PlanComercial, PlanModulo, RolSistema, Usuario
from .listado_asientos import contexto_listado_asientos
from .models import AsientoContable, CuentaContable, LineaAsientoContable
from .views import asientos_contables


class ListaAsientosTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        modulo, _ = Modulo.objects.get_or_create(codigo="contabilidad", defaults={"nombre": "Contabilidad"})
        plan = PlanComercial.objects.create(nombre="Diario de consulta", codigo="lista-diario", precio_mensual="199")
        PlanModulo.objects.create(plan=plan, modulo=modulo, activo=True)
        cls.empresa = Empresa.objects.create(
            nombre="Empresa diario A", slug="lista-diario-a", rtn="08011999199501",
            estado_licencia="activa", plan_comercial=plan,
        )
        cls.otra = Empresa.objects.create(
            nombre="Empresa diario B", slug="lista-diario-b", rtn="08011999199502",
            estado_licencia="activa", plan_comercial=plan,
        )
        rol = RolSistema.objects.create(nombre="Consulta diario", codigo="consulta-lista-diario", puede_contabilidad=True)
        cls.usuario = Usuario.objects.create_user(username="consulta-lista-diario", empresa=cls.empresa, rol_sistema=rol)
        cls.usuario_ajeno = Usuario.objects.create_user(username="consulta-lista-ajeno", empresa=cls.otra, rol_sistema=rol)
        cls.sin_permiso = Usuario.objects.create_user(username="consulta-lista-sin-permiso", empresa=cls.empresa)
        cls.caja = CuentaContable.objects.create(empresa=cls.empresa, codigo="110101", nombre="Caja del diario", tipo="activo")
        cls.ingreso = CuentaContable.objects.create(empresa=cls.empresa, codigo="410101", nombre="Ingreso del diario", tipo="ingreso")
        cls.historica = CuentaContable.objects.create(
            empresa=cls.empresa, codigo="110102", nombre="Banco histórico", tipo="activo", activa=False,
        )
        cls.cuenta_ajena = CuentaContable.objects.create(
            empresa=cls.otra, codigo="999999", nombre="Cuenta secreta extranjera", tipo="activo",
        )
        cls.borrador = cls.crear_asiento(
            cls.empresa, cls.caja, cls.ingreso, "ASI-2026-001", "Cobro de cartera", "0.30",
            fecha=date(2026, 10, 1), referencia="REC-OCT", origen_modulo="prestamos",
        )
        # Dos líneas coincidentes deben devolver un solo asiento, con todos sus importes.
        primera = cls.borrador.lineas.order_by("id").first()
        primera.debe = Decimal("0.10")
        primera.detalle = "Cuota compartida de octubre"
        primera.save()
        LineaAsientoContable.objects.create(
            asiento=cls.borrador, cuenta=cls.caja, detalle="Cuota compartida adicional", debe=Decimal("0.20"),
        )
        cls.contabilizado = cls.crear_asiento(
            cls.empresa, cls.historica, cls.ingreso, "ASI-2026-002", "Depósito histórico", "50.25",
            fecha=date(2026, 10, 15), estado="contabilizado",
        )
        cls.anulado = cls.crear_asiento(
            cls.empresa, cls.caja, cls.ingreso, "ASI-2026-003", "Operación anulada", "10.10",
            fecha=date(2026, 10, 20), estado="anulado",
        )
        cls.ajeno = cls.crear_asiento(
            cls.otra, cls.cuenta_ajena, cls.cuenta_ajena, "PRIVADO-001", "Operación privada", "999.99",
            fecha=date(2026, 10, 10), referencia="PRIVADO", origen_modulo="origen-privado",
        )

    @staticmethod
    def crear_asiento(empresa, debe, haber, numero, descripcion, importe, **datos):
        # Se crean como borradores para que el fixture no modifique un asiento ya contabilizado.
        estado = datos.pop("estado", "borrador")
        asiento = AsientoContable.objects.create(
            empresa=empresa, numero=numero, descripcion=descripcion, fecha=datos.pop("fecha", date(2026, 10, 10)), **datos,
        )
        LineaAsientoContable.objects.create(asiento=asiento, cuenta=debe, debe=Decimal(importe))
        LineaAsientoContable.objects.create(asiento=asiento, cuenta=haber, haber=Decimal(importe))
        if estado != "borrador":
            asiento.estado = estado
            asiento.save(update_fields=["estado"])
        return asiento

    def consultar(self, **filtros):
        parametros = QueryDict("", mutable=True)
        parametros.update(filtros)
        return contexto_listado_asientos(self.empresa, parametros)

    def setUp(self):
        self.client.force_login(self.usuario)
        self.url = reverse("asientos_contables", args=[self.empresa.slug])

    def test_lista_contadores_y_cuentas_estan_delimitados_por_empresa(self):
        contexto = self.consultar()
        self.assertEqual({item.pk for item in contexto["asientos"]}, {self.borrador.pk, self.contabilizado.pk, self.anulado.pk})
        self.assertEqual(contexto["resumen_lista"], {"total": 3, "contabilizados": 1, "borradores": 1, "anulados": 1})
        self.assertEqual({item.pk for item in contexto["cuentas_filtro"]}, {self.caja.pk, self.ingreso.pk, self.historica.pk})
        self.assertNotIn(self.cuenta_ajena, contexto["cuentas_filtro"])

    def test_busca_encabezado_cuenta_codigo_y_detalle_sin_duplicar_totales(self):
        for q in ("ASI-2026-001", "cartera", "REC-OCT", "prestamos", "110101", "Caja del diario", "Cuota compartida"):
            with self.subTest(q=q):
                contexto = self.consultar(q=q, estado="borrador")
                self.assertEqual([item.pk for item in contexto["asientos"]], [self.borrador.pk])
                asiento = contexto["asientos"][0]
                self.assertEqual(asiento.importe_debe, Decimal("0.30"))
                self.assertEqual(asiento.importe_haber, Decimal("0.30"))
                self.assertEqual(asiento.numero_lineas, 3)
                self.assertEqual(len(asiento.lineas_revision), 3)
                self.assertEqual(contexto["total_resultados"], 1)

    def test_busquedas_no_exponen_datos_del_otro_diario(self):
        for q in ("PRIVADO", "999999", "Cuenta secreta extranjera", "origen-privado"):
            with self.subTest(q=q):
                self.assertEqual(self.consultar(q=q)["total_resultados"], 0)

    def test_vinculo_historico_inconsistente_no_revela_cuenta_ajena_ni_oculta_importes(self):
        linea = self.borrador.lineas.order_by("id").first()
        # Simula únicamente en la base de pruebas una inconsistencia anterior
        # a las validaciones de escritura actuales.
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE contabilidad_lineaasientocontable SET cuenta_id = %s WHERE id = %s",
                [self.cuenta_ajena.pk, linea.pk],
            )
        self.assertEqual(self.consultar(q="Cuenta secreta extranjera")["total_resultados"], 0)
        respuesta = self.client.get(self.url, {"q": self.borrador.numero})
        self.assertEqual(respuesta.status_code, 200)
        self.assertNotContains(respuesta, "Cuenta secreta extranjera")
        self.assertNotContains(respuesta, "999999")
        self.assertContains(respuesta, "Cuenta pendiente de revisión")
        asiento = respuesta.context["asientos"][0]
        self.assertEqual(asiento.importe_debe, Decimal("0.30"))
        self.assertEqual(asiento.numero_lineas, 3)

    def test_cuenta_historica_inactiva_se_puede_consultar(self):
        contexto = self.consultar(cuenta=str(self.historica.pk))
        self.assertEqual([item.pk for item in contexto["asientos"]], [self.contabilizado.pk])
        self.assertFalse(contexto["errores_filtros"])

    def test_cuenta_ajena_inexistente_o_malformada_no_se_ignora(self):
        for cuenta in (str(self.cuenta_ajena.pk), "99999999999999999999999999999999999", "no-es-id", "-1"):
            with self.subTest(cuenta=cuenta):
                contexto = self.consultar(cuenta=cuenta)
                self.assertEqual(contexto["total_resultados"], 0)
                self.assertIn("esta empresa", contexto["errores_filtros"][0])
                self.assertEqual(contexto["resumen_lista"]["total"], 3)

    def test_fechas_inclusivas_y_filtros_combinados(self):
        contexto = self.consultar(fecha_inicio="2026-10-01", fecha_fin="2026-10-15", estado="contabilizado", cuenta=str(self.historica.pk), q="histórico")
        self.assertEqual([item.pk for item in contexto["asientos"]], [self.contabilizado.pk])
        self.assertTrue(contexto["hay_filtros"])

    def test_fechas_invalidas_y_rango_invertido_muestran_error_sin_500(self):
        for filtros in (
            {"fecha_inicio": "2026-02-30"}, {"fecha_fin": "incorrecto"},
            {"fecha_inicio": "2026-10-31", "fecha_fin": "2026-10-01"},
            {"fecha_fin": "20261001"}, {"fecha_inicio": "2026-1-1"},
        ):
            with self.subTest(filtros=filtros):
                respuesta = self.client.get(self.url, filtros)
                self.assertEqual(respuesta.status_code, 200)
                self.assertEqual(respuesta.context["total_resultados"], 0)
                self.assertTrue(respuesta.context["errores_filtros"])

    def test_estado_invalido_muestra_error_sin_resultados(self):
        contexto = self.consultar(estado="publicado")
        self.assertEqual(contexto["total_resultados"], 0)
        self.assertIn("estado", contexto["errores_filtros"][0])

    def test_ordenes_son_estables_y_las_sumas_no_se_multiplican_por_filtros(self):
        esperados = {
            "recientes": [self.anulado.pk, self.contabilizado.pk, self.borrador.pk],
            "antiguos": [self.borrador.pk, self.contabilizado.pk, self.anulado.pk],
            "importe_desc": [self.contabilizado.pk, self.anulado.pk, self.borrador.pk],
            "importe_asc": [self.borrador.pk, self.anulado.pk, self.contabilizado.pk],
        }
        for orden, ids in esperados.items():
            with self.subTest(orden=orden):
                self.assertEqual([item.pk for item in self.consultar(orden=orden)["asientos"]], ids)
        contexto = self.consultar(q="Cuota compartida", orden="importe_desc", cuenta=str(self.caja.pk))
        self.assertEqual(contexto["total_resultados"], 1)
        self.assertEqual(contexto["asientos"][0].importe_debe, Decimal("0.30"))

    def test_paginacion_conserva_filtros_y_las_pestanas_reinician_pagina(self):
        for indice in range(28):
            self.crear_asiento(self.empresa, self.caja, self.ingreso, f"EXTRA-{indice:03}", "Captura paginada", "1.00")
        filtros = {"q": "Captura paginada", "cuenta": str(self.caja.pk), "estado": "borrador", "fecha_inicio": "2026-10-01", "fecha_fin": "2026-10-31", "orden": "antiguos", "por_pagina": "25"}
        contexto = self.consultar(**filtros)
        self.assertEqual(len(contexto["asientos"]), 25)
        self.assertEqual(contexto["paginator"].count, 28)
        siguiente = parse_qs(urlsplit(contexto["paginacion"]["siguiente_url"]).query)
        self.assertEqual(siguiente, {**{clave: [valor] for clave, valor in filtros.items()}, "page": ["2"]})
        segunda = self.consultar(**filtros, page="2")
        self.assertEqual(len(segunda["asientos"]), 3)
        self.assertFalse({a.pk for a in contexto["asientos"]} & {a.pk for a in segunda["asientos"]})
        pestaña = next(item for item in segunda["enlaces_estado"] if item["valor"] == "contabilizado")
        consulta = parse_qs(urlsplit(pestaña["url"]).query)
        self.assertNotIn("page", consulta)
        self.assertEqual(consulta["q"], [filtros["q"]])
        self.assertEqual(consulta["cuenta"], [filtros["cuenta"]])
        self.assertEqual(consulta["estado"], ["contabilizado"])

    def test_parametros_de_pagina_y_orden_invalidos_no_producen_500(self):
        for por_pagina in ("-1", "0", "1000", "abc"):
            contexto = self.consultar(por_pagina=por_pagina, page="no-es-pagina", orden="sql-invalido")
            self.assertEqual(contexto["paginator"].per_page, 25)
            self.assertEqual(contexto["page_obj"].number, 1)
            self.assertEqual(contexto["filtros"]["orden"], "recientes")
        for por_pagina in ("25", "50", "100"):
            self.assertEqual(self.consultar(por_pagina=por_pagina)["paginator"].per_page, int(por_pagina))
        self.assertEqual(self.consultar(page="999999")["page_obj"].number, 1)

    def test_vista_refuerza_empresa_y_permiso_aun_sin_middleware(self):
        for usuario in (self.usuario_ajeno, self.sin_permiso):
            with self.subTest(usuario=usuario.username):
                request = RequestFactory().get(self.url)
                request.user = usuario
                with self.assertRaises(PermissionDenied):
                    asientos_contables(request, self.empresa.slug)

    def test_get_no_escribe_y_las_consultas_no_crecen_con_cada_partida(self):
        with CaptureQueriesContext(connection) as consultas:
            contexto = self.consultar(q="diario", orden="importe_desc")
            for asiento in contexto["asientos"]:
                for linea in asiento.lineas_revision:
                    _ = (linea.cuenta.codigo, linea.cuenta.nombre, linea.debe, linea.haber)
        self.assertLessEqual(len(consultas), 5)
        self.assertTrue(all(consulta["sql"].lstrip().upper().startswith("SELECT") for consulta in consultas))
        with CaptureQueriesContext(connection) as consultas:
            respuesta = self.client.get(self.url)
        self.assertEqual(respuesta.status_code, 200)
        escrituras = [consulta["sql"] for consulta in consultas if consulta["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))]
        self.assertEqual(escrituras, [])
