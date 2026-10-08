"""Desglose informativo de GA con los importes fiscales existentes."""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.db import connection
from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from contabilidad.models import AsientoContable
from core.models import Empresa, EmpresaModulo, Modulo, Usuario
from facturacion.models import CAI, Cliente, Factura, InventarioProducto, LineaFactura, MovimientoInventario, PagoFactura, Producto, TipoImpuesto
from facturacion.services_clientes_compartidos import suspender_sincronizacion_clientes_compartidos

from .importes_gastos_adicionales import desglose_gasto_adicional
from .models import GastoAdicional, LineaGastoAdicional, Paciente


class LineaFacturaCalcularImportesTests(SimpleTestCase):
    def test_calculo_en_memoria_conserva_formula_descuentos_y_no_persiste(self):
        impuesto = TipoImpuesto(nombre="ISV 15", porcentaje=Decimal("15.00"))
        for incluido, precio in ((True, "115.00"), (False, "100.00")):
            with self.subTest(incluido=incluido):
                linea = LineaFactura(
                    cantidad=Decimal("1.00"), precio_unitario=Decimal(precio),
                    precio_incluye_impuesto=incluido, descuento_porcentaje=Decimal("10.00"),
                    impuesto=impuesto,
                )
                linea.calcular_importes()
                self.assertEqual(linea.subtotal, Decimal("90.00"))
                self.assertEqual(linea.impuesto_monto, Decimal("13.50"))
                self.assertEqual(linea.descuento_monto, Decimal("10.00"))
                self.assertEqual(linea.total_linea, Decimal("103.50"))
                self.assertIsNone(linea.pk)
                self.assertTrue(linea._state.adding)


class GastoAdicionalImportesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa_inclusiva = Empresa.objects.create(nombre="Hospital MIA", slug="hospital_mia", rtn="GA-IMP-1")
        cls.empresa_exclusiva = Empresa.objects.create(nombre="Clínica prueba precios netos", slug="ga_importes_neto", rtn="GA-IMP-2")
        cls.empresas = {True: cls.empresa_inclusiva, False: cls.empresa_exclusiva}
        cls.pacientes = {}
        cls.clientes = {}
        for incluido, empresa in cls.empresas.items():
            cls.pacientes[incluido] = Paciente.objects.create(empresa=empresa, nombre="Paciente de importes", identidad=f"GA-IMP-P{empresa.pk}", expediente_codigo="GA-IMP-001")
            with suspender_sincronizacion_clientes_compartidos():
                cls.clientes[incluido] = Cliente.objects.create(empresa=empresa, nombre="Cliente para comparación", rtn=f"GA-IMP-C{empresa.pk}")
            CAI.objects.create(
                empresa=empresa, numero_cai=f"GA-IMP-CAI-{empresa.pk}", establecimiento="001", punto_emision="001", tipo_documento="01",
                rango_inicial=1, rango_final=100, correlativo_actual=0,
                fecha_activacion=timezone.localdate() - timedelta(days=1), fecha_limite=timezone.localdate() + timedelta(days=30),
            )
        cls.impuestos = {
            tasa: TipoImpuesto.objects.create(nombre=f"ISV {tasa}" if tasa != "0" else "Exento", porcentaje=Decimal(tasa))
            for tasa in ("15", "18", "0")
        }

    def gasto(self, incluido=True, casos=(("15", "1.00", "115.00"),)):
        empresa = self.empresas[incluido]
        gasto = GastoAdicional.objects.create(empresa=empresa, paciente=self.pacientes[incluido])
        for indice, (tasa, cantidad, precio) in enumerate(casos):
            producto = Producto.objects.create(
                empresa=empresa, nombre=f"Servicio fiscal {gasto.numero} {indice}", precio=Decimal(precio),
                tipo_item="servicio", controla_inventario=False,
                impuesto_predeterminado=self.impuestos[tasa] if tasa is not None else None,
            )
            LineaGastoAdicional.objects.create(gasto=gasto, producto=producto, cantidad=Decimal(cantidad), precio_unitario=Decimal(precio))
        gasto.calcular_totales()
        gasto.save(update_fields=["subtotal", "total"])
        self.assertEqual(gasto.precio_incluye_impuesto, incluido)
        return gasto

    def test_compara_factura_guardada_con_tasas_15_18_0_y_ambos_tipos_de_precio(self):
        casos = (
            (True, "15", "1.00", "115.00", "100.00", "15.00", "115.00"),
            (False, "15", "1.00", "100.00", "100.00", "15.00", "115.00"),
            (True, "18", "1.00", "118.00", "100.00", "18.00", "118.00"),
            (False, "18", "1.00", "100.00", "100.00", "18.00", "118.00"),
            (True, "0", "1.23", "77.77", "95.66", "0.00", "95.66"),
            (False, "0", "1.23", "77.77", "95.66", "0.00", "95.66"),
            (True, "15", "3.50", "0.01", "0.03", "0.01", "0.04"),
            (False, "15", "1.50", "0.03", "0.04", "0.01", "0.05"),
            (True, "18", "0.59", "0.01", "0.01", "0.00", "0.01"),
            (False, "18", "0.10", "0.05", "0.00", "0.00", "0.00"),
        )
        for incluido, tasa, cantidad, precio, neto, isv, total in casos:
            with self.subTest(incluido=incluido, tasa=tasa, cantidad=cantidad, precio=precio):
                gasto = self.gasto(incluido, ((tasa, cantidad, precio),))
                desglose = desglose_gasto_adicional(gasto)
                original = gasto.lineas.get()
                factura = Factura.objects.create(empresa=gasto.empresa, cliente=self.clientes[incluido], fecha_emision=gasto.fecha)
                fiscal = LineaFactura.objects.create(
                    factura=factura, producto=original.producto, cantidad=original.cantidad,
                    precio_unitario=original.precio_unitario, precio_incluye_impuesto=incluido,
                    impuesto=original.producto.impuesto_predeterminado,
                )
                factura.calcular_totales()
                self.assertEqual(desglose["subtotal"], Decimal(neto))
                self.assertEqual(desglose["impuestos"], Decimal(isv))
                self.assertEqual(desglose["total"], Decimal(total))
                self.assertEqual(desglose["subtotal"], factura.subtotal)
                self.assertEqual(desglose["impuestos"], factura.impuesto)
                self.assertEqual(desglose["total"], factura.total)
                self.assertEqual(desglose["lineas"][0]["subtotal"], fiscal.subtotal)
                self.assertEqual(desglose["lineas"][0]["impuesto_monto"], fiscal.impuesto_monto)
                self.assertEqual(desglose["lineas"][0]["total_linea"], fiscal.total_linea)
                self.assertTrue(desglose["impuestos_configurados"])
                self.assertEqual(desglose["lineas"][0]["impuesto_tasa"], self.impuestos[tasa].porcentaje)

    def test_tasas_mixtas_desglosan_sin_sumar_isv_dos_veces(self):
        gasto = self.gasto(casos=(("15", "1.00", "115.00"), ("18", "1.00", "118.00"), ("0", "1.00", "50.00")))
        desglose = desglose_gasto_adicional(gasto)
        self.assertEqual(desglose["subtotal"], Decimal("250.00"))
        self.assertEqual(desglose["impuestos"], Decimal("33.00"))
        self.assertEqual(desglose["total"], Decimal("283.00"))
        self.assertEqual(desglose["total_historico"], Decimal("283.00"))
        self.assertTrue(desglose["total_coincide"])
        self.assertEqual(desglose["nota_impuestos"], "")
        self.assertEqual(desglose["lineas"][2]["impuesto_nombre"], "Exento")
        self.assertEqual(desglose["lineas"][2]["impuesto_monto"], Decimal("0.00"))

    def test_sin_impuesto_no_inventa_exento_ni_total_fiscal(self):
        gasto = self.gasto(casos=(("15", "1.00", "115.00"), (None, "2.00", "20.00")))
        desglose = desglose_gasto_adicional(gasto)
        sin_impuesto = desglose["lineas"][1]
        self.assertFalse(sin_impuesto["impuesto_configurado"])
        self.assertIsNone(sin_impuesto["impuesto_tasa"])
        self.assertIsNone(sin_impuesto["impuesto_nombre"])
        self.assertIsNone(sin_impuesto["impuesto_monto"])
        self.assertEqual(sin_impuesto["subtotal"], Decimal("40.00"))
        self.assertEqual(sin_impuesto["total_linea"], Decimal("40.00"))
        self.assertFalse(desglose["impuestos_configurados"])
        self.assertFalse(desglose["total_coincide"])
        for clave in ("subtotal", "impuestos", "total"):
            self.assertIsNone(desglose[clave])
        self.assertEqual(desglose["total_historico"], Decimal("155.00"))
        self.assertIn("sin impuesto configurado", desglose["nota_impuestos"])

    def test_discrepancia_precio_neto_conserva_importe_historico(self):
        gasto = self.gasto(False, (("18", "1.00", "100.00"),))
        original = tuple(GastoAdicional.objects.filter(pk=gasto.pk).values_list("subtotal", "total", "fecha_actualizacion"))[0]
        desglose = desglose_gasto_adicional(gasto)
        self.assertEqual(desglose["total"], Decimal("118.00"))
        self.assertEqual(desglose["total_historico"], Decimal("100.00"))
        self.assertFalse(desglose["total_coincide"])
        self.assertIn("importe original", desglose["nota_impuestos"])
        self.assertIn("configuración actual", desglose["nota_impuestos"])
        self.assertEqual(tuple(GastoAdicional.objects.filter(pk=gasto.pk).values_list("subtotal", "total", "fecha_actualizacion"))[0], original)

    def test_impuesto_inactivo_muestra_pendiente_igual_que_conversion(self):
        gasto = self.gasto()
        impuesto = self.impuestos["15"]
        impuesto.activo = False
        impuesto.save(update_fields=["activo"])
        desglose = desglose_gasto_adicional(gasto)
        detalle = desglose["lineas"][0]
        self.assertFalse(detalle["impuesto_configurado"])
        self.assertIsNone(detalle["impuesto_monto"])
        self.assertIsNone(detalle["impuesto_tasa"])
        self.assertIsNone(detalle["impuesto_nombre"])
        self.assertFalse(desglose["impuestos_configurados"])
        self.assertIsNone(desglose["total"])
        self.assertEqual(desglose["total_historico"], Decimal("115.00"))
        self.assertIn("impuesto inactivo", desglose["nota_impuestos"])

    def test_desglose_solo_consulta_y_no_crea_efectos_fiscales(self):
        gasto = self.gasto()
        original = tuple(gasto.lineas.values_list("pk", "producto_id", "cantidad", "precio_unitario", "subtotal"))
        with patch.object(LineaFactura, "save", side_effect=AssertionError("El desglose no debe guardar líneas fiscales")):
            with CaptureQueriesContext(connection) as consultas:
                desglose = desglose_gasto_adicional(gasto)
        self.assertEqual(len(consultas), 1)
        self.assertTrue(consultas[0]["sql"].lstrip().upper().startswith("SELECT"))
        self.assertEqual(desglose["total"], gasto.total)
        self.assertEqual(tuple(gasto.lineas.values_list("pk", "producto_id", "cantidad", "precio_unitario", "subtotal")), original)
        self.assertFalse(Factura.objects.exists())
        self.assertFalse(LineaFactura.objects.exists())
        self.assertFalse(PagoFactura.objects.exists())
        self.assertFalse(MovimientoInventario.objects.exists())
        self.assertFalse(InventarioProducto.objects.exists())
        self.assertFalse(AsientoContable.objects.exists())
        self.assertFalse(CAI.objects.exclude(correlativo_actual=0).exists())

    def test_contexto_pdf_y_detalle_muestran_isv_y_pendientes(self):
        from .views_gastos_adicionales import contexto_pdf

        modulo, _ = Modulo.objects.get_or_create(codigo="gastos_adicionales", defaults={"nombre": "Gastos Adicionales"})
        EmpresaModulo.objects.get_or_create(empresa=self.empresa_inclusiva, modulo=modulo)
        usuario = Usuario.objects.create_superuser(username="ga-importes-render", password=None, empresa=self.empresa_inclusiva)
        self.client.force_login(usuario)
        for configurado in (True, False):
            with self.subTest(configurado=configurado):
                gasto = self.gasto(casos=(("15" if configurado else None, "1.00", "115.00"),))
                respuesta = self.client.get(reverse("clinica_gasto_adicional_detalle", kwargs={"empresa_slug": gasto.empresa.slug, "gasto_id": gasto.pk}))
                self.assertEqual(respuesta.status_code, 200)
                contexto = contexto_pdf(gasto.empresa, gasto)
                pdf_html = render_to_string("clinica/gastos_adicionales_pdf.html", contexto)
                self.assertEqual(respuesta.context["importes"]["impuestos"], contexto["importes"]["impuestos"])
                self.assertEqual(respuesta.context["importes"]["total_historico"], Decimal("115.00"))
                for texto in ("Subtotal sin ISV", "ISV", "Total del documento", "L 115.00"):
                    self.assertContains(respuesta, texto)
                    self.assertIn(texto, pdf_html)
                if configurado:
                    for texto in ("L 100.00", "L 15.00"):
                        self.assertContains(respuesta, texto)
                        self.assertIn(texto, pdf_html)
                else:
                    self.assertContains(respuesta, "Pendiente")
                    self.assertIn("Pendiente", pdf_html)
                    self.assertNotIn("Exento", pdf_html)
                    self.assertIsNone(contexto["importes"]["impuestos"])
        self.assertFalse(Factura.objects.exists())
        self.assertFalse(MovimientoInventario.objects.exists())
        self.assertFalse(CAI.objects.exclude(correlativo_actual=0).exists())
