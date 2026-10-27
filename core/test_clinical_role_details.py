"""Descriptions explain configured flags; they never resolve or grant access.

All objects are synthetic. Existing assignment forms and savers retain their
native fields, local-company scope, and independent account authorizations.
"""
from datetime import datetime, timezone as datetime_timezone
from html.parser import HTMLParser
import json
import re
from urllib.parse import urlencode, urlsplit
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.exceptions import PermissionDenied
from django.db import connection, reset_queries
from django.http import Http404
from django.test import RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import resolve, reverse

from . import test_mobile_users as _users_tests
from . import views
from .clinical_permissions import PERMISOS_CLINICOS
from .models import Empresa, RegistroAuditoria, RolSistema, Usuario, UsuarioEmpresaPermiso


class _RoleDetailsMarkup(HTMLParser):
    def __init__(self, content):
        super().__init__(convert_charrefs=True)
        self.nodes, self.scripts, self.controls = [], {}, []
        self.script_id, self.in_post_form = None, False
        self.feed(content.decode() if isinstance(content, bytes) else content)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs); self.nodes.append((tag, values))
        if tag == "script" and values.get("id"):
            self.script_id = values["id"]; self.scripts[self.script_id] = ""
        if tag == "form": self.in_post_form = values.get("method", "get").lower() == "post"
        if self.in_post_form and tag in {"input", "select", "textarea", "button"} and values.get("name"):
            self.controls.append((tag, values))

    def handle_data(self, data):
        if self.script_id: self.scripts[self.script_id] += data

    def handle_endtag(self, tag):
        if tag == "script": self.script_id = None
        if tag == "form": self.in_post_form = False


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class ClinicalRoleDetailsTests(TestCase):
    SLUGS = _users_tests.MobileClinicalUsersTests.SLUGS
    NOW = datetime(2026, 10, 23, 18, tzinfo=datetime_timezone.utc)
    FIELDS = tuple(field for _, entries in PERMISOS_CLINICOS for field, _ in entries)

    @classmethod
    def setUpTestData(cls):
        with patch("django.utils.timezone.now", return_value=cls.NOW):
            _users_tests.MobileClinicalUsersTests.setUpTestData.__func__(cls)
            cls.extra_roles = [RolSistema.objects.create(
                nombre=f"Detalle rol {index:02d}", codigo=f"role-description-{index}",
                es_rol_clinico=True, usa_permisos_clinicos_granulares=True,
                puede_ver_pacientes=bool(index % 2), puede_editar_pacientes=not bool(index % 2),
                activo=index != 24,
            ) for index in range(25)]
            cls.legacy_clinical = RolSistema.objects.create(
                nombre="Legado clínico descripción QA", codigo="role-description-legacy",
                es_rol_clinico=True, puede_pacientes=True, puede_expediente_clinico=True,
                puede_contabilidad=True,
            )
            cls.admin_target = Usuario.objects.create_user(
                username="role-description-independent-admin", password="test-only",
                empresa=cls.empresa, rol_sistema=cls.role_a, es_administrador_empresa=True,
            )

    snapshot = _users_tests.MobileClinicalUsersTests.snapshot

    def setUp(self):
        clock = patch("django.utils.timezone.now", return_value=self.NOW)
        clock.start(); self.addCleanup(clock.stop)
        self.client.force_login(self.actor)
        self.url()  # Resolve metadata only, before transport mocks.
        for name in ("core.views.enviar_correo_acceso", "core.views.emitir_token_acceso", "facturacion.views.enviar_documento_whatsapp", "facturacion.views.subir_documento_whatsapp", "crm.views.urlopen", "crm.services._post_whatsapp", "django.core.mail.EmailMessage.send", "socket.create_connection", "socket.socket.connect", "socket.socket.connect_ex", "facturacion.views.HTML"):
            blocker = patch(name, side_effect=AssertionError(f"No role-description transport: {name}"))
            mock = blocker.start(); self.addCleanup(blocker.stop); self.addCleanup(mock.assert_not_called)

    def url(self, assignment=False, company=None, target=None, mobile=True, **params):
        company = company or self.empresa; target = target or self.targets.get(company.slug, self.target)
        route = "usuario_clinico_permisos" if assignment else "roles_clinicos"
        args = [company.slug] + ([target.pk] if assignment else [])
        query = {"app": "1", **params} if mobile else params
        return reverse(route, args=args) + ("?"+urlencode(query) if query else "")

    def immutable(self, url=None, method="get"):
        before = self.snapshot(); reset_queries()
        with CaptureQueriesContext(connection) as queries:
            response = getattr(self.client, method)(url or self.url())
        self.assertEqual([row["sql"] for row in queries if re.match(r"\s*(INSERT|UPDATE|DELETE|REPLACE|CREATE|ALTER|DROP)\b", row["sql"], re.I)], [])
        self.assertEqual(self.snapshot(), before)
        return response

    def detail(self, role):
        from .clinical_role_details import detalle_rol_clinico
        reset_queries()
        with CaptureQueriesContext(connection) as queries: result = detalle_rol_clinico(role)
        self.assertEqual(len(queries), 0)
        return result

    def entries(self, detail):
        return [entry for group in detail["grupos"] for entry in group["permisos"]]

    def direct(self, url, actor=None, method="get", data=None):
        factory = RequestFactory()
        request = factory.post(url, data or {}) if method == "post" else factory.get(url)
        request.user = actor if actor is not None else Usuario.objects.get(pk=self.actor.pk)
        request.session = {}; request._messages = FallbackStorage(request)
        match = resolve(urlsplit(url).path); request.resolver_match = match
        return match.func(request, **match.kwargs)

    def assert_old_visible_authority(self, role, detail):
        expected = views._permisos_visibles_rol(role)
        self.assertEqual([{ "grupo": group["grupo"], "permisos": [entry["etiqueta"] for entry in group["permisos"]]} for group in detail["grupos"]], expected)
        self.assertEqual(detail["total_permisos"], sum(len(group["permisos"]) for group in expected))

    def test_none_role_metadata_is_none_and_queries_nothing(self):
        self.assertIsNone(self.detail(None))

    def test_complete_clinical_catalog_has_exact_labels_and_nonempty_descriptions(self):
        role = RolSistema(pk=900001, nombre="Personalizado completo QA", es_rol_clinico=True, usa_permisos_clinicos_granulares=True, **{name: True for name in self.FIELDS})
        detail = self.detail(role)
        self.assertEqual({entry["campo"] for entry in self.entries(detail)}, set(self.FIELDS))
        self.assertEqual(len(self.entries(detail)), len(self.FIELDS))
        self.assert_old_visible_authority(role, detail)
        self.assertEqual(detail["modo"], "granular")
        self.assertTrue(detail["modo_label"])
        for entry in self.entries(detail):
            self.assertEqual(set(entry), {"campo", "etiqueta", "descripcion"})
            self.assertIsInstance(entry["descripcion"], str)
            self.assertTrue(entry["descripcion"].strip())

    def test_complete_legacy_catalog_uses_original_legacy_authority_not_granular_names(self):
        fields = tuple(name for _, group in views.PERMISOS_ROL_CLINICO_LEGADO for name, _ in group)
        role = RolSistema(pk=900002, nombre="Legado personalizado QA", **{name: True for name in fields})
        detail = self.detail(role)
        self.assertEqual(detail["modo"], "legado")
        self.assertEqual({entry["campo"] for entry in self.entries(detail)}, set(fields))
        self.assert_old_visible_authority(role, detail)
        for entry in self.entries(detail): self.assertTrue(entry["descripcion"].strip())

    def test_only_actual_true_flags_appear_even_for_same_names_and_custom_role_codes(self):
        first = RolSistema(pk=900003, nombre="Doctora QA", codigo="arbitrary-a", es_rol_clinico=True, usa_permisos_clinicos_granulares=True, puede_editar_pacientes=True)
        second = RolSistema(pk=900004, nombre=first.nombre, codigo="arbitrary-b", es_rol_clinico=True, usa_permisos_clinicos_granulares=True, puede_crear_recetas=True)
        self.assertEqual([entry["campo"] for entry in self.entries(self.detail(first))], ["puede_editar_pacientes"])
        self.assertEqual([entry["campo"] for entry in self.entries(self.detail(second))], ["puede_crear_recetas"])
        self.assertEqual(self.detail(first)["nombre"], self.detail(second)["nombre"])

    def test_inactive_role_describes_configured_flags_but_does_not_grant_any_effective_permission(self):
        detail = self.detail(self.inactive_role)
        self.assertFalse(detail["activo"])
        self.assertEqual([entry["campo"] for entry in self.entries(detail)], ["puede_ver_pacientes"])
        self.assertTrue(detail["advertencias"])
        user = Usuario(username="Unsaved role-reader QA", rol_sistema=self.inactive_role)
        self.assertFalse(user.tiene_permiso_erp("puede_ver_pacientes"))

    def test_non_catalog_flags_are_not_invented_as_configured_permissions(self):
        role = RolSistema(pk=900005, nombre="Flags ocultos QA", es_rol_clinico=True, usa_permisos_clinicos_granulares=True, puede_contabilidad=True, puede_rrhh=True, puede_campanias=True)
        detail = self.detail(role)
        self.assertEqual(detail["total_permisos"], 0)
        self.assertEqual(detail["grupos"], [])
        self.assertTrue(detail["advertencias"])

    def test_pure_detail_has_stable_metadata_keys_and_does_not_mutate_the_role(self):
        before = vars(self.role_a).copy()
        detail = self.detail(self.role_a)
        self.assertEqual(set(detail), {"id", "nombre", "descripcion_registrada", "activo", "modo", "modo_label", "total_permisos", "grupos", "advertencias"})
        self.assertEqual((detail["id"], detail["nombre"]), (self.role_a.pk, self.role_a.nombre))
        self.assertEqual(vars(self.role_a), before)
        self.assert_old_visible_authority(self.role_a, detail)
        from .clinical_role_details import contexto_detalles_roles
        with CaptureQueriesContext(connection) as queries:
            context = contexto_detalles_roles(self.empresa, [self.role_a], self.base_role, self.actor)
            outside = contexto_detalles_roles(self.externa, [self.role_a], self.role_a, self.actor)
        self.assertEqual(len(queries), 0)
        self.assertTrue(context["clinical_role_help_enabled"])
        self.assertIsNone(context["clinical_role_selection_details"])
        self.assertEqual(context["clinical_current_role_details"], self.detail(self.base_role))
        self.assertEqual(outside, {"clinical_role_help_enabled": False})

    def test_four_companies_both_actor_modes_show_app_and_web_details_without_any_model_writes(self):
        for granular in (False, True):
            role = RolSistema.objects.create(nombre="Actor descripciones QA", codigo=f"role-description-actor-{granular}", es_rol_clinico=granular, usa_permisos_clinicos_granulares=granular)
            Usuario.objects.filter(pk=self.actor.pk).update(rol_sistema=role)
            for company in self.empresas.values():
                for mobile in (False, True):
                    for assignment in (False, True):
                        page = self.immutable(self.url(assignment, company, mobile=mobile))
                        self.assertEqual(page.status_code, 200)
                        self.assertTrue(page.context["clinical_role_help_enabled"])
                        self.assertTemplateUsed(page, "core/"+("mobile_user_permissions.html" if assignment else "mobile_roles_list.html") if mobile else "core/"+("usuario_clinico_permisos.html" if assignment else "roles_clinicos.html"))

    def test_app_role_pagination_keeps_old_permission_labels_and_details_for_exact_visible_roles(self):
        for number in (1, 2):
            page = self.immutable(self.url(q="Detalle rol", page=number))
            self.assertEqual(page.status_code, 200)
            self.assertEqual(page.context["mobile_roles_total"], 25)
            self.assertEqual(len(page.context["mobile_role_items"]), 20 if number == 1 else 5)
            for item in page.context["mobile_role_items"]:
                self.assertEqual(item["permisos"], views._permisos_visibles_rol(item["rol"]))
                self.assertEqual(item["detalle"], self.detail(item["rol"]))

    def test_web_catalog_uses_all_original_global_roles_with_existing_owner_controls_unchanged(self):
        page = self.immutable(self.url(mobile=False))
        items = page.context["clinical_role_items"]
        self.assertEqual([item["rol"].pk for item in items], list(page.context["roles"].values_list("pk", flat=True)))
        self.assertContains(page, reverse("rol_clinico_crear", args=[self.empresa.slug]))
        for item in items:
            self.assertEqual(item["detalle"], self.detail(item["rol"]))
            self.assertContains(page, reverse("rol_clinico_editar", args=[self.empresa.slug, item["rol"].pk]))

    def test_assignment_metadata_contains_every_option_not_only_a_role_catalog_page(self):
        expected = set(RolSistema.objects.filter(es_rol_clinico=True).values_list("pk", flat=True))
        self.assertGreater(len(expected), 20)
        for mobile in (False, True):
            page = self.immutable(self.url(True, mobile=mobile))
            details = page.context["clinical_role_details"]
            self.assertEqual(set(details), {str(pk) for pk in expected})
            self.assertEqual(set(details), {str(role.pk) for role in page.context["roles"]})
            for role in page.context["roles"]: self.assertEqual(details[str(role.pk)], self.detail(role))
            self.assertIn(str(self.extra_roles[-1].pk), details)

    def test_current_legacy_role_outside_options_is_described_separately_without_automatic_selection(self):
        page = self.immutable(self.url(True))
        self.assertEqual(page.context["clinical_current_role_details"], self.detail(self.base_role))
        self.assertNotIn(str(self.base_role.pk), page.context["clinical_role_details"])
        self.assertTrue(page.context["mobile_user_role_requires_selection"])
        options = [attrs for tag, attrs in _RoleDetailsMarkup(page.content).nodes if tag == "option"]
        selected = [attrs for attrs in options if "selected" in attrs]
        self.assertEqual(len(selected), 1)
        self.assertIn("disabled", selected[0])
        self.assertIn("data-role-placeholder", selected[0])
        self.assertNotIn("data-role-clear", selected[0])

    def test_explicit_none_override_has_no_current_detail_and_no_fabricated_permissions(self):
        UsuarioEmpresaPermiso.objects.create(usuario=self.target, empresa=self.empresa, rol_sistema=None)
        for mobile in (False, True):
            page = self.immutable(self.url(True, mobile=mobile))
            self.assertIsNone(page.context["clinical_current_role_details"])
            self.assertFalse(page.context["clinical_role_independent_admin"])

    def test_current_clinical_role_keeps_original_selected_option_and_original_flags(self):
        UsuarioEmpresaPermiso.objects.create(usuario=self.target, empresa=self.empresa, rol_sistema=self.role_a)
        for mobile in (False, True):
            page = self.immutable(self.url(True, mobile=mobile))
            self.assertEqual(page.context["clinical_current_role_details"], self.detail(self.role_a))
            selected = [attrs for tag, attrs in _RoleDetailsMarkup(page.content).nodes if tag == "option" and "selected" in attrs]
            self.assertEqual([attrs.get("value") for attrs in selected], [str(self.role_a.pk)])

    def test_json_script_round_trips_hostile_role_name_without_executable_markup_or_account_data(self):
        hostile = '</script><svg onload="ROLE-DESCRIPTION-XSS"> & " \' descripción'
        RolSistema.objects.filter(pk=self.role_a.pk).update(nombre=hostile, descripcion=hostile)
        for mobile in (False, True):
            page = self.immutable(self.url(True, mobile=mobile))
            markup = _RoleDetailsMarkup(page.content)
            encoded = markup.scripts["clinicalRoleDetailsData"]
            decoded = json.loads(encoded)
            self.assertEqual(decoded[str(self.role_a.pk)]["nombre"], hostile)
            self.assertNotIn("<svg", encoded)
            self.assertNotIn("</script>", encoded)
            self.assertNotContains(page, '<svg onload="ROLE-DESCRIPTION-XSS">')
            self.assertNotIn(self.target.password, encoded)
            self.assertNotIn(self.target.email, encoded)

    def test_catalog_role_name_and_free_description_are_autoescaped_on_app_and_web(self):
        hostile = '<img src=x onerror="ROLE-CATALOG-XSS"> & "'
        RolSistema.objects.filter(pk=self.role_a.pk).update(nombre=hostile, descripcion=hostile)
        for mobile in (False, True):
            page = self.immutable(self.url(mobile=mobile))
            self.assertNotContains(page, '<img src=x onerror="ROLE-CATALOG-XSS">')
            self.assertContains(page, "&lt;img")

    def test_native_post_fields_are_identical_to_original_assignment_contracts(self):
        for mobile in (False, True):
            page = self.immutable(self.url(True, mobile=mobile)); markup = _RoleDetailsMarkup(page.content)
            names = [attrs["name"] for _, attrs in markup.controls]
            self.assertEqual(set(names), {"rol_sistema", "csrfmiddlewaretoken"} | ({"app"} if mobile else set()))
            self.assertEqual(len(names), len(set(names)))
            self.assertFalse(any(name in names for name in ("clinical_role_details", "nombre", "descripcion", "usuario", "empresa", "is_superuser")))

    def test_app_assignment_uses_original_local_saver_and_never_changes_role_definition_or_account(self):
        page = self.immutable(self.url(True)); self.assertTrue(page.context["clinical_role_details"])
        before_roles = list(RolSistema.objects.order_by("pk").values())
        before_users = list(Usuario.objects.order_by("pk").values())
        response = self.client.post(self.url(True), {"app": "1", "rol_sistema": self.role_b.pk})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("usuarios_clinicos", args=[self.empresa.slug])+"?app=1")
        self.assertEqual(UsuarioEmpresaPermiso.objects.get(usuario=self.target, empresa=self.empresa).rol_sistema_id, self.role_b.pk)
        self.assertEqual(list(RolSistema.objects.order_by("pk").values()), before_roles)
        self.assertEqual(list(Usuario.objects.order_by("pk").values()), before_users)

    def test_web_assignment_still_accepts_original_body_and_redirect_without_app_flag(self):
        before_roles = list(RolSistema.objects.order_by("pk").values())
        response = self.client.post(self.url(True, mobile=False), {"rol_sistema": self.role_a.pk})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("usuarios_clinicos", args=[self.empresa.slug]))
        self.assertEqual(UsuarioEmpresaPermiso.objects.get(usuario=self.target, empresa=self.empresa).rol_sistema_id, self.role_a.pk)
        self.assertEqual(list(RolSistema.objects.order_by("pk").values()), before_roles)

    def test_none_and_inactive_assignments_keep_original_effective_permission_rules(self):
        for role in (None, self.inactive_role):
            self.assertEqual(self.client.post(self.url(True), {"app": "1", "rol_sistema": role.pk if role else ""}).status_code, 302)
            user = Usuario.objects.get(pk=self.target.pk)
            if role is None:
                self.assertIsNone(user.rol_para_empresa(self.empresa))
            else:
                self.assertEqual(user.rol_para_empresa(self.empresa).pk, role.pk)
            self.assertFalse(user.tiene_permiso_erp("puede_ver_pacientes", self.empresa))

    def test_independent_administrator_warning_and_rights_are_not_redefined_by_descriptions(self):
        for mobile in (False, True):
            page = self.immutable(self.url(True, target=self.admin_target, mobile=mobile))
            self.assertTrue(page.context["clinical_role_independent_admin"])
            self.assertContains(page, "administrador")
        self.assertEqual(self.client.post(self.url(True, target=self.admin_target), {"app": "1", "rol_sistema": ""}).status_code, 302)
        self.admin_target.refresh_from_db()
        self.assertTrue(self.admin_target.es_administrador_empresa)
        self.assertTrue(self.admin_target.tiene_permiso_erp("puede_editar_pacientes", self.empresa))

    def test_user_flag_guard_and_original_license_module_exemptions_are_unchanged(self):
        for state in ("activa", "suspendida", "vencida"):
            Empresa.objects.filter(pk=self.empresa.pk).update(estado_licencia=state)
            self.assertEqual(self.immutable().status_code, 200)
            self.assertEqual(self.immutable(self.url(True)).status_code, 200)
        Usuario.objects.filter(pk=self.actor.pk).update(puede_administrar_usuarios_clinicos=False, es_administrador_empresa=True)
        before = self.snapshot()
        for mobile in (False, True):
            if mobile:
                with self.assertRaises(PermissionDenied): self.direct(self.url(mobile=mobile))
            else: self.assertEqual(self.direct(self.url(mobile=mobile)).status_code, 403)
        self.assertEqual(self.snapshot(), before)

    def test_unauthorized_foreign_user_and_outside_four_keep_original_denials_without_metadata_queries(self):
        before = self.snapshot()
        with self.assertRaises(Http404): self.direct(self.url(True, target=self.foreign))
        self.assertEqual(self.direct(self.url(company=self.externa, mobile=False)).status_code, 403)
        anonymous = self.direct(self.url(), actor=AnonymousUser())
        self.assertEqual(anonymous.status_code, 302)
        self.assertEqual(self.snapshot(), before)

    def test_app_catalog_adds_no_global_edit_create_or_new_account_links_and_keeps_readonly_post_rejection(self):
        page = self.immutable(); urls = {attrs.get("href", "") for tag, attrs in _RoleDetailsMarkup(page.content).nodes if tag == "a"}
        self.assertNotIn(reverse("rol_clinico_crear", args=[self.empresa.slug]), urls)
        self.assertNotIn(reverse("rol_clinico_editar", args=[self.empresa.slug, self.role_a.pk]), urls)
        before = self.snapshot()
        with self.assertRaises(PermissionDenied): self.direct(self.url(), method="post", data={"app": "1", "nombre": "No role writes"})
        self.assertEqual(self.snapshot(), before)
