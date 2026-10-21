"""Los cobros de RUMI usan su catálogo sin recrear la plantilla Honduras."""

from datetime import date
from decimal import Decimal
import json
from unittest.mock import patch

from django.contrib.messages import get_messages
from django.contrib.messages.storage.fallback import FallbackStorage
from django.http import HttpResponse
from django.test import RequestFactory, TestCase
from django.urls import reverse

from core.models import ConfiguracionAvanzadaEmpresa, Empresa, Usuario
from facturacion import mobile_payments, views
from facturacion.models import (
    CAI, Cliente, ConfiguracionFacturacionEmpresa, Factura, LineaFactura,
    MovimientoInventario, PagoFactura, Producto, ReciboPago, TipoImpuesto,
)
from .models import (
    AsientoContable, ClasificacionMovimientoBanco, ConfiguracionContableEmpresa,
    CuentaContable, CuentaFinanciera, LineaAsientoContable,
)


class PagosCatalogoRumiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.rumi = Empresa.objects.create(nombre="RUMI", slug="rumi", rtn="08011999188101")
        cls.otra = Empresa.objects.create(nombre="Otra empresa", slug="rumi-otra", rtn="08011999188102")
        ConfiguracionContableEmpresa.objects.create(empresa=cls.rumi, catalogo_rumi_instalado=True)
        ConfiguracionAvanzadaEmpresa.objects.create(empresa=cls.rumi)
        ConfiguracionFacturacionEmpresa.objects.create(empresa=cls.rumi)
        cls.usuario = Usuario.objects.create_user(
            username="pruebas-pagos-rumi", password=None, empresa=cls.rumi,
            is_superuser=True, is_staff=True,
        )
        cls.cliente = Cliente.objects.create(empresa=cls.rumi, nombre="Cliente de pruebas RUMI")
        cls.impuesto = TipoImpuesto.objects.create(nombre="Exento para pruebas RUMI", porcentaje=0)
        cls.cai = CAI.objects.create(
            empresa=cls.rumi, numero_cai="CAI-QA-PAGOS-RUMI", establecimiento="001",
            punto_emision="001", tipo_documento="01", rango_inicial=1,
            rango_final=1000, correlativo_actual=0,
            fecha_activacion=date(2020, 1, 1), fecha_limite=date(2030, 12, 31),
        )
        cls.factura = Factura.objects.create(
            empresa=cls.rumi, cliente=cls.cliente, estado="borrador",
            fecha_emision=date(2026, 10, 14), moneda="HNL", tipo_cambio=Decimal("1"),
        )
        LineaFactura.objects.create(
            factura=cls.factura, descripcion_manual="Servicio de pruebas",
            cantidad=Decimal("1"), precio_unitario=Decimal("100"), impuesto=cls.impuesto,
        )
        cls.factura.calcular_totales()
        cls.factura.estado = "emitida"
        cls.factura.save()
        cls.producto = Producto.objects.create(
            empresa=cls.rumi, nombre="Servicio de pruebas POS", codigo="QA-RUMI-POS",
            precio=Decimal("100"), controla_inventario=False,
            impuesto_predeterminado=cls.impuesto,
        )

    def cuenta(self, codigo, empresa=None, **kwargs):
        return CuentaContable.objects.create(
            empresa=empresa or self.rumi, codigo=codigo, nombre=f"Disponibilidad {codigo}",
            tipo="activo", **kwargs,
        )

    def financiera(self, nombre, cuenta, **kwargs):
        return CuentaFinanciera.objects.create(
            empresa=self.rumi, nombre=nombre, tipo="banco", cuenta_contable=cuenta, **kwargs,
        )

    def request(self, method="get", route=None, data=None, **extra):
        route = route or reverse("registrar_pago", args=[self.rumi.slug, self.factura.pk])
        request = getattr(RequestFactory(), method)(route, data or {}, **extra)
        request.user = self.usuario
        request.session = {}
        request._messages = FallbackStorage(request)
        return request

    def datos_cobro(self):
        return {
            "fecha": "2026-10-14", "monto": "10.00", "metodo": "efectivo",
            "cuenta_financiera": "", "referencia": "QA-RUMI",
            "retencion_isr": "0", "retencion_isv": "0",
            "porcentaje_comision": "0", "monto_comision": "0",
            "proveedor_comision": "", "cuenta_financiera_impuesto": "",
        }

    def snapshot(self):
        modelos = (
            CuentaContable, CuentaFinanciera, ClasificacionMovimientoBanco,
            AsientoContable, LineaAsientoContable, Cliente, Factura, PagoFactura,
            ReciboPago, Producto, MovimientoInventario, CAI,
        )
        return {modelo._meta.label_lower: list(modelo.objects.order_by("pk").values()) for modelo in modelos}

    def test_rumi_sin_financieras_no_carga_ni_recrea_catalogo_honduras(self):
        antes = self.snapshot()
        with patch.object(views, "asegurar_cuentas_financieras_base_honduras") as asegurar, \
                patch.object(views, "cargar_catalogo_base_honduras") as cargar:
            self.assertEqual(list(views._cuentas_financieras_activas_para_pago(self.rumi)), [])
        asegurar.assert_not_called()
        cargar.assert_not_called()
        self.assertEqual(self.snapshot(), antes)

    def test_otra_empresa_conserva_autocarga_y_rumi_permanece_aislada(self):
        antes_rumi = list(CuentaContable.objects.filter(empresa=self.rumi).values())
        financieras = list(views._cuentas_financieras_activas_para_pago(self.otra))

        self.assertTrue(financieras)
        self.assertTrue(all(cuenta.empresa_id == self.otra.pk for cuenta in financieras))
        self.assertTrue(CuentaContable.objects.filter(empresa=self.otra, codigo="110201").exists())
        self.assertEqual(list(CuentaContable.objects.filter(empresa=self.rumi).values()), antes_rumi)
        self.assertFalse(CuentaFinanciera.objects.filter(empresa=self.rumi).exists())

    def test_rumi_filtra_financieras_inactivas_y_cuentas_incompatibles(self):
        valida = self.financiera("Vigente", self.cuenta("101001"))
        self.financiera("Financiera inactiva", self.cuenta("101002"), activa=False)
        self.financiera("Cuenta retirada", self.cuenta("HIST-HN-001", activa=False))
        self.financiera("Cuenta agrupadora", self.cuenta("101003", acepta_movimientos=False))
        extranjera = self.financiera("Enlace histórico incompatible", self.cuenta("101004"))
        cuenta_otra = self.cuenta("101001", empresa=self.otra)
        # Representa un enlace legado incorrecto; no es una operación de usuario.
        CuentaFinanciera.objects.filter(pk=extranjera.pk).update(cuenta_contable=cuenta_otra)
        antes = self.snapshot()

        self.assertEqual(list(views._cuentas_financieras_activas_para_pago(self.rumi)), [valida])
        self.assertEqual(self.snapshot(), antes)

    def test_aviso_en_espanol_exclusivo_de_rumi_sin_financieras(self):
        request = self.request()
        activo = views._avisar_configuracion_cuentas_financieras_rumi(
            request, self.rumi, CuentaFinanciera.objects.none(),
        )
        self.assertTrue(activo)
        mensajes = [str(mensaje) for mensaje in get_messages(request)]
        self.assertEqual(len(mensajes), 1)
        self.assertIn("RUMI no tiene una caja", mensajes[0])
        self.assertIn("Configure las cuentas financieras en Contabilidad", mensajes[0])

        request_otra = self.request()
        self.assertFalse(views._avisar_configuracion_cuentas_financieras_rumi(
            request_otra, self.otra, CuentaFinanciera.objects.none(),
        ))
        self.assertEqual(list(get_messages(request_otra)), [])

    def test_cobro_get_y_post_sin_financieras_conservan_documentos_y_saldos(self):
        antes = self.snapshot()
        for metodo in ("get", "post"):
            with self.subTest(metodo=metodo):
                request = self.request(metodo, data=self.datos_cobro() if metodo == "post" else None)
                with patch.object(views, "render", return_value=HttpResponse("Formulario")) as presentar, \
                        patch.object(views, "asegurar_cuentas_financieras_base_honduras") as asegurar, \
                        patch.object(views, "cargar_catalogo_base_honduras") as cargar:
                    response = views.registrar_pago(request, self.rumi.slug, self.factura.pk)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(list(presentar.call_args.args[2]["cuentas_financieras"]), [])
                mensajes = [str(mensaje) for mensaje in get_messages(request)]
                self.assertTrue(any("RUMI" in mensaje and "cuenta" in mensaje for mensaje in mensajes))
                if metodo == "post":
                    self.assertTrue(any("antes de registrar este cobro" in mensaje for mensaje in mensajes))
                asegurar.assert_not_called()
                cargar.assert_not_called()
                self.assertEqual(self.snapshot(), antes)

    def test_pos_sin_financieras_avisa_y_rechaza_antes_de_registrar_venta(self):
        antes = self.snapshot()
        route = reverse("punto_venta", args=[self.rumi.slug])
        request_get = self.request(route=route)
        with patch.object(Empresa, "tiene_modulo_activo", return_value=True), \
                patch.object(views, "render", return_value=HttpResponse("Punto de venta")):
            response_get = views.punto_venta(request_get, self.rumi.slug)
        self.assertEqual(response_get.status_code, 200)
        self.assertTrue(any("RUMI no tiene una caja" in str(m) for m in get_messages(request_get)))
        self.assertEqual(self.snapshot(), antes)

        payload = {
            "items": [{"producto_id": self.producto.pk, "cantidad": "1", "precio_unitario": "100"}],
            "metodo": "efectivo", "cuenta_financiera": "", "monto_recibido": "100",
            "cliente_id": self.cliente.pk, "fecha": "2026-10-14",
        }
        request_post = self.request(
            "post", route=route, data={"payload": json.dumps(payload)},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        with patch.object(Empresa, "tiene_modulo_activo", return_value=True), \
                patch.object(views, "cargar_catalogo_base_honduras") as cargar:
            response_post = views.punto_venta(request_post, self.rumi.slug)
        self.assertEqual(response_post.status_code, 400)
        self.assertIn("Configure una caja o cuenta financiera de RUMI", json.loads(response_post.content)["error"])
        cargar.assert_not_called()
        self.assertEqual(self.snapshot(), antes)

    def test_editar_cobro_sin_financieras_preserva_el_pago_existente(self):
        cuenta = self.cuenta("101001")
        financiera = self.financiera("Banco de pruebas", cuenta)
        pago = PagoFactura.objects.create(
            factura=self.factura, monto=Decimal("10"), fecha=date(2026, 10, 14),
            metodo="efectivo", cuenta_financiera=financiera,
        )
        financiera.activa = False
        financiera.save(update_fields=["activa"])
        antes = self.snapshot()
        route = reverse("editar_pago_factura", args=[self.rumi.slug, self.factura.pk, pago.pk])

        for metodo in ("get", "post"):
            with self.subTest(metodo=metodo):
                request = self.request(metodo, route=route, data=self.datos_cobro() if metodo == "post" else None)
                with patch.object(views, "render", return_value=HttpResponse("Editar cobro")) as presentar, \
                        patch.object(views, "cargar_catalogo_base_honduras") as cargar:
                    response = views.editar_pago_factura(request, self.rumi.slug, self.factura.pk, pago.pk)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(list(presentar.call_args.args[2]["cuentas_financieras"]), [])
                if metodo == "post":
                    self.assertTrue(any("antes de actualizar este cobro" in str(m) for m in get_messages(request)))
                cargar.assert_not_called()
                self.assertEqual(self.snapshot(), antes)

    def test_rama_movil_con_catalogo_rumi_no_regenera_cuentas_ni_guarda_cobro_sin_financiera(self):
        # RUMI no se incorpora aquí a las empresas clínicas habilitadas para app.
        # El selector simulado ejercita la protección contable de esa rama sin
        # modificar su autorización ni la habilitación móvil del producto.
        antes = self.snapshot()
        for metodo in ("get", "post"):
            with self.subTest(metodo=metodo):
                request = self.request(metodo, data=self.datos_cobro() if metodo == "post" else None)
                with patch.object(mobile_payments, "es_cobros_app", return_value=True), \
                        patch.object(mobile_payments, "autorizar_cobros_app"), \
                        patch.object(mobile_payments, "render_cobro_app", return_value=HttpResponse("Cobro móvil")) as presentar, \
                        patch.object(views, "asegurar_cuentas_financieras_base_honduras") as asegurar, \
                        patch.object(views, "cargar_catalogo_base_honduras") as cargar:
                    response = views.registrar_pago(request, self.rumi.slug, self.factura.pk)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(list(presentar.call_args.args[2]["cuentas_financieras"]), [])
                if metodo == "post":
                    self.assertTrue(any("antes de registrar este cobro" in str(m) for m in get_messages(request)))
                asegurar.assert_not_called()
                cargar.assert_not_called()
                self.assertEqual(self.snapshot(), antes)

    def test_crear_contacto_rumi_difiere_auxiliar_y_otras_empresas_lo_conservan(self):
        for empresa in (self.rumi, self.otra):
            with self.subTest(empresa=empresa.slug):
                nombre = f"Contacto nuevo {empresa.slug}"
                route = reverse("crear_cliente_facturacion", args=[empresa.slug])
                request = self.request("post", route=route, data={"nombre": nombre, "activo": "on"})
                with patch.object(
                    views, "asegurar_cuenta_contable_cliente", wraps=views.asegurar_cuenta_contable_cliente,
                ) as asegurar:
                    response = views.crear_cliente(request, empresa.slug)
                self.assertEqual(response.status_code, 302)
                contacto = Cliente.objects.get(empresa=empresa, nombre=nombre)
                if empresa.pk == self.rumi.pk:
                    asegurar.assert_not_called()
                    self.assertIsNone(contacto.cuenta_contable_id)
                    self.assertFalse(CuentaContable.objects.filter(empresa=self.rumi).exists())
                else:
                    asegurar.assert_called_once()
                    self.assertIsNotNone(contacto.cuenta_contable_id)
                    self.assertEqual(contacto.cuenta_contable.empresa_id, self.otra.pk)
        self.assertFalse(AsientoContable.objects.exists())

    def test_editar_contacto_rumi_conserva_cuenta_existente_sin_exigir_rol_clientes(self):
        cuenta = self.cuenta("QA-AUXILIAR-CLIENTE")
        self.cliente.cuenta_contable = cuenta
        self.cliente.save(update_fields=["cuenta_contable"])
        cuentas_antes = list(CuentaContable.objects.order_by("pk").values())
        route = reverse("editar_cliente_facturacion", args=[self.rumi.slug, self.cliente.pk])
        request = self.request(
            "post", route=route, data={"nombre": "Contacto RUMI actualizado", "activo": "on"},
        )
        with patch.object(views, "asegurar_cuenta_contable_cliente") as asegurar:
            response = views.editar_cliente(request, self.rumi.slug, self.cliente.pk)
        self.assertEqual(response.status_code, 302)
        self.cliente.refresh_from_db()
        self.assertEqual(self.cliente.nombre, "Contacto RUMI actualizado")
        self.assertEqual(self.cliente.cuenta_contable_id, cuenta.pk)
        asegurar.assert_not_called()
        self.assertEqual(list(CuentaContable.objects.order_by("pk").values()), cuentas_antes)
        self.assertFalse(AsientoContable.objects.exists())
