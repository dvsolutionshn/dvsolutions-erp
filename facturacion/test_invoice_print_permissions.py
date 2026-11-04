from datetime import timedelta
from unittest.mock import patch

from django.core.exceptions import PermissionDenied
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Empresa, EmpresaModulo, Modulo, RolSistema, Usuario, UsuarioEmpresaPermiso
from facturacion.models import CAI, Cliente, ConfiguracionFacturacionEmpresa, Factura
from facturacion import views


class InvoicePrintPermissionTests(TestCase):
    """The print launcher and its document must authorize the same action."""

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            nombre="Medical Spa impresión", slug="medical_spa", rtn="PRINT-MEDICAL", tipo_solucion="clinica",
        )
        cls.otra_empresa = Empresa.objects.create(
            nombre="Hospital impresión", slug="hospital_mia", rtn="PRINT-HOSPITAL", tipo_solucion="clinica",
        )
        modulo, _ = Modulo.objects.get_or_create(codigo="facturacion", defaults={"nombre": "Facturación"})
        for empresa in (cls.empresa, cls.otra_empresa):
            EmpresaModulo.objects.create(empresa=empresa, modulo=modulo, activo=True)
            ConfiguracionFacturacionEmpresa.objects.create(empresa=empresa, plantilla_factura_pdf="termica_80mm")
            CAI.objects.create(
                empresa=empresa, numero_cai=f"CAI-PRINT-{empresa.slug}", uso_documento="factura",
                establecimiento="001", punto_emision="001", tipo_documento="01",
                rango_inicial=1, rango_final=10, correlativo_actual=0,
                fecha_activacion=timezone.localdate() - timedelta(days=1),
                fecha_limite=timezone.localdate() + timedelta(days=30),
            )
        cls.rol = RolSistema.objects.create(
            nombre="Operador de caja impresión", codigo="test-caja-impresion", activo=True,
            es_rol_clinico=True, usa_permisos_clinicos_granulares=True,
            usa_permisos_clinicos_v2=True, usa_permisos_acciones_v3=True,
            puede_punto_venta=True, puede_ver_facturas=True,
        )
        cls.usuario = Usuario.objects.create_user(username="cajera-impresion-prueba", empresa=cls.empresa)
        UsuarioEmpresaPermiso.objects.create(usuario=cls.usuario, empresa=cls.empresa, rol_sistema=cls.rol)
        cliente = Cliente.objects.create(empresa=cls.empresa, nombre="Cliente de prueba Medical", rtn="08011999000001")
        otro_cliente = Cliente.objects.create(empresa=cls.otra_empresa, nombre="Cliente de prueba Hospital", rtn="08011999000002")
        cls.factura = Factura.objects.create(
            empresa=cls.empresa, cliente=cliente, estado="emitida",
        )
        cls.otra_factura = Factura.objects.create(
            empresa=cls.otra_empresa, cliente=otro_cliente, estado="emitida",
        )

    def setUp(self):
        self.client.force_login(self.usuario)

    def _url(self, view, *, empresa=None, factura=None):
        return reverse(view, args=[(empresa or self.empresa).slug, (factura or self.factura).pk])

    def _permissions(self, *, imprimir, pos):
        self.rol.puede_imprimir_facturas = imprimir
        self.rol.puede_punto_venta = pos
        self.rol.save(update_fields=["puede_imprimir_facturas", "puede_punto_venta"])

    def _assert_permission_denied(self, response, empresa=None):
        self.assertRedirects(
            response, reverse("dashboard", args=[(empresa or self.empresa).slug]),
            fetch_redirect_response=False,
        )
        self.assertTemplateNotUsed(response, "facturacion/factura_pos_auto_print.html")

    def test_pos_without_print_permission_cannot_start_automatic_printing(self):
        for method in ("get", "head"):
            with self.subTest(method=method):
                self._assert_permission_denied(getattr(self.client, method)(self._url("imprimir_factura_pos")))

    def test_direct_document_views_also_enforce_print_permission(self):
        factory = RequestFactory()
        for view in (views.imprimir_factura_pos, views.vista_previa_factura_pdf, views.descargar_factura_pdf):
            for method in ("get", "head"):
                with self.subTest(view=view.__name__, method=method):
                    request = getattr(factory, method)(self._url(view.__name__))
                    request.user = self.usuario
                    with self.assertRaises(PermissionDenied):
                        view(request, self.empresa.slug, self.factura.pk)

    @patch("facturacion.views._generar_factura_pdf_bytes", return_value=b"%PDF-test-invoice")
    def test_v2_roles_use_the_same_print_permission_for_launcher_and_document(self, pdf):
        self.rol.usa_permisos_acciones_v3 = False
        self.rol.save(update_fields=["usa_permisos_acciones_v3"])
        for view in ("imprimir_factura_pos", "vista_previa_factura_pdf", "descargar_factura_pdf"):
            with self.subTest(view=view, authorized=False):
                self._assert_permission_denied(self.client.get(self._url(view)))
        pdf.assert_not_called()
        self._permissions(imprimir=True, pos=False)
        for view in ("imprimir_factura_pos", "vista_previa_factura_pdf", "descargar_factura_pdf"):
            with self.subTest(view=view, authorized=True):
                self.assertEqual(self.client.get(self._url(view)).status_code, 200)

    @patch("facturacion.views._generar_factura_pdf_bytes", return_value=b"%PDF-test-invoice")
    def test_legacy_roles_keep_invoice_reading_as_the_print_authorization(self, pdf):
        self.rol.usa_permisos_clinicos_granulares = False
        self.rol.usa_permisos_clinicos_v2 = False
        self.rol.usa_permisos_acciones_v3 = False
        self.rol.puede_punto_venta = False
        self.rol.save(update_fields=[
            "usa_permisos_clinicos_granulares", "usa_permisos_clinicos_v2", "usa_permisos_acciones_v3",
            "puede_punto_venta",
        ])
        for view in ("imprimir_factura_pos", "vista_previa_factura_pdf", "descargar_factura_pdf"):
            with self.subTest(view=view, authorized=True):
                self.assertEqual(self.client.get(self._url(view)).status_code, 200)
        pdf.reset_mock()
        self.rol.puede_ver_facturas = False
        self.rol.save(update_fields=["puede_ver_facturas"])
        for view in ("imprimir_factura_pos", "vista_previa_factura_pdf", "descargar_factura_pdf"):
            with self.subTest(view=view, authorized=False):
                self._assert_permission_denied(self.client.get(self._url(view)))
        pdf.assert_not_called()

    def test_print_permission_can_launch_invoice_without_point_of_sale_access(self):
        self._permissions(imprimir=True, pos=False)
        for method in ("get", "head"):
            with self.subTest(method=method):
                response = getattr(self.client, method)(self._url("imprimir_factura_pos"))
                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, "facturacion/factura_pos_auto_print.html")
                self.assertEqual(response.context["factura"], self.factura)
                self.assertEqual(response.context["pdf_url"], self._url("vista_previa_factura_pdf"))

    @patch("facturacion.views._generar_factura_pdf_bytes", return_value=b"%PDF-test-invoice")
    def test_preview_and_download_require_print_permission_even_with_pos_and_invoice_reading(self, pdf):
        for view in ("vista_previa_factura_pdf", "descargar_factura_pdf"):
            for method in ("get", "head"):
                with self.subTest(view=view, method=method):
                    self._assert_permission_denied(getattr(self.client, method)(self._url(view)))
        pdf.assert_not_called()

    @patch("facturacion.views._generar_factura_pdf_bytes", return_value=b"%PDF-test-invoice")
    def test_authorized_preview_and_download_use_medical_spa_thermal_template(self, pdf):
        self._permissions(imprimir=True, pos=False)
        for view, disposition in (("vista_previa_factura_pdf", "inline"), ("descargar_factura_pdf", "attachment")):
            for method in ("get", "head"):
                with self.subTest(view=view, method=method):
                    response = getattr(self.client, method)(self._url(view))
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response["Content-Type"], "application/pdf")
                    self.assertTrue(response["Content-Disposition"].startswith(disposition + ";"))
                    self.assertEqual(response.content, b"%PDF-test-invoice" if method == "get" else b"")
                    pdf.assert_called_with(self.empresa, self.factura, "facturacion/factura_pdf_termica_80mm.html")

    @patch("facturacion.views._generar_factura_pdf_bytes", return_value=b"%PDF-test-invoice")
    def test_print_permission_never_exposes_an_invoice_from_another_company(self, pdf):
        self._permissions(imprimir=True, pos=True)
        for view in ("imprimir_factura_pos", "vista_previa_factura_pdf", "descargar_factura_pdf"):
            for method in ("get", "head"):
                with self.subTest(view=view, method=method):
                    response = getattr(self.client, method)(self._url(view, factura=self.otra_factura))
                    self.assertEqual(response.status_code, 404)
                    response = getattr(self.client, method)(self._url(view, empresa=self.otra_empresa, factura=self.otra_factura))
                    self.assertRedirects(
                        response, reverse("empresa_login", args=[self.otra_empresa.slug]),
                        fetch_redirect_response=False,
                    )
        pdf.assert_not_called()

    @patch("facturacion.views._generar_factura_pdf_bytes", return_value=b"%PDF-test-invoice")
    def test_printing_rights_are_resolved_independently_for_each_company(self, pdf):
        self._permissions(imprimir=True, pos=True)
        self.usuario.empresas_acceso.add(self.otra_empresa)
        otro_rol = RolSistema.objects.create(
            nombre="Caja sin impresión", codigo="test-caja-sin-impresion", activo=True,
            es_rol_clinico=True, usa_permisos_clinicos_granulares=True,
            usa_permisos_clinicos_v2=True, usa_permisos_acciones_v3=True,
            puede_punto_venta=True, puede_ver_facturas=True,
        )
        UsuarioEmpresaPermiso.objects.create(usuario=self.usuario, empresa=self.otra_empresa, rol_sistema=otro_rol)
        for view in ("imprimir_factura_pos", "vista_previa_factura_pdf", "descargar_factura_pdf"):
            with self.subTest(view=view):
                response = self.client.get(self._url(view, empresa=self.otra_empresa, factura=self.otra_factura))
                self._assert_permission_denied(response, self.otra_empresa)
        pdf.assert_not_called()
