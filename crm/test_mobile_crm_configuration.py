"""Configuración CRM móvil sobre el formulario y guardado originales.

Sólo datos sintéticos y la base temporal del runner Django. GET nunca crea;
POST conserva deliberadamente get_or_create, incluso con un formulario inválido.
"""
from datetime import timedelta
from html.parser import HTMLParser
import json
from urllib.parse import urlencode, urlsplit
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.http import Http404
from django.test import Client, RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import resolve, reverse
from django.utils import timezone

from contabilidad.models import CuentaContable
from core.models import (
    ConfiguracionAvanzadaEmpresa, Empresa, EmpresaModulo, Modulo, RegistroAuditoria,
    RolSistema, Usuario, UsuarioEmpresaPermiso,
)
from facturacion.models import InventarioProducto, Producto
from .forms import ConfiguracionCRMForm
from .models import ConfiguracionCRM


class _CrmConfigurationMarkup(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.in_form = False
        self.fields = []
        self.forms = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.in_form = attrs.get("method", "get").lower() == "post"
            if self.in_form:
                self.forms.append(attrs)
        if self.in_form and tag in {"input", "select", "textarea", "button"} and attrs.get("name"):
            self.fields.append((tag, attrs))

    def handle_endtag(self, tag):
        if tag == "form":
            self.in_form = False


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class MobileCrmConfigurationTests(TestCase):
    SLUGS = ("hospital_mia", "medical_spa", "luque_aestetic", "serviciosmedicos")
    FIELDS = (
        "whatsapp_activo", "whatsapp_api_version", "whatsapp_phone_number_id",
        "whatsapp_business_account_id", "whatsapp_numero_prueba", "whatsapp_plantilla_prueba",
        "whatsapp_idioma_plantilla", "whatsapp_plantilla_marketing", "whatsapp_idioma_marketing",
        "whatsapp_plantilla_cita", "whatsapp_idioma_cita", "whatsapp_cita_incluir_enlace",
        "mensaje_cita_confirmacion", "mensaje_cita_recordatorio_7_dias", "mensaje_cita_recordatorio_3_dias",
        "mensaje_cita_recordatorio_1_dia", "mensaje_cita_cancelada", "mensaje_cita_reagendada",
        "whatsapp_plantilla_preconsulta", "whatsapp_idioma_preconsulta", "remitente_correo",
        "recordatorio_cumpleanos_activo", "cumpleanos_recordatorio_1_dia", "cumpleanos_recordatorio_7_dias",
        "recordatorio_citas_activo", "dias_alerta_producto",
    )
    BOOLEAN_FIELDS = (
        "whatsapp_activo", "whatsapp_cita_incluir_enlace", "recordatorio_cumpleanos_activo",
        "cumpleanos_recordatorio_1_dia", "cumpleanos_recordatorio_7_dias", "recordatorio_citas_activo",
    )
    TEMPLATE = "crm/mobile_configuration_form.html"

    @classmethod
    def setUpTestData(cls):
        cls.empresas = {slug: Empresa.objects.create(nombre=f"Agenda configuración QA {slug}", slug=slug,
            rtn=f"CRM-CONFIG-QA-{index}", tipo_solucion="clinica", estado_licencia="activa")
            for index, slug in enumerate(cls.SLUGS, 1)}
        cls.empresa = cls.empresas["hospital_mia"]
        cls.external = Empresa.objects.create(nombre="Empresa ajena CRM QA", slug="external-crm-configuration-qa",
            rtn="CRM-CONFIG-QA-EXT", tipo_solucion="clinica", estado_licencia="activa")
        cls.modulo, _ = Modulo.objects.get_or_create(codigo="crm_marketing", defaults={"nombre": "CRM y Agenda"})
        for empresa in (*cls.empresas.values(), cls.external):
            EmpresaModulo.objects.create(empresa=empresa, modulo=cls.modulo, activo=True)
        cls.configs = {slug: ConfiguracionCRM.objects.create(empresa=empresa, whatsapp_activo=True,
            whatsapp_token=f"SYNTHETIC-PRIVATE-CRM-{slug}\r\n ", whatsapp_numero_conectado="+504 0000 0000",
            whatsapp_numero_prueba="50400000000",
            whatsapp_phone_number_id=f"phone-qa-{index}", whatsapp_business_account_id=f"waba-qa-{index}")
            for index, (slug, empresa) in enumerate(cls.empresas.items(), 1)}
        cls.configuration = cls.configs[cls.empresa.slug]
        cls.external_configuration = ConfiguracionCRM.objects.create(empresa=cls.external,
            whatsapp_token="SYNTHETIC-FOREIGN-CRM-SECRET", mensaje_cita_confirmacion="NO MOSTRAR EMPRESA AJENA QA")
        cls.roles = {name: RolSistema.objects.create(nombre=f"Configuración CRM {name} QA", codigo=f"crm-config-{name}-qa",
            es_rol_clinico=True, usa_permisos_clinicos_granulares=name == "granular", puede_configuracion_crm=True)
            for name in ("legacy", "granular")}
        cls.users = {name: Usuario.objects.create_user(username=f"crm-config-{name}-qa", password="test-only",
            empresa=cls.empresa, rol_sistema=role) for name, role in cls.roles.items()}
        for user in cls.users.values():
            user.empresas_acceso.add(*cls.empresas.values())
        cls.supervisor = Usuario.objects.create_superuser(username="crm-config-super-qa", password="test-only",
            email="super@example.invalid", empresa=cls.external)
        cls.company_admin = Usuario.objects.create_user(username="crm-config-company-admin-qa", password="test-only",
            empresa=cls.empresa, es_administrador_empresa=True)
        cls.company_admin.empresas_acceso.add(*cls.empresas.values())
        cls.no_permission = Usuario.objects.create_user(username="crm-config-none-qa", password="test-only", empresa=cls.empresa)
        cls.foreign_user = Usuario.objects.create_user(username="crm-config-foreign-qa", password="test-only",
            empresa=cls.external, rol_sistema=cls.roles["granular"])
        cls.clinic_role = RolSistema.objects.create(nombre="Sólo clínica no CRM QA", codigo="crm-config-clinic-only-qa",
            es_rol_clinico=True, usa_permisos_clinicos_granulares=True, puede_configuracion_clinica=True,
            puede_ver_calendario=True, puede_inventario=True)
        cls.clinic_user = Usuario.objects.create_user(username="crm-config-clinic-only-qa", password="test-only",
            empresa=cls.empresa, rol_sistema=cls.clinic_role)
        cls.django_admin = Usuario.objects.create_user(username="crm-config-django-only-qa", password="test-only",
            empresa=cls.empresa, is_staff=True)
        content_type = ContentType.objects.get_for_model(ConfiguracionAvanzadaEmpresa)
        cls.django_admin.user_permissions.add(Permission.objects.get(content_type=content_type,
            codename="change_configuracionavanzadaempresa"))
        for index, empresa in enumerate((*cls.empresas.values(), cls.external), 1):
            product = Producto.objects.create(empresa=empresa, codigo=f"CRM-CONFIG-STOCK-{index}",
                nombre=f"Existencia intacta CRM QA {index}", precio="19.75", costo_promedio="5.1250")
            InventarioProducto.objects.create(empresa=empresa, producto=product, existencias="23.00", stock_minimo="4.00")
            CuentaContable.objects.create(empresa=empresa, codigo=f"CRM-CFG-ACCT-{index}", nombre=f"Cuenta intacta CRM {index}", tipo="activo")

    def setUp(self):
        self.client.force_login(self.users["granular"])
        for target in ("crm.views.urlopen", "crm.views.enviar_plantilla_whatsapp", "crm.services._post_whatsapp",
                       "crm.services._post_multipart", "django.core.mail.EmailMessage.send",
                       "core.views.enviar_correo_acceso", "core.views.emitir_token_acceso",
                       "facturacion.views.enviar_documento_whatsapp"):
            mocked = patch(target, side_effect=AssertionError("No OAuth, mensajes ni transportes reales"))
            transport = mocked.start()
            self.addCleanup(mocked.stop)
            self.addCleanup(transport.assert_not_called)

    def url(self, empresa=None, *, mobile=True, route="crm_configuracion", **params):
        path = reverse(route, args=[(empresa or self.empresa).slug])
        query = {"app": "1"} if mobile else {}
        query.update(params)
        return path+("?"+urlencode(query, doseq=True) if query else "")

    def data(self, empresa=None, **updates):
        empresa = empresa or self.empresa
        instance = self.configs.get(empresa.slug) or ConfiguracionCRM(empresa=empresa)
        data = {"app": "1"}
        for field in self.FIELDS:
            value = getattr(instance, field)
            if field in self.BOOLEAN_FIELDS:
                if value:
                    data[field] = "on"
            else:
                data[field] = str(value) if value is not None else ""
        data.update(updates)
        return data

    def request(self, url, *, actor=None, method="get", data=None, json_body=None):
        factory = RequestFactory()
        if json_body is not None:
            request = factory.post(url, json.dumps(json_body), content_type="application/json")
        else:
            request = factory.post(url, data or {}) if method == "post" else factory.generic(method.upper(), url)
        request.user = Usuario.objects.get(pk=(actor or self.users["granular"]).pk)
        request.session = {}
        request._messages = FallbackStorage(request)
        request._dont_enforce_csrf_checks = True  # Real CSRF is tested separately.
        match = resolve(urlsplit(url).path)
        request.resolver_match = match
        return request, match

    def direct(self, url=None, **kwargs):
        request, match = self.request(url or self.url(), **kwargs)
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
            self.assertIn(response.status_code, (403, 404, 405))
        self.assertEqual(self.snapshot(), before)

    def no_writes(self, queries):
        self.assertFalse(any(row["sql"].lstrip().upper().startswith(("INSERT ", "UPDATE ", "DELETE ", "REPLACE ")) for row in queries))

    def remove_configuration(self, empresa=None):
        ConfiguracionCRM.objects.filter(empresa=empresa or self.empresa).delete()

    def test_four_clinics_and_both_role_types_use_mobile_without_replacing_web(self):
        for user in self.users.values():
            self.client.force_login(user)
            for empresa in self.empresas.values():
                response = self.client.get(self.url(empresa))
                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, self.TEMPLATE)
                web = self.client.get(self.url(empresa, mobile=False))
                self.assertEqual(web.status_code, 200)
                self.assertTemplateUsed(web, "crm/form.html")
                self.assertTemplateNotUsed(web, self.TEMPLATE)

    def test_web_form_class_keeps_all_fields_without_rendering_saved_token(self):
        form = ConfiguracionCRMForm(instance=self.configuration)
        self.assertEqual(set(form.fields), {*self.FIELDS, "whatsapp_token"})
        self.assertEqual(form.fields["whatsapp_token"].widget.input_type, "password")
        self.assertFalse(form.fields["whatsapp_token"].widget.render_value)
        self.assertNotIn(self.configuration.whatsapp_token, str(form["whatsapp_token"]))
        response = self.client.get(self.url(mobile=False))
        self.assertIn("whatsapp_token", response.context["form"].fields)

    def test_logout_redirects_without_seeding_missing_configuration(self):
        self.remove_configuration()
        self.client.logout()
        before = self.snapshot()
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 302)
        self.assertIn("next=", response.url)
        self.assertEqual(self.snapshot(), before)

    def test_company_must_be_active_even_for_super_and_company_admin(self):
        Empresa.objects.filter(pk=self.empresa.pk).update(activa=False)
        for actor in (*self.users.values(), self.supervisor, self.company_admin):
            self.denied(actor=actor)
            self.denied(actor=actor, method="post", data=self.data())

    def test_crm_module_is_required_even_for_super_and_not_replaced_by_clinical_or_agenda_modules(self):
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.modulo).update(activo=False)
        for code in ("clinica_medica", "agenda_citas", "facturacion"):
            module, _ = Modulo.objects.get_or_create(codigo=code, defaults={"nombre": code})
            EmpresaModulo.objects.update_or_create(empresa=self.empresa, modulo=module, defaults={"activo": True})
        for actor in (*self.users.values(), self.supervisor, self.company_admin):
            self.denied(actor=actor)
            self.denied(actor=actor, method="post", data=self.data())

    def test_license_guard_preserves_original_super_exception_and_company_admin_is_not_exempt(self):
        for state in ("suspendida", "vencida"):
            Empresa.objects.filter(pk=self.empresa.pk).update(estado_licencia=state)
            for actor in (*self.users.values(), self.company_admin):
                self.denied(actor=actor)
                self.denied(actor=actor, method="post", data=self.data())
            self.client.force_login(self.supervisor)
            before = self.snapshot()
            self.assertEqual(self.client.get(self.url()).status_code, 200)
            self.assertEqual(self.snapshot(), before)

    def test_expired_active_license_uses_original_actual_expiry_not_only_status_string(self):
        Empresa.objects.filter(pk=self.empresa.pk).update(estado_licencia="activa",
            fecha_vencimiento_plan=timezone.localdate()-timedelta(days=1))
        self.denied()
        self.denied(actor=self.company_admin)
        self.client.force_login(self.supervisor)
        self.assertEqual(self.client.get(self.url()).status_code, 200)

    def test_membership_is_required_for_company_admin_and_roles_but_super_preserves_global_access(self):
        self.denied(actor=self.foreign_user)
        Usuario.objects.filter(pk=self.foreign_user.pk).update(es_administrador_empresa=True)
        self.denied(actor=self.foreign_user, method="post", data=self.data())
        self.client.force_login(self.supervisor)
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)

    def test_clinical_configuration_inventory_calendar_and_django_admin_rights_do_not_grant_crm_configuration(self):
        for actor in (self.no_permission, self.clinic_user, self.django_admin):
            self.denied(actor=actor)
            self.denied(actor=actor, method="post", data=self.data())

    def test_original_company_admin_permission_bypass_remains_legitimate_without_any_role(self):
        self.client.force_login(self.company_admin)
        self.assertIsNone(self.company_admin.rol_sistema_id)
        self.assertEqual(self.client.get(self.url()).status_code, 200)
        response = self.client.post(self.url(), self.data(mensaje_cita_confirmacion="Admin legítimo QA"))
        self.assertEqual(response.status_code, 302)
        self.configuration.refresh_from_db()
        self.assertEqual(self.configuration.mensaje_cita_confirmacion, "Admin legítimo QA")

    def test_local_company_role_override_and_null_override_are_authoritative(self):
        actor = self.users["granular"]
        permission = UsuarioEmpresaPermiso.objects.create(usuario=actor, empresa=self.empresa, rol_sistema=self.clinic_role)
        self.denied(actor=actor)
        UsuarioEmpresaPermiso.objects.filter(pk=permission.pk).update(rol_sistema_id=None)
        self.denied(actor=actor, method="post", data=self.data())
        UsuarioEmpresaPermiso.objects.filter(pk=permission.pk).update(rol_sistema=self.roles["legacy"])
        self.client.force_login(actor)
        self.assertEqual(self.client.get(self.url()).status_code, 200)

    def test_inactive_role_does_not_authorize_configuration_for_either_role_type(self):
        for name, role in self.roles.items():
            RolSistema.objects.filter(pk=role.pk).update(activo=False)
            self.denied(actor=self.users[name])
            self.denied(actor=self.users[name], method="post", data=self.data())

    def test_app_scope_is_exact_four_clinical_profiles_before_original_getter(self):
        with patch("crm.views._configuracion_crm", side_effect=AssertionError("No getter outside app scope")):
            self.denied(self.url(self.external), actor=self.supervisor)
            self.denied(self.url(self.external), actor=self.supervisor, method="post", data=self.data(self.external))
            Empresa.objects.filter(pk=self.empresa.pk).update(tipo_solucion="erp")
            self.denied(actor=self.supervisor)
            self.denied(actor=self.supervisor, method="post", data=self.data())

    def test_get_head_existing_configuration_has_no_writes_in_any_model_or_through_table(self):
        before = self.snapshot()
        for method in ("get", "head"):
            for empresa in self.empresas.values():
                with CaptureQueriesContext(connection) as queries:
                    response = getattr(self.client, method)(self.url(empresa))
                self.assertEqual(response.status_code, 200)
                self.no_writes(queries)
                self.assertIn("no-store", response["Cache-Control"])
                if method == "head":
                    self.assertEqual(response.content, b"")
        self.assertEqual(self.snapshot(), before)

    def test_get_head_missing_configuration_uses_unsaved_defaults_without_get_or_create(self):
        self.remove_configuration()
        before = self.snapshot()
        with patch("crm.views._configuracion_crm", side_effect=AssertionError("GET must not initialize CRM")):
            for method in ("get", "head"):
                with CaptureQueriesContext(connection) as queries:
                    response = getattr(self.client, method)(self.url())
                self.assertEqual(response.status_code, 200)
                self.assertIsNone(response.context["form"].instance.pk)
                self.assertFalse(response.context["configuracion_existe"])
                self.assertEqual(response.context["form"]["whatsapp_api_version"].value(), "v25.0")
                self.no_writes(queries)
        self.assertEqual(self.snapshot(), before)

    def test_get_with_missing_content_type_never_recreates_admin_metadata_or_configuration(self):
        self.remove_configuration()
        ContentType.objects.filter(app_label="crm", model="configuracioncrm").delete()
        ContentType.objects.clear_cache()
        self.addCleanup(ContentType.objects.clear_cache)
        self.client.force_login(self.supervisor)
        before = self.snapshot()
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        self.no_writes(queries)
        self.assertEqual(self.snapshot(), before)

    def test_native_mobile_form_has_exact_twenty_six_original_fields_and_only_app_csrf_metadata(self):
        response = self.client.get(self.url())
        form = response.context["form"]
        markup = _CrmConfigurationMarkup(response.content.decode())
        self.assertEqual(len(markup.forms), 1)
        self.assertEqual(markup.forms[0]["action"], self.url())
        names = [attrs["name"] for _, attrs in markup.fields]
        self.assertEqual(set(names), {*self.FIELDS, "app", "csrfmiddlewaretoken"})
        for name in names:
            self.assertEqual(names.count(name), 1, name)
        self.assertEqual(set(form.fields), set(self.FIELDS))
        self.assertEqual(len(response.context["mobile_crm_sections"]), 6)
        self.assertEqual(len([field.name for section in response.context["mobile_crm_sections"] for field in section["campos"]]), 26)
        self.assertEqual({field.name for section in response.context["mobile_crm_sections"] for field in section["campos"]}, set(self.FIELDS))
        original = ConfiguracionCRMForm(instance=self.configuration)
        for name in self.FIELDS:
            self.assertEqual(form.fields[name].required, original.fields[name].required)
            self.assertEqual(form.fields[name].validators, original.fields[name].validators)
            self.assertEqual(type(form.fields[name].widget), type(original.fields[name].widget))

    def test_token_is_removed_from_instance_fields_and_initial_without_mutating_base_form(self):
        response = self.client.get(self.url())
        form = response.context["form"]
        self.assertNotIn("whatsapp_token", form.fields)
        self.assertNotIn("whatsapp_token", form.initial)
        self.assertEqual(form.instance.whatsapp_token, self.configuration.whatsapp_token)
        self.assertIn("whatsapp_token", ConfiguracionCRMForm.base_fields)
        self.assertIn("whatsapp_token", ConfiguracionCRMForm(instance=self.configuration).initial)

    @override_settings(META_WHATSAPP_APP_SECRET="SYNTHETIC-GLOBAL-SECRET-DO-NOT-RENDER",
                       META_WHATSAPP_APP_ID="SYNTHETIC-GLOBAL-APP", META_WHATSAPP_CONFIG_ID="SYNTHETIC-GLOBAL-CONFIG")
    def test_html_and_serializable_initial_never_expose_saved_token_or_global_meta_secret(self):
        response = self.client.get(self.url())
        self.assertNotContains(response, self.configuration.whatsapp_token.strip())
        self.assertNotContains(response, self.external_configuration.whatsapp_token)
        self.assertNotContains(response, "SYNTHETIC-GLOBAL-SECRET-DO-NOT-RENDER")
        self.assertNotIn("whatsapp_token", json.dumps(response.context["form"].initial, default=str))
        self.assertTrue(response.context["mobile_crm_token_present"])
        contexts = response.context if isinstance(response.context, list) else [response.context]
        for key in ("meta_whatsapp_app_id", "meta_whatsapp_config_id", "meta_whatsapp_ready"):
            self.assertTrue(all(key not in context.flatten() for context in contexts))
        self.assertNotContains(response, 'name="whatsapp_token"')

    def test_none_empty_whitespace_and_literal_token_are_preserved_exactly_after_app_save(self):
        for token in (None, "", "   ", "  SYNTHETIC-TOKEN-LITERAL\r\n  "):
            ConfiguracionCRM.objects.filter(pk=self.configuration.pk).update(whatsapp_token=token)
            response = self.client.get(self.url())
            self.assertEqual(response.context["mobile_crm_token_present"], bool(token))
            response = self.client.post(self.url(), self.data(mensaje_cita_confirmacion="Sólo texto CRM QA"))
            self.assertEqual(response.status_code, 302)
            stored = ConfiguracionCRM.objects.get(pk=self.configuration.pk)
            self.assertEqual(stored.whatsapp_token, token)

    def test_posting_secret_field_is_denied_before_getter_or_default_creation(self):
        self.remove_configuration()
        with patch("crm.views._configuracion_crm", side_effect=AssertionError("No initializer for token payload")):
            for token in ("", "SYNTHETIC-PAYLOAD-SECRET"):
                self.denied(method="post", data=self.data(whatsapp_token=token))
        self.assertFalse(ConfiguracionCRM.objects.filter(empresa=self.empresa).exists())

    def test_secret_initial_clear_json_mode_and_foreign_metadata_are_denied_before_getter(self):
        self.remove_configuration()
        with patch("crm.views._configuracion_crm", side_effect=AssertionError("No metadata initializer")):
            for key in ("initial-whatsapp_token", "whatsapp_token-clear", "raw_mode", "mode", "empresa", "empresa_id",
                        "config_id", "configuracion_id", "whatsapp_numero_conectado", "next", "_saveasnew"):
                self.denied(method="post", data={**self.data(), key: "1"})

    def test_query_is_closed_unique_and_app_is_required_without_foreign_selection(self):
        with patch("crm.views._configuracion_crm", side_effect=AssertionError("No getter for malformed query")):
            for key in ("q", "page", "empresa", "config", "config_id", "token", "next", "editar"):
                self.denied(self.url()+"&"+urlencode({key: "1"}))
            self.denied(self.url(app=["1", "1"]))
            self.denied(self.url(app=["0", "1"]))

    def test_all_scalar_post_duplicates_are_denied_before_original_initializer(self):
        self.remove_configuration()
        with patch("crm.views._configuracion_crm", side_effect=AssertionError("No getter for duplicates")):
            for key in (*self.FIELDS, "app", "csrfmiddlewaretoken"):
                values = self.data()
                values[key] = [values.get(key, ""), values.get(key, "")]
                self.denied(method="post", data=values)

    def test_files_are_denied_before_original_initializer_or_any_storage_write(self):
        self.remove_configuration()
        with patch("crm.views._configuracion_crm", side_effect=AssertionError("No getter for files")):
            self.denied(method="post", data=self.data(archivo=SimpleUploadedFile("qa.txt", b"synthetic only")))

    def test_app_configuration_rejects_non_get_head_post_verbs_without_business_queries_or_writes(self):
        self.remove_configuration()
        for method in ("put", "patch", "delete", "options", "trace"):
            with patch("crm.views._configuracion_crm", side_effect=AssertionError("No method initializer")):
                self.denied(method=method)

    def test_query_body_app_mismatch_and_missing_app_are_denied_before_creation(self):
        self.remove_configuration()
        with patch("crm.views._configuracion_crm", side_effect=AssertionError("No getter for inconsistent mode")):
            self.denied(self.url(app="0"), method="post", data=self.data())
            self.denied(self.url(mobile=False), method="post", data=self.data())
            without_app = self.data()
            without_app.pop("app")
            self.denied(method="post", data=without_app)

    def test_invalid_existing_form_preserves_all_twenty_six_values_without_writes(self):
        data = self.data(whatsapp_api_version="v"*21, remitente_correo="not-an-email", dias_alerta_producto="NaN",
            mensaje_cita_confirmacion="No perder texto\nCRM", whatsapp_plantilla_marketing="plantilla_borrador",
            **{field: "on" for field in self.BOOLEAN_FIELDS})
        before = self.snapshot()
        with CaptureQueriesContext(connection) as queries:
            response = self.client.post(self.url(), data)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, self.TEMPLATE)
        form = response.context["form"]
        self.assertTrue(form.is_bound)
        self.assertEqual(set(form.fields), set(self.FIELDS))
        for key, value in data.items():
            self.assertEqual(form.data[key], value)
        self.assertTrue({"whatsapp_api_version", "remitente_correo", "dias_alerta_producto"} <= set(form.errors))
        self.assertNotIn("whatsapp_token", form.initial)
        self.no_writes(queries)
        self.assertEqual(self.snapshot(), before)

    def test_invalid_numeric_and_required_values_use_original_validation_not_server_errors(self):
        for key, values in (("dias_alerta_producto", ("-1", "NaN", "Infinity", "1e3", "")),
                            ("whatsapp_idioma_cita", ("", "x"*13))):
            for value in values:
                before = self.snapshot()
                response = self.client.post(self.url(), self.data(**{key: value}))
                self.assertEqual(response.status_code, 200)
                self.assertIn(key, response.context["form"].errors)
                self.assertEqual(response.context["form"].data[key], value)
                self.assertEqual(self.snapshot(), before)

    def test_invalid_missing_post_deliberately_creates_only_original_default_configuration_and_audit(self):
        self.remove_configuration()
        excluded = {"crm.configuracioncrm", "core.registroauditoria"}
        before = self.snapshot(exclude=excluded)
        existing_audits = set(RegistroAuditoria.objects.values_list("pk", flat=True))
        response = self.client.post(self.url(), self.data(whatsapp_api_version="x"*21, mensaje_cita_confirmacion="Borrador no persistido"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].is_bound)
        stored = ConfiguracionCRM.objects.get(empresa=self.empresa)
        defaults = ConfiguracionCRM(empresa=self.empresa)
        for key in (*self.FIELDS, "whatsapp_token", "whatsapp_numero_conectado"):
            self.assertEqual(getattr(stored, key), getattr(defaults, key))
        added_audits = RegistroAuditoria.objects.exclude(pk__in=existing_audits)
        self.assertEqual(added_audits.count(), 1)
        self.assertTrue(added_audits.filter(modelo="configuracioncrm", accion="crear", empresa=self.empresa,
            usuario=self.users["granular"], metodo_http="POST").exists())
        self.assertEqual(self.snapshot(exclude=excluded), before)

    def test_missing_invalid_post_matches_original_web_get_or_create_and_default_audit_effects(self):
        first, second = self.empresa, self.empresas["medical_spa"]
        self.remove_configuration(first)
        self.remove_configuration(second)
        mobile = self.client.post(self.url(first), self.data(first, whatsapp_api_version="x"*21))
        web_data = self.data(second, whatsapp_api_version="x"*21, whatsapp_token="SYNTHETIC-WEB-DRAFT")
        web_data.pop("app")
        web = self.client.post(self.url(second, mobile=False), web_data)
        self.assertEqual(mobile.status_code, 200)
        self.assertEqual(web.status_code, 200)
        self.assertTemplateUsed(web, "crm/form.html")
        a, b = ConfiguracionCRM.objects.get(empresa=first), ConfiguracionCRM.objects.get(empresa=second)
        for field in (*self.FIELDS, "whatsapp_token"):
            self.assertEqual(getattr(a, field), getattr(b, field))
        for stored in (a, b):
            self.assertTrue(RegistroAuditoria.objects.filter(modelo="configuracioncrm", objeto_id=str(stored.pk), accion="crear").exists())

    def test_valid_missing_post_uses_original_initializer_and_form_save_not_a_parallel_saver(self):
        self.remove_configuration()
        from . import views
        original_getter = views._configuracion_crm
        with patch("crm.views._configuracion_crm", wraps=original_getter) as getter:
            response = self.client.post(self.url(), self.data(mensaje_cita_confirmacion="Creación original QA", whatsapp_activo=""))
        self.assertEqual(response.status_code, 302)
        getter.assert_called_once()
        stored = ConfiguracionCRM.objects.get(empresa=self.empresa)
        self.assertEqual(stored.mensaje_cita_confirmacion, "Creación original QA")
        self.assertIsNone(stored.whatsapp_token)
        self.assertFalse(stored.whatsapp_activo)
        self.assertTrue(stored.whatsapp_pendiente_validacion)
        self.assertEqual(ConfiguracionCRM.objects.filter(empresa=self.empresa).count(), 1)

    def test_valid_save_preserves_original_twenty_six_field_values_booleans_and_trimming(self):
        data = self.data(mensaje_cita_confirmacion="  Texto original con espacios  ", remitente_correo="qa@example.invalid",
            dias_alerta_producto="0", whatsapp_phone_number_id="  phone original QA  ",
            **{field: "on" for field in self.BOOLEAN_FIELDS})
        data["whatsapp_activo"] = ""
        response = self.client.post(self.url(), data)
        self.assertEqual(response.status_code, 302)
        stored = ConfiguracionCRM.objects.get(pk=self.configuration.pk)
        self.assertTrue(all(getattr(stored, field) for field in self.BOOLEAN_FIELDS if field != "whatsapp_activo"))
        self.assertFalse(stored.whatsapp_activo)
        self.assertTrue(stored.whatsapp_pendiente_validacion)
        self.assertEqual(stored.mensaje_cita_confirmacion, "Texto original con espacios")
        self.assertEqual(stored.whatsapp_phone_number_id, "phone original QA")
        self.assertEqual(stored.dias_alerta_producto, 0)
        self.assertEqual(stored.whatsapp_token, self.configuration.whatsapp_token)
        data = self.data()
        for field in self.BOOLEAN_FIELDS:
            data.pop(field, None)
        response = self.client.post(self.url(), data)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(any(getattr(ConfiguracionCRM.objects.get(pk=stored.pk), field) for field in self.BOOLEAN_FIELDS))

    def test_original_global_audit_records_actor_company_values_but_never_raw_token(self):
        response = self.client.post(self.url(), self.data(mensaje_cita_confirmacion="Auditoría del actor QA"))
        self.assertEqual(response.status_code, 302)
        audit = RegistroAuditoria.objects.filter(modelo="configuracioncrm", objeto_id=str(self.configuration.pk),
            accion="modificar", usuario=self.users["granular"]).latest("pk")
        self.assertEqual(audit.empresa_id, self.empresa.pk)
        self.assertEqual(audit.metodo_http, "POST")
        self.assertEqual(audit.cambios["mensaje_cita_confirmacion"],
            {"anterior": self.configuration.mensaje_cita_confirmacion, "nuevo": "Auditoría del actor QA"})
        self.assertNotIn(self.configuration.whatsapp_token.strip(), json.dumps(audit.cambios))
        self.assertNotIn("whatsapp_token", audit.cambios)

    def test_only_selected_configuration_and_original_audit_change_not_stock_accounts_users_roles_or_other_configs(self):
        excluded = {"crm.configuracioncrm", "core.registroauditoria"}
        before = self.snapshot(exclude=excluded)
        others = list(ConfiguracionCRM.objects.exclude(pk=self.configuration.pk).order_by("pk").values())
        response = self.client.post(self.url(), self.data(mensaje_cita_reagendada="Sólo empresa actual QA"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.snapshot(exclude=excluded), before)
        self.assertEqual(list(ConfiguracionCRM.objects.exclude(pk=self.configuration.pk).order_by("pk").values()), others)

    def test_mobile_and_original_web_save_have_equivalent_nonsecret_field_effects(self):
        first, second = self.empresa, self.empresas["medical_spa"]
        data = self.data(first, mensaje_cita_confirmacion="Texto compartido QA", dias_alerta_producto="13",
            remitente_correo="comparacion@example.invalid", **{field: "on" for field in self.BOOLEAN_FIELDS})
        data["whatsapp_activo"] = ""
        mobile = self.client.post(self.url(first), data)
        original = {**data, "whatsapp_token": "  SYNTHETIC-WEB-TOKEN  "}
        original["whatsapp_phone_number_id"] = self.configs[second.slug].whatsapp_phone_number_id
        original["whatsapp_business_account_id"] = self.configs[second.slug].whatsapp_business_account_id
        original.pop("app")
        web = self.client.post(self.url(second, mobile=False), original)
        self.assertEqual(mobile.status_code, 302)
        self.assertEqual(web.status_code, 302)
        self.assertEqual(web.url, reverse("crm_dashboard", args=[second.slug]))
        a, b = ConfiguracionCRM.objects.get(empresa=first), ConfiguracionCRM.objects.get(empresa=second)
        for field in set(self.FIELDS) - {"whatsapp_phone_number_id", "whatsapp_business_account_id"}:
            self.assertEqual(getattr(a, field), getattr(b, field))
        self.assertEqual(a.whatsapp_token, self.configuration.whatsapp_token)
        self.assertEqual(b.whatsapp_token, "SYNTHETIC-WEB-TOKEN")

    def test_invalid_form_summary_uses_original_snapshot_not_model_form_tentative_values(self):
        response = self.client.post(self.url(), self.data(whatsapp_api_version="x"*21, whatsapp_activo="",
            recordatorio_citas_activo="", recordatorio_cumpleanos_activo=""))
        self.assertEqual(response.status_code, 200)
        summary = response.context["mobile_crm_summary"]
        self.assertEqual(summary, {"whatsapp_activo": self.configuration.whatsapp_activo,
            "recordatorio_citas_activo": self.configuration.recordatorio_citas_activo,
            "recordatorio_cumpleanos_activo": self.configuration.recordatorio_cumpleanos_activo,
            "whatsapp_numero_conectado": self.configuration.whatsapp_numero_conectado})
        self.assertEqual(response.context["form"].data["whatsapp_activo"], "")

    def test_xss_and_invalid_fields_are_escaped_and_fixed_company_identity_is_preserved(self):
        attempted = '<script>crm_configuration_qa()</script><img src=x onerror="crm_configuration_qa()">'
        response = self.client.post(self.url(), self.data(whatsapp_api_version="x"*21, mensaje_cita_confirmacion=attempted))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["empresa_original"].pk, self.empresa.pk)
        self.assertEqual(response.context["form"].data["mensaje_cita_confirmacion"], attempted)
        self.assertNotContains(response, attempted)
        self.assertNotContains(response, self.external_configuration.mensaje_cita_confirmacion)
        self.assertNotContains(response, self.configuration.whatsapp_token.strip())

    def test_csrf_failure_has_original_login_redirect_without_business_mutation_and_valid_token_saves(self):
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(self.users["granular"])
        protected.get(self.url())
        before = self.snapshot(exclude={"sessions.session"})
        response = protected.post(self.url(), self.data(mensaje_cita_confirmacion="Sin CSRF no guardar"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(urlsplit(response.url).path, reverse("empresa_login", args=[self.empresa.slug]))
        self.assertEqual(self.snapshot(exclude={"sessions.session"}), before)
        response = protected.post(self.url(), self.data(mensaje_cita_confirmacion="CSRF válido QA",
            csrfmiddlewaretoken=protected.cookies["csrftoken"].value))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(ConfiguracionCRM.objects.get(pk=self.configuration.pk).mensaje_cita_confirmacion, "CSRF válido QA")

    def test_crm_only_navigation_and_success_return_do_not_grant_home_modules_or_clinical_configuration(self):
        response = self.client.get(self.url())
        self.assertFalse(response.context["app_home_disponible"])
        self.assertFalse(response.context["can_view_app_modules"])
        self.assertFalse(response.context["can_view_clinical_configuration"])
        self.assertNotContains(response, 'rel="manifest"')
        for path in (reverse("agenda_mobile", args=[self.empresa.slug]),
                     reverse("agenda_mobile_modules_screen", args=[self.empresa.slug]),
                     reverse("clinica_configuracion", args=[self.empresa.slug]),
                     reverse("crm_dashboard", args=[self.empresa.slug])):
            self.assertNotContains(response, 'href="'+path+'"')
        saved = self.client.post(self.url(), self.data(mensaje_cita_confirmacion="Volver seguro QA"))
        self.assertEqual(saved.status_code, 302)
        self.assertEqual(saved.url, self.url())
        self.assertEqual(self.client.get(saved.url).status_code, 200)

    def test_capability_is_pure_exact_crm_permission_without_configuration_queries_or_grants(self):
        from .mobile_crm_configuration import configuracion_crm_disponible_app
        for actor in (*self.users.values(), self.supervisor, self.company_admin, self.no_permission, self.clinic_user, self.django_admin):
            request, _ = self.request(self.url(), actor=actor)
            before = self.snapshot()
            with CaptureQueriesContext(connection) as queries:
                available = configuracion_crm_disponible_app(request, self.empresa)
            self.assertEqual(available, actor.pk in {*(user.pk for user in self.users.values()), self.supervisor.pk, self.company_admin.pk})
            self.no_writes(queries)
            self.assertFalse(any('"crm_configuracioncrm"' in row["sql"].lower() for row in queries))
            self.assertEqual(self.snapshot(), before)

    def test_meta_connect_app_query_or_form_body_is_denied_before_config_or_network(self):
        self.remove_configuration()
        for mobile, data in ((True, {"code": "fake", "waba_id": "qa", "phone_number_id": "qa"}),
                             (False, {"app": "1", "code": "fake"})):
            url = self.url(route="crm_conectar_whatsapp_meta", mobile=mobile)
            with patch("crm.views._configuracion_crm", side_effect=AssertionError("No Meta initializer")):
                self.denied(url, method="post", data=data)
                self.denied(self.url(route="crm_conectar_whatsapp_meta"))

    @override_settings(META_WHATSAPP_APP_SECRET="SYNTHETIC-OAUTH-SECRET", META_WHATSAPP_APP_ID="synthetic-app",
                       META_WHATSAPP_CONFIG_ID="synthetic-config")
    def test_meta_connect_json_app_flag_is_denied_after_original_parse_before_exchange_or_configuration(self):
        self.remove_configuration()
        data = {"app": "1", "code": "synthetic-code", "waba_id": "synthetic-waba", "phone_number_id": "synthetic-phone"}
        with patch("crm.views._configuracion_crm", side_effect=AssertionError("No JSON Meta initializer")):
            self.denied(self.url(route="crm_conectar_whatsapp_meta", mobile=False), json_body=data)
            self.denied(self.url(route="crm_conectar_whatsapp_meta"), json_body=data)
            # Without a server secret, the original503 precedes JSON parsing.
            # Preserve that ordering; it neither initializes nor sends anything.
            before = self.snapshot()
            with override_settings(META_WHATSAPP_APP_SECRET=""):
                response = self.direct(self.url(route="crm_conectar_whatsapp_meta", mobile=False), json_body=data)
            self.assertEqual(response.status_code, 503)
            self.assertEqual(self.snapshot(), before)

    def test_whatsapp_test_app_flag_is_denied_before_default_creation_or_transport_for_query_and_body(self):
        self.remove_configuration()
        with patch("crm.views._configuracion_crm", side_effect=AssertionError("No WhatsApp test initializer")):
            self.denied(self.url(route="crm_enviar_prueba_whatsapp"), method="post", data={})
            self.denied(self.url(route="crm_enviar_prueba_whatsapp", mobile=False), method="post", data={"app": "1"})
            self.denied(self.url(route="crm_enviar_prueba_whatsapp"))

    def test_original_web_test_send_route_remains_unchanged_with_only_a_mocked_transport(self):
        with patch("crm.views.enviar_plantilla_whatsapp", return_value={"messages": [{"id": "synthetic-only"}]}) as transport:
            response = self.client.post(self.url(route="crm_enviar_prueba_whatsapp", mobile=False), {})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("crm_configuracion", args=[self.empresa.slug]))
        transport.assert_called_once()
        self.assertEqual(transport.call_args.args[0].pk, self.configuration.pk)

    def test_web_get_preserves_original_default_initialization_while_mobile_get_does_not(self):
        self.remove_configuration()
        self.client.get(self.url())
        self.assertFalse(ConfiguracionCRM.objects.filter(empresa=self.empresa).exists())
        web = self.client.get(self.url(mobile=False))
        self.assertEqual(web.status_code, 200)
        self.assertTemplateUsed(web, "crm/form.html")
        self.assertTrue(ConfiguracionCRM.objects.filter(empresa=self.empresa).exists())
        self.assertIn("whatsapp_token", web.context["form"].fields)

    def test_existing_combined_permissions_offer_working_mobile_navigation_and_crm_cards_without_financial_grants(self):
        from crm.mobile_modules import MODULOS_OCULTOS_APP_CLINICA, construir_contexto_modulos_app

        clinical_module, _ = Modulo.objects.get_or_create(codigo="clinica_medica", defaults={"nombre": "Clínica QA"})
        for empresa in self.empresas.values():
            EmpresaModulo.objects.update_or_create(empresa=empresa, modulo=clinical_module, defaults={"activo": True})
        for name, actor in self.users.items():
            # These legitimate combinations are fixture setup, not permissions
            # granted by the mobile configuration or the presentation registry.
            RolSistema.objects.filter(pk=self.roles[name].pk).update(puede_citas=True, puede_configuracion_clinica=True)
            self.client.force_login(actor)
            before = self.snapshot()
            for empresa in (self.empresa, self.empresas["medical_spa"]):
                response = self.client.get(self.url(empresa))
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["can_view_app_modules"])
                self.assertTrue(response.context["can_view_clinical_configuration"])
                modules_url = response.context["app_modules_url"]
                clinical_url = response.context["app_clinical_configuration_url"]
                self.assertEqual(modules_url, reverse("agenda_mobile_modules_screen", args=[empresa.slug]))
                self.assertEqual(clinical_url, reverse("clinica_configuracion", args=[empresa.slug])+"?app=1")
                self.assertContains(response, 'href="'+modules_url+'"')
                self.assertContains(response, 'href="'+clinical_url+'"')
                modules = self.client.get(modules_url)
                self.assertEqual(modules.status_code, 200)
                request, _ = self.request(self.url(empresa), actor=actor)
                items = construir_contexto_modulos_app(request, empresa)["modulos_app"]
                card = next(item for item in items if item["key"] == "configuracion_crm")
                self.assertEqual(card["url"], self.url(empresa))
                self.assertEqual(card["estado"], "disponible")
                self.assertTrue(MODULOS_OCULTOS_APP_CLINICA.isdisjoint(item["key"] for item in items))
                clinical = self.client.get(clinical_url)
                self.assertEqual(clinical.status_code, 200)
                clinic_card = next(item for item in clinical.context["mobile_configuration_items"] if item["codigo"] == "crm")
                self.assertEqual(clinic_card["url"], self.url(empresa))
                self.assertEqual(clinic_card["estado"], "disponible")
            self.assertEqual(self.snapshot(), before)
            RolSistema.objects.filter(pk=self.roles[name].pk).update(puede_configuracion_crm=False)
            request, _ = self.request(self.url(), actor=actor)
            items = construir_contexto_modulos_app(request, self.empresa)["modulos_app"]
            self.assertNotIn("configuracion_crm", {item["key"] for item in items})
            self.denied(actor=actor)
