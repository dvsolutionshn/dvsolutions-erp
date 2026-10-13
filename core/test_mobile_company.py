"""Configuración móvil reutilizada, exclusiva de superadministración.

Abrir el editor no inicializa configuración. Guardar conserva EmpresaControlForm
y sus sincronizaciones originales; las pruebas nunca usan datos de producción.
"""
from datetime import timedelta
from html.parser import HTMLParser
from io import BytesIO
from tempfile import TemporaryDirectory
from urllib.parse import parse_qs, urlencode, urlsplit
from unittest.mock import patch

from PIL import Image
from django.apps import apps
from django.contrib.auth.models import AnonymousUser
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.http import Http404
from django.test import Client, RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import resolve, reverse
from django.utils import timezone

from . import views
from .forms import EmpresaControlForm
from .models import (
    ConfiguracionAvanzadaEmpresa, Empresa, EmpresaModulo, Modulo, PlanComercial,
    PlanModulo, RegistroAuditoria, RolSistema, Usuario,
)


class _CompanyFormMarkup(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.in_form = False
        self.fields = []
        self.attrs = {}
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form" and attrs.get("id") == "mobileCompanyForm":
            self.in_form = True
            self.attrs = attrs
        if self.in_form and tag in {"input", "select", "textarea", "button"} and attrs.get("name"):
            self.fields.append((tag, attrs))

    def handle_endtag(self, tag):
        if tag == "form":
            self.in_form = False


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class MobileClinicalCompanyTests(TestCase):
    SLUGS = ("hospital_mia", "medical_spa", "luque_aestetic", "serviciosmedicos")
    FIELDS = (
        "nombre", "tipo_solucion", "slug", "rtn", "plan_comercial", "estado_licencia",
        "fecha_inicio_plan", "fecha_vencimiento_plan", "correo", "telefono", "sitio_web",
        "direccion", "ciudad", "departamento", "pais", "slogan", "condiciones_pago",
        "observaciones_comerciales", "logo", "activa", "modulos_activos",
        "permite_cai_historico", "permite_plantilla_factura_independiente",
        "manuales_pdf_habilitados", "modulos_adicionales_visibles_clinica",
    )
    MULTIPLE = {"modulos_activos", "modulos_adicionales_visibles_clinica"}

    @classmethod
    def setUpTestData(cls):
        cls.empresas = {
            slug: Empresa.objects.create(nombre=f"Empresa móvil QA {slug}", slug=slug,
                rtn=f"COMPANY-QA-{index}", tipo_solucion="clinica", estado_licencia="activa")
            for index, slug in enumerate(cls.SLUGS, 1)
        }
        cls.empresa = cls.empresas["hospital_mia"]
        cls.external = Empresa.objects.create(nombre="Empresa externa no adaptada", slug="external-company-qa",
            rtn="COMPANY-QA-EXT", tipo_solucion="clinica")
        cls.supervisor = Usuario.objects.create_superuser(username="company-super-qa", password="test-only",
            email="super@example.invalid", empresa=cls.external)
        cls.admin = Usuario.objects.create_user(username="company-admin-qa", password="test-only",
            empresa=cls.empresa, es_administrador_empresa=True)
        cls.users_admin = Usuario.objects.create_user(username="company-users-admin-qa", password="test-only",
            empresa=cls.empresa, puede_administrar_usuarios_clinicos=True)
        cls.role = RolSistema.objects.create(nombre="Config clínica no maestra QA", codigo="company-clinic-config-qa",
            es_rol_clinico=True, usa_permisos_clinicos_granulares=True, puede_configuracion_clinica=True)
        cls.clinic_admin = Usuario.objects.create_user(username="company-clinic-admin-qa", password="test-only",
            empresa=cls.empresa, rol_sistema=cls.role)
        cls.plan = PlanComercial.objects.create(nombre="Plan sintético QA", codigo="company-plan-qa", precio_mensual="15.25")
        cls.extra, _ = Modulo.objects.get_or_create(codigo="company-extra-qa", defaults={"nombre": "Extra comercial QA"})
        cls.other, _ = Modulo.objects.get_or_create(codigo="company-other-qa", defaults={"nombre": "Otro comercial QA"})
        cls.private = Modulo.objects.create(codigo="company-private-qa", nombre="No comercial QA", es_comercial=False)
        # El saver original crea estos módulos si faltan. Prepararlos fuera del
        # snapshot separa ese efecto legítimo del aislamiento de otros negocios.
        for code, name in (("facturacion", "Facturacion"), ("punto_venta", "Punto de Venta"),
                           ("clinica_medica", "Clinica Medica"), ("agenda_citas", "Agenda de Citas")):
            Modulo.objects.get_or_create(codigo=code, defaults={"nombre": name, "es_comercial": True})
        PlanModulo.objects.create(plan=cls.plan, modulo=cls.other, activo=True)

    def setUp(self):
        self.client.force_login(self.supervisor)
        self.media = TemporaryDirectory(prefix="mobile-company-tests-")
        self.addCleanup(self.media.cleanup)
        self.media_settings = override_settings(MEDIA_ROOT=self.media.name)
        self.media_settings.enable()
        self.addCleanup(self.media_settings.disable)
        for target in ("core.views.enviar_correo_acceso", "core.views.emitir_token_acceso",
                       "facturacion.views.enviar_documento_whatsapp", "django.core.mail.EmailMessage.send"):
            mocked = patch(target)
            transport = mocked.start()
            self.addCleanup(mocked.stop)
            self.addCleanup(transport.assert_not_called)

    def url(self, empresa=None, *, listing=False, mobile=True, **params):
        path = reverse("superadmin_empresas_app") if listing else reverse("superadmin_empresa_edit", args=[(empresa or self.empresa).pk])
        query = {"app": "1"} if mobile else {}
        query.update(params)
        return path + ("?" + urlencode(query, doseq=True) if query else "")

    def data(self, empresa=None, **updates):
        empresa = empresa or self.empresa
        result = {
            "app": "1", "nombre": empresa.nombre, "tipo_solucion": empresa.tipo_solucion,
            "slug": empresa.slug, "rtn": empresa.rtn, "plan_comercial": "", "estado_licencia": empresa.estado_licencia,
            "fecha_inicio_plan": timezone.localdate().isoformat(),
            "fecha_vencimiento_plan": (timezone.localdate()+timedelta(days=45)).isoformat(),
            "correo": "contacto@example.invalid", "telefono": "+504 9999-0000", "sitio_web": "example.invalid",
            "direccion": "Dirección sintética QA", "ciudad": "Tegucigalpa", "departamento": "Francisco Morazán",
            "pais": "Honduras", "slogan": "Atención QA", "condiciones_pago": "Pago inmediato",
            "observaciones_comerciales": "Observaciones sintéticas", "modulos_activos": [],
            "modulos_adicionales_visibles_clinica": [],
        }
        if empresa.activa:
            result["activa"] = "on"
        result.update(updates)
        return result

    def direct(self, *, url=None, listing=False, empresa=None, method="get", data=None, actor=None):
        url = url or self.url(empresa, listing=listing)
        factory = RequestFactory()
        request = factory.post(url, data=data or {}) if method == "post" else factory.generic(method.upper(), url)
        request.user = actor if actor is not None else Usuario.objects.get(pk=self.supervisor.pk)
        request.session = {}
        request._messages = FallbackStorage(request)
        match = resolve(urlsplit(url).path)
        request.resolver_match = match
        return match.func(request, **match.kwargs)

    def snapshot(self, exclude=()):
        return tuple((model._meta.label_lower, list(model.objects.order_by("pk").values()))
            for model in apps.get_models(include_auto_created=True) if model._meta.label_lower not in exclude)

    def denied(self, **kwargs):
        before = self.snapshot()
        try:
            response = self.direct(**kwargs)
        except (PermissionDenied, Http404):
            pass
        else:
            self.assertIn(response.status_code, (403, 404, 405))
        self.assertEqual(self.snapshot(), before)

    def no_writes(self, queries):
        statements = [row["sql"].lstrip().upper() for row in queries]
        self.assertFalse(any(sql.startswith(("INSERT ", "UPDATE ", "DELETE ", "REPLACE ")) for sql in statements))

    def logo(self, name="qa.png"):
        buffer = BytesIO()
        Image.new("RGB", (2, 2), "teal").save(buffer, format="PNG")
        return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")

    def test_four_companies_use_mobile_editor_and_pc_keeps_original_template(self):
        for empresa in self.empresas.values():
            with self.subTest(empresa=empresa.slug):
                app = self.client.get(self.url(empresa))
                self.assertEqual(app.status_code, 200)
                self.assertTemplateUsed(app, "core/mobile_company_form.html")
                self.assertEqual(set(app.context["form"].fields), set(self.FIELDS))
                web = self.client.get(self.url(empresa, mobile=False))
                self.assertTemplateUsed(web, "core/superadmin_empresa_form.html")
                self.assertTemplateNotUsed(web, "core/mobile_company_form.html")

    def test_company_admin_users_flag_and_clinical_config_do_not_authorize_master_editor(self):
        for actor in (self.admin, self.users_admin, self.clinic_admin):
            for listing in (False, True):
                before = self.snapshot()
                response = self.direct(actor=actor, listing=listing)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.url, reverse("superadmin_login"))
                self.assertEqual(self.snapshot(), before)
            response = self.direct(actor=actor, method="post", data=self.data(nombre="No autorizado"))
            self.assertEqual(response.url, reverse("superadmin_login"))
            self.empresa.refresh_from_db()
            self.assertNotEqual(self.empresa.nombre, "No autorizado")

    def test_superuser_needs_no_company_membership_module_or_operating_license(self):
        for empresa in self.empresas.values():
            Empresa.objects.filter(pk=empresa.pk).update(activa=False, estado_licencia="suspendida")
        before = self.snapshot()
        for empresa in self.empresas.values():
            response = self.client.get(self.url(empresa))
            self.assertEqual(response.status_code, 200)
        self.assertEqual(self.snapshot(), before)

    def test_logout_remains_original_superadmin_login_redirect(self):
        for listing in (False, True):
            response = self.direct(actor=AnonymousUser(), listing=listing)
            self.assertEqual(response.status_code, 302)
            self.assertEqual(urlsplit(response.url).path, reverse("superadmin_login"))

    def test_other_slug_or_nonclinical_type_is_rejected_before_constructing_form(self):
        with patch("core.views.EmpresaControlForm") as constructor:
            self.denied(empresa=self.external)
            self.denied(empresa=self.external, method="post", data=self.data(self.external))
            Empresa.objects.filter(pk=self.empresa.pk).update(tipo_solucion="erp")
            self.denied()
            constructor.assert_not_called()

    def test_new_picker_is_exact_four_scoped_companies_including_inactive(self):
        Empresa.objects.filter(pk=self.empresa.pk).update(activa=False, estado_licencia="vencida")
        before = self.snapshot()
        response = self.client.get(self.url(listing=True))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "core/mobile_company_list.html")
        self.assertEqual({obj.pk for obj in response.context["empresas"]}, {obj.pk for obj in self.empresas.values()})
        self.assertNotContains(response, self.external.nombre)
        self.assertEqual(self.snapshot(), before)

    def test_picker_has_only_mobile_edit_links_no_control_backup_or_licensing_actions(self):
        response = self.client.get(self.url(listing=True))
        for empresa in self.empresas.values():
            self.assertContains(response, self.url(empresa), html=False)
        self.assertNotContains(response, "<table")
        for route in ("superadmin_empresas", "superadmin_empresa_create", "superadmin_respaldos", "superadmin_licencias"):
            self.assertNotContains(response, f'href="{reverse(route)}"')

    def test_get_and_head_all_models_and_through_tables_are_read_only(self):
        before = self.snapshot()
        for method in ("get", "head"):
            for url in (self.url(), self.url(listing=True)):
                with CaptureQueriesContext(connection) as queries:
                    response = getattr(self.client, method)(url)
                self.assertEqual(response.status_code, 200)
                self.assertIn("no-store", response["Cache-Control"])
                self.no_writes(queries)
                if method == "head":
                    self.assertEqual(response.content, b"")
        self.assertEqual(self.snapshot(), before)

    def test_mobile_constructor_never_calls_configuration_initializer_or_savers(self):
        with patch.object(ConfiguracionAvanzadaEmpresa, "para_empresa", side_effect=AssertionError("GET seed")), \
             patch.object(EmpresaControlForm, "_save_modules", side_effect=AssertionError("GET modules")), \
             patch.object(EmpresaControlForm, "_save_advanced_config", side_effect=AssertionError("GET config")):
            self.assertEqual(self.client.get(self.url()).status_code, 200)
            self.assertEqual(self.client.get(self.url(listing=True)).status_code, 200)

    def test_absent_advanced_configuration_uses_original_false_defaults_without_creating_row(self):
        before = self.snapshot()
        response = self.client.get(self.url())
        for field in ("permite_cai_historico", "permite_plantilla_factura_independiente", "manuales_pdf_habilitados"):
            self.assertFalse(response.context["form"][field].value())
        self.assertFalse(ConfiguracionAvanzadaEmpresa.objects.filter(empresa=self.empresa).exists())
        self.assertEqual(self.snapshot(), before)

    def test_existing_advanced_configuration_and_module_initials_are_preserved(self):
        config = ConfiguracionAvanzadaEmpresa.objects.create(empresa=self.empresa, permite_cai_historico=True,
            permite_plantilla_factura_independiente=True, manuales_pdf_habilitados=True, usa_control_lotes_fefo=True)
        config.modulos_adicionales_visibles_clinica.add(self.extra)
        EmpresaModulo.objects.create(empresa=self.empresa, modulo=self.extra, activo=True)
        before = self.snapshot()
        form = self.client.get(self.url()).context["form"]
        for field in ("permite_cai_historico", "permite_plantilla_factura_independiente", "manuales_pdf_habilitados"):
            self.assertTrue(form[field].value())
        self.assertEqual(set(form["modulos_activos"].value()), {self.extra.pk})
        self.assertEqual(set(form["modulos_adicionales_visibles_clinica"].value()), {self.extra.pk})
        self.assertEqual(self.snapshot(), before)

    def test_default_web_form_constructor_still_initializes_configuration(self):
        self.assertFalse(ConfiguracionAvanzadaEmpresa.objects.filter(empresa=self.empresa).exists())
        EmpresaControlForm(instance=self.empresa)
        self.assertTrue(ConfiguracionAvanzadaEmpresa.objects.filter(empresa=self.empresa).exists())

    def test_noncanonical_foreign_and_missing_company_ids_fail_before_form(self):
        path = reverse("superadmin_empresa_edit", args=[self.empresa.pk])
        with patch("core.views.EmpresaControlForm") as constructor:
            for identifier in ("0", "00"+str(self.empresa.pk), "9223372036854775808", "2147483647"):
                self.denied(url=path.replace(f"/{self.empresa.pk}/", f"/{identifier}/")+"?app=1")
            self.denied(empresa=self.external)
            constructor.assert_not_called()

    def test_closed_get_envelope_rejects_unknown_or_duplicate_keys(self):
        for listing in (False, True):
            path = self.url(listing=listing).split("?")[0]
            for query in ("app=1&app=1", "app=1&app=0", "app=1&next=/control/", "app=1&q=private",
                          "app=1&empresa=1", "app=1&origen=hospital_mia&origen=hospital_mia"):
                self.denied(url=path+"?"+query)
        self.denied(url=self.url(listing=True, mobile=False))
        self.denied(url=self.url(listing=True, app="0"))

    def test_picker_origin_is_presentation_only_and_must_be_one_supported_slug(self):
        for slug in self.SLUGS:
            response = self.client.get(self.url(listing=True, origen=slug))
            self.assertEqual(response.status_code, 200)
        self.denied(url=self.url(listing=True, origen=self.external.slug))
        self.denied(url=self.url(listing=True, origen="https://example.invalid/"))

    def test_picker_is_get_head_only_and_editor_has_only_original_get_head_post(self):
        for method in ("post", "put", "patch", "delete"):
            self.denied(listing=True, method=method, data=self.data())
        for method in ("put", "patch", "delete"):
            self.denied(method=method)

    def test_duplicate_scalar_fields_are_rejected_without_saving(self):
        for field in ("app", *(name for name in self.FIELDS if name not in self.MULTIPLE and name != "logo")):
            data = self.data()
            data[field] = [data.get(field, ""), data.get(field, "")]
            self.denied(method="post", data=data)

    def test_unknown_identity_security_and_financial_body_fields_are_rejected(self):
        for field in ("empresa", "empresa_id", "usuario", "rol_sistema", "is_superuser", "csrf_secret", "next",
                      "accion", "fecha_creacion", "licencia_operativa", "cantidad", "cuenta_financiera", "password"):
            data = self.data()
            data[field] = "1"
            self.denied(method="post", data=data)

    def test_post_app_is_required_and_conflicting_query_is_rejected(self):
        data = self.data()
        data.pop("app")
        self.denied(method="post", data=data)
        for value in ("0", "", "1,1"):
            self.denied(method="post", data=self.data(app=value))
        self.denied(method="post", url=self.url(app="0"), data=self.data())
        response = self.client.post(self.url(mobile=False), self.data(nombre="Body opt-in móvil"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(urlsplit(response.url).path, reverse("superadmin_empresas_app"))

    def test_plan_and_multiple_choice_ids_must_be_canonical_before_queries(self):
        for field in ("plan_comercial", *self.MULTIPLE):
            for identifier in ("00"+str(self.extra.pk), "-1", "NaN", "١", "9223372036854775808", "1e3"):
                value = [identifier] if field in self.MULTIPLE else identifier
                self.denied(method="post", data=self.data(**{field: value}))

    def test_repeated_multiselect_values_remain_legitimate_original_choices(self):
        response = self.client.post(self.url(), self.data(modulos_activos=[str(self.extra.pk), str(self.other.pk)],
            modulos_adicionales_visibles_clinica=[str(self.extra.pk), str(self.other.pk)]))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.extra, activo=True).exists())
        config = ConfiguracionAvanzadaEmpresa.objects.get(empresa=self.empresa)
        self.assertEqual(set(config.modulos_adicionales_visibles_clinica.values_list("pk", flat=True)), {self.extra.pk, self.other.pk})

    def test_noncommercial_or_nonexistent_module_is_original_bound_form_error(self):
        before = self.snapshot()
        for field in self.MULTIPLE:
            for identifier in (str(self.private.pk), "2147483647"):
                response = self.client.post(self.url(), self.data(**{field: [identifier]}))
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["form"].is_bound)
                self.assertIn(field, response.context["form"].errors)
        self.assertEqual(self.snapshot(), before)

    def test_files_are_closed_to_only_original_logo_upload(self):
        self.denied(method="post", data=self.data(archivo=SimpleUploadedFile("unexpected.txt", b"no")))
        self.denied(method="post", data=self.data(logo=[self.logo("one.png"), self.logo("two.png")]))

    def test_all_twenty_five_fields_and_native_widget_names_are_rendered(self):
        response = self.client.get(self.url())
        form = response.context["form"]
        markup = _CompanyFormMarkup(response.content.decode())
        names = [attrs["name"] for _, attrs in markup.fields]
        self.assertEqual(set(form.fields), set(self.FIELDS))
        self.assertEqual(set(names)-{"app", "csrfmiddlewaretoken", "logo-clear"}, set(self.FIELDS))
        for name in self.FIELDS:
            if name not in self.MULTIPLE and name != "tipo_solucion":
                self.assertEqual(names.count(name), 1)
        self.assertEqual(names.count("app"), 1)
        self.assertEqual(names.count("csrfmiddlewaretoken"), 1)
        radio = [attrs for _, attrs in markup.fields if attrs["name"] == "tipo_solucion"]
        self.assertEqual({attrs["value"] for attrs in radio}, {value for value, _ in Empresa.TIPO_SOLUCION_CHOICES})
        self.assertTrue(all(attrs.get("type") == "radio" for attrs in radio))
        self.assertEqual([attrs["value"] for attrs in radio if "checked" in attrs], [self.empresa.tipo_solucion])
        self.assertEqual(markup.attrs.get("enctype"), "multipart/form-data")
        self.assertEqual(parse_qs(urlsplit(markup.attrs["action"]).query), {"app": ["1"]})
        for name in ("fecha_inicio_plan", "fecha_vencimiento_plan"):
            attrs = next(attrs for _, attrs in markup.fields if attrs["name"] == name)
            self.assertEqual(attrs.get("type"), "date")
            self.assertEqual(attrs.get("value"), getattr(self.empresa, name).isoformat())

    def test_invalid_post_keeps_all_fields_and_original_identity_navigation(self):
        before = self.snapshot()
        data = self.data(nombre="", slug="intentado-cambio-qa", correo="invalid-email", direccion="Borrador completo",
            telefono="555 draft", modulos_activos=[str(self.extra.pk)], permite_cai_historico="on")
        response = self.client.post(self.url(), data)
        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertTrue(form.is_bound)
        for field, value in data.items():
            if field not in {"app", *self.MULTIPLE}:
                self.assertEqual(form.data.get(field), value)
        self.assertEqual(form.data.getlist("modulos_activos"), [str(self.extra.pk)])
        original = response.context["empresa_original"]
        self.assertEqual(original.slug, self.empresa.slug)
        self.assertEqual(original.nombre, self.empresa.nombre)
        self.assertNotContains(response, 'href="/intentado-cambio-qa/')
        self.assertEqual(self.snapshot(), before)

    def test_original_date_and_email_validation_are_not_replaced(self):
        before = self.snapshot()
        for updates, field in (({"fecha_inicio_plan": "2026-02-30"}, "fecha_inicio_plan"),
                               ({"fecha_inicio_plan": "2026-06-10", "fecha_vencimiento_plan": "2026-06-01"}, "fecha_vencimiento_plan"),
                               ({"correo": "not-an-email"}, "correo")):
            response = self.client.post(self.url(), self.data(**updates))
            self.assertEqual(response.status_code, 200)
            self.assertIn(field, response.context["form"].errors)
        self.assertEqual(self.snapshot(), before)

    def test_original_unique_slug_and_rtn_errors_preserve_work(self):
        before = self.snapshot()
        for updates, field in (({"slug": self.external.slug}, "slug"), ({"rtn": self.external.rtn}, "rtn")):
            response = self.client.post(self.url(), self.data(**updates, observaciones_comerciales="No perder nota"))
            self.assertEqual(response.status_code, 200)
            self.assertIn(field, response.context["form"].errors)
            self.assertEqual(response.context["form"].data["observaciones_comerciales"], "No perder nota")
        self.assertEqual(self.snapshot(), before)

    def test_valid_edit_reuses_original_save_and_audits_real_actor_changes_for_each_company(self):
        for empresa in self.empresas.values():
            old = empresa.nombre
            response = self.client.post(self.url(empresa), self.data(empresa, nombre=f"Actualizada QA {empresa.slug}",
                condiciones_pago="Crédito acordado", fecha_inicio_plan="2026-01-01", fecha_vencimiento_plan="2027-01-01"))
            self.assertEqual(response.status_code, 302)
            empresa.refresh_from_db()
            self.assertEqual(empresa.condiciones_pago, "Crédito acordado")
            self.assertEqual(empresa.fecha_inicio_plan.isoformat(), "2026-01-01")
            audit = RegistroAuditoria.objects.filter(modelo="empresa", objeto_id=str(empresa.pk), usuario=self.supervisor,
                accion="modificar").latest("pk")
            self.assertEqual(audit.empresa_id, empresa.pk)
            self.assertEqual(audit.cambios["nombre"], {"anterior": old, "nuevo": empresa.nombre})
            self.assertEqual(audit.metodo_http, "POST")
            self.assertTrue(audit.fecha)

    def test_saves_change_only_target_company_config_and_module_relations_not_other_business(self):
        excluded = {"core.empresa", "core.empresamodulo", "core.configuracionavanzadaempresa",
                    "core.configuracionavanzadaempresa_modulos_adicionales_visibles_clinica", "core.registroauditoria"}
        before = self.snapshot(excluded)
        others = list(Empresa.objects.exclude(pk=self.empresa.pk).order_by("pk").values())
        response = self.client.post(self.url(), self.data(nombre="Cambio exclusivamente local",
            modulos_activos=[str(self.extra.pk)], manuales_pdf_habilitados="on"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.snapshot(excluded), before)
        self.assertEqual(list(Empresa.objects.exclude(pk=self.empresa.pk).order_by("pk").values()), others)
        self.assertEqual(ConfiguracionAvanzadaEmpresa.objects.exclude(empresa=self.empresa).count(), 0)

    def test_original_plan_and_clinical_essential_modules_are_synced_without_permission_changes(self):
        EmpresaModulo.objects.create(empresa=self.empresa, modulo=self.extra, activo=True)
        response = self.client.post(self.url(), self.data(plan_comercial=str(self.plan.pk), modulos_activos=[]))
        self.assertEqual(response.status_code, 302)
        codes = set(EmpresaModulo.objects.filter(empresa=self.empresa, activo=True).values_list("modulo__codigo", flat=True))
        self.assertTrue({"facturacion", "punto_venta", "clinica_medica", "agenda_citas", self.other.codigo}.issubset(codes))
        self.assertNotIn(self.extra.codigo, codes)
        self.admin.refresh_from_db()
        self.assertFalse(self.admin.puede_administrar_usuarios_clinicos)

    def test_advanced_config_flags_and_visibility_use_original_save_without_changing_other_flags(self):
        config = ConfiguracionAvanzadaEmpresa.objects.create(empresa=self.empresa, usa_control_lotes_fefo=True,
            usa_bodegas_internas=True, notas="No modificar notas", permite_cai_historico=True)
        response = self.client.post(self.url(), self.data(permite_plantilla_factura_independiente="on", manuales_pdf_habilitados="on",
            modulos_adicionales_visibles_clinica=[str(self.extra.pk)]))
        self.assertEqual(response.status_code, 302)
        config.refresh_from_db()
        self.assertFalse(config.permite_cai_historico)
        self.assertTrue(config.permite_plantilla_factura_independiente)
        self.assertTrue(config.manuales_pdf_habilitados)
        self.assertTrue(config.usa_control_lotes_fefo)
        self.assertTrue(config.usa_bodegas_internas)
        self.assertEqual(config.notas, "No modificar notas")

    def test_app_save_effects_match_original_form_save_in_equivalent_company_fixture(self):
        twin = Empresa.objects.create(nombre=self.empresa.nombre, slug="company-twin-qa", rtn="COMPANY-QA-TWIN",
            tipo_solucion="clinica", estado_licencia="activa")
        data = self.data(plan_comercial=str(self.plan.pk), manuales_pdf_habilitados="on", permite_cai_historico="on",
            modulos_activos=[str(self.extra.pk)], modulos_adicionales_visibles_clinica=[str(self.extra.pk)])
        response = self.client.post(self.url(), data)
        self.assertEqual(response.status_code, 302)
        original_data = {**data, "slug": twin.slug, "rtn": twin.rtn}
        original_data.pop("app")
        original = EmpresaControlForm(data=original_data, instance=twin)
        self.assertTrue(original.is_valid(), original.errors)
        original.save()
        self.empresa.refresh_from_db()
        twin.refresh_from_db()
        for field in EmpresaControlForm.Meta.fields:
            if field not in {"slug", "rtn", "logo"}:
                self.assertEqual(getattr(self.empresa, field), getattr(twin, field))
        for empresa in (self.empresa, twin):
            self.assertEqual(set(EmpresaModulo.objects.filter(empresa=empresa, activo=True).values_list("modulo_id", flat=True)),
                set(EmpresaModulo.objects.filter(empresa=self.empresa, activo=True).values_list("modulo_id", flat=True)))
        first = ConfiguracionAvanzadaEmpresa.objects.get(empresa=self.empresa)
        second = ConfiguracionAvanzadaEmpresa.objects.get(empresa=twin)
        for field in ("permite_cai_historico", "permite_plantilla_factura_independiente", "manuales_pdf_habilitados"):
            self.assertEqual(getattr(first, field), getattr(second, field))
        self.assertEqual(set(first.modulos_adicionales_visibles_clinica.values_list("pk", flat=True)),
            set(second.modulos_adicionales_visibles_clinica.values_list("pk", flat=True)))

    def test_change_slug_returns_scoped_picker_with_honest_notice_and_no_new_editor_scope(self):
        response = self.client.post(self.url(), self.data(slug="clinic-moved-outside-qa"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(urlsplit(response.url).path, reverse("superadmin_empresas_app"))
        picker = self.client.get(response.url)
        self.assertEqual(picker.status_code, 200)
        self.assertNotIn(self.empresa.pk, {item.pk for item in picker.context["empresas"]})
        self.assertNotContains(picker, 'href="/clinic-moved-outside-qa/')
        self.assertTrue(any(message.level >= 30 for message in picker.context["messages"]))
        self.denied()

    def test_change_type_is_originally_allowed_then_outside_mobile_scope_without_web_fallback(self):
        response = self.client.post(self.url(), self.data(tipo_solucion="erp"))
        self.assertEqual(response.status_code, 302)
        self.empresa.refresh_from_db()
        self.assertEqual(self.empresa.tipo_solucion, "erp")
        self.assertEqual(urlsplit(response.url).path, reverse("superadmin_empresas_app"))
        self.denied()

    def test_deactivate_is_originally_allowed_and_can_be_reopened_by_super_on_mobile(self):
        data = self.data()
        data.pop("activa")
        response = self.client.post(self.url(), data)
        self.assertEqual(response.status_code, 302)
        self.empresa.refresh_from_db()
        self.assertFalse(self.empresa.activa)
        self.assertEqual(urlsplit(response.url).path, reverse("superadmin_empresas_app"))
        self.assertEqual(self.client.get(self.url()).status_code, 200)

    def test_original_logo_upload_clear_and_invalid_image_validation_are_retained(self):
        response = self.client.post(self.url(), self.data(logo=self.logo()))
        self.assertEqual(response.status_code, 302)
        self.empresa.refresh_from_db()
        self.assertTrue(self.empresa.logo.name.startswith("logos/"))
        with self.empresa.logo.open("rb") as handle:
            self.assertEqual(Image.open(handle).size, (2, 2))
        form_page = self.client.get(self.url())
        self.assertIn("logo-clear", [attrs["name"] for _, attrs in _CompanyFormMarkup(form_page.content.decode()).fields])
        before = self.snapshot()
        invalid = self.client.post(self.url(), self.data(logo=SimpleUploadedFile("fake.png", b"not an image", content_type="image/png")))
        self.assertEqual(invalid.status_code, 200)
        self.assertIn("logo", invalid.context["form"].errors)
        self.assertEqual(self.snapshot(), before)
        response = self.client.post(self.url(), self.data(**{"logo-clear": "on"}))
        self.assertEqual(response.status_code, 302)
        self.empresa.refresh_from_db()
        self.assertFalse(self.empresa.logo)

    def test_csrf_rejection_preserves_business_and_valid_token_uses_original_save(self):
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(self.supervisor)
        protected.get(self.url())
        before = self.snapshot(exclude={"sessions.session"})
        rejected = protected.post(self.url(), self.data(nombre="CSRF no autorizado"))
        self.assertEqual(rejected.status_code, 302)
        self.assertEqual(urlsplit(rejected.url).path, reverse("superadmin_login"))
        self.assertEqual(self.snapshot(exclude={"sessions.session"}), before)
        response = protected.post(self.url(), self.data(nombre="CSRF autorizado", csrfmiddlewaretoken=protected.cookies["csrftoken"].value))
        self.assertEqual(response.status_code, 302)
        self.empresa.refresh_from_db()
        self.assertEqual(self.empresa.nombre, "CSRF autorizado")

    def test_configuration_and_module_cards_offer_company_editor_only_for_superuser(self):
        from core.mobile_company import configuracion_empresa_disponible_app
        from crm.mobile_modules import construir_contexto_modulos_app
        for actor in (self.supervisor, self.admin, self.users_admin, self.clinic_admin):
            request = RequestFactory().get("/")
            request.user = actor
            request.empresa = self.empresa
            self.assertEqual(configuracion_empresa_disponible_app(request, self.empresa), actor.is_superuser)
            context = construir_contexto_modulos_app(request, self.empresa)
            cards = {item["key"]: item for item in context["modulos_app"]}
            if actor.is_superuser:
                self.assertIn("empresa", cards)
                self.assertEqual(cards["empresa"]["estado"], "disponible")
                self.assertEqual(cards["empresa"]["url"], self.url())
                self.assertEqual(parse_qs(urlsplit(cards["empresa"]["url"]).query).get("app"), ["1"])
            else:
                self.assertNotIn("empresa", cards)

    def test_xss_primitives_are_escaped_and_no_password_or_global_control_links_exposed(self):
        Empresa.objects.filter(pk=self.empresa.pk).update(nombre='<img src=x onerror="alert(1)"> QA',
            observaciones_comerciales="<script>secret-qa</script>")
        for listing in (False, True):
            response = self.client.get(self.url(listing=listing))
            self.assertNotContains(response, '<img src=x onerror="alert(1)">')
            self.assertNotContains(response, "<script>secret-qa</script>")
            self.assertNotContains(response, self.supervisor.password)
            self.assertNotContains(response, 'href="'+reverse("superadmin_roles")+'"')
            self.assertNotContains(response, 'href="'+reverse("superadmin_usuarios")+'"')

    def test_mobile_does_not_create_global_companies_before_form_or_seed_queries(self):
        url = reverse("superadmin_empresa_create")+"?app=1"
        with patch("core.views.EmpresaControlForm") as constructor:
            for method in ("get", "post"):
                with CaptureQueriesContext(connection) as queries:
                    self.denied(url=url, method=method, data=self.data(slug="not-created-qa", rtn="NO-CREATED-QA"))
                self.no_writes(queries)
                constructor.assert_not_called()
        self.assertFalse(Empresa.objects.filter(slug="not-created-qa").exists())

    def test_native_hidden_initial_allowlist_is_exact_and_no_extra_initial_metadata_is_accepted(self):
        data = self.data(nombre="Initial canónico QA")
        for name, field in EmpresaControlForm.base_fields.items():
            if field.show_hidden_initial:
                data["initial-"+name] = str(self.client.get(self.url()).context["form"][name].value() or "")
        response = self.client.post(self.url(), data)
        self.assertEqual(response.status_code, 302)
        self.denied(method="post", data=self.data(**{"initial-unknown": "secret"}))

    def test_corrupt_plan_module_and_advanced_visibility_relations_are_denied_before_form(self):
        config = ConfiguracionAvanzadaEmpresa.objects.create(empresa=self.empresa)
        relation = EmpresaModulo.objects.create(empresa=self.empresa, modulo=self.extra)
        config.modulos_adicionales_visibles_clinica.add(self.other)
        visible_model = ConfiguracionAvanzadaEmpresa.modulos_adicionales_visibles_clinica.through
        visible = visible_model.objects.get(configuracionavanzadaempresa_id=config.pk)
        scenarios = (
            (Empresa, self.empresa.pk, "plan_comercial_id", self.empresa.plan_comercial_id),
            (EmpresaModulo, relation.pk, "modulo_id", relation.modulo_id),
            (visible_model, visible.pk, "modulo_id", visible.modulo_id),
        )
        with connection.constraint_checks_disabled():
            for model, pk, field, original in scenarios:
                try:
                    model.objects.filter(pk=pk).update(**{field: 2147483647})
                    with patch("core.views.EmpresaControlForm") as constructor:
                        self.denied()
                        self.denied(method="post", data=self.data())
                        constructor.assert_not_called()
                finally:
                    model.objects.filter(pk=pk).update(**{field: original})
