from datetime import date
from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.urls import reverse

from core.access import permiso_contabilidad_accion
from core.models import Empresa, RolSistema, Usuario
from facturacion.models import Cliente, Factura, LineaFactura
from .models import AsientoContable, CuentaContable, PeriodoContable, RegularizacionMonedaFactura
from .regularizacion_views import regularizar_moneda_factura
from .services import asegurar_cuenta_contable_cliente, registrar_asiento_documento
from . import tests as pruebas_base


class PermisoRegularizacionMonedaTests(SimpleTestCase):
    def test_ruta_exige_permiso_contabilizar(self):
        self.assertEqual(permiso_contabilidad_accion("asientos/12/regularizar-moneda/"), "puede_contabilizar_asientos")


class RegularizacionMonedaInterfazTests(TestCase):
    def setUp(self):
        pruebas_base.ContabilidadTests.setUp(self)
        self.cai_factura.fecha_activacion = date(2020, 1, 1)
        self.cai_factura.fecha_limite = date(2030, 12, 31)
        self.cai_factura.save(update_fields=["fecha_activacion", "fecha_limite"])
        self.factura = Factura.objects.create(
            empresa=self.empresa, cliente=self.cliente, vendedor=self.usuario,
            fecha_emision=date(2026, 4, 10), fecha_vencimiento=date(2026, 4, 30),
            moneda="USD", tipo_cambio=25, estado="borrador",
        )
        LineaFactura.objects.create(factura=self.factura, producto=self.producto, cantidad=1, precio_unitario=100, impuesto=self.impuesto)
        self.factura.calcular_totales()
        self.factura.estado = "emitida"
        self.factura.save()
        cuenta_cliente = asegurar_cuenta_contable_cliente(self.cliente)
        self.asiento = registrar_asiento_documento(
            empresa=self.empresa, documento_tipo="factura", documento_id=self.factura.pk, evento="emision",
            fecha=self.factura.fecha_emision, descripcion="Factura USD histórica sin convertir",
            referencia=self.factura.numero_factura, origen_modulo="facturacion",
            moneda_origen="USD", tipo_cambio=25, conversion_verificada=False,
            lineas=[
                {"cuenta": cuenta_cliente, "detalle": "Cobro a cliente histórico", "debe": 115, "haber": 0},
                {"cuenta": "ventas_15", "debe": 0, "haber": 100},
                {"cuenta": "isv_15_por_pagar", "debe": 0, "haber": 15},
            ],
        )
        self.contrapartida = CuentaContable.objects.create(empresa=self.empresa, codigo="3199", nombre="Correcciones de períodos anteriores", tipo="patrimonio")
        self.otra = Empresa.objects.create(nombre="Otra empresa revisión", slug="otra-revision-moneda", rtn="08011999111201")
        self.cuenta_otra = CuentaContable.objects.create(empresa=self.otra, codigo="3199", nombre="Patrimonio otra empresa", tipo="patrimonio")
        self.url = reverse("regularizar_moneda_factura", args=[self.empresa.slug, self.asiento.pk])
        self.client.force_login(self.usuario)

    def datos(self, **cambios):
        datos = {"fecha": "2026-05-05", "tipo_cambio": "25", "cuenta_contrapartida": self.contrapartida.pk, "motivo": "Revisión del documento y respaldo del tipo de cambio histórico", "accion": "revisar"}
        datos.update(cambios)
        return datos

    def revisar(self, **cambios):
        response = self.client.post(self.url, self.datos(**cambios))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["lista_para_confirmar"], response.context["form"].errors)
        return response.context["revision_token"]

    def test_get_presenta_moneda_tasa_saldo_y_costo_sin_mutar(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Regularizar saldo histórico en dólares")
        self.assertContains(response, "Saldo registrado en HNL")
        self.assertContains(response, "Costo de inventario")
        self.assertFalse(RegularizacionMonedaFactura.objects.exists())
        self.asiento.refresh_from_db()
        self.assertFalse(self.asiento.conversion_verificada)
        self.assertEqual(self.asiento.total_debe, Decimal("115.00"))

    def test_concepto_manual_sin_producto_no_impide_consultar_revision(self):
        LineaFactura.objects.create(factura=self.factura, producto=None, descripcion_manual="Concepto manual", cantidad=1, precio_unitario=0, impuesto=self.impuesto)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Costo de inventario")

    def test_permiso_en_vista_rechaza_bypass_de_middleware(self):
        rol = RolSistema.objects.create(nombre="Captura revisión", codigo="captura-revision", puede_contabilidad=True, puede_crear_asientos=True)
        usuario = Usuario.objects.create_user(username="captura-revision", empresa=self.empresa, rol_sistema=rol)
        request = RequestFactory().post(self.url, self.datos())
        request.user = usuario
        with self.assertRaises(PermissionDenied):
            regularizar_moneda_factura(request, empresa_slug=self.empresa.slug, asiento_id=self.asiento.pk)
        self.assertFalse(RegularizacionMonedaFactura.objects.exists())

    def test_sin_permiso_no_muestra_accion_y_middleware_bloquea(self):
        rol = RolSistema.objects.create(nombre="Consulta revisión", codigo="consulta-revision", puede_contabilidad=True)
        usuario = Usuario.objects.create_user(username="consulta-revision", empresa=self.empresa, rol_sistema=rol)
        self.client.force_login(usuario)
        detalle = self.client.get(reverse("ver_asiento_contable", args=[self.empresa.slug, self.asiento.pk]))
        self.assertEqual(detalle.status_code, 200)
        self.assertNotContains(detalle, "Regularizar moneda de factura")
        response = self.client.post(self.url, self.datos())
        self.assertEqual(response.status_code, 302)
        self.assertFalse(RegularizacionMonedaFactura.objects.exists())

    def test_aisla_asiento_y_contrapartida_por_empresa(self):
        response = self.client.post(self.url, self.datos(cuenta_contrapartida=self.cuenta_otra.pk))
        self.assertEqual(response.status_code, 200)
        self.assertIn("cuenta_contrapartida", response.context["form"].errors)
        ajeno = AsientoContable.objects.create(empresa=self.otra, fecha=date(2026, 4, 10), descripcion="Documento ajeno", documento_tipo="factura", documento_id=self.factura.pk, evento="emision", estado="contabilizado", conversion_verificada=False)
        response = self.client.get(reverse("regularizar_moneda_factura", args=[self.empresa.slug, ajeno.pk]))
        self.assertEqual(response.status_code, 404)
        cliente_ajeno = Cliente.objects.create(empresa=self.otra, nombre="Cliente confidencial de otra empresa")
        Factura.objects.filter(pk=self.factura.pk).update(cliente_id=cliente_ajeno.pk)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 404)
        self.assertNotContains(response, cliente_ajeno.nombre, status_code=404)
        self.assertFalse(RegularizacionMonedaFactura.objects.exists())

    def test_tasa_invalida_y_fecha_cerrada_quedan_en_formulario(self):
        response = self.client.post(self.url, self.datos(tipo_cambio="0"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("tipo_cambio", response.context["form"].errors)
        PeriodoContable.objects.create(empresa=self.empresa, anio=2026, mes=5, estado="cerrado")
        response = self.client.post(self.url, self.datos())
        self.assertEqual(response.status_code, 200)
        self.assertIn("fecha", response.context["form"].errors)
        self.assertContains(response, "período contable está cerrado")
        self.assertFalse(RegularizacionMonedaFactura.objects.exists())

    def test_fecha_no_puede_anticipar_el_documento_original(self):
        response = self.client.post(self.url, self.datos(fecha="2026-03-31"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("fecha", response.context["form"].errors)
        self.assertContains(response, "igual o posterior a la fecha del asiento original")
        self.assertFalse(RegularizacionMonedaFactura.objects.exists())

    def test_revision_previa_no_contabiliza_y_exige_confirmacion_firmada(self):
        token = self.revisar()
        self.assertTrue(token)
        self.assertFalse(RegularizacionMonedaFactura.objects.exists())
        response = self.client.post(self.url, self.datos(accion="registrar", confirmar="1"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Revisa el ajuste antes de confirmarlo")
        self.assertFalse(RegularizacionMonedaFactura.objects.exists())

    def test_datos_cambiados_requieren_nueva_vista_previa(self):
        token = self.revisar()
        response = self.client.post(self.url, self.datos(accion="registrar", confirmar="1", revision_token=token, tipo_cambio="26"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Los datos del ajuste cambiaron")
        self.assertFalse(RegularizacionMonedaFactura.objects.exists())

    def test_regulariza_en_mes_abierto_y_conserva_asiento_cerrado(self):
        originales = list(self.asiento.lineas.order_by("pk").values("pk", "cuenta_id", "debe", "haber", "debe_origen", "haber_origen"))
        PeriodoContable.objects.create(empresa=self.empresa, anio=2026, mes=4, estado="cerrado")
        token = self.revisar()
        response = self.client.post(self.url, self.datos(accion="registrar", confirmar="1", revision_token=token))
        revision = RegularizacionMonedaFactura.objects.get(empresa=self.empresa, asiento_origen=self.asiento)
        self.assertRedirects(response, reverse("ver_asiento_contable", args=[self.empresa.slug, revision.asiento_ajuste_id]))
        self.assertEqual(revision.saldo_hnl_corregido, Decimal("2875.00"))
        self.assertEqual(revision.asiento_ajuste.total_debe, Decimal("2760.00"))
        self.assertEqual(list(self.asiento.lineas.order_by("pk").values("pk", "cuenta_id", "debe", "haber", "debe_origen", "haber_origen")), originales)
        self.asiento.refresh_from_db()
        self.assertFalse(self.asiento.conversion_verificada)
        detalle = self.client.get(reverse("ver_asiento_contable", args=[self.empresa.slug, self.asiento.pk]))
        self.assertContains(detalle, "Conversión histórica revisada")
        self.assertNotContains(detalle, "Regularizar moneda de factura")
        self.client.post(self.url, self.datos(accion="registrar", confirmar="1", revision_token=token))
        self.assertEqual(RegularizacionMonedaFactura.objects.filter(asiento_origen=self.asiento).count(), 1)
