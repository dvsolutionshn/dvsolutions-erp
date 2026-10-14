"""Configuration navigation composes the existing, deliberately different ACLs.

Independent synthetic fixtures; no inherited tests, new account provisioner,
global-role saver, operational transport or financial configuration endpoint.
"""
from datetime import datetime, timezone as datetime_timezone
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlencode, urlsplit
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from django.db import connection, reset_queries
from django.http import Http404
from django.test import RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import resolve, reverse

from core.mobile_advanced_configuration import configuracion_avanzada_disponible_app
from core.mobile_company import configuracion_empresa_disponible_app
from core.mobile_users import usuarios_disponibles_app
from core.models import ConfiguracionAvanzadaEmpresa, Empresa, EmpresaModulo, Modulo, RolSistema, Usuario, UsuarioEmpresaPermiso
from crm import views as crm_views
from crm.mobile_crm_configuration import configuracion_crm_disponible_app
from crm.mobile_modules import construir_contexto_modulos_app
from crm.models import ConfiguracionCRM
from facturacion import views as billing_views
from .mobile_configuration import config_disponible_app
from .models import ProfesionalSalud, ServicioClinico


class _ConfigurationSectionMarkup(HTMLParser):
    def __init__(self, content):
        super().__init__(convert_charrefs=True)
        self.nodes = []
        self.form = None
        self.feed(content.decode() if isinstance(content, bytes) else content)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form": self.form = attrs.get("id")
        self.nodes.append((tag, {**attrs, "_form": self.form}))

    def handle_endtag(self, tag):
        if tag == "form": self.form = None

    @property
    def hrefs(self):
        return [attrs["href"] for tag, attrs in self.nodes if tag == "a" and attrs.get("href")]


@override_settings(ENABLE_DJANGO_ADMIN=True, EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", STORAGES={"default": {"BACKEND": "django.core.files.storage.InMemoryStorage"}, "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}})
class MobileConfigurationSectionNavigationTests(TestCase):
    SLUGS = ("hospital_mia", "medical_spa", "luque_aestetic", "serviciosmedicos")
    NOW = datetime(2026, 10, 14, 18, 0, tzinfo=datetime_timezone.utc)
    TOKEN = "SYNTHETIC-CONFIG-PRIVATE-TOKEN\r\n  "
    FULL = {
        "puede_clinica": True, "puede_configuracion_clinica": True,
        "puede_configuracion_crm": True, "puede_tratamientos_clinicos": True,
        "puede_ver_planes_tratamiento": True, "puede_editar_planes_tratamiento": True,
        "puede_editar_historia_clinica": True, "puede_expediente_clinico": True,
        "puede_ver_manuales_pdf": True, "puede_administrar_manuales_pdf": True,
    }

    @classmethod
    def setUpTestData(cls):
        with patch("django.utils.timezone.now", return_value=cls.NOW):
            cls.modules = {code: Modulo.objects.get_or_create(codigo=code, defaults={"nombre": f"Configuración QA {code}"})[0] for code in ("clinica_medica", "facturacion", "agenda_citas", "crm_marketing")}
            cls.empresas, cls.profesionales, cls.servicios, cls.advanced, cls.configsCRM = {}, {}, {}, {}, {}
            for number, slug in enumerate(cls.SLUGS, 1):
                company = Empresa.objects.create(nombre=f"Sección Configuración QA {slug}", slug=slug, rtn=f"CFG-SECTION-{number}", tipo_solucion="clinica", estado_licencia="activa")
                cls.empresas[slug] = company
                for module in cls.modules.values(): EmpresaModulo.objects.create(empresa=company, modulo=module)
                cls.advanced[slug] = ConfiguracionAvanzadaEmpresa.objects.create(empresa=company, manuales_pdf_habilitados=True, notas=f"Opciones preparadas {slug}")
                cls.configsCRM[slug] = ConfiguracionCRM.objects.create(empresa=company, whatsapp_token=cls.TOKEN, mensaje_cita_confirmacion=f"Mensaje propio {slug}")
                cls.profesionales[slug] = ProfesionalSalud.objects.create(empresa=company, nombre=f"Profesional Configuración {slug}", especialidad="Especialidad sintética")
                cls.servicios[slug] = ServicioClinico.objects.create(empresa=company, nombre=f"Servicio Configuración {slug}", categoria="consulta", duracion_minutos=30, color_calendario="#123abc")
            cls.empresa = cls.empresas["hospital_mia"]
            cls.external = Empresa.objects.create(nombre="Configuración fuera del scope", slug="configuracion_externa", rtn="CFG-SECTION-EXT", tipo_solucion="clinica", estado_licencia="activa")
            cls.roles = {
                name: RolSistema.objects.create(nombre=f"Configuración integrada {name}", codigo=f"cfg-section-{name}", es_rol_clinico=True, usa_permisos_clinicos_granulares=name == "granular", **cls.FULL)
                for name in ("granular", "legacy")
            }
            cls.selected_role = RolSistema.objects.create(nombre="Rol clínico de consulta QA", codigo="cfg-section-selected", es_rol_clinico=True, usa_permisos_clinicos_granulares=True)
            cls.actores = {}
            for mode in ("granular", "legacy"):
                cls.actores[mode] = Usuario.objects.create_user(username=f"cfg-section-{mode}", password="test-only", empresa=cls.empresa, rol_sistema=cls.roles[mode], puede_administrar_usuarios_clinicos=True, last_login=cls.NOW)
            cls.actores["super"] = Usuario.objects.create_superuser(username="cfg-section-super", email="super@example.invalid", password="test-only", empresa=cls.external, last_login=cls.NOW)
            cls.actores["userflag"] = Usuario.objects.create_user(username="cfg-section-userflag", password="test-only", empresa=cls.empresa, puede_administrar_usuarios_clinicos=True, last_login=cls.NOW)
            cls.actores["companyadmin"] = Usuario.objects.create_user(username="cfg-section-companyadmin", password="test-only", empresa=cls.empresa, es_administrador_empresa=True, last_login=cls.NOW)
            cls.actores["owner"] = Usuario.objects.create_user(username="daniel_varela", password="test-only", empresa=cls.empresa, rol_sistema=cls.roles["granular"], last_login=cls.NOW)
            cls.actores["none"] = Usuario.objects.create_user(username="cfg-section-none", password="test-only", empresa=cls.empresa, last_login=cls.NOW)
            ct = ContentType.objects.get_for_model(ConfiguracionAvanzadaEmpresa)
            for mode, codename in (("staffview", "view"), ("staffchange", "change"), ("staffadd", "add")):
                user = Usuario.objects.create_user(username=f"cfg-section-{mode}", password="test-only", empresa=cls.external, is_staff=True, last_login=cls.NOW)
                user.user_permissions.add(Permission.objects.get(content_type=ct, codename=f"{codename}_configuracionavanzadaempresa"))
                cls.actores[mode] = user
            for user in cls.actores.values():
                if not user.is_superuser: user.empresas_acceso.add(*cls.empresas.values())
            cls.targetroleuser = Usuario.objects.create_user(username="cfg-section-target", password="test-only", first_name="Persona sintética", empresa=cls.empresa, rol_sistema=cls.selected_role, last_login=cls.NOW)
            cls.targetroleuser.empresas_acceso.add(*cls.empresas.values())
            cls.configs = cls.advanced
            cls.crm_configs = cls.configsCRM

    def setUp(self):
        clock = patch("django.utils.timezone.now", return_value=self.NOW)
        clock.start(); self.addCleanup(clock.stop)
        self.login(self.actores["granular"])
        self.url()  # Resolve URL metadata only, never invoke a view.
        pdf = patch.object(billing_views, "HTML")
        renderer = pdf.start(); self.addCleanup(pdf.stop)
        renderer.return_value.write_pdf.return_value = b"%PDF-1.4\nSynthetic unused configuration renderer\n%%EOF"
        for target in ("crm.views.urlopen", "crm.views.enviar_plantilla_whatsapp", "crm.services._post_whatsapp", "crm.services._post_multipart", "core.views.enviar_correo_acceso", "core.views.emitir_token_acceso", "facturacion.views.enviar_documento_whatsapp", "facturacion.views.subir_documento_whatsapp", "django.core.mail.EmailMessage.send", "socket.create_connection", "socket.socket.connect", "socket.socket.connect_ex"):
            blocker = patch(target, side_effect=AssertionError(f"Configuration transport forbidden: {target}"))
            mock = blocker.start(); self.addCleanup(blocker.stop); self.addCleanup(mock.assert_not_called)

    def login(self, actor):
        self.actor = actor
        self.client.force_login(actor)

    def url(self, route="clinica_configuracion", company=None, obj=None, **query):
        company = company or self.empresa
        global_ids = {"superadmin_empresa_edit", "configuracion_avanzada_empresa_app"}
        no_args = {"superadmin_empresas_app", "configuracion_avanzada_empresas_app"}
        args = [] if route in no_args else [company.pk] if route in global_ids else [company.slug]
        if obj is not None: args.append(obj.pk if hasattr(obj, "pk") else obj)
        return reverse(route, args=args) + "?" + urlencode({"app": "1", **query}, doseq=True)

    def request(self, company=None, method="get", url=None, data=None):
        target = url or self.url(company=company)
        request = RequestFactory().post(target, data or {}) if method == "post" else RequestFactory().generic(method.upper(), target)
        request.user = Usuario.objects.get(pk=self.actor.pk)
        return request

    def snapshot(self):
        return {model._meta.label_lower: list(model._base_manager.order_by("pk").values()) for model in apps.get_models(include_auto_created=True) if model._meta.managed and not model._meta.proxy}

    def passive_get(self, url, method="get"):
        before = self.snapshot()
        reset_queries()
        with CaptureQueriesContext(connection) as queries:
            response = getattr(self.client, method)(url)
        self.assertEqual(response.status_code, 200, url)
        self.assertEqual(self.snapshot(), before, url)
        for query in queries: self.assertNotRegex(query["sql"].lstrip(), r"(?i)^(INSERT|UPDATE|DELETE|REPLACE|CREATE|ALTER|DROP)\b")
        if method == "head": self.assertEqual(response.content, b"")
        return response

    def cards(self, response):
        return {item["codigo"]: item for item in response.context["mobile_configuration_items"]}

    def direct(self, url, method="get", data=None):
        match = resolve(urlsplit(url).path)
        return match.func(self.request(url=url, method=method, data=data), **match.kwargs)

    def test_registry_configuration_is_available_with_same_original_mobile_destination_four_by_two(self):
        for mode in ("granular", "legacy"):
            self.login(self.actores[mode])
            for company in self.empresas.values():
                modules = construir_contexto_modulos_app(self.request(company), company)["modulos_app"]
                item = next(item for item in modules if item["key"] == "configuracion")
                self.assertEqual(item["estado"], "disponible")
                self.assertEqual(item["url"], self.url(company=company))
                self.passive_get(item["url"])

    def test_hub_offers_only_working_cards_without_billing_placeholder_or_false_pending_filter(self):
        for company in self.empresas.values():
            response = self.passive_get(self.url(company=company))
            cards = self.cards(response)
            self.assertNotIn("configuracion_facturacion", cards)
            self.assertFalse(response.context["mobile_configuration_has_pending"])
            self.assertNotContains(response, 'data-configuration-state-filter="pendiente"')
            self.assertTrue(all(item["estado"] == "disponible" and item["url"] for item in cards.values()))

    def test_every_offered_hub_destination_is_real_authorized_get_without_mutation(self):
        for mode in ("granular", "legacy", "super"):
            self.login(self.actores[mode])
            for company in self.empresas.values():
                response = self.passive_get(self.url(company=company))
                for item in self.cards(response).values():
                    with self.subTest(mode=mode, company=company.slug, card=item["codigo"]): self.passive_get(item["url"])

    def test_direct_roles_card_matches_users_gate_and_global_catalogue_is_readonly(self):
        for mode in ("granular", "legacy"):
            self.login(self.actores[mode])
            for company in self.empresas.values():
                cards = self.cards(self.passive_get(self.url(company=company)))
                self.assertEqual(cards["roles_clinicos"]["url"], self.url("roles_clinicos", company))
                roles = self.passive_get(cards["roles_clinicos"]["url"])
                self.assertFalse(roles.context["can_edit_global_roles"])
                self.assertNotContains(roles, 'method="post"')
                for route in ("rol_clinico_crear", "rol_clinico_editar"):
                    href = self.url(route, company, self.selected_role if route.endswith("editar") else None)
                    self.assertNotIn(href, _ConfigurationSectionMarkup(roles.content).hrefs)

    def test_roles_search_state_paging_and_return_keep_original_shared_roles_without_writes(self):
        for number in range(23): RolSistema.objects.create(nombre=f"SECTION SEARCH ROLE {number:02d}", codigo=f"cfg-search-{number}", es_rol_clinico=True, activo=True)
        roles = self.passive_get(self.url("roles_clinicos", q="SECTION SEARCH ROLE", estado="activos"))
        self.assertEqual(roles.context["mobile_roles_total"], 23)
        self.assertEqual(roles.context["mobile_roles_page"], 1)
        next_url = roles.context["mobile_roles_next_url"]
        self.assertEqual(parse_qs(urlsplit(next_url).query), {"app": ["1"], "q": ["SECTION SEARCH ROLE"], "estado": ["activos"], "page": ["2"]})
        self.assertEqual(self.passive_get(next_url).context["mobile_roles_page"], 2)
        self.assertEqual(roles.context["app_roles_back_url"], self.url("usuarios_clinicos"))

    def test_users_only_without_modules_or_operational_license_keeps_original_standalone_roundtrip(self):
        self.login(self.actores["userflag"])
        for company in self.empresas.values():
            EmpresaModulo.objects.filter(empresa=company).update(activo=False)
            Empresa.objects.filter(pk=company.pk).update(estado_licencia="suspendida")
            for route in ("usuarios_clinicos", "roles_clinicos"):
                response = self.passive_get(self.url(route, company))
                self.assertFalse(response.context["app_home_disponible"])
                self.assertNotContains(response, 'rel="manifest"')
            modules = self.passive_get(reverse("agenda_mobile_modules_screen", args=[company.slug]))
            self.assertEqual({item["key"] for item in modules.context["modulos_app"]}, {"usuarios"})

    def test_clinical_configuration_role_alone_is_not_user_master_or_django_administration(self):
        Usuario.objects.filter(pk=self.actor.pk).update(puede_administrar_usuarios_clinicos=False)
        self.actor = Usuario.objects.get(pk=self.actor.pk)
        request = self.request()
        self.assertTrue(config_disponible_app(request, self.empresa)["hub"])
        self.assertFalse(usuarios_disponibles_app(request, self.empresa))
        self.assertFalse(configuracion_empresa_disponible_app(request, self.empresa))
        self.assertFalse(any(configuracion_avanzada_disponible_app(request, self.empresa).values()))
        cards = self.cards(self.passive_get(self.url()))
        self.assertTrue({"usuarios_roles", "roles_clinicos", "empresa", "configuracion_avanzada"}.isdisjoint(cards))

    def test_company_admin_does_not_substitute_users_flag_super_or_django_model_rights(self):
        self.login(self.actores["companyadmin"])
        for company in self.empresas.values():
            request = self.request(company)
            self.assertFalse(usuarios_disponibles_app(request, company))
            self.assertFalse(configuracion_empresa_disponible_app(request, company))
            self.assertFalse(any(configuracion_avanzada_disponible_app(request, company).values()))
            cards = self.cards(self.passive_get(self.url(company=company)))
            self.assertTrue({"usuarios_roles", "roles_clinicos", "empresa", "configuracion_avanzada"}.isdisjoint(cards))

    def test_professional_and_service_edit_flags_retain_different_original_admin_and_owner_rules(self):
        for mode in ("granular", "companyadmin", "super", "owner"):
            self.login(self.actores[mode])
            for company in self.empresas.values():
                capability = config_disponible_app(self.request(company), company)
                self.assertEqual(capability["editar_profesionales"], mode in {"companyadmin", "super"})
                expected = mode == "owner" and company.slug == "hospital_mia"
                self.assertEqual(capability["editar_servicios"], expected)
                self.assertEqual(capability["eliminar_servicios"], expected)

    def test_company_super_original_global_scope_includes_inactive_suspended_companies_without_membership(self):
        self.login(self.actores["super"])
        for company in self.empresas.values():
            Empresa.objects.filter(pk=company.pk).update(activa=False, estado_licencia="suspendida")
            EmpresaModulo.objects.filter(empresa=company).update(activo=False)
            response = self.passive_get(self.url("superadmin_empresa_edit", company))
            self.assertFalse(response.context["app_home_disponible"])
        picker = self.passive_get(self.url("superadmin_empresas_app"))
        self.assertEqual(picker.context["total_empresas"], 4)

    def test_master_editor_not_granted_by_users_flag_clinical_role_company_admin_or_staff(self):
        for mode in ("userflag", "granular", "companyadmin", "staffchange"):
            self.login(self.actores[mode])
            self.assertFalse(configuracion_empresa_disponible_app(self.request(), self.empresa))
            from core.mobile_company import autorizar_empresa_app
            with self.assertRaises(PermissionDenied): autorizar_empresa_app(self.request(url=self.url("superadmin_empresa_edit")), self.empresa.pk)

    def test_django_advanced_view_change_and_add_are_separate_without_erp_substitutes(self):
        expected = {"staffview": {"ver": True, "editar": False, "crear": False}, "staffchange": {"ver": True, "editar": True, "crear": False}, "staffadd": {"ver": False, "editar": False, "crear": True}}
        for mode, capabilities in expected.items():
            self.login(self.actores[mode])
            self.assertEqual(configuracion_avanzada_disponible_app(self.request(), self.empresa), capabilities)
            response = self.passive_get(self.url("configuracion_avanzada_empresa_app" if mode != "staffadd" else "configuracion_avanzada_empresas_app"))
            if mode == "staffview": self.assertNotContains(response, 'id="mobileAdvancedForm"')
            if mode == "staffchange": self.assertContains(response, 'id="mobileAdvancedForm"')

    def test_advanced_global_django_rights_preserve_inactive_company_and_no_module_license_requirements(self):
        self.login(self.actores["staffview"])
        self.actor.empresas_acceso.clear()
        for company in self.empresas.values():
            Empresa.objects.filter(pk=company.pk).update(activa=False, estado_licencia="vencida")
            EmpresaModulo.objects.filter(empresa=company).update(activo=False)
            response = self.passive_get(self.url("configuracion_avanzada_empresa_app", company))
            self.assertNotContains(response, 'id="mobileAdvancedForm"')
            self.assertNotContains(response, 'rel="manifest"')

    def test_disabled_django_admin_closes_advanced_cards_and_does_not_enable_an_alternate_editor(self):
        self.login(self.actores["super"])
        with override_settings(ENABLE_DJANGO_ADMIN=False):
            self.assertFalse(any(configuracion_avanzada_disponible_app(self.request(), self.empresa).values()))
            self.assertNotIn("configuracion_avanzada", self.cards(self.passive_get(self.url())))

    def test_crm_own_gate_and_token_status_do_not_expose_secret_or_offer_oauth_test_send(self):
        for company in self.empresas.values():
            response = self.passive_get(self.url("crm_configuracion", company))
            self.assertTrue(response.context["mobile_crm_token_present"])
            self.assertNotContains(response, self.TOKEN)
            self.assertNotContains(response, 'name="whatsapp_token"')
            hrefs = _ConfigurationSectionMarkup(response.content).hrefs
            for route in ("crm_conectar_whatsapp_meta", "crm_enviar_prueba_whatsapp"):
                self.assertNotIn(self.url(route, company), hrefs)

    def test_missing_crm_and_advanced_gets_do_not_seed_configuration_or_content_type(self):
        ConfiguracionCRM.objects.filter(empresa=self.empresa).delete()
        ConfiguracionAvanzadaEmpresa.objects.filter(empresa=self.empresa).delete()
        self.passive_get(self.url("crm_configuracion"))
        self.passive_get(self.url("crm_configuracion"), method="head")
        self.login(self.actores["super"])
        self.passive_get(self.url("superadmin_empresa_edit"))
        self.passive_get(self.url("configuracion_avanzada_empresa_app"))
        self.assertFalse(ConfiguracionCRM.objects.filter(empresa=self.empresa).exists())
        self.assertFalse(ConfiguracionAvanzadaEmpresa.objects.filter(empresa=self.empresa).exists())

    def test_readonly_get_head_all_five_families_preserve_every_model_and_auth_through_table(self):
        self.login(self.actores["super"])
        for route in ("clinica_configuracion", "usuarios_clinicos", "roles_clinicos", "superadmin_empresa_edit", "configuracion_avanzada_empresa_app", "crm_configuracion"):
            self.passive_get(self.url(route))
            self.passive_get(self.url(route), method="head")

    def test_native_forms_keep_original_field_counts_and_no_extra_identity_or_secret_metadata(self):
        self.login(self.actores["super"])
        for route, expected in (("superadmin_empresa_edit", 25), ("configuracion_avanzada_empresa_app", 14), ("crm_configuracion", 26)):
            response = self.passive_get(self.url(route))
            self.assertEqual(len(response.context["form"].fields), expected)
            self.assertNotIn("whatsapp_token", response.context["form"].fields)
        assignment = self.passive_get(self.url("usuario_clinico_permisos", obj=self.targetroleuser))
        parsed = _ConfigurationSectionMarkup(assignment.content)
        names = {attrs["name"] for tag, attrs in parsed.nodes if tag in {"input", "select", "textarea", "button"} and attrs.get("_form") == "mobileUserPermissionForm" and attrs.get("name")}
        self.assertEqual(names, {"app", "csrfmiddlewaretoken", "rol_sistema"})

    def test_global_role_create_edit_app_markers_remain_denied_before_global_role_queries(self):
        for route, obj in (("rol_clinico_crear", None), ("rol_clinico_editar", self.selected_role)):
            for method in ("get", "post"):
                before = self.snapshot()
                reset_queries()
                with CaptureQueriesContext(connection) as queries:
                    with self.assertRaises(PermissionDenied): self.direct(self.url(route, obj=obj), method=method, data={"app": "1", "nombre": "No guardar"})
                self.assertEqual(self.snapshot(), before)
                self.assertFalse(any('"core_rolsistema"' in query["sql"] for query in queries))

    def test_invalid_assignment_target_role_does_not_change_shared_roles_or_local_permissions(self):
        nonclinical = RolSistema.objects.create(nombre="Rol global fuera del selector", codigo="cfg-section-nonclinical")
        before = self.snapshot()
        result = self.client.post(self.url("usuario_clinico_permisos", obj=self.targetroleuser), {"app": "1", "rol_sistema": str(nonclinical.pk)})
        self.assertEqual(result.status_code, 404)
        self.assertEqual(self.snapshot(), before)

    def test_local_no_role_override_hides_clinical_crm_but_keeps_independent_users_flag(self):
        UsuarioEmpresaPermiso.objects.update_or_create(usuario=self.actor, empresa=self.empresa, defaults={"rol_sistema": None, "activo": True})
        # No effective granular role retains the original legacy read-only hub;
        # it does not grant either catalogue action or CRM configuration.
        capabilities = config_disponible_app(self.request(), self.empresa)
        self.assertTrue(capabilities["hub"])
        self.assertFalse(capabilities["profesionales"])
        self.assertFalse(capabilities["servicios"])
        self.assertFalse(configuracion_crm_disponible_app(self.request(), self.empresa))
        self.assertTrue(usuarios_disponibles_app(self.request(), self.empresa))
        hub = self.passive_get(self.url())
        self.assertTrue({"profesionales", "servicios", "crm"}.isdisjoint(self.cards(hub)))
        # The original menu includes the independent users-administration flag.
        modules = construir_contexto_modulos_app(self.request(), self.empresa)["modulos_app"]
        card = next(item for item in modules if item["key"] == "configuracion")
        self.assertEqual((card["estado"], card["url"]), ("disponible", self.url()))
        self.passive_get(self.url("usuarios_clinicos"))
        self.passive_get(self.url("roles_clinicos"))

    def test_scope_and_membership_do_not_expand_through_configuration_cards_or_global_readonly_roles(self):
        request = self.request(self.external)
        self.assertFalse(config_disponible_app(request, self.external)["hub"])
        self.assertFalse(usuarios_disponibles_app(request, self.external))
        self.assertFalse(configuracion_crm_disponible_app(request, self.external))
        self.login(self.actores["super"])
        self.assertFalse(configuracion_empresa_disponible_app(self.request(self.external), self.external))
        self.assertFalse(any(configuracion_avanzada_disponible_app(self.request(self.external), self.external).values()))
        self.login(self.actores["granular"])
        company = self.empresas["medical_spa"]
        self.actor.empresas_acceso.remove(company)
        self.assertFalse(usuarios_disponibles_app(self.request(company), company))
        self.assertFalse(config_disponible_app(self.request(company), company)["hub"])

    def test_labels_search_and_company_identity_are_escaped_without_credentials_or_financial_links(self):
        Empresa.objects.filter(pk=self.empresa.pk).update(nombre='Clínica <img src=x onerror="alert(1)"> & QA')
        role = self.roles["granular"]
        RolSistema.objects.filter(pk=role.pk).update(nombre='Rol <script>NO_EJECUTAR</script> & QA')
        response = self.passive_get(self.url())
        self.assertNotContains(response, '<img src=x onerror="alert(1)">')
        self.assertNotContains(response, '<script>NO_EJECUTAR</script>')
        self.assertNotContains(response, self.TOKEN)
        cards = self.cards(response)
        self.assertTrue({"configuracion_facturacion", "contabilidad", "rrhh", "tecnicentro"}.isdisjoint(cards))
        for item in cards.values():
            self.assertEqual(parse_qs(urlsplit(item["url"]).query).get("app"), ["1"])

    def test_unflagged_web_forms_and_role_routes_keep_original_templates(self):
        self.login(self.actores["super"])
        cases = (("clinica_configuracion", "clinica/configuracion.html"), ("usuarios_clinicos", "core/usuarios_clinicos.html"), ("roles_clinicos", "core/roles_clinicos.html"), ("crm_configuracion", "crm/form.html"))
        for route, template in cases:
            url = reverse(route, args=[self.empresa.slug])
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertTemplateUsed(response, template)
            self.assertNotContains(response, 'id="mobileUserPermissionForm"')
