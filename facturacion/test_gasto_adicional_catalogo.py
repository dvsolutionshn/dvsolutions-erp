"""La revisión de facturas conserva solo referencias compartidas originales de GA."""

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from clinica.models import GastoAdicional, LineaGastoAdicional, Paciente
from clinica.services_gastos_adicionales import convertir_gasto_adicional
from contabilidad.models import AsientoContable
from core.models import Empresa, EmpresaModulo, Modulo, Usuario
from facturacion.models import CAI, Cliente, Factura, InventarioProducto, LineaFactura, MovimientoInventario, PagoFactura, Producto, TipoImpuesto
from facturacion.services_clientes_compartidos import suspender_sincronizacion_clientes_compartidos


class FacturaGastoAdicionalCatalogoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(nombre="MIA", slug="hospital_mia", rtn="GA-EDIT-1", tipo_solucion="clinica")
        cls.spa = Empresa.objects.create(nombre="Medical Spa", slug="medical_spa", rtn="GA-EDIT-2", tipo_solucion="clinica")
        cls.ajena = Empresa.objects.create(nombre="Otra clínica", slug="otra_clinica", rtn="GA-EDIT-3", tipo_solucion="clinica")
        for codigo in ("facturacion", "gastos_adicionales"):
            modulo, _ = Modulo.objects.get_or_create(codigo=codigo, defaults={"nombre": codigo})
            EmpresaModulo.objects.get_or_create(empresa=cls.empresa, modulo=modulo)
        cls.usuario = Usuario.objects.create_superuser(username="ga-revision-catalogo", password=None, empresa=cls.empresa)
        with suspender_sincronizacion_clientes_compartidos():
            cls.cliente = Cliente.objects.create(empresa=cls.empresa, nombre="Paciente local", rtn="GA-EDIT-P1")
        cls.paciente = Paciente.objects.get(empresa=cls.empresa, cliente=cls.cliente)
        cls.impuesto = TipoImpuesto.objects.create(nombre="ISV catálogo GA", porcentaje=15)
        producto_base = {"precio": "115.00", "tipo_item": "servicio", "controla_inventario": False, "impuesto_predeterminado": cls.impuesto}
        cls.local = Producto.objects.create(empresa=cls.empresa, nombre="Servicio local", **producto_base)
        cls.compartido = Producto.objects.create(empresa=cls.spa, nombre="Servicio compartido", **producto_base)
        cls.compartido_no_usado = Producto.objects.create(empresa=cls.spa, nombre="Servicio sin selección original", **producto_base)
        cls.producto_ajeno = Producto.objects.create(empresa=cls.ajena, nombre="Servicio de empresa ajena", **producto_base)
        cls.inventariado_compartido = Producto.objects.create(empresa=cls.spa, nombre="Material compartido inventariado", precio="115.00", controla_inventario=True, impuesto_predeterminado=cls.impuesto)
        InventarioProducto.objects.create(empresa=cls.spa, producto=cls.inventariado_compartido, existencias="20.00")
        cls.cai = CAI.objects.create(
            empresa=cls.empresa, numero_cai="GA-EDIT-CAI", establecimiento="001", punto_emision="001", tipo_documento="01",
            rango_inicial=1, rango_final=100, correlativo_actual=0,
            fecha_activacion=timezone.localdate() - timedelta(days=1), fecha_limite=timezone.localdate() + timedelta(days=30),
        )

    def setUp(self):
        self.client.force_login(self.usuario)

    def crear_gasto(self, producto=None):
        gasto = GastoAdicional.objects.create(empresa=self.empresa, paciente=self.paciente, creado_por=self.usuario)
        LineaGastoAdicional.objects.create(gasto=gasto, producto=producto or self.compartido, cantidad=2, precio_unitario="115.00")
        gasto.calcular_totales()
        gasto.save(update_fields=["subtotal", "total"])
        return gasto

    def editar_url(self, factura):
        return reverse("editar_factura", kwargs={"empresa_slug": self.empresa.slug, "factura_id": factura.pk})

    def datos_revision(self, factura, producto=None):
        linea = factura.lineas.get()
        return {
            "cliente": self.cliente.pk, "fecha_emision": factura.fecha_emision.isoformat(),
            "fecha_vencimiento": "", "vendedor": self.usuario.pk, "moneda": "HNL",
            "tipo_cambio": "1.0000", "estado": "borrador", "orden_compra_exenta": "",
            "registro_exonerado": "", "registro_sag": "", "motivo_auditoria": "Revisión clínica del documento",
            "lineas-TOTAL_FORMS": "1", "lineas-INITIAL_FORMS": "1",
            "lineas-MIN_NUM_FORMS": "0", "lineas-MAX_NUM_FORMS": "1000",
            "lineas-0-id": linea.pk, "lineas-0-factura": factura.pk,
            "lineas-0-producto": (producto or linea.producto).pk,
            "lineas-0-descripcion_manual": linea.descripcion_manual or "", "lineas-0-cantidad": "2.00",
            "lineas-0-precio_unitario": "115.00", "lineas-0-descuento_porcentaje": "0",
            "lineas-0-comentario": linea.comentario or "", "lineas-0-impuesto": self.impuesto.pk,
        }

    def test_producto_compartido_original_se_revisa_sin_emision_ni_mutar_gasto(self):
        gasto = self.crear_gasto()
        original = (gasto.numero, gasto.total, gasto.lineas.get().producto_id)
        factura, _ = convertir_gasto_adicional(gasto, self.usuario)
        response = self.client.get(self.editar_url(factura))
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.compartido, response.context["productos"])
        self.assertEqual(response.context["formset"].forms[0].initial["producto"], self.compartido.pk)
        response = self.client.post(self.editar_url(factura), self.datos_revision(factura))
        self.assertEqual(response.status_code, 302)
        factura.refresh_from_db()
        gasto.refresh_from_db()
        self.compartido.refresh_from_db()
        self.assertEqual(factura.estado, "borrador")
        self.assertFalse(factura.numero_factura)
        self.assertFalse(MovimientoInventario.objects.exists())
        self.assertEqual(factura.lineas.get().producto_id, self.compartido.pk)
        self.assertEqual(factura.total, Decimal("230.00"))
        self.assertEqual(self.compartido.empresa_id, self.spa.pk)
        self.assertEqual((gasto.numero, gasto.total, gasto.lineas.get().producto_id), original)

    def test_producto_original_inactivo_se_conserva_sin_ofrecer_otros_productos_externos(self):
        gasto = self.crear_gasto()
        factura, _ = convertir_gasto_adicional(gasto, self.usuario)
        Producto.objects.filter(pk=self.compartido.pk).update(activo=False)
        response = self.client.get(self.editar_url(factura))
        ids = set(response.context["productos"].values_list("pk", flat=True))
        self.assertEqual(ids, {self.local.pk, self.compartido.pk})
        for producto in (self.compartido_no_usado, self.producto_ajeno):
            with self.subTest(producto=producto.pk):
                response = self.client.post(self.editar_url(factura), self.datos_revision(factura, producto))
                self.assertEqual(response.status_code, 200)
                self.assertIn("producto", response.context["formset"].forms[0].errors)
                self.assertEqual(factura.lineas.get().producto_id, self.compartido.pk)
        response = self.client.post(self.editar_url(factura), self.datos_revision(factura))
        self.assertEqual(response.status_code, 302)

    def test_factura_normal_conserva_catalogo_local(self):
        factura = Factura.objects.create(empresa=self.empresa, cliente=self.cliente, vendedor=self.usuario, fecha_emision=timezone.localdate())
        LineaFactura.objects.create(factura=factura, producto=self.local, cantidad=2, precio_unitario="115.00", impuesto=self.impuesto)
        response = self.client.get(self.editar_url(factura))
        self.assertEqual(set(response.context["productos"].values_list("pk", flat=True)), {self.local.pk})
        response = self.client.post(self.editar_url(factura), self.datos_revision(factura, self.compartido))
        self.assertEqual(response.status_code, 200)
        self.assertIn("producto", response.context["formset"].forms[0].errors)
        self.assertEqual(factura.lineas.get().producto_id, self.local.pk)

    def test_referencia_original_fuera_whitelist_no_amplia_revision(self):
        gasto = self.crear_gasto(self.local)
        factura, _ = convertir_gasto_adicional(gasto, self.usuario)
        # Simula datos heredados incoherentes sin eludir los filtros HTTP.
        LineaGastoAdicional.objects.filter(gasto=gasto).update(producto=self.producto_ajeno)
        LineaFactura.objects.filter(factura=factura).update(producto=self.producto_ajeno)
        response = self.client.get(self.editar_url(factura))
        self.assertNotIn(self.producto_ajeno.pk, response.context["productos"].values_list("pk", flat=True))
        response = self.client.post(self.editar_url(factura), self.datos_revision(factura))
        self.assertEqual(response.status_code, 200)
        self.assertIn("producto", response.context["formset"].forms[0].errors)

    def test_servicio_compartido_se_emite_por_motor_actual(self):
        gasto = self.crear_gasto()
        factura, _ = convertir_gasto_adicional(gasto, self.usuario)
        datos = self.datos_revision(factura)
        datos["estado"] = "emitida"
        response = self.client.post(self.editar_url(factura), datos)
        self.assertEqual(response.status_code, 302)
        factura.refresh_from_db()
        gasto.refresh_from_db()
        self.cai.refresh_from_db()
        self.assertEqual(factura.estado, "emitida")
        self.assertEqual(gasto.estado, "facturado")
        self.assertEqual(self.cai.correlativo_actual, 1)
        self.assertTrue(factura.numero_factura)
        self.assertEqual(factura.lineas.get().producto_id, self.compartido.pk)
        self.assertEqual(factura.impuesto, Decimal("30.00"))
        self.assertFalse(MovimientoInventario.objects.exists())
        self.assertEqual(InventarioProducto.objects.get(producto=self.inventariado_compartido).existencias, Decimal("20.00"))

    def test_producto_inventariado_externo_guarda_borrador_y_bloquea_emision_antes_de_efectos(self):
        gasto = self.crear_gasto(self.inventariado_compartido)
        factura, _ = convertir_gasto_adicional(gasto, self.usuario)
        response = self.client.post(self.editar_url(factura), self.datos_revision(factura))
        self.assertEqual(response.status_code, 302)
        datos = self.datos_revision(factura)
        datos["estado"] = "emitida"
        response = self.client.post(self.editar_url(factura), datos)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "controla inventario de otra empresa")
        validar_url = reverse("validar_factura", kwargs={"empresa_slug": self.empresa.slug, "factura_id": factura.pk})
        response = self.client.post(validar_url)
        self.assertEqual(response.status_code, 302)
        factura.refresh_from_db()
        gasto.refresh_from_db()
        self.cai.refresh_from_db()
        self.assertEqual(factura.estado, "borrador")
        self.assertFalse(factura.numero_factura)
        self.assertIsNone(factura.cai_id)
        self.assertEqual(self.cai.correlativo_actual, 0)
        self.assertEqual(gasto.estado, "pendiente")
        self.assertEqual(InventarioProducto.objects.get(producto=self.inventariado_compartido).existencias, Decimal("20.00"))
        self.assertFalse(MovimientoInventario.objects.exists())
        self.assertFalse(PagoFactura.objects.exists())
        self.assertFalse(AsientoContable.objects.exists())

    def test_duplicar_ga_inventariado_externo_no_elude_aislamiento(self):
        gasto = self.crear_gasto(self.inventariado_compartido)
        factura, _ = convertir_gasto_adicional(gasto, self.usuario)
        duplicar_url = reverse("duplicar_factura", kwargs={"empresa_slug": self.empresa.slug, "factura_id": factura.pk})
        response = self.client.post(duplicar_url, follow=True)
        self.assertContains(response, "controla inventario de otra empresa")
        self.assertEqual(Factura.objects.count(), 1)
        self.cai.refresh_from_db()
        factura.refresh_from_db()
        self.assertEqual(self.cai.correlativo_actual, 0)
        self.assertEqual(factura.estado, "borrador")
        self.assertFalse(factura.numero_factura)
        self.assertEqual(InventarioProducto.objects.get(producto=self.inventariado_compartido).existencias, Decimal("20.00"))
        self.assertFalse(MovimientoInventario.objects.exists())
        self.assertFalse(PagoFactura.objects.exists())
        self.assertFalse(AsientoContable.objects.exists())

    def test_formset_que_omite_linea_externa_no_elude_guardia(self):
        gasto = self.crear_gasto(self.inventariado_compartido)
        factura, _ = convertir_gasto_adicional(gasto, self.usuario)
        datos = self.datos_revision(factura)
        datos.update({"estado": "emitida", "lineas-TOTAL_FORMS": "0", "lineas-INITIAL_FORMS": "0"})
        response = self.client.post(self.editar_url(factura), datos)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "controla inventario de otra empresa")
        factura.refresh_from_db()
        self.cai.refresh_from_db()
        self.assertEqual(factura.estado, "borrador")
        self.assertEqual(self.cai.correlativo_actual, 0)
        self.assertFalse(factura.numero_factura)
        self.assertEqual(InventarioProducto.objects.get(producto=self.inventariado_compartido).existencias, Decimal("20.00"))
        self.assertFalse(MovimientoInventario.objects.exists())
        self.assertFalse(AsientoContable.objects.exists())

    def test_reemplazar_linea_inventariada_externa_por_servicio_local_permite_emitir(self):
        gasto = self.crear_gasto(self.inventariado_compartido)
        factura, _ = convertir_gasto_adicional(gasto, self.usuario)
        datos = self.datos_revision(factura, self.local)
        datos["estado"] = "emitida"
        response = self.client.post(self.editar_url(factura), datos)
        self.assertEqual(response.status_code, 302)
        factura.refresh_from_db()
        self.assertEqual(factura.estado, "emitida")
        self.assertEqual(factura.lineas.get().producto_id, self.local.pk)
        self.assertEqual(InventarioProducto.objects.get(producto=self.inventariado_compartido).existencias, Decimal("20.00"))
        self.assertFalse(MovimientoInventario.objects.exists())
        self.assertEqual(gasto.lineas.get().producto_id, self.inventariado_compartido.pk)

    def test_eliminar_linea_inventariada_externa_conservando_local_permite_emitir(self):
        gasto = self.crear_gasto(self.inventariado_compartido)
        factura, _ = convertir_gasto_adicional(gasto, self.usuario)
        datos = self.datos_revision(factura)
        linea_local = LineaFactura.objects.create(factura=factura, producto=self.local, cantidad=2, precio_unitario="115.00", impuesto=self.impuesto)
        datos.update({"estado": "emitida", "lineas-TOTAL_FORMS": "2", "lineas-INITIAL_FORMS": "2", "lineas-0-DELETE": "on"})
        datos.update({key.replace("lineas-0-", "lineas-1-"): value for key, value in list(datos.items()) if key.startswith("lineas-0-") and key != "lineas-0-DELETE"})
        datos.update({"lineas-1-id": linea_local.pk, "lineas-1-producto": self.local.pk})
        response = self.client.post(self.editar_url(factura), datos)
        self.assertEqual(response.status_code, 302)
        factura.refresh_from_db()
        self.assertEqual(factura.estado, "emitida")
        self.assertEqual(factura.lineas.get().producto_id, self.local.pk)
        self.assertEqual(InventarioProducto.objects.get(producto=self.inventariado_compartido).existencias, Decimal("20.00"))
        self.assertFalse(MovimientoInventario.objects.exists())
