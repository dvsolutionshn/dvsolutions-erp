"""La configuración avanzada móvil conserva ModelAdmin y sus permisos reales.

Datos sintéticos, transportes bloqueados y base temporal del runner Django.
La consulta no inicializa filas; guardar mantiene el pipeline y las dos auditorías.
"""
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlencode, urlsplit
from unittest.mock import patch

from django.apps import apps
from django.contrib import admin
from django.contrib.admin.models import ADDITION, CHANGE, LogEntry
from django.contrib.auth.models import AnonymousUser, Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.http import Http404
from django.test import Client, RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import resolve, reverse

from contabilidad.models import CuentaContable
from facturacion.models import InventarioProducto, Producto

from .models import ConfiguracionAvanzadaEmpresa, Empresa, Modulo, RegistroAuditoria, RolSistema, Usuario


class _AdvancedMarkup(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.in_form = False
        self.fields = []
        self.form = {}
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form" and attrs.get("id") == "mobileAdvancedForm":
            self.in_form = True
            self.form = attrs
        if self.in_form and tag in {"input", "select", "textarea", "button"} and attrs.get("name"):
            self.fields.append((tag, attrs))

    def handle_endtag(self, tag):
        if tag == "form":
            self.in_form = False


@override_settings(ENABLE_DJANGO_ADMIN=True, EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class MobileAdvancedConfigurationTests(TestCase):
    SLUGS = ("hospital_mia", "medical_spa", "luque_aestetic", "serviciosmedicos")
    BOOLEAN_FIELDS = (
        "usa_cierre_caja", "usa_pagos_mixtos", "usa_reporte_bancos", "usa_inventario_farmaceutico",
        "usa_control_lotes_fefo", "manuales_pdf_habilitados", "usa_bodegas_internas",
        "ventas_solo_desde_vitrina", "permite_cai_historico", "permite_plantilla_factura_independiente",
    )
    FIELDS = ("empresa", *BOOLEAN_FIELDS, "modulos_adicionales_visibles_clinica", "bodega_venta_predeterminada", "notas")
    PICKER = "configuracion_avanzada_empresas_app"
    PRESENTER = "configuracion_avanzada_empresa_app"
    ADD = "admin:core_configuracionavanzadaempresa_add"
    EDIT = "admin:core_configuracionavanzadaempresa_change"

    @classmethod
    def setUpTestData(cls):
        cls.empresas = {slug: Empresa.objects.create(nombre=f"Avanzada QA {slug}", slug=slug,
            rtn=f"ADV-QA-{index}", tipo_solucion="clinica", estado_licencia="activa")
            for index, slug in enumerate(cls.SLUGS, 1)}
        cls.empresa = cls.empresas["hospital_mia"]
        cls.external = Empresa.objects.create(nombre="Externa avanzada QA", slug="external-advanced-qa", rtn="ADV-QA-EXT", tipo_solucion="clinica")
        cls.configs = {slug: ConfiguracionAvanzadaEmpresa.objects.create(empresa=empresa,
            bodega_venta_predeterminada=f"Bodega histórica {index}", notas=f"Nota histórica {slug}")
            for index, (slug, empresa) in enumerate(cls.empresas.items(), 1)}
        cls.configuration = cls.configs[cls.empresa.slug]
        cls.external_config = ConfiguracionAvanzadaEmpresa.objects.create(empresa=cls.external, notas="NO EXPONER OTRO ALCANCE")
        cls.module, _ = Modulo.objects.get_or_create(codigo="advanced-commercial-qa", defaults={"nombre": "Comercial avanzada QA"})
        cls.private = Modulo.objects.create(codigo="advanced-noncommercial-qa", nombre="Módulo no comercial QA", es_comercial=False)
        cls.configuration.modulos_adicionales_visibles_clinica.add(cls.private)
        # Nonempty local and foreign business rows make isolation assertions
        # detect destructive rewrites as well as accidental new rows.
        for index, empresa in enumerate((*cls.empresas.values(), cls.external), 1):
            producto = Producto.objects.create(empresa=empresa, nombre=f"Stock intacto QA {index}",
                codigo=f"ADV-STOCK-{index}", precio="12.75", costo_promedio="4.1250")
            InventarioProducto.objects.create(empresa=empresa, producto=producto, existencias="17.25", stock_minimo="3.00")
            CuentaContable.objects.create(empresa=empresa, codigo=f"QA-ADV-{index}", nombre=f"Cuenta intacta {index}", tipo="activo")
        content_type = ContentType.objects.get_for_model(ConfiguracionAvanzadaEmpresa)
        cls.permissions = {key: Permission.objects.get(content_type=content_type, codename=f"{key}_configuracionavanzadaempresa")
            for key in ("view", "change", "add", "delete")}
        cls.supervisor = Usuario.objects.create_superuser(username="advanced-super-qa", password="test-only",
            email="super@example.invalid", empresa=cls.external)
        cls.reader = Usuario.objects.create_user(username="advanced-reader-qa", password="test-only", empresa=cls.external, is_staff=True)
        cls.reader.user_permissions.add(cls.permissions["view"])
        cls.editor = Usuario.objects.create_user(username="advanced-editor-qa", password="test-only", empresa=cls.external, is_staff=True)
        cls.editor.user_permissions.add(cls.permissions["change"])
        cls.creator = Usuario.objects.create_user(username="advanced-creator-qa", password="test-only", empresa=cls.external, is_staff=True)
        cls.creator.user_permissions.add(cls.permissions["add"])
        cls.view_creator = Usuario.objects.create_user(username="advanced-view-creator-qa", password="test-only", empresa=cls.external, is_staff=True)
        cls.view_creator.user_permissions.add(cls.permissions["view"], cls.permissions["add"])
        cls.staff_none = Usuario.objects.create_user(username="advanced-staff-none-qa", password="test-only", empresa=cls.empresa, is_staff=True)
        cls.nonstaff = Usuario.objects.create_user(username="advanced-no-staff-qa", password="test-only", empresa=cls.empresa)
        cls.nonstaff.user_permissions.add(*cls.permissions.values())
        cls.inactive = Usuario.objects.create_user(username="advanced-inactive-qa", password="test-only", empresa=cls.empresa, is_staff=True, is_active=False)
        cls.inactive.user_permissions.add(*cls.permissions.values())
        cls.company_admin = Usuario.objects.create_user(username="advanced-company-admin-qa", password="test-only", empresa=cls.empresa,
            is_staff=True, es_administrador_empresa=True, puede_administrar_usuarios_clinicos=True)
        cfg_role = RolSistema.objects.create(nombre="Config ERP no Django QA", codigo="advanced-erp-config-qa",
            es_rol_clinico=True, usa_permisos_clinicos_granulares=True, puede_configuracion_clinica=True)
        cls.company_admin.rol_sistema = cfg_role
        cls.company_admin.save(update_fields=["rol_sistema"])

    def setUp(self):
        self.client.force_login(self.supervisor)
        for target in ("core.views.enviar_correo_acceso", "core.views.emitir_token_acceso",
                       "facturacion.views.enviar_documento_whatsapp", "django.core.mail.EmailMessage.send"):
            mocked = patch(target)
            transport = mocked.start()
            self.addCleanup(mocked.stop)
            self.addCleanup(transport.assert_not_called)

    def model_admin(self):
        return admin.site._registry[ConfiguracionAvanzadaEmpresa]

    def url(self, empresa=None, *, picker=False, **params):
        path = reverse(self.PICKER) if picker else reverse(self.PRESENTER, args=[(empresa or self.empresa).pk])
        return path+"?"+urlencode({"app": "1", **params}, doseq=True)

    def admin_url(self, empresa=None, *, adding=False, configuration=None, mobile=True, **params):
        empresa = empresa or self.empresa
        configuration = configuration or self.configs.get(empresa.slug)
        path = reverse(self.ADD) if adding else reverse(self.EDIT, args=[configuration.pk])
        query = {"app": "1", "empresa": str(empresa.pk)} if mobile else {}
        query.update(params)
        return path+("?"+urlencode(query, doseq=True) if query else "")

    def data(self, empresa=None, **updates):
        empresa = empresa or self.empresa
        result = {"app": "1", "empresa": str(empresa.pk), "bodega_venta_predeterminada": "Bodega QA libre",
            "notas": "Nota propuesta QA", "modulos_adicionales_visibles_clinica": []}
        result.update(updates)
        return result

    def request(self, url, *, actor=None, method="get", data=None):
        factory = RequestFactory()
        request = factory.post(url, data or {}) if method == "post" else factory.generic(method.upper(), url)
        # Direct guard checks must reach ModelAdmin rather than stop at CSRF.
        # A separate Client(enforce_csrf_checks=True) verifies real protection.
        request._dont_enforce_csrf_checks = True
        request.user = Usuario.objects.get(pk=(actor or self.supervisor).pk) if not isinstance(actor, AnonymousUser) else actor
        request.session = {}
        request._messages = FallbackStorage(request)
        match = resolve(urlsplit(url).path)
        request.resolver_match = match
        return request, match

    def direct(self, url=None, *, actor=None, method="get", data=None):
        request, match = self.request(url or self.url(), actor=actor, method=method, data=data)
        return match.func(request, **match.kwargs)

    def snapshot(self, exclude=()):
        return tuple((model._meta.label_lower, list(model.objects.order_by("pk").values()))
            for model in apps.get_models(include_auto_created=True) if model._meta.label_lower not in exclude)

    def denied(self, url=None, **kwargs):
        before = self.snapshot()
        try:
            response = self.direct(url, **kwargs)
        except (PermissionDenied, Http404):
            pass
        else:
            if response.status_code == 302:
                self.assertIn(urlsplit(response.url).path, {reverse("admin:login"), reverse("public_access")})
            else:
                self.assertIn(response.status_code, (403, 404, 405))
        self.assertEqual(self.snapshot(), before)

    def remove_configuration(self, empresa=None):
        empresa = empresa or self.empresa
        ConfiguracionAvanzadaEmpresa.objects.filter(empresa=empresa).delete()

    def login(self, actor):
        self.client.force_login(actor)

    def no_writes(self, queries):
        self.assertFalse(any(row["sql"].lstrip().upper().startswith(("INSERT ", "UPDATE ", "DELETE ", "REPLACE ")) for row in queries))

    def assert_no_post(self, response):
        self.assertNotContains(response, 'id="mobileAdvancedForm"')
        self.assertNotContains(response, 'method="post"')

    def test_existing_four_companies_use_mobile_presenter_without_replacing_admin_web(self):
        for empresa in self.empresas.values():
            response = self.client.get(self.url(empresa))
            self.assertEqual(response.status_code, 200)
            self.assertTemplateUsed(response, "core/mobile_advanced_form.html")
            web = self.client.get(self.admin_url(empresa, mobile=False))
            self.assertEqual(web.status_code, 200)
            self.assertTemplateUsed(web, "admin/change_form.html")
            self.assertTemplateNotUsed(web, "core/mobile_advanced_form.html")

    def test_original_permission_matrix_separates_view_change_and_add(self):
        for actor in (self.reader, self.editor, self.view_creator):
            self.login(actor)
            for empresa in self.empresas.values():
                response = self.client.get(self.url(empresa))
                self.assertEqual(response.status_code, 200)
                if actor.pk == self.editor.pk:
                    self.assertContains(response, 'id="mobileAdvancedForm"')
                else:
                    self.assert_no_post(response)
        for actor in (self.reader, self.view_creator, self.creator):
            self.denied(self.admin_url(), actor=actor, method="post", data=self.data(usa_cierre_caja="on"))
        self.denied(actor=self.creator)

    def test_django_group_permission_is_legitimate_without_role_or_extra_user_flags(self):
        group = Group.objects.create(name="Grupo global avanzado QA")
        group.permissions.add(self.permissions["change"])
        self.staff_none.groups.add(group)
        self.login(self.staff_none)
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="mobileAdvancedForm"')
        self.assertFalse(self.staff_none.es_administrador_empresa)
        self.assertIsNone(self.staff_none.rol_sistema_id)

    def test_staff_and_active_are_required_even_if_superuser_or_has_all_permissions(self):
        for actor in (self.nonstaff, self.inactive, self.staff_none):
            self.denied(actor=actor)
            self.denied(self.admin_url(), actor=actor, method="post", data=self.data())
        for field in ("is_staff", "is_active"):
            Usuario.objects.filter(pk=self.supervisor.pk).update(**{field: False})
            self.denied(actor=self.supervisor)
            Usuario.objects.filter(pk=self.supervisor.pk).update(**{field: True})

    def test_erp_admin_or_users_flag_and_clinical_role_do_not_replace_django_model_permissions(self):
        self.denied(actor=self.company_admin)
        self.denied(self.admin_url(), actor=self.company_admin, method="post", data=self.data())
        self.company_admin.refresh_from_db()
        self.assertTrue(self.company_admin.es_administrador_empresa)
        self.assertTrue(self.company_admin.puede_administrar_usuarios_clinicos)

    def test_existing_global_rights_need_no_membership_license_or_operational_modules(self):
        self.login(self.editor)
        for empresa in self.empresas.values():
            Empresa.objects.filter(pk=empresa.pk).update(activa=False, estado_licencia="suspendida")
        before = self.snapshot()
        for empresa in self.empresas.values():
            self.assertEqual(self.client.get(self.url(empresa)).status_code, 200)
        self.assertEqual(self.snapshot(), before)

    def test_disabled_admin_flag_closes_app_routes_and_canonical_admin_app_posts(self):
        with override_settings(ENABLE_DJANGO_ADMIN=False):
            with patch.object(self.model_admin(), "get_form", side_effect=AssertionError("Form must not load")):
                self.denied()
                self.denied(self.url(picker=True))
                self.denied(self.admin_url(), method="post", data=self.data())
                self.denied(self.admin_url(adding=True), method="post", data=self.data())

    def test_logout_keeps_protection_without_any_domain_writes(self):
        for url in (self.url(), self.url(picker=True), self.admin_url()):
            self.denied(url, actor=AnonymousUser())

    def test_scope_is_exact_four_clinical_profiles_before_form_or_any_config_creation(self):
        with patch.object(self.model_admin(), "get_form", side_effect=AssertionError("No foreign form")):
            self.denied(self.url(self.external))
            self.denied(self.admin_url(self.external, configuration=self.external_config), method="post", data=self.data(self.external))
            Empresa.objects.filter(pk=self.empresa.pk).update(tipo_solucion="erp")
            self.denied()
            self.denied(self.admin_url(), method="post", data=self.data())

    def test_picker_minimal_metadata_scopes_four_and_never_creates_missing_configurations(self):
        self.remove_configuration()
        before = self.snapshot()
        response = self.client.get(self.url(picker=True))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "core/mobile_advanced_list.html")
        self.assertNotContains(response, self.external.nombre)
        for empresa in self.empresas.values():
            self.assertContains(response, empresa.nombre)
        self.assertFalse(ConfiguracionAvanzadaEmpresa.objects.filter(empresa=self.empresa).exists())
        self.assertEqual(self.snapshot(), before)

    def test_get_and_head_all_models_and_through_tables_have_no_writes_or_seeds(self):
        before = self.snapshot()
        for method in ("get", "head"):
            for url in (self.url(), self.url(picker=True), self.admin_url()):
                with CaptureQueriesContext(connection) as queries:
                    response = getattr(self.client, method)(url)
                self.assertEqual(response.status_code, 200)
                self.assertIn("no-store", response["Cache-Control"])
                self.no_writes(queries)
                if method == "head":
                    self.assertEqual(response.content, b"")
        self.assertEqual(self.snapshot(), before)

    def test_missing_content_type_on_get_is_not_recreated_by_admin_metadata(self):
        ContentType.objects.filter(app_label="core", model="configuracionavanzadaempresa").delete()
        ContentType.objects.clear_cache()
        self.addCleanup(ContentType.objects.clear_cache)
        before = self.snapshot()
        for url in (self.url(), self.url(picker=True)):
            with CaptureQueriesContext(connection) as queries:
                response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.no_writes(queries)
        self.assertEqual(self.snapshot(), before)

    def test_missing_configuration_is_unsaved_original_form_for_add_permission_only(self):
        self.remove_configuration()
        self.login(self.creator)
        before = self.snapshot()
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertIsNone(form.instance.pk)
        self.assertEqual(form["bodega_venta_predeterminada"].value(), "Vitrina")
        self.assertContains(response, 'id="mobileAdvancedForm"')
        self.assertEqual(self.snapshot(), before)

    def test_missing_configuration_without_add_is_explanation_not_auto_create_or_edit_form(self):
        self.remove_configuration()
        for actor in (self.reader, self.editor):
            self.login(actor)
            before = self.snapshot()
            response = self.client.get(self.url())
            self.assertEqual(response.status_code, 200)
            self.assert_no_post(response)
            self.assertEqual(self.snapshot(), before)

    def test_get_configuration_does_not_call_initializer_or_any_saver(self):
        with patch.object(ConfiguracionAvanzadaEmpresa, "para_empresa", side_effect=AssertionError("GET seed")), \
             patch.object(self.model_admin(), "save_model", side_effect=AssertionError("GET save")), \
             patch.object(self.model_admin(), "save_related", side_effect=AssertionError("GET M2M")):
            self.assertEqual(self.client.get(self.url()).status_code, 200)
            self.remove_configuration()
            self.assertEqual(self.client.get(self.url()).status_code, 200)

    def test_native_form_has_all_fourteen_fields_fixed_company_and_no_global_related_crud_controls(self):
        response = self.client.get(self.url())
        form = response.context["form"]
        self.assertEqual(set(form.fields), set(self.FIELDS))
        markup = _AdvancedMarkup(response.content.decode())
        names = [attrs["name"] for _, attrs in markup.fields]
        self.assertEqual(set(names)-{"app", "csrfmiddlewaretoken"}, set(self.FIELDS))
        for name in self.FIELDS:
            if name != "modulos_adicionales_visibles_clinica":
                self.assertEqual(names.count(name), 1)
        company = next(attrs for _, attrs in markup.fields if attrs["name"] == "empresa")
        self.assertEqual(company.get("type"), "hidden")
        self.assertEqual(company.get("value"), str(self.empresa.pk))
        self.assertEqual(names.count("app"), 1)
        self.assertEqual(names.count("csrfmiddlewaretoken"), 1)
        self.assertNotContains(response, 'class="related-widget-wrapper-link')
        self.assertNotContains(response, "_popup")
        self.assertNotContains(response, 'name="fecha_actualizacion"')

    def test_m2m_catalog_is_original_all_modules_including_noncommercial_and_other_company_usage(self):
        response = self.client.get(self.url())
        form = response.context["form"]
        choices = set(form.fields["modulos_adicionales_visibles_clinica"].queryset.values_list("pk", flat=True))
        self.assertEqual(choices, set(Modulo.objects.values_list("pk", flat=True)))
        self.assertIn(self.private.pk, choices)
        self.assertIn(self.private.pk, form["modulos_adicionales_visibles_clinica"].value())

    def test_readonly_view_displays_existing_values_but_has_no_post_or_admin_action_links(self):
        self.login(self.reader)
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.configuration.notas)
        self.assertContains(response, self.configuration.bodega_venta_predeterminada)
        self.assertContains(response, self.private.nombre)
        self.assert_no_post(response)
        self.assertNotContains(response, self.admin_url())
        self.assertNotContains(response, "<table")

    def test_core_presenters_are_get_head_only_and_admin_app_rejects_other_verbs(self):
        for url in (self.url(), self.url(picker=True)):
            for method in ("post", "put", "patch", "delete", "options", "trace"):
                self.denied(url, method=method, data=self.data())
        for method in ("put", "patch", "delete", "options", "trace"):
            self.denied(self.admin_url(), method=method)

    def test_closed_query_duplicates_unknown_and_admin_override_metadata_are_denied(self):
        for url in (self.url(), self.url(picker=True), self.admin_url()):
            for key in ("app", "empresa"):
                values = parse_qs(urlsplit(url).query)
                if key in values:
                    bad = url+"&"+urlencode({key: values[key][0]})
                    self.denied(bad)
            for key in ("q", "page", "next", "_popup", "_to_field", "_saveasnew", "action", "ids"):
                self.denied(url+"&"+urlencode({key: "1"}))

    def test_canonical_presenter_and_admin_object_and_company_ids_prevent_lookup_fallback(self):
        path = reverse(self.PRESENTER, args=[self.empresa.pk])
        for value in ("0", "00"+str(self.empresa.pk), "2147483647", "9223372036854775808"):
            self.denied(path.replace(f"/{self.empresa.pk}/", f"/{value}/")+"?app=1")
        for value in ("0", "01", "١", "1e3", "NaN", "9223372036854775808"):
            query = urlencode({"app": "1", "empresa": value})
            self.denied(self.admin_url().split("?")[0]+"?"+query)
            path = reverse(self.EDIT, args=[value])
            self.denied(path+"?"+urlencode({"app": "1", "empresa": str(self.empresa.pk)}))

    def test_duplicate_scalar_fields_are_rejected_but_m2m_repeated_choices_are_legitimate(self):
        for field in ("app", *(field for field in self.FIELDS if field != "modulos_adicionales_visibles_clinica")):
            data = self.data()
            data[field] = [data.get(field, ""), data.get(field, "")]
            self.denied(self.admin_url(), method="post", data=data)
        response = self.client.post(self.admin_url(), self.data(modulos_adicionales_visibles_clinica=[str(self.module.pk), str(self.private.pk)]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(set(self.configuration.modulos_adicionales_visibles_clinica.values_list("pk", flat=True)), {self.module.pk, self.private.pk})

    def test_extra_body_admin_actions_financial_values_and_files_are_rejected_before_save(self):
        for field in ("_saveasnew", "_continue", "_addanother", "_popup", "_to_field", "action", "object_id",
                      "configuration_id", "fecha_actualizacion", "nombre", "activa", "is_superuser", "cuenta_financiera", "next"):
            self.denied(self.admin_url(), method="post", data=self.data(**{field: "1"}))
        self.denied(self.admin_url(), method="post", data=self.data(archivo=SimpleUploadedFile("no.txt", b"not accepted")))

    def test_post_company_cannot_be_retargeted_or_mismatched_to_route_query_or_configuration(self):
        other = self.empresas["medical_spa"]
        self.denied(self.admin_url(), method="post", data={**self.data(), "empresa": str(other.pk)})
        self.denied(self.admin_url(empresa=other, configuration=self.configuration), method="post", data=self.data(other))
        self.denied(self.admin_url(), method="post", data={**self.data(), "empresa": str(self.external.pk)})

    def test_malformed_m2m_and_company_ids_and_mismatched_app_never_reach_mutations(self):
        for value in ("-1", "01", "١", "Infinity", "1e3", "9223372036854775808"):
            self.denied(self.admin_url(), method="post", data={**self.data(), "empresa": value})
            self.denied(self.admin_url(), method="post", data=self.data(modulos_adicionales_visibles_clinica=[value]))
        self.denied(self.admin_url(app="0"), method="post", data=self.data())
        missing = self.data()
        missing.pop("app")
        self.denied(self.admin_url(), method="post", data=missing)

    def test_invalid_form_preserves_all_drafts_booleans_m2m_notes_and_fixed_identity_without_seeds(self):
        self.remove_configuration()
        before = self.snapshot()
        data = self.data(bodega_venta_predeterminada="x"*81, notas="No perder borrador",
            modulos_adicionales_visibles_clinica=[str(self.private.pk)], **{name: "on" for name in self.BOOLEAN_FIELDS})
        response = self.client.post(self.admin_url(adding=True), data)
        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertTrue(form.is_bound)
        self.assertIn("bodega_venta_predeterminada", form.errors)
        self.assertEqual(form.data["notas"], "No perder borrador")
        for name in self.BOOLEAN_FIELDS:
            self.assertEqual(form.data[name], "on")
        self.assertEqual(form.data.getlist("modulos_adicionales_visibles_clinica"), [str(self.private.pk)])
        self.assertEqual(form.data["empresa"], str(self.empresa.pk))
        self.assertEqual(self.snapshot(), before)

    def test_nonexistent_module_id_uses_original_modelchoice_error_without_writing(self):
        before = self.snapshot()
        response = self.client.post(self.admin_url(), self.data(modulos_adicionales_visibles_clinica=["2147483647"], notas="Trabajo intacto"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("modulos_adicionales_visibles_clinica", response.context["form"].errors)
        self.assertEqual(response.context["form"].data["notas"], "Trabajo intacto")
        self.assertEqual(self.snapshot(), before)

    def test_bodega_is_original_free_text_not_new_inventory_choice_or_initializer(self):
        response = self.client.post(self.admin_url(), self.data(bodega_venta_predeterminada="Bodega histórica sin catálogo físico"))
        self.assertEqual(response.status_code, 302)
        self.configuration.refresh_from_db()
        self.assertEqual(self.configuration.bodega_venta_predeterminada, "Bodega histórica sin catálogo físico")

    def test_original_all_boolean_values_and_full_notes_are_saved_without_new_financial_operations(self):
        notes = "Historia completa "*130
        data = self.data(notas=notes, **{field: "on" for field in self.BOOLEAN_FIELDS})
        response = self.client.post(self.admin_url(), data)
        self.assertEqual(response.status_code, 302)
        self.configuration.refresh_from_db()
        self.assertTrue(all(getattr(self.configuration, field) for field in self.BOOLEAN_FIELDS))
        self.assertEqual(self.configuration.notas, notes.strip())
        response = self.client.post(self.admin_url(), self.data(notas=""))
        self.assertEqual(response.status_code, 302)
        self.configuration.refresh_from_db()
        self.assertFalse(any(getattr(self.configuration, field) for field in self.BOOLEAN_FIELDS))

    def test_change_records_original_admin_log_and_global_audit_real_actor_owner_and_values(self):
        self.login(self.editor)
        response = self.client.post(self.admin_url(), self.data(usa_cierre_caja="on", notas="Cambio documentado QA"))
        self.assertEqual(response.status_code, 302)
        log = LogEntry.objects.get(object_id=str(self.configuration.pk), user=self.editor, action_flag=CHANGE)
        self.assertEqual(log.content_type.model, "configuracionavanzadaempresa")
        self.assertTrue(log.change_message)
        audit = RegistroAuditoria.objects.filter(modelo="configuracionavanzadaempresa", objeto_id=str(self.configuration.pk),
            usuario=self.editor, accion="modificar").latest("pk")
        self.assertEqual(audit.empresa_id, self.empresa.pk)
        self.assertEqual(audit.cambios["notas"], {"anterior": self.configuration.notas, "nuevo": "Cambio documentado QA"})
        self.assertEqual(audit.metodo_http, "POST")

    def test_add_records_original_log_and_global_audit_without_creating_business_catalogs(self):
        self.remove_configuration()
        self.login(self.creator)
        before = self.snapshot(exclude={"core.configuracionavanzadaempresa", "core.registroauditoria", "admin.logentry",
            "core.configuracionavanzadaempresa_modulos_adicionales_visibles_clinica"})
        response = self.client.post(self.admin_url(adding=True), self.data(usa_control_lotes_fefo="on"))
        self.assertEqual(response.status_code, 302)
        created = ConfiguracionAvanzadaEmpresa.objects.get(empresa=self.empresa)
        self.assertEqual(ConfiguracionAvanzadaEmpresa.objects.filter(empresa=self.empresa).count(), 1)
        self.assertTrue(LogEntry.objects.filter(user=self.creator, object_id=str(created.pk), action_flag=ADDITION).exists())
        self.assertTrue(RegistroAuditoria.objects.filter(usuario=self.creator, empresa=self.empresa,
            modelo="configuracionavanzadaempresa", objeto_id=str(created.pk), accion="crear").exists())
        self.assertEqual(self.snapshot(exclude={"core.configuracionavanzadaempresa", "core.registroauditoria", "admin.logentry",
            "core.configuracionavanzadaempresa_modulos_adicionales_visibles_clinica"}), before)

    def test_original_save_related_m2m_logs_rollback_atomically_on_failure_after_model_save(self):
        before = self.snapshot()
        with patch.object(self.model_admin(), "save_related", side_effect=RuntimeError("M2M failure QA")):
            with self.assertRaisesRegex(RuntimeError, "M2M failure QA"):
                self.client.post(self.admin_url(), self.data(notas="Debe deshacerse", modulos_adicionales_visibles_clinica=[str(self.module.pk)]))
        self.assertEqual(self.snapshot(), before)

    def test_original_log_failure_rolls_back_model_m2m_and_both_audits(self):
        before = self.snapshot()
        with patch.object(self.model_admin(), "log_change", side_effect=RuntimeError("Log failure QA")):
            with self.assertRaisesRegex(RuntimeError, "Log failure QA"):
                self.client.post(self.admin_url(), self.data(notas="No persistir", modulos_adicionales_visibles_clinica=[str(self.module.pk)]))
        self.assertEqual(self.snapshot(), before)

    def test_add_race_never_switches_to_change_and_preserves_original_one_to_one_error(self):
        self.remove_configuration()
        self.login(self.creator)
        self.assertEqual(self.client.get(self.url()).status_code, 200)
        existing = ConfiguracionAvanzadaEmpresa.objects.create(empresa=self.empresa, notas="Creación concurrente que no debe cambiar")
        before = self.snapshot()
        response = self.client.post(self.admin_url(adding=True), self.data(notas="No sobrescribir"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors)
        existing.refresh_from_db()
        self.assertEqual(existing.notas, "Creación concurrente que no debe cambiar")
        self.assertEqual(self.snapshot(), before)

    def test_writer_cannot_add_without_add_permission_or_edit_without_change_permission(self):
        self.denied(self.admin_url(adding=True), actor=self.editor, method="post", data=self.data())
        self.denied(self.admin_url(), actor=self.creator, method="post", data=self.data())
        self.remove_configuration()
        self.denied(self.admin_url(adding=True), actor=self.reader, method="post", data=self.data())

    def test_only_own_configuration_and_m2m_change_not_other_companies_users_roles_or_business(self):
        excluded = {"core.configuracionavanzadaempresa", "core.configuracionavanzadaempresa_modulos_adicionales_visibles_clinica",
            "core.registroauditoria", "admin.logentry"}
        before = self.snapshot(exclude=excluded)
        others = list(ConfiguracionAvanzadaEmpresa.objects.exclude(pk=self.configuration.pk).order_by("pk").values())
        response = self.client.post(self.admin_url(), self.data(notas="Cambio local", modulos_adicionales_visibles_clinica=[str(self.module.pk)]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.snapshot(exclude=excluded), before)
        self.assertEqual(list(ConfiguracionAvanzadaEmpresa.objects.exclude(pk=self.configuration.pk).order_by("pk").values()), others)

    def test_original_admin_save_pipeline_and_form_fields_remain_available_without_mobile_flag(self):
        request, _ = self.request(self.admin_url(mobile=False))
        form_class = self.model_admin().get_form(request, self.configuration, change=True)
        self.assertEqual(set(form_class.base_fields), set(self.FIELDS))
        web_data = self.data(notas="Original WEB QA", usa_cierre_caja="on", modulos_adicionales_visibles_clinica=[str(self.private.pk)])
        web_data.pop("app")
        response = self.client.post(self.admin_url(mobile=False), web_data)
        self.assertEqual(response.status_code, 302)
        self.configuration.refresh_from_db()
        self.assertEqual(self.configuration.notas, "Original WEB QA")
        self.assertTrue(self.configuration.usa_cierre_caja)
        self.assertTrue(LogEntry.objects.filter(user=self.supervisor, object_id=str(self.configuration.pk), action_flag=CHANGE).exists())

    def test_success_redirect_is_mobile_safe_without_granting_patient_calendar_or_global_admin_routes(self):
        self.login(self.editor)
        response = self.client.post(self.admin_url(), self.data(notas="Retorno seguro"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(parse_qs(urlsplit(response.url).query).get("app"), ["1"])
        followed = self.client.get(response.url)
        self.assertEqual(followed.status_code, 200)
        self.assertIn("no-store", followed["Cache-Control"])
        self.assertNotContains(followed, 'href="'+reverse("admin:index")+'"')
        self.assertNotContains(followed, 'href="'+reverse("agenda_mobile", args=[self.empresa.slug])+'#inicio"')

    def test_invalid_values_and_xss_keep_safe_original_identity_and_escape_metadata(self):
        data = self.data(bodega_venta_predeterminada="x"*81, notas='<img src=x onerror="alert(1)">')
        response = self.client.post(self.admin_url(), data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["form"].data["notas"], data["notas"])
        self.assertNotContains(response, data["notas"])
        self.assertNotContains(response, self.external_config.notas)

    def test_csrf_original_failure_has_no_business_writes_and_valid_token_saves(self):
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(self.supervisor)
        protected.get(self.url())
        before = self.snapshot(exclude={"sessions.session"})
        response = protected.post(self.admin_url(), self.data(notas="No debe guardar"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(urlsplit(response.url).path, reverse("public_access"))
        self.assertEqual(self.snapshot(exclude={"sessions.session"}), before)
        response = protected.post(self.admin_url(), self.data(notas="Token válido", csrfmiddlewaretoken=protected.cookies["csrftoken"].value))
        self.assertEqual(response.status_code, 302)
        self.configuration.refresh_from_db()
        self.assertEqual(self.configuration.notas, "Token válido")

    def test_corrupt_company_or_m2m_module_reference_is_denied_before_join_or_save(self):
        through = ConfiguracionAvanzadaEmpresa.modulos_adicionales_visibles_clinica.through
        row = through.objects.get(configuracionavanzadaempresa_id=self.configuration.pk, modulo_id=self.private.pk)
        with connection.constraint_checks_disabled():
            try:
                through.objects.filter(pk=row.pk).update(modulo_id=2147483647)
                self.denied()
                self.denied(self.admin_url(), method="post", data=self.data())
            finally:
                through.objects.filter(pk=row.pk).update(modulo_id=self.private.pk)
            try:
                ConfiguracionAvanzadaEmpresa.objects.filter(pk=self.configuration.pk).update(empresa_id=2147483647)
                self.denied(self.admin_url())
                self.denied(self.admin_url(), method="post", data=self.data())
            finally:
                ConfiguracionAvanzadaEmpresa.objects.filter(pk=self.configuration.pk).update(empresa_id=self.empresa.pk)

    def test_module_cards_and_clinical_configuration_do_not_invent_advanced_rights_or_enable_disabled_admin(self):
        from core.mobile_advanced_configuration import configuracion_avanzada_disponible_app
        for actor in (self.supervisor, self.reader, self.editor, self.creator, self.company_admin, self.nonstaff, self.inactive):
            request, _ = self.request(self.url(), actor=actor)
            capability = configuracion_avanzada_disponible_app(request, self.empresa)
            self.assertEqual(any(capability.values()), actor.pk in {self.supervisor.pk, self.reader.pk, self.editor.pk, self.creator.pk})
        request, _ = self.request(self.url())
        with override_settings(ENABLE_DJANGO_ADMIN=False):
            self.assertFalse(any(configuracion_avanzada_disponible_app(request, self.empresa).values()))

    def test_mobile_and_original_admin_save_have_equivalent_fields_m2m_and_audit_effects(self):
        data = self.data(notas="Paridad exacta QA", bodega_venta_predeterminada="Texto sin catálogo",
            modulos_adicionales_visibles_clinica=[str(self.private.pk), str(self.module.pk)],
            **{name: "on" for name in self.BOOLEAN_FIELDS})
        mobile = self.client.post(self.admin_url(), data)
        self.assertEqual(mobile.status_code, 302)
        original_data = {**data, "empresa": str(self.external.pk)}
        original_data.pop("app")
        web = self.client.post(self.admin_url(self.external, configuration=self.external_config, mobile=False), original_data)
        self.assertEqual(web.status_code, 302)
        self.configuration.refresh_from_db()
        self.external_config.refresh_from_db()
        for field in (*self.BOOLEAN_FIELDS, "bodega_venta_predeterminada", "notas"):
            self.assertEqual(getattr(self.configuration, field), getattr(self.external_config, field))
        self.assertEqual(set(self.configuration.modulos_adicionales_visibles_clinica.values_list("pk", flat=True)),
            set(self.external_config.modulos_adicionales_visibles_clinica.values_list("pk", flat=True)))
        for configuration in (self.configuration, self.external_config):
            self.assertTrue(LogEntry.objects.filter(user=self.supervisor, object_id=str(configuration.pk), action_flag=CHANGE).exists())
            self.assertTrue(RegistroAuditoria.objects.filter(usuario=self.supervisor, empresa_id=configuration.empresa_id,
                modelo="configuracionavanzadaempresa", objeto_id=str(configuration.pk), accion="modificar").exists())

    def test_disappearing_change_object_cannot_fall_through_to_global_admin_or_new_configuration(self):
        self.remove_configuration()
        self.denied(self.admin_url())
        self.denied(self.admin_url(), method="post", data=self.data())
        self.assertFalse(ConfiguracionAvanzadaEmpresa.objects.filter(empresa=self.empresa).exists())

    def test_app_object_delete_is_denied_before_lookup_for_local_foreign_and_missing_objects(self):
        before = self.snapshot()
        for object_id in (self.configuration.pk, self.external_config.pk, 2147483647):
            path = reverse("admin:core_configuracionavanzadaempresa_delete", args=[object_id])
            attempts = [(path+"?app=1", method, {"post": "yes"})
                for method in ("get", "head", "post", "put", "delete", "options")]
            attempts.append((path, "post", {"app": "1", "post": "yes"}))
            for url, method, data in attempts:
                with self.subTest(object_id=object_id, url=url, method=method):
                    request, match = self.request(url, method=method, data=data)
                    with patch.object(self.model_admin(), "get_queryset", side_effect=AssertionError("No delete lookup")):
                        with CaptureQueriesContext(connection) as queries:
                            with self.assertRaises(PermissionDenied):
                                match.func(request, **match.kwargs)
                    self.no_writes(queries)
        self.assertEqual(self.snapshot(), before)
        web = self.client.get(reverse("admin:core_configuracionavanzadaempresa_delete", args=[self.external_config.pk]))
        self.assertEqual(web.status_code, 200)
        self.assertTemplateUsed(web, "admin/delete_confirmation.html")
        self.assertContains(web, self.external.nombre)
        self.assertEqual(self.snapshot(), before)

    def test_app_changelist_and_bulk_delete_are_denied_before_global_query_or_any_mutation(self):
        path = reverse("admin:core_configuracionavanzadaempresa_changelist")
        before = self.snapshot()
        bulk = {"action": "delete_selected", "_selected_action": [str(self.configuration.pk), str(self.external_config.pk)],
            "post": "yes", "select_across": "1", "index": "0"}
        attempts = [(path+"?app=1", method, bulk) for method in ("get", "head", "post", "put", "delete")]
        attempts.append((path, "post", {**bulk, "app": "1"}))
        for url, method, data in attempts:
            with self.subTest(url=url, method=method):
                request, match = self.request(url, method=method, data=data)
                with patch.object(self.model_admin(), "get_queryset", side_effect=AssertionError("No global catalogue query")):
                    with CaptureQueriesContext(connection) as queries:
                        with self.assertRaises(PermissionDenied):
                            match.func(request, **match.kwargs)
                self.no_writes(queries)
        self.assertEqual(self.snapshot(), before)
        web = self.client.get(path)
        self.assertEqual(web.status_code, 200)
        self.assertTemplateUsed(web, "admin/change_list.html")
        self.assertContains(web, self.external.nombre)
        self.assertEqual(self.snapshot(), before)

    def test_app_global_object_history_is_denied_without_foreign_data_or_lookup_and_web_is_unchanged(self):
        before = self.snapshot()
        for object_id in (self.configuration.pk, self.external_config.pk, 2147483647):
            path = reverse("admin:core_configuracionavanzadaempresa_history", args=[object_id])
            attempts = [(path+"?app=1", method, {}) for method in ("get", "head", "post")]
            attempts.append((path, "post", {"app": "1"}))
            for url, method, data in attempts:
                with self.subTest(object_id=object_id, url=url, method=method):
                    request, match = self.request(url, method=method, data=data)
                    with patch.object(self.model_admin(), "get_queryset", side_effect=AssertionError("No history object query")):
                        with CaptureQueriesContext(connection) as queries:
                            with self.assertRaises(PermissionDenied):
                                match.func(request, **match.kwargs)
                    self.no_writes(queries)
        self.assertEqual(self.snapshot(), before)
        web = self.client.get(reverse("admin:core_configuracionavanzadaempresa_history", args=[self.external_config.pk]))
        self.assertEqual(web.status_code, 200)
        self.assertTemplateUsed(web, "admin/object_history.html")
        self.assertContains(web, self.external.nombre)
        self.assertEqual(self.snapshot(), before)

    def test_advanced_cards_send_add_only_to_picker_and_view_or_change_to_company_presenter_without_seeds(self):
        from clinica.mobile_configuration import render_configuracion_hub_app
        from crm.mobile_modules import construir_contexto_modulos_app
        from core.mobile_advanced_configuration import configuracion_avanzada_disponible_app

        # Membership only lets the presentation registry run; no new ERP role,
        # operational module or Django permission is granted by these helpers.
        for actor in (self.creator, self.reader, self.editor):
            actor.empresas_acceso.add(self.empresa)
        before = self.snapshot()
        picker = self.url(picker=True)
        presenter = self.url()
        for actor in (self.creator, self.reader, self.editor):
            with self.subTest(actor=actor.username):
                request, _ = self.request(self.url(), actor=actor)
                with CaptureQueriesContext(connection) as capability_queries:
                    capabilities = configuracion_avanzada_disponible_app(request, self.empresa)
                self.no_writes(capability_queries)
                self.assertFalse(any('"core_configuracionavanzadaempresa"' in query["sql"].lower()
                    for query in capability_queries))
                self.assertEqual(capabilities["ver"], actor.pk != self.creator.pk)
                expected = picker if actor.pk == self.creator.pk else presenter
                with patch.object(ConfiguracionAvanzadaEmpresa, "para_empresa", side_effect=AssertionError("No configuration initializer")):
                    with CaptureQueriesContext(connection) as queries:
                        modules = construir_contexto_modulos_app(request, self.empresa)
                        hub = render_configuracion_hub_app(request, self.empresa)
                self.no_writes(queries)
                module = next(item for item in modules["modulos_app"] if item["key"] == "configuracion_avanzada")
                self.assertEqual(module["url"], expected)
                self.assertEqual(module["estado"], "disponible")
                self.assertContains(hub, 'href="'+expected+'"')
                if actor.pk == self.creator.pk:
                    self.assertNotContains(hub, 'href="'+presenter+'"')
        self.assertEqual(self.snapshot(), before)
