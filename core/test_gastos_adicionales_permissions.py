from types import SimpleNamespace
from importlib import import_module

from django.apps import apps
from django.contrib.messages.storage.fallback import FallbackStorage
from django.http import HttpResponse
from django.test import RequestFactory, TestCase
from django.urls import reverse

from core.access import gastos_adicionales_habilitados
from core.clinical_permissions import permisos_gastos_adicionales_desde_ruta
from core.context_processors import erp_access
from core.forms import RolSistemaForm
from core.middleware import EmpresaAccessMiddleware
from core.models import Empresa, EmpresaModulo, Modulo, RolSistema, Usuario, UsuarioEmpresaPermiso


class GastosAdicionalesPermissionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(nombre="Hospital GA", slug="hospital_mia", rtn="GA-PERM-1", tipo_solucion="clinica")
        cls.otra = Empresa.objects.create(nombre="Medical Spa GA", slug="medical_spa", rtn="GA-PERM-2", tipo_solucion="clinica")
        cls.no_autorizada = Empresa.objects.create(nombre="Otra clínica", slug="otra_clinica", rtn="GA-PERM-3", tipo_solucion="clinica")
        for codigo in ("clinica_medica", "facturacion", "gastos_adicionales"):
            modulo, _ = Modulo.objects.get_or_create(codigo=codigo, defaults={"nombre": codigo})
            for empresa in (cls.empresa, cls.otra, cls.no_autorizada):
                EmpresaModulo.objects.create(empresa=empresa, modulo=modulo)
        cls.rol = RolSistema.objects.create(nombre="GA permisos", codigo="ga-permisos")
        cls.usuario = Usuario.objects.create_user(username="ga-permisos", empresa=cls.empresa, rol_sistema=cls.rol)
        cls.usuario.empresas_acceso.add(cls.otra)

    def setUp(self):
        self.factory = RequestFactory()
        self.middleware = EmpresaAccessMiddleware(lambda request: HttpResponse("permitido"))

    def _request(self, suffix="", method="GET", empresa=None, usuario=None):
        empresa = empresa or self.empresa
        request = self.factory.generic(method, f"/{empresa.slug}/dashboard/clinica/gastos-adicionales/{suffix}")
        request.user = usuario or self.usuario
        request.session = {}
        request._messages = FallbackStorage(request)
        request.resolver_match = SimpleNamespace(kwargs={"empresa_slug": empresa.slug})
        return request

    def _grant(self, *fields, granular=False):
        for field in fields:
            setattr(self.rol, field, True)
        self.rol.usa_permisos_clinicos_granulares = granular
        self.rol.save()
        self.usuario.rol_sistema = self.rol

    def test_view_does_not_grant_create_edit_send_or_conversion(self):
        self._grant("puede_ver_gastos_adicionales", granular=True)
        for suffix in ("", "7/", "7/pdf/"):
            with self.subTest(suffix=suffix):
                self.assertEqual(self.middleware(self._request(suffix)).status_code, 200)
        for suffix, method in (("nuevo/", "GET"), ("7/editar/", "POST"), ("7/enviar-correo/", "POST"), ("7/convertir/", "POST")):
            with self.subTest(suffix=suffix):
                self.assertEqual(self.middleware(self._request(suffix, method)).status_code, 302)

    def test_legacy_broad_clinical_permissions_do_not_grant_ga(self):
        self._grant("puede_clinica", "puede_expediente_clinico", "puede_pacientes", "puede_productos")
        self.assertEqual(self.middleware(self._request()).status_code, 302)
        self.assertEqual(self.middleware(self._request("nuevo/", "POST")).status_code, 302)
        self.assertFalse(erp_access(self._request())["erp_access"]["modulo_gastos_adicionales"])

    def test_catalogs_allow_create_or_edit_without_general_catalog_permission(self):
        for field in ("puede_crear_gastos_adicionales", "puede_editar_gastos_adicionales"):
            with self.subTest(field=field):
                RolSistema.objects.filter(pk=self.rol.pk).update(puede_crear_gastos_adicionales=False, puede_editar_gastos_adicionales=False)
                self.rol.refresh_from_db()
                self._grant(field)
                for suffix in ("pacientes/buscar/", "productos/buscar/"):
                    self.assertEqual(self.middleware(self._request(suffix)).status_code, 200)
                    self.assertEqual(self.middleware(self._request(suffix, "POST")).status_code, 302)

    def test_sending_and_conversion_require_post_even_for_company_admin(self):
        self.usuario.es_administrador_empresa = True
        for suffix in ("7/enviar-correo/", "7/enviar-whatsapp/", "7/convertir/"):
            with self.subTest(suffix=suffix):
                self.assertEqual(self.middleware(self._request(suffix)).status_code, 302)
                self.assertEqual(self.middleware(self._request(suffix, "POST")).status_code, 200)

    def test_company_role_override_and_explicit_no_role_are_respected(self):
        self._grant("puede_ver_gastos_adicionales")
        UsuarioEmpresaPermiso.objects.create(usuario=self.usuario, empresa=self.otra, rol_sistema=None)
        self.assertEqual(self.middleware(self._request()).status_code, 200)
        self.assertEqual(self.middleware(self._request(empresa=self.otra)).status_code, 302)
        self.assertFalse(erp_access(self._request(empresa=self.otra))["erp_access"]["ver_gastos_adicionales"])

    def test_tenant_membership_is_required_before_permissions(self):
        self._grant("puede_ver_gastos_adicionales")
        self.usuario.empresas_acceso.clear()
        response = self.middleware(self._request(empresa=self.otra))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("empresa_login", kwargs={"slug": self.otra.slug}))

    def test_disabled_role_and_unknown_routes_are_denied(self):
        self._grant("puede_ver_gastos_adicionales")
        self.rol.activo = False
        self.rol.save(update_fields=["activo"])
        self.assertEqual(self.middleware(self._request()).status_code, 302)
        self.usuario.es_administrador_empresa = True
        self.assertEqual(self.middleware(self._request("7/eliminar/", "POST")).status_code, 302)

    def test_authorized_slug_and_active_ga_module_are_required(self):
        self.usuario.es_administrador_empresa = True
        self.usuario.empresas_acceso.add(self.no_autorizada)
        self.assertFalse(gastos_adicionales_habilitados(self.no_autorizada))
        self.assertEqual(self.middleware(self._request(empresa=self.no_autorizada)).status_code, 302)
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo__codigo="gastos_adicionales").update(activo=False)
        self.assertFalse(gastos_adicionales_habilitados(self.empresa))
        self.assertEqual(self.middleware(self._request()).status_code, 302)

    def test_ga_operates_without_clinical_or_billing_activation(self):
        self._grant("puede_ver_gastos_adicionales", "puede_crear_gastos_adicionales")
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo__codigo__in=("clinica_medica", "facturacion")).update(activo=False)
        self.assertTrue(gastos_adicionales_habilitados(self.empresa))
        self.assertEqual(self.middleware(self._request()).status_code, 200)
        self.assertEqual(self.middleware(self._request("pacientes/buscar/")).status_code, 200)
        self.client.force_login(self.usuario)
        dashboard = self.client.get(reverse("dashboard", kwargs={"slug": self.empresa.slug}))
        self.assertContains(dashboard, "Gastos Adicionales")
        historial_url = reverse("clinica_gastos_adicionales", kwargs={"empresa_slug": self.empresa.slug})
        self.assertContains(dashboard, f'href="{historial_url}"')
        self.assertEqual(self.client.get(historial_url).status_code, 200)
        self.assertEqual(self.client.get(reverse("clinica_gasto_adicional_crear", kwargs={"empresa_slug": self.empresa.slug})).status_code, 200)
        self.assertFalse(erp_access(self._request())["erp_access"]["convertir_gastos_adicionales_factura"])
        self.empresa.tipo_solucion = "erp"
        self.empresa.save(update_fields=["tipo_solucion"])
        dashboard = self.client.get(reverse("dashboard", kwargs={"slug": self.empresa.slug}))
        self.assertContains(dashboard, "<h3>Gastos Adicionales</h3>", count=1)
        self.assertContains(dashboard, f'href="{historial_url}"')

    def test_activation_migration_keeps_existing_companies_and_other_modules(self):
        servicios = Empresa.objects.create(nombre="Servicios Médicos GA", slug="serviciosmedicos", rtn="GA-PERM-4")
        luque = Empresa.objects.create(nombre="Luque GA", slug="luque_aestetic", rtn="GA-PERM-5")
        alias = Empresa.objects.create(nombre="Alias no canónico", slug="servicios_medicos", rtn="GA-PERM-6")
        total_empresas = Empresa.objects.count()
        EmpresaModulo.objects.filter(modulo__codigo__in=("clinica_medica", "facturacion", "gastos_adicionales")).update(activo=False)
        migration = import_module("core.migrations.0055_habilitar_gastos_adicionales")
        migration.habilitar_gastos_adicionales(apps, None)
        self.assertEqual(Empresa.objects.count(), total_empresas)
        for empresa in (self.empresa, self.otra, servicios, luque):
            self.assertTrue(empresa.tiene_modulo_activo("gastos_adicionales"))
            self.assertFalse(empresa.tiene_modulo_activo("clinica_medica"))
            self.assertFalse(empresa.tiene_modulo_activo("facturacion"))
        self.assertFalse(self.no_autorizada.tiene_modulo_activo("gastos_adicionales"))
        self.assertFalse(alias.tiene_modulo_activo("gastos_adicionales"))
        slugs_activos = set(EmpresaModulo.objects.filter(modulo__codigo="gastos_adicionales", activo=True).values_list("empresa__slug", flat=True))
        self.assertEqual(slugs_activos, {"hospital_mia", "medical_spa", "serviciosmedicos", "luque_aestetic"})
        total_relaciones = EmpresaModulo.objects.count()
        migration.habilitar_gastos_adicionales(apps, None)
        self.assertEqual(Empresa.objects.count(), total_empresas)
        self.assertEqual(EmpresaModulo.objects.count(), total_relaciones)

    def test_conversion_context_requires_normal_invoice_review_permissions(self):
        self._grant("puede_convertir_gastos_adicionales_factura", "puede_crear_facturas")
        self.assertFalse(erp_access(self._request())["erp_access"]["convertir_gastos_adicionales_factura"])
        self._grant("puede_editar_facturas")
        self.assertFalse(erp_access(self._request())["erp_access"]["convertir_gastos_adicionales_factura"])
        self._grant("puede_ver_facturas")
        self.assertTrue(erp_access(self._request())["erp_access"]["convertir_gastos_adicionales_factura"])
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo__codigo="facturacion").update(activo=False)
        self.assertFalse(erp_access(self._request())["erp_access"]["convertir_gastos_adicionales_factura"])

    def test_creator_has_module_entry_without_history_permission(self):
        self._grant("puede_crear_gastos_adicionales")
        access = erp_access(self._request())["erp_access"]
        self.assertTrue(access["modulo_gastos_adicionales"])
        self.assertTrue(access["crear_gastos_adicionales"])
        self.assertFalse(access["ver_gastos_adicionales"])
        self.assertTrue(self.rol.tiene_algun_acceso_clinica)

    def test_creator_dashboard_and_form_only_link_to_allowed_actions(self):
        self._grant("puede_crear_gastos_adicionales")
        self.client.force_login(self.usuario)
        crear_url = reverse("clinica_gasto_adicional_crear", kwargs={"empresa_slug": self.empresa.slug})
        historial_url = reverse("clinica_gastos_adicionales", kwargs={"empresa_slug": self.empresa.slug})
        dashboard = self.client.get(reverse("dashboard", kwargs={"slug": self.empresa.slug}))
        self.assertContains(dashboard, "Gastos Adicionales")
        self.assertContains(dashboard, f'href="{crear_url}"')
        self.assertNotContains(dashboard, f'href="{historial_url}"')
        formulario = self.client.get(crear_url)
        self.assertEqual(formulario.status_code, 200)
        self.assertNotContains(formulario, "Guardar y generar PDF")
        self.assertNotContains(formulario, f'href="{historial_url}"')

    def test_dashboard_hides_module_when_only_broad_clinical_permission_is_granted(self):
        self._grant("puede_clinica", "puede_expediente_clinico")
        self.client.force_login(self.usuario)
        dashboard = self.client.get(reverse("dashboard", kwargs={"slug": self.empresa.slug}))
        self.assertEqual(dashboard.status_code, 200)
        self.assertNotContains(dashboard, "Gastos Adicionales")

    def test_permission_form_exposes_all_five_capabilities(self):
        form = RolSistemaForm()
        for field in (
            "puede_ver_gastos_adicionales", "puede_crear_gastos_adicionales",
            "puede_editar_gastos_adicionales", "puede_enviar_gastos_adicionales",
            "puede_convertir_gastos_adicionales_factura",
        ):
            self.assertIn(field, form.fields)

    def test_permission_resolution_uses_explicit_actions(self):
        self.assertEqual(permisos_gastos_adicionales_desde_ruta("gastos-adicionales/7/pdf/"), ("puede_ver_gastos_adicionales",))
        self.assertEqual(permisos_gastos_adicionales_desde_ruta("gastos-adicionales/7/convertir/", "POST"), ("puede_convertir_gastos_adicionales_factura",))
        self.assertEqual(permisos_gastos_adicionales_desde_ruta("gastos-adicionales/7/convertir/", "GET"), ())
