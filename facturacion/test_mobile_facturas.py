from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import (
    ConfiguracionAvanzadaEmpresa,
    Empresa,
    EmpresaModulo,
    Modulo,
    RegistroAuditoria,
    RolSistema,
)
from .models import (
    BodegaInventario,
    CAI,
    Cliente,
    CorreccionNumeroFactura,
    ExistenciaLoteBodega,
    Factura,
    InventarioProducto,
    LineaFactura,
    LoteInventario,
    MovimientoInventario,
    MovimientoLoteBodega,
    NotaCredito,
    PagoFactura,
    Producto,
    TipoImpuesto,
)
from .views import _registrar_salida_factura
from . import views


class MobileInvoiceIntegrationTests(TestCase):
    """The app must use the same invoice mutations and tenant boundaries as web."""

    def setUp(self):
        self.today = timezone.localdate()
        self.empresa = Empresa.objects.create(
            nombre="Hospital Mía",
            slug="hospital_mia",
            rtn="08011999111113",
            estado_licencia="activa",
            tipo_solucion="clinica",
        )
        for codigo in ("facturacion", "agenda_citas"):
            modulo, _ = Modulo.objects.get_or_create(
                codigo=codigo, defaults={"nombre": codigo}
            )
            EmpresaModulo.objects.create(empresa=self.empresa, modulo=modulo, activo=True)
        self.rol = RolSistema.objects.create(
            nombre="Facturación móvil",
            codigo="facturacion-movil-regression",
            puede_citas=True,
            puede_facturas=True,
            puede_ver_facturas=True,
            puede_editar_facturas=True,
            puede_cambiar_fecha_factura=True,
            puede_anular_facturas=True,
            puede_eliminar_borradores=True,
            puede_eliminar_facturas=True,
        )
        self.user = get_user_model().objects.create_user(
            username="mobile-invoices",
            password="test-password",
            empresa=self.empresa,
            rol_sistema=self.rol,
        )
        self.client.force_login(self.user)
        self.cliente = Cliente.objects.create(
            empresa=self.empresa, nombre="Paciente móvil", rtn="0801199912345",
        )
        self.producto = Producto.objects.create(
            empresa=self.empresa,
            nombre="Consulta médica",
            precio=Decimal("100.00"),
            tipo_item="servicio",
            controla_inventario=False,
        )
        self.impuesto = TipoImpuesto.objects.create(nombre="ISV 15", porcentaje=Decimal("15"))
        self.cai = CAI.objects.create(
            empresa=self.empresa,
            numero_cai="CAI-MOBILE-TEST",
            uso_documento="factura",
            establecimiento="001",
            punto_emision="001",
            tipo_documento="01",
            rango_inicial=1,
            rango_final=100,
            correlativo_actual=0,
            fecha_activacion=self.today - timedelta(days=365),
            fecha_limite=self.today + timedelta(days=365),
        )

    def invoice(self, estado="borrador", producto=None):
        factura = Factura.objects.create(
            empresa=self.empresa,
            cliente=self.cliente,
            estado=estado,
            fecha_emision=self.today,
        )
        LineaFactura.objects.create(
            factura=factura,
            producto=producto or self.producto,
            cantidad=Decimal("1.00"),
            precio_unitario=Decimal("100.00"),
            impuesto=self.impuesto,
        )
        factura.calcular_totales()
        factura.save(update_fields=["subtotal", "impuesto", "total", "total_lempiras"])
        return factura

    def app_url(self, view, factura):
        return reverse(view, args=[self.empresa.slug, factura.pk]) + "?app=1"

    def edit_data(self, factura, **overrides):
        linea = factura.lineas.get()
        datos = {
            "app": "1",
            "cliente": str(self.cliente.pk),
            "fecha_emision": str(factura.fecha_emision),
            "fecha_vencimiento": "",
            "vendedor": "",
            "tipo_cambio": "1.0000",
            "moneda": "HNL",
            "estado": factura.estado,
            "orden_compra_exenta": "",
            "registro_exonerado": "",
            "registro_sag": "",
            "motivo_auditoria": "Corrección solicitada durante la revisión clínica",
            "lineas-TOTAL_FORMS": "1",
            "lineas-INITIAL_FORMS": "1",
            "lineas-MIN_NUM_FORMS": "0",
            "lineas-MAX_NUM_FORMS": "1000",
            "lineas-0-id": str(linea.pk),
            "lineas-0-producto": str(linea.producto_id),
            "lineas-0-descripcion_manual": "",
            "lineas-0-cantidad": "1.00",
            "lineas-0-precio_unitario": "100.00",
            "lineas-0-descuento_porcentaje": "0",
            "lineas-0-comentario": "",
            "lineas-0-impuesto": str(self.impuesto.pk),
        }
        if factura.numero_factura:
            datos["numero_factura_sufijo"] = factura.numero_factura[-3:]
        datos.update(overrides)
        return datos

    def enable_historical(self):
        configuracion = ConfiguracionAvanzadaEmpresa.para_empresa(self.empresa)
        configuracion.permite_cai_historico = True
        configuracion.save(update_fields=["permite_cai_historico"])

    def invoice_audit(self, factura, accion):
        return RegistroAuditoria.objects.filter(
            empresa=self.empresa,
            app_label="facturacion",
            modelo="factura",
            objeto_id=str(factura.pk),
            cambios__accion_factura__nuevo=accion,
        ).latest("fecha")

    def test_mobile_detail_and_forms_keep_web_templates_separate(self):
        self.enable_historical()
        factura = self.invoice(estado="emitida")
        mobile = self.client.get(self.app_url("ver_factura", factura))
        self.assertEqual(mobile.status_code, 200)
        self.assertTemplateUsed(mobile, "facturacion/mobile_invoice_detail.html")
        for flag in (
            "can_edit_invoice", "can_change_invoice_date", "can_correct_invoice_fiscal",
            "can_void_invoice", "can_delete_invoice",
        ):
            self.assertTrue(mobile.context[flag], flag)
        self.assertContains(mobile, "Más acciones")
        for view, template in (
            ("editar_factura", "mobile_invoice_edit.html"),
            ("cambiar_fecha_factura", "mobile_invoice_date.html"),
            ("corregir_numero_factura", "mobile_invoice_fiscal.html"),
        ):
            response = self.client.get(self.app_url(view, factura))
            self.assertEqual(response.status_code, 200)
            self.assertTemplateUsed(response, "facturacion/" + template)
        web = self.client.get(reverse("ver_factura", args=[self.empresa.slug, factura.pk]))
        self.assertEqual(web.status_code, 200)
        self.assertTemplateUsed(web, "facturacion/ver_factura_premium.html")
        self.assertTemplateNotUsed(web, "facturacion/mobile_invoice_detail.html")

    def test_reader_has_no_actions_and_cannot_execute_their_backend_urls(self):
        self.enable_historical()
        factura = self.invoice(estado="emitida")
        permisos = (
            "puede_editar_facturas", "puede_cambiar_fecha_factura", "puede_anular_facturas",
            "puede_eliminar_borradores", "puede_eliminar_facturas",
        )
        for permiso in permisos:
            setattr(self.rol, permiso, False)
        self.rol.save(update_fields=permisos)
        detalle = self.client.get(self.app_url("ver_factura", factura))
        self.assertEqual(detalle.status_code, 200)
        for flag in (
            "can_edit_invoice", "can_change_invoice_date", "can_correct_invoice_fiscal",
            "can_void_invoice", "can_delete_invoice",
        ):
            self.assertFalse(detalle.context[flag], flag)
        for label in ("Editar factura", "Corregir fecha", "Corrección fiscal", "Anular factura", "Eliminar factura"):
            self.assertNotContains(detalle, label)
        for view in (
            "editar_factura", "cambiar_fecha_factura", "corregir_numero_factura",
            "anular_factura", "eliminar_factura", "eliminar_factura_borrador",
        ):
            with self.subTest(view=view):
                response = self.client.post(
                    self.app_url(view, factura),
                    {"app": "1", "motivo": "Acción sin autorización", "confirmacion": "ELIMINAR"},
                )
                self.assertIn(response.status_code, (302, 403))
                if response.status_code == 302:
                    self.assertEqual(response.url, reverse("dashboard", args=[self.empresa.slug]))
                request = RequestFactory().post(
                    self.app_url(view, factura),
                    {"app": "1", "motivo": "Acción sin autorización", "confirmacion": "ELIMINAR"},
                )
                request.user = self.user
                view_function = {
                    "eliminar_factura": "eliminar_factura_historica",
                    "eliminar_factura_borrador": "eliminar_factura",
                }.get(view, view)
                with self.assertRaises(PermissionDenied):
                    getattr(views, view_function)(
                        request, empresa_slug=self.empresa.slug, factura_id=factura.pk,
                    )
        factura.refresh_from_db()
        self.assertEqual(factura.estado, "emitida")
        self.assertFalse(CorreccionNumeroFactura.objects.filter(factura=factura).exists())

    def test_same_mobile_invoice_flow_is_available_in_four_clinical_companies(self):
        self.enable_historical()
        factura = self.invoice(estado="emitida")
        for slug in ("hospital_mia", "medical_spa", "luque_aestetic", "serviciosmedicos"):
            with self.subTest(empresa=slug):
                self.empresa.slug = slug
                self.empresa.save(update_fields=["slug"])
                detalle = self.client.get(self.app_url("ver_factura", factura))
                self.assertEqual(detalle.status_code, 200)
                self.assertTemplateUsed(detalle, "facturacion/mobile_invoice_detail.html")
                self.assertContains(detalle, self.app_url("editar_factura", factura))
                self.assertTrue(detalle.context["can_change_invoice_date"])
                self.assertIn("no-store", detalle["Cache-Control"])
                manifest = self.client.get(reverse("agenda_mobile_manifest", args=[slug])).json()
                self.assertEqual(manifest["start_url"], reverse("agenda_mobile", args=[slug]))
                self.assertEqual(manifest["scope"], reverse("dashboard", args=[slug]))
                self.assertTrue(self.app_url("ver_factura", factura).startswith(manifest["scope"]))
                worker = self.client.get(reverse("agenda_mobile_service_worker", args=[slug]))
                self.assertEqual(worker["Service-Worker-Allowed"], manifest["scope"])
                editor = self.client.get(self.app_url("editar_factura", factura))
                self.assertEqual(editor.status_code, 200)
                self.assertTemplateUsed(editor, "facturacion/mobile_invoice_edit.html")

    def test_tenant_invoice_id_cannot_be_read_or_changed_through_current_company(self):
        otra = Empresa.objects.create(nombre="Otra empresa", slug="otra-clinica")
        cliente = Cliente.objects.create(empresa=otra, nombre="Paciente privado de otra empresa")
        factura = Factura.objects.create(empresa=otra, cliente=cliente, estado="borrador", fecha_emision=self.today)
        for view in ("ver_factura", "editar_factura", "cambiar_fecha_factura", "corregir_numero_factura"):
            with self.subTest(view=view):
                self.assertEqual(self.client.get(self.app_url(view, factura)).status_code, 404)
        for view in ("anular_factura", "eliminar_factura", "eliminar_factura_borrador"):
            with self.subTest(view=view):
                self.assertEqual(self.client.post(self.app_url(view, factura), {
                    "app": "1", "motivo": "No pertenece a la empresa activa", "confirmacion": "ELIMINAR",
                }).status_code, 404)
        factura.refresh_from_db()
        self.assertEqual(factura.estado, "borrador")

    def test_mobile_edit_recalculates_discount_tax_and_records_previous_lines(self):
        factura = self.invoice()
        response = self.client.post(self.app_url("editar_factura", factura), self.edit_data(
            factura,
            **{"lineas-0-cantidad": "2.00", "lineas-0-precio_unitario": "150.00",
               "lineas-0-descuento_porcentaje": "10", "lineas-0-comentario": "Nota clínica en factura"},
        ))
        self.assertRedirects(response, self.app_url("ver_factura", factura), fetch_redirect_response=False)
        factura.refresh_from_db()
        self.assertEqual(factura.subtotal, Decimal("270.00"))
        self.assertEqual(factura.impuesto, Decimal("40.50"))
        self.assertEqual(factura.total, Decimal("310.50"))
        linea = factura.lineas.get()
        self.assertEqual(linea.comentario, "Nota clínica en factura")
        audit = self.invoice_audit(factura, "editar")
        self.assertEqual(audit.usuario, self.user)
        self.assertEqual(audit.cambios["factura"]["anterior"]["lineas"][0]["cantidad"], "1.00")
        self.assertEqual(audit.cambios["factura"]["nuevo"]["lineas"][0]["cantidad"], "2.00")
        detalle = self.client.get(self.app_url("ver_factura", factura))
        self.assertContains(detalle, audit.motivo)
        historial = detalle.context["mobile_invoice_audit"]
        edicion = next(evento for evento in historial if evento["accion"] == "Factura editada")
        self.assertEqual(edicion["usuario"], self.user.username)
        self.assertTrue(any(
            cambio["campo"].endswith("Cantidad")
            and cambio["anterior"] == "1.00" and cambio["nuevo"] == "2.00"
            for cambio in edicion["cambios"]
        ))
        self.assertTrue(any(evento["usuario"] == "Sistema" for evento in historial))

    def test_mobile_edit_rejects_foreign_client_and_product_without_saving(self):
        factura = self.invoice()
        otra = Empresa.objects.create(nombre="Otra empresa", slug="otra-facturacion")
        cliente = Cliente.objects.create(empresa=otra, nombre="Cliente aislado")
        producto = Producto.objects.create(
            empresa=otra, nombre="Servicio aislado", precio=Decimal("100.00"),
            tipo_item="servicio", controla_inventario=False,
        )
        response = self.client.post(self.app_url("editar_factura", factura), self.edit_data(
            factura, cliente=str(cliente.pk), **{"lineas-0-producto": str(producto.pk)},
        ))
        self.assertEqual(response.status_code, 200)
        self.assertIn("cliente", response.context["form"].errors)
        self.assertIn("producto", response.context["formset"].forms[0].errors)
        factura.refresh_from_db()
        self.assertEqual(factura.cliente_id, self.cliente.pk)
        self.assertEqual(factura.lineas.get().producto_id, self.producto.pk)
        self.assertEqual(factura.total, Decimal("115.00"))

    def test_edit_cannot_change_date_without_the_separate_existing_permission(self):
        factura = self.invoice()
        self.rol.puede_cambiar_fecha_factura = False
        self.rol.save(update_fields=["puede_cambiar_fecha_factura"])
        page = self.client.get(self.app_url("editar_factura", factura))
        self.assertTrue(page.context["form"].fields["fecha_emision"].disabled)
        response = self.client.post(self.app_url("editar_factura", factura), self.edit_data(
            factura, fecha_emision=str(self.today - timedelta(days=1)),
        ))
        self.assertEqual(response.status_code, 403)
        factura.refresh_from_db()
        self.assertEqual(factura.fecha_emision, self.today)

    def test_mobile_date_correction_updates_only_date_and_keeps_audit(self):
        factura = self.invoice()
        fecha = self.today - timedelta(days=3)
        motivo = "Fecha corregida con el documento de respaldo"
        response = self.client.post(self.app_url("cambiar_fecha_factura", factura), {
            "app": "1", "fecha_emision": str(fecha), "motivo_auditoria": motivo,
            "total": "0", "cliente": "9999", "estado": "anulada",
        })
        self.assertRedirects(response, self.app_url("ver_factura", factura), fetch_redirect_response=False)
        factura.refresh_from_db()
        self.assertEqual(factura.fecha_emision, fecha)
        self.assertEqual(factura.total, Decimal("115.00"))
        self.assertEqual(factura.cliente_id, self.cliente.pk)
        self.assertEqual(factura.estado, "borrador")
        audit = self.invoice_audit(factura, "cambiar_fecha")
        self.assertEqual(audit.usuario, self.user)
        self.assertEqual(audit.motivo, motivo)
        self.assertEqual(audit.cambios["fecha_emision"]["anterior"], str(self.today))
        self.assertEqual(audit.cambios["fecha_emision"]["nuevo"], str(fecha))

    def test_emitted_invoice_edit_keeps_existing_payment_total_validation(self):
        factura = self.invoice(estado="emitida")
        PagoFactura.objects.create(factura=factura, fecha=self.today, monto=Decimal("100"), metodo="efectivo")
        response = self.client.post(self.app_url("editar_factura", factura), self.edit_data(
            factura, **{"lineas-0-precio_unitario": "50.00"},
        ))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "no puede ser menor que los pagos ya registrados")
        factura.refresh_from_db()
        self.assertEqual(factura.total, Decimal("115.00"))
        self.assertEqual(factura.lineas.get().precio_unitario, Decimal("100.00"))
        self.assertEqual(factura.pagos_facturacion.get().monto, Decimal("100.00"))

    def test_annul_requires_reason_reverses_fefo_once_and_preserves_invoice_audit(self):
        producto = Producto.objects.create(empresa=self.empresa, nombre="Producto con lote", precio=Decimal("100"))
        inventario = InventarioProducto.objects.create(
            empresa=self.empresa, producto=producto, existencias=Decimal("10"), stock_minimo=0,
        )
        bodega = BodegaInventario.objects.create(empresa=self.empresa, nombre="Bodega clínica")
        lote = LoteInventario.objects.create(
            empresa=self.empresa, producto=producto, numero_lote="MOB-001",
            fecha_vencimiento=self.today + timedelta(days=90),
        )
        existencia = ExistenciaLoteBodega.objects.create(
            empresa=self.empresa, bodega=bodega, lote=lote, cantidad=Decimal("10"),
        )
        factura = self.invoice(estado="emitida", producto=producto)
        _registrar_salida_factura(factura)
        sin_motivo = self.client.post(self.app_url("anular_factura", factura), {"app": "1", "motivo": "no"})
        self.assertEqual(sin_motivo.status_code, 302)
        factura.refresh_from_db()
        self.assertEqual(factura.estado, "emitida")
        motivo = "Factura emitida al paciente equivocado"
        response = self.client.post(self.app_url("anular_factura", factura), {"app": "1", "motivo": motivo})
        self.assertRedirects(response, self.app_url("ver_factura", factura), fetch_redirect_response=False)
        factura.refresh_from_db()
        inventario.refresh_from_db()
        existencia.refresh_from_db()
        self.assertEqual(factura.estado, "anulada")
        self.assertEqual(factura.total, Decimal("0.00"))
        self.assertEqual(inventario.existencias, Decimal("10.00"))
        self.assertEqual(existencia.cantidad, Decimal("10.00"))
        audit = self.invoice_audit(factura, "anular")
        self.assertEqual(audit.usuario, self.user)
        self.assertEqual(audit.motivo, motivo)
        self.assertEqual(audit.cambios["estado"], {"anterior": "emitida", "nuevo": "anulada"})
        self.client.post(self.app_url("anular_factura", factura), {"app": "1", "motivo": motivo})
        self.assertEqual(MovimientoInventario.objects.filter(factura=factura, tipo="reversion_factura").count(), 1)
        self.assertEqual(MovimientoLoteBodega.objects.filter(factura=factura, tipo="reversion").count(), 1)
        detalle = self.client.get(self.app_url("ver_factura", factura))
        self.assertContains(detalle, "ANULADA")
        self.assertFalse(detalle.context["can_edit_invoice"])
        self.assertFalse(detalle.context["can_change_invoice_date"])

    def test_mobile_delete_requires_strong_confirmation_and_keeps_deletion_audit(self):
        factura = self.invoice()
        factura_id = factura.pk
        url = self.app_url("eliminar_factura_borrador", factura)
        motivo = "Borrador duplicado durante la atención del paciente"
        for datos in (
            {"app": "1", "motivo": motivo},
            {"app": "1", "confirmacion": "ELIMINAR", "motivo": "no"},
        ):
            self.assertEqual(self.client.post(url, datos).status_code, 302)
            self.assertTrue(Factura.objects.filter(pk=factura_id).exists())
        response = self.client.post(url, {"app": "1", "confirmacion": "ELIMINAR", "motivo": motivo})
        self.assertRedirects(
            response, reverse("agenda_mobile", args=[self.empresa.slug]) + "#facturas-app",
            fetch_redirect_response=False,
        )
        self.assertFalse(Factura.objects.filter(pk=factura_id).exists())
        audit = RegistroAuditoria.objects.get(
            empresa=self.empresa, app_label="facturacion", modelo="factura",
            objeto_id=str(factura_id), accion="eliminar",
        )
        self.assertEqual(audit.usuario, self.user)
        self.assertEqual(audit.motivo, motivo)
        self.assertEqual(audit.cambios["registro_eliminado"]["anterior"]["estado"], "borrador")

    def test_historical_delete_obeys_same_payment_restriction(self):
        self.enable_historical()
        factura = self.invoice(estado="emitida")
        PagoFactura.objects.create(factura=factura, fecha=self.today, monto=Decimal("20"), metodo="efectivo")
        detail = self.client.get(self.app_url("ver_factura", factura))
        self.assertFalse(detail.context["can_delete_invoice"])
        response = self.client.post(self.app_url("eliminar_factura", factura), {
            "app": "1", "confirmacion": "ELIMINAR", "motivo": "Intento de eliminación de factura pagada",
        })
        self.assertEqual(response.status_code, 302)
        factura.refresh_from_db()
        self.assertEqual(factura.estado, "emitida")
        self.assertTrue(factura.pagos_facturacion.exists())
        self.assertFalse(RegistroAuditoria.objects.filter(
            app_label="facturacion", modelo="factura", objeto_id=str(factura.pk), accion="eliminar",
        ).exists())

    def test_mobile_historical_fiscal_correction_preserves_lines_payments_and_trace(self):
        self.enable_historical()
        factura = self.invoice(estado="emitida")
        numero_original = factura.numero_factura
        pago = PagoFactura.objects.create(factura=factura, fecha=self.today, monto=Decimal("20"), metodo="efectivo")
        motivo = "Correlativo corregido contra el archivo fiscal original"
        response = self.client.post(self.app_url("corregir_numero_factura", factura), {
            "app": "1", "numero_factura": "001-001-01-00000002", "motivo": motivo,
        })
        self.assertRedirects(response, self.app_url("ver_factura", factura), fetch_redirect_response=False)
        factura.refresh_from_db()
        pago.refresh_from_db()
        self.assertEqual(factura.numero_factura, "001-001-01-00000002")
        self.assertEqual(factura.total, Decimal("115.00"))
        self.assertEqual(factura.lineas.get().cantidad, Decimal("1.00"))
        self.assertEqual(pago.monto, Decimal("20.00"))
        correction = CorreccionNumeroFactura.objects.get(factura=factura)
        self.assertEqual(correction.numero_anterior, numero_original)
        self.assertEqual(correction.motivo, motivo)
        self.assertEqual(correction.realizado_por, self.user)
        detalle = self.client.get(self.app_url("ver_factura", factura))
        self.assertContains(detalle, motivo)

    def test_historical_correction_needs_company_configuration_and_valid_cai(self):
        factura = self.invoice(estado="emitida")
        detail = self.client.get(self.app_url("ver_factura", factura))
        self.assertFalse(detail.context["can_correct_invoice_fiscal"])
        blocked = self.client.post(self.app_url("corregir_numero_factura", factura), {
            "app": "1", "numero_factura": "001-001-01-00000002", "motivo": "Intento sin habilitación fiscal",
        })
        self.assertEqual(blocked.status_code, 302)
        self.enable_historical()
        invalid = self.client.post(self.app_url("corregir_numero_factura", factura), {
            "app": "1", "numero_factura": "001-001-01-00000999", "motivo": "Intento fuera de rango del CAI",
        })
        self.assertEqual(invalid.status_code, 200)
        self.assertContains(invalid, "No existe un CAI que cubra este numero")
        factura.refresh_from_db()
        self.assertEqual(factura.numero_factura, "001-001-01-00000001")
        self.assertFalse(CorreccionNumeroFactura.objects.filter(factura=factura).exists())

    def test_active_credit_note_blocks_edit_and_historical_delete(self):
        self.enable_historical()
        factura = self.invoice(estado="emitida")
        NotaCredito.objects.create(
            empresa=self.empresa, cliente=self.cliente, factura_origen=factura,
            estado="borrador", fecha_emision=self.today,
        )
        detail = self.client.get(self.app_url("ver_factura", factura))
        self.assertFalse(detail.context["can_edit_invoice"])
        self.assertFalse(detail.context["can_delete_invoice"])
        edit = self.client.get(self.app_url("editar_factura", factura))
        self.assertEqual(edit.status_code, 302)
        delete = self.client.post(self.app_url("eliminar_factura", factura), {
            "app": "1", "confirmacion": "ELIMINAR", "motivo": "Intento con nota de crédito relacionada",
        })
        self.assertEqual(delete.status_code, 302)
        self.assertTrue(Factura.objects.filter(pk=factura.pk).exists())

    def test_app_invoice_list_offers_current_tenant_detail_pdf_and_state(self):
        factura = self.invoice(estado="emitida")
        Factura.objects.filter(pk=factura.pk).update(estado="anulada", subtotal=0, impuesto=0, total=0, total_lempiras=0)
        otra = Empresa.objects.create(nombre="Empresa externa", slug="tenant-externo")
        cliente = Cliente.objects.create(empresa=otra, nombre="Paciente externo confidencial")
        externa = Factura.objects.create(empresa=otra, cliente=cliente, estado="borrador", fecha_emision=self.today)
        response = self.client.get(reverse("agenda_mobile", args=[self.empresa.slug]))
        self.assertEqual(response.status_code, 200)
        listed = response.context["facturas_app"]
        self.assertEqual([item["id"] for item in listed], [factura.pk])
        self.assertEqual(listed[0]["detalle_url"], self.app_url("ver_factura", factura))
        self.assertEqual(listed[0]["pdf_url"], reverse("descargar_factura_pdf", args=[self.empresa.slug, factura.pk]))
        self.assertEqual(listed[0]["estado"], "anulada")
        self.assertContains(response, "ANULADA")
        self.assertNotContains(response, cliente.nombre)
        self.assertNotIn(externa.pk, [item["id"] for item in listed])
