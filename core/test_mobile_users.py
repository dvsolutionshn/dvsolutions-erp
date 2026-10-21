"""La administración móvil asigna roles existentes sólo a la empresa actual.

El catálogo de roles sigue siendo global y de consulta: esta interfaz no crea
usuarios, cambia contraseñas ni modifica capacidades de los roles reutilizados.
"""
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlencode, urlsplit
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth.models import AnonymousUser
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.http import Http404
from django.test import Client, RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import resolve, reverse

from . import views
from .models import (
    ConfiguracionAvanzadaEmpresa, Empresa, EmpresaModulo, Modulo,
    RegistroAuditoria, RolSistema, Usuario, UsuarioEmpresaPermiso,
)


class _NativeRoleMarkup(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.select = False
        self.options = []
        self.form = False
        self.names = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form" and attrs.get("id") == "mobileUserPermissionForm":
            self.form = True
        if tag == "select" and attrs.get("id") == "mobileUserRole":
            self.select = True
        if tag == "option" and self.select:
            self.options.append(attrs)
        if self.form and tag in {"input", "select", "textarea", "button"} and attrs.get("name"):
            self.names.append(attrs["name"])

    def handle_endtag(self, tag):
        if tag == "select":
            self.select = False
        if tag == "form":
            self.form = False


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class MobileClinicalUsersTests(TestCase):
    SLUGS = ("hospital_mia", "medical_spa", "luque_aestetic", "serviciosmedicos")
    ROUTES = ("usuarios_clinicos", "roles_clinicos", "usuario_clinico_permisos")
    HIDDEN_MODULES = {
        "egresos", "cxc", "cxp", "notas_credito", "cierres", "reportes",
        "contabilidad", "rrhh", "tecnicentro", "crm", "cotizaciones",
    }

    @classmethod
    def setUpTestData(cls):
        cls.empresas = {
            slug: Empresa.objects.create(
                nombre=f"Clínica Usuarios QA {slug}", slug=slug,
                rtn=f"USERS-{index}", tipo_solucion="clinica", estado_licencia="activa",
            ) for index, slug in enumerate(cls.SLUGS, 1)
        }
        cls.empresa = cls.empresas["hospital_mia"]
        cls.externa = Empresa.objects.create(
            nombre="Externa Usuarios QA", slug="externa_users_qa", rtn="USERS-EXT",
            tipo_solucion="clinica", estado_licencia="activa",
        )
        cls.base_role = RolSistema.objects.create(
            nombre="Base legado no clínico QA", codigo="users-base-legacy",
            puede_pacientes=True, puede_facturas=True,
        )
        cls.role_a = RolSistema.objects.create(
            nombre="Recepción clínica QA", codigo="users-reception", es_rol_clinico=True,
            usa_permisos_clinicos_granulares=True, puede_ver_pacientes=True,
            puede_crear_facturas=True,
        )
        cls.role_b = RolSistema.objects.create(
            nombre="Enfermería clínica QA", codigo="users-nursing", es_rol_clinico=True,
            usa_permisos_clinicos_granulares=True, puede_escribir_enfermeria=True,
        )
        cls.inactive_role = RolSistema.objects.create(
            nombre="Rol clínico inactivo QA", codigo="users-inactive", es_rol_clinico=True,
            usa_permisos_clinicos_granulares=True, activo=False, puede_ver_pacientes=True,
        )
        cls.actor = Usuario.objects.create_user(
            username="users-mobile-admin", password="test-only", empresa=cls.empresa,
            puede_administrar_usuarios_clinicos=True,
        )
        cls.actor.empresas_acceso.add(*cls.empresas.values(), cls.externa)
        cls.targets = {
            slug: Usuario.objects.create_user(
                username=f"target-{slug}", password="test-only", empresa=empresa,
                first_name=f"Paciente administrativo {slug}", last_name="QA",
                email=f"target-{slug}@example.invalid", rol_sistema=cls.base_role,
            ) for slug, empresa in cls.empresas.items()
        }
        cls.target = cls.targets["hospital_mia"]
        cls.shared = Usuario.objects.create_user(
            username="shared-other-primary", password="test-only",
            empresa=cls.empresas["medical_spa"], rol_sistema=cls.role_a,
            first_name="Compartido", email="shared@example.invalid",
        )
        cls.shared.empresas_acceso.add(cls.empresa)
        cls.inactive_user = Usuario.objects.create_user(
            username="inactive-operational", password="test-only", empresa=cls.empresa,
            is_active=False, rol_sistema=cls.inactive_role,
        )
        cls.retired = Usuario.objects.create_user(
            username="retired-user-hidden", password="test-only", empresa=cls.empresa,
            is_active=False, retirado_control=True, rol_sistema=cls.role_a,
        )
        cls.foreign = Usuario.objects.create_user(
            username="foreign-target-hidden", password="test-only", empresa=cls.externa,
            email="foreign-target@example.invalid", rol_sistema=cls.role_a,
        )
        cls.supervisor = Usuario.objects.create_superuser(
            username="users-mobile-super", password="test-only", empresa=cls.empresa,
            email="super@example.invalid",
        )

    def setUp(self):
        self.client.force_login(self.actor)
        for target in ("core.views.enviar_correo_acceso", "core.views.emitir_token_acceso",
                       "facturacion.views.enviar_documento_whatsapp", "django.core.mail.EmailMessage.send"):
            transport = patch(target)
            mocked = transport.start()
            self.addCleanup(transport.stop)
            self.addCleanup(mocked.assert_not_called)

    def url(self, route=None, empresa=None, usuario=None, mobile=True, **params):
        route = route or self.ROUTES[0]
        args = [(empresa or self.empresa).slug]
        if route == self.ROUTES[2]:
            args.append((usuario or self.target).pk)
        query = {"app": "1"} if mobile else {}
        query.update(params)
        return reverse(route, args=args) + ("?" + urlencode(query) if query else "")

    def role(self, granular=True, **permissions):
        role = RolSistema.objects.create(
            nombre="Actor restringido QA", codigo=f"users-actor-{RolSistema.objects.count()}",
            es_rol_clinico=granular, usa_permisos_clinicos_granulares=granular, **permissions,
        )
        UsuarioEmpresaPermiso.objects.update_or_create(
            usuario=self.actor, empresa=self.empresa, defaults={"rol_sistema": role, "activo": True},
        )
        return role

    def direct(self, route=None, url=None, method="get", data=None, actor=None):
        url = url or self.url(route)
        factory = RequestFactory()
        request = factory.post(url, data or {}) if method == "post" else factory.generic(method.upper(), url)
        request.user = actor if actor is not None else Usuario.objects.get(pk=self.actor.pk)
        request.session = {}
        request._messages = FallbackStorage(request)
        match = resolve(urlsplit(url).path)
        request.resolver_match = match
        return match.func(request, **match.kwargs)

    def snapshot(self, exclude=()):
        return tuple(
            (model._meta.label_lower, list(model.objects.order_by("pk").values()))
            for model in apps.get_models(include_auto_created=True)
            if model._meta.label_lower not in exclude
        )

    def denied(self, url=None, route=None, method="get", data=None, actor=None):
        before = self.snapshot()
        try:
            response = self.direct(route, url, method, data, actor)
        except (PermissionDenied, Http404):
            pass
        else:
            self.assertIn(response.status_code, (403, 404, 405))
        self.assertEqual(self.snapshot(), before)

    def no_domain_queries(self, queries):
        forbidden = ("facturacion_", "contabilidad_", "rrhh_", "clinica_", "crm_citacliente")
        for query in queries:
            sql = query["sql"].lower()
            self.assertFalse(any(f'"{prefix}' in sql for prefix in forbidden), sql)

    def test_four_clinics_and_both_role_types_get_mobile_templates_without_changing_web(self):
        for granular in (True, False):
            self.role(granular=granular)
            for empresa in self.empresas.values():
                for route, mobile, web in (
                    (self.ROUTES[0], "core/mobile_users_list.html", "core/usuarios_clinicos.html"),
                    (self.ROUTES[1], "core/mobile_roles_list.html", "core/roles_clinicos.html"),
                    (self.ROUTES[2], "core/mobile_user_permissions.html", "core/usuario_clinico_permisos.html"),
                ):
                    with self.subTest(granular=granular, empresa=empresa.slug, route=route):
                        user = self.targets[empresa.slug]
                        app = self.client.get(self.url(route, empresa, user))
                        self.assertEqual(app.status_code, 200)
                        self.assertTemplateUsed(app, mobile)
                        self.assertTemplateUsed(self.client.get(self.url(route, empresa, user, mobile=False)), web)

    def test_admin_user_flag_is_required_not_company_admin_or_clinic_configuration_permission(self):
        for granular in (True, False):
            self.role(granular=granular, puede_configuracion_clinica=True, puede_clinica=True)
            self.actor.puede_administrar_usuarios_clinicos = False
            self.actor.save(update_fields=["puede_administrar_usuarios_clinicos"])
            for company_admin in (False, True):
                self.actor.es_administrador_empresa = company_admin
                self.actor.save(update_fields=["es_administrador_empresa"])
                for route in self.ROUTES:
                    self.denied(route=route)
                self.denied(route=self.ROUTES[2], method="post", data={"app": "1", "rol_sistema": self.role_a.pk})

    def test_original_core_admin_does_not_require_modules_license_calendar_or_billing(self):
        self.assertFalse(EmpresaModulo.objects.exists())
        for estado in ("activa", "suspendida", "vencida"):
            Empresa.objects.filter(pk=self.empresa.pk).update(estado_licencia=estado)
            for route in self.ROUTES:
                with self.subTest(estado=estado, route=route):
                    self.assertEqual(self.client.get(self.url(route)).status_code, 200)
        self.actor.refresh_from_db()
        self.assertIsNone(self.actor.rol_sistema)
        self.assertFalse(self.actor.es_administrador_empresa)

    def test_superuser_uses_original_administration_exception_without_user_flag(self):
        self.client.force_login(self.supervisor)
        before = self.snapshot()
        for route in self.ROUTES:
            self.assertEqual(self.client.get(self.url(route)).status_code, 200)
        self.assertEqual(self.snapshot(), before)

    def test_company_membership_and_active_company_are_enforced_directly(self):
        outsider = Usuario.objects.create_user(
            username="admin-without-membership", password="test-only", empresa=self.externa,
            puede_administrar_usuarios_clinicos=True,
        )
        for route in self.ROUTES:
            self.denied(route=route, actor=outsider)
        Empresa.objects.filter(pk=self.empresa.pk).update(activa=False)
        for route in self.ROUTES:
            self.denied(route=route)

    def test_other_company_and_nonclinical_company_do_not_gain_app_administration(self):
        for route in self.ROUTES[:2]:
            self.denied(url=self.url(route, empresa=self.externa))
        Empresa.objects.filter(pk=self.empresa.pk).update(tipo_solucion="erp")
        for route in self.ROUTES:
            self.denied(route=route)

    def test_logout_keeps_login_protection_and_does_not_write_operational_data(self):
        self.client.logout()
        before = self.snapshot()
        for route in self.ROUTES:
            self.assertEqual(self.client.get(self.url(route)).status_code, 302)
        self.assertEqual(self.snapshot(), before)

    def test_get_and_head_have_no_writes_for_all_models_including_auth_through_tables(self):
        before = self.snapshot()
        for route in self.ROUTES:
            for method in ("get", "head"):
                with CaptureQueriesContext(connection) as queries:
                    response = getattr(self.client, method)(self.url(route))
                self.assertEqual(response.status_code, 200)
                self.assertIn("no-store", response["Cache-Control"])
                self.assertFalse(any(q["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for q in queries))
                if method == "head":
                    self.assertEqual(response.content, b"")
        self.assertEqual(self.snapshot(), before)

    def test_admin_pages_do_not_initialize_company_configuration_or_read_financial_clinical_records(self):
        with patch.object(ConfiguracionAvanzadaEmpresa, "para_empresa", side_effect=AssertionError("GET seed")):
            for route in self.ROUTES:
                with CaptureQueriesContext(connection) as queries:
                    response = self.client.get(self.url(route))
                self.assertEqual(response.status_code, 200)
                self.no_domain_queries(queries)
        self.assertFalse(ConfiguracionAvanzadaEmpresa.objects.exists())

    def test_user_and_global_role_lists_are_readonly_even_for_admin_post(self):
        for route in self.ROUTES[:2]:
            for method in ("post", "put", "patch", "delete", "options"):
                self.denied(route=route, method=method, data={"app": "1", "nombre": "No crear rol", "activo": "1"})

    def test_assignment_only_accepts_get_head_post_not_unsafe_alternate_verbs(self):
        for method in ("put", "patch", "delete", "options"):
            self.denied(route=self.ROUTES[2], method=method)

    def test_list_query_envelope_rejects_duplicates_unknown_keys_and_invalid_states(self):
        for route in self.ROUTES[:2]:
            base = self.url(route)
            for suffix in ("&app=1", "&app=0", "&q=a&q=b", "&estado=activos&estado=inactivos", "&page=1&page=2", "&accion=crear", "&usuario=1", "&estado=retirados"):
                self.denied(url=base + suffix)

    def test_page_is_canonical_positive_ascii_int64_before_user_or_role_queries(self):
        for route in self.ROUTES[:2]:
            for value in ("0", "-1", "01", "1.0", "1e3", " 1 ", "١", "9223372036854775808", ""):
                self.denied(url=self.url(route, page=value))

    def test_assignment_get_envelope_is_closed_and_path_user_id_is_canonical(self):
        base = self.url(self.ROUTES[2])
        for suffix in ("&app=1", "&app=0", "&q=a", "&rol_sistema=1", "&usuario_id=1"):
            self.denied(url=base + suffix)
        path = reverse(self.ROUTES[2], args=[self.empresa.slug, self.target.pk])
        for value in ("0", "00" + str(self.target.pk), "9223372036854775808"):
            altered = path.replace(f"/{self.target.pk}/permisos/", f"/{value}/permisos/")
            self.denied(url=altered + "?app=1")

    def test_post_envelope_rejects_duplicate_unknown_mixed_or_missing_selection(self):
        valid = {"app": "1", "rol_sistema": str(self.role_a.pk)}
        for extra in ({"app": ["1", "1"]}, {"rol_sistema": [str(self.role_a.pk), ""]}, {"usuario_id": self.foreign.pk}, {"empresa": self.externa.pk}, {"is_superuser": "1"}, {"puede_ver_facturas": "1"}, {"nombre": "No editar rol"}, {"next": "/control/"}):
            self.denied(route=self.ROUTES[2], method="post", data={**valid, **extra})
        self.denied(route=self.ROUTES[2], method="post", data={"app": "1"})
        self.denied(route=self.ROUTES[2], method="post", data={"rol_sistema": str(self.role_a.pk)})
        self.denied(url=self.url(self.ROUTES[2]).replace("app=1", "app=0"), method="post", data=valid)

    def test_uploaded_files_are_not_accepted_by_assignment_or_readonly_pages(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        for route in self.ROUTES:
            self.denied(route=route, method="post", data={"app": "1", "rol_sistema": self.role_a.pk, "archivo": SimpleUploadedFile("unrequested.txt", b"synthetic")})

    def test_foreign_superuser_and_retired_targets_cannot_be_read_or_changed(self):
        for target in (self.foreign, self.supervisor, self.retired):
            url = self.url(self.ROUTES[2], usuario=target)
            self.denied(url=url)
            self.denied(url=url, method="post", data={"app": "1", "rol_sistema": self.role_a.pk})

    def test_list_matches_original_scope_and_keeps_inactive_operational_users(self):
        response = self.client.get(self.url())
        expected = set(views._usuarios_operativos_de_empresa(self.empresa).values_list("pk", flat=True))
        self.assertEqual({row["usuario"].pk for row in response.context["filas"]}, expected)
        self.assertIn(self.inactive_user.pk, expected)
        for target in (self.foreign, self.supervisor, self.retired):
            self.assertNotContains(response, target.username)
        self.assertContains(response, self.inactive_user.username)

    def test_shared_user_with_other_primary_company_can_receive_local_assignment(self):
        url = self.url(self.ROUTES[2], usuario=self.shared)
        self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.post(url, {"app": "1", "rol_sistema": str(self.role_b.pk)})
        self.assertEqual(response.status_code, 302)
        self.shared.refresh_from_db()
        self.assertEqual(self.shared.empresa_id, self.empresas["medical_spa"].pk)
        self.assertEqual(self.shared.rol_sistema_id, self.role_a.pk)
        self.assertEqual(self.shared.rol_para_empresa(self.empresa).pk, self.role_b.pk)
        self.assertEqual(self.shared.rol_para_empresa(self.empresas["medical_spa"]).pk, self.role_a.pk)

    def test_current_role_uses_same_local_override_and_explicit_none_as_original(self):
        for role in (self.role_a, None):
            UsuarioEmpresaPermiso.objects.update_or_create(usuario=self.target, empresa=self.empresa, defaults={"rol_sistema": role, "activo": True})
            response = self.client.get(self.url(self.ROUTES[2]))
            self.assertEqual(response.context["rol_actual"], self.target.rol_para_empresa(self.empresa))
            rows = self.client.get(self.url()).context["filas"]
            row = next(row for row in rows if row["usuario"].pk == self.target.pk)
            self.assertEqual(row["rol"], role)

    def test_inactive_local_assignment_retains_original_global_role_fallback(self):
        UsuarioEmpresaPermiso.objects.create(usuario=self.target, empresa=self.empresa, rol_sistema=self.role_b, activo=False)
        response = self.client.get(self.url(self.ROUTES[2]))
        self.assertEqual(response.context["rol_actual"], self.base_role)
        self.assertEqual(self.target.rol_para_empresa(self.empresa), self.base_role)

    def test_legacy_nonclinical_current_role_is_visible_without_defaulting_to_clear(self):
        response = self.client.get(self.url(self.ROUTES[2]))
        self.assertContains(response, self.base_role.nombre)
        self.assertFalse(response.context["mobile_user_role_in_choices"])
        self.assertTrue(response.context["mobile_user_role_requires_selection"])
        markup = _NativeRoleMarkup(response.content.decode())
        placeholders = [item for item in markup.options if "data-role-placeholder" in item]
        clears = [item for item in markup.options if "data-role-clear" in item]
        self.assertEqual(len(placeholders), 1)
        self.assertIn("disabled", placeholders[0])
        self.assertIn("selected", placeholders[0])
        self.assertEqual(len(clears), 1)
        self.assertNotIn("selected", clears[0])
        self.assertNotIn(str(self.base_role.pk), [item.get("value") for item in markup.options])

    def test_global_role_catalog_includes_inactive_clinical_roles_without_edit_create_actions(self):
        response = self.client.get(self.url(self.ROUTES[1]))
        self.assertEqual(
            {role.pk for role in response.context["roles"]},
            set(RolSistema.objects.filter(es_rol_clinico=True).values_list("pk", flat=True)),
        )
        self.assertContains(response, self.inactive_role.nombre)
        self.assertNotContains(response, self.base_role.nombre)
        for route, args in (("rol_clinico_crear", [self.empresa.slug]), ("rol_clinico_editar", [self.empresa.slug, self.role_a.pk]), ("superadmin_roles", []), ("superadmin_usuario_create", [])):
            self.assertNotContains(response, f'href="{reverse(route, args=args)}"')
        self.assertNotContains(response, 'method="post"')

    def test_app_optin_global_role_create_and_edit_are_blocked_before_role_queries_or_writes(self):
        before = self.snapshot()
        for route, args in (("rol_clinico_crear", [self.empresa.slug]), ("rol_clinico_editar", [self.empresa.slug, self.role_a.pk])):
            url = reverse(route, args=args) + "?app=1"
            for method in ("get", "post"):
                with self.subTest(route=route, method=method), CaptureQueriesContext(connection) as queries:
                    with self.assertRaises(PermissionDenied):
                        self.direct(url=url, method=method, data={"app": "1", "nombre": "No editar global", "activo": "1", "puede_ver_facturas": "1"})
                self.assertFalse(any('"core_rolsistema"' in q["sql"].lower() for q in queries))
        self.assertEqual(self.snapshot(), before)

    def test_imported_orphan_user_and_current_company_role_relations_are_denied_before_filters(self):
        self.target.empresas_acceso.add(self.empresa)
        assignment = UsuarioEmpresaPermiso.objects.create(usuario=self.target, empresa=self.empresa, rol_sistema=self.role_a)
        for model, row, field, old in (
            (Usuario, self.target, "rol_sistema_id", self.base_role.pk),
            (Usuario, self.target, "empresa_id", self.empresa.pk),
            (UsuarioEmpresaPermiso, assignment, "rol_sistema_id", self.role_a.pk),
        ):
            try:
                model.objects.filter(pk=row.pk).update(**{field: 9000000000})
                self.denied(url=self.url(q="No coincide", page="2"))
                self.denied(route=self.ROUTES[2])
                self.denied(route=self.ROUTES[2], method="post", data={"app": "1", "rol_sistema": str(self.role_b.pk)})
            finally:
                model.objects.filter(pk=row.pk).update(**{field: old})

    def test_permission_metadata_is_exact_original_catalog_without_automatic_grants(self):
        UsuarioEmpresaPermiso.objects.create(usuario=self.target, empresa=self.empresa, rol_sistema=self.role_a)
        response = self.client.get(self.url())
        row = next(row for row in response.context["filas"] if row["usuario"].pk == self.target.pk)
        self.assertEqual(row["permisos"], views._permisos_visibles_rol(self.role_a))
        self.assertEqual(row["total_permisos"], 2)
        self.assertFalse(self.target.tiene_permiso_erp("puede_ver_facturas", self.empresa))
        self.assertFalse(self.target.tiene_permiso_erp("puede_ver_historia_clinica", self.empresa))

    def test_user_search_is_server_side_and_never_exposes_foreign_results(self):
        for term in (self.target.username, self.target.first_name, self.target.email):
            response = self.client.get(self.url(q=" " + term + " "))
            self.assertIn(self.target.pk, [row["usuario"].pk for row in response.context["filas"]])
            self.assertEqual(response.context["mobile_users_query"], term)
        response = self.client.get(self.url(q=self.foreign.email))
        self.assertEqual(response.context["mobile_users_total"], 0)
        self.assertNotContains(response, self.foreign.username)

    def test_active_and_inactive_filters_keep_original_total_and_real_user_state(self):
        total = views._usuarios_operativos_de_empresa(self.empresa).count()
        for state, active in (("activos", True), ("inactivos", False)):
            response = self.client.get(self.url(estado=state))
            self.assertTrue(all(row["usuario"].is_active == active for row in response.context["filas"]))
            self.assertEqual(response.context["total_usuarios"], total)
        inactive = self.client.get(self.url(estado="inactivos"))
        self.assertEqual([row["usuario"].pk for row in inactive.context["filas"]], [self.inactive_user.pk])

    def test_user_pagination_search_and_counts_cover_all_results_with_stable_order(self):
        created = [Usuario.objects.create_user(username=f"paging-user-{n:02}", empresa=self.empresa, first_name="Paging usuarios", last_name=f"{n:02}") for n in range(26)]
        page = self.client.get(self.url(q="Paging usuarios", page="2"))
        self.assertEqual(page.context["mobile_users_total"], 26)
        self.assertEqual([row["usuario"].pk for row in page.context["filas"]], [user.pk for user in created[20:]])
        self.assertEqual(page.context["filas"].paginator.per_page, 20)
        self.assertEqual(parse_qs(urlsplit(page.context["mobile_users_previous_url"]).query), {"app": ["1"], "q": ["Paging usuarios"], "page": ["1"]})

    def test_global_roles_search_state_and_pagination_do_not_change_role_definitions(self):
        roles = [RolSistema.objects.create(nombre=f"Paging role {n:02}", codigo=f"users-page-role-{n}", es_rol_clinico=True, activo=(n % 2 == 0)) for n in range(26)]
        before = self.snapshot()
        page = self.client.get(self.url(self.ROUTES[1], q="Paging role", page="2"))
        self.assertEqual(page.context["mobile_roles_total"], 26)
        self.assertEqual([role.pk for role in page.context["roles"]], [role.pk for role in roles[20:]])
        active = self.client.get(self.url(self.ROUTES[1], q="Paging role", estado="activos"))
        self.assertEqual(active.context["mobile_roles_total"], 13)
        self.assertTrue(all(role.activo for role in active.context["roles"]))
        self.assertEqual(self.snapshot(), before)

    def test_valid_post_changes_only_current_company_assignment_and_original_audit(self):
        other = UsuarioEmpresaPermiso.objects.create(usuario=self.target, empresa=self.empresas["medical_spa"], rol_sistema=self.role_b)
        before = self.snapshot(exclude=("core.usuarioempresapermiso", "core.registroauditoria"))
        response = self.client.post(self.url(self.ROUTES[2]), {"app": "1", "rol_sistema": str(self.role_a.pk)})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, self.url())
        assigned = UsuarioEmpresaPermiso.objects.get(usuario=self.target, empresa=self.empresa)
        self.assertEqual(assigned.rol_sistema_id, self.role_a.pk)
        self.assertTrue(assigned.activo)
        self.target.refresh_from_db(); other.refresh_from_db()
        self.assertEqual(self.target.rol_sistema_id, self.base_role.pk)
        self.assertEqual(other.rol_sistema_id, self.role_b.pk)
        self.assertEqual(self.snapshot(exclude=("core.usuarioempresapermiso", "core.registroauditoria")), before)
        audit = RegistroAuditoria.objects.filter(modelo="usuarioempresapermiso", objeto_id=str(assigned.pk), usuario=self.actor).latest("pk")
        self.assertEqual((audit.empresa_id, audit.metodo_http, audit.ruta), (self.empresa.pk, "POST", urlsplit(self.url(self.ROUTES[2])).path))
        self.assertEqual(audit.cambios["rol_sistema"]["nuevo"], self.role_a.pk)

    def test_inactive_clinical_role_can_be_assigned_but_does_not_grant_access(self):
        response = self.client.post(self.url(self.ROUTES[2]), {"app": "1", "rol_sistema": str(self.inactive_role.pk)})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.target.rol_para_empresa(self.empresa).pk, self.inactive_role.pk)
        self.assertFalse(self.target.tiene_permiso_erp("puede_ver_pacientes", self.empresa))

    def test_explicit_clear_creates_none_override_instead_of_using_global_role(self):
        UsuarioEmpresaPermiso.objects.create(usuario=self.target, empresa=self.empresa, rol_sistema=self.role_a)
        response = self.client.post(self.url(self.ROUTES[2]), {"app": "1", "rol_sistema": ""})
        self.assertEqual(response.status_code, 302)
        assignment = UsuarioEmpresaPermiso.objects.get(usuario=self.target, empresa=self.empresa)
        self.assertIsNone(assignment.rol_sistema_id)
        self.assertTrue(assignment.activo)
        self.assertIsNone(self.target.rol_para_empresa(self.empresa))
        self.target.refresh_from_db()
        self.assertEqual(self.target.rol_sistema_id, self.base_role.pk)

    def test_nonclinical_missing_or_malformed_posted_roles_cannot_mutate_assignment(self):
        for value in (str(self.base_role.pk), "9999999", "0", "00" + str(self.role_a.pk), "-1", "1.2", "١", "9223372036854775808"):
            self.denied(route=self.ROUTES[2], method="post", data={"app": "1", "rol_sistema": value})

    def test_reusable_role_assigned_to_other_companies_remains_global_and_unchanged(self):
        UsuarioEmpresaPermiso.objects.create(usuario=self.targets["medical_spa"], empresa=self.empresas["medical_spa"], rol_sistema=self.role_b)
        original = list(RolSistema.objects.order_by("pk").values())
        self.assertEqual(self.client.post(self.url(self.ROUTES[2]), {"app": "1", "rol_sistema": str(self.role_b.pk)}).status_code, 302)
        self.assertEqual(list(RolSistema.objects.order_by("pk").values()), original)
        self.assertEqual(self.targets["medical_spa"].rol_para_empresa(self.empresas["medical_spa"]).pk, self.role_b.pk)

    def test_assignment_update_preserves_all_other_users_assignments_and_original_values_audit(self):
        assignment = UsuarioEmpresaPermiso.objects.create(usuario=self.target, empresa=self.empresa, rol_sistema=self.role_a, activo=False)
        unrelated = UsuarioEmpresaPermiso.objects.create(usuario=self.shared, empresa=self.empresa, rol_sistema=self.role_b)
        original_other = UsuarioEmpresaPermiso.objects.filter(pk=unrelated.pk).values().get()
        response = self.client.post(self.url(self.ROUTES[2]), {"app": "1", "rol_sistema": str(self.role_b.pk)})
        self.assertEqual(response.status_code, 302)
        assignment.refresh_from_db()
        self.assertEqual((assignment.rol_sistema_id, assignment.activo), (self.role_b.pk, True))
        self.assertEqual(UsuarioEmpresaPermiso.objects.filter(pk=unrelated.pk).values().get(), original_other)
        audit = RegistroAuditoria.objects.filter(modelo="usuarioempresapermiso", objeto_id=str(assignment.pk), usuario=self.actor).latest("pk")
        self.assertEqual(audit.cambios["rol_sistema"], {"anterior": self.role_a.pk, "nuevo": self.role_b.pk})

    def test_native_assignment_form_contains_only_original_role_app_and_csrf_fields(self):
        response = self.client.get(self.url(self.ROUTES[2]))
        markup = _NativeRoleMarkup(response.content.decode())
        self.assertCountEqual(markup.names, ["app", "csrfmiddlewaretoken", "rol_sistema"])
        for name in ("usuario_id", "empresa", "is_superuser", "password", "confirmacion", "accion"):
            self.assertNotIn(name, markup.names)
        self.assertContains(response, 'id="mobileUserRoleConfirmation"')

    def test_csrf_original_failure_redirect_and_valid_token_reuse_same_local_saver(self):
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(self.actor)
        # El handler CSRF original guarda el aviso de sesión caducada. Esa
        # escritura de sesión es explícita; ninguna tabla operativa cambia.
        from django.contrib.sessions.models import Session
        before = self.snapshot(exclude=("sessions.session",))
        session_keys = set(Session.objects.values_list("session_key", flat=True))
        decoded = dict(protected.session)
        rejected = protected.post(self.url(self.ROUTES[2]), {"app": "1", "rol_sistema": self.role_a.pk})
        self.assertEqual(rejected.status_code, 302)
        self.assertEqual(rejected.url, reverse("empresa_login", args=[self.empresa.slug]))
        self.assertEqual(self.snapshot(exclude=("sessions.session",)), before)
        self.assertEqual(set(Session.objects.values_list("session_key", flat=True)), session_keys)
        self.assertEqual(dict(protected.session), {
            **decoded, views.SESSION_EXPIRED_MESSAGE_KEY: views.SESSION_EXPIRED_MESSAGE,
        })
        self.assertEqual(protected.get(self.url(self.ROUTES[2])).status_code, 200)
        token = protected.cookies["csrftoken"].value
        accepted = protected.post(self.url(self.ROUTES[2]), {"app": "1", "rol_sistema": self.role_a.pk, "csrfmiddlewaretoken": token})
        self.assertEqual(accepted.status_code, 302)
        self.assertEqual(UsuarioEmpresaPermiso.objects.get(usuario=self.target, empresa=self.empresa).rol_sistema_id, self.role_a.pk)

    def test_web_assignment_keeps_original_template_save_and_redirect_without_app_flag(self):
        self.assertTemplateUsed(self.client.get(self.url(self.ROUTES[2], mobile=False)), "core/usuario_clinico_permisos.html")
        response = self.client.post(self.url(self.ROUTES[2], mobile=False), {"rol_sistema": str(self.role_a.pk)})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, self.url(mobile=False))
        self.assertEqual(self.target.rol_para_empresa(self.empresa).pk, self.role_a.pk)

    def test_admin_navigation_has_no_forbidden_control_financial_or_ungranted_patient_links(self):
        for route in self.ROUTES:
            response = self.client.get(self.url(route))
            self.assertFalse(response.context["app_home_disponible"])
            self.assertNotContains(response, 'rel="manifest"')
            self.assertNotContains(response, "<table")
            for forbidden in ("superadmin_empresas", "superadmin_usuarios", "superadmin_roles", "superadmin_usuario_create"):
                self.assertNotContains(response, f'href="{reverse(forbidden)}"')
            self.assertNotContains(response, f'href="{reverse("clinica_pacientes", args=[self.empresa.slug])}?app=1"')

    def test_modules_hide_accounting_keys_without_changing_permissions_or_readonly_backend(self):
        from crm.mobile_modules import construir_contexto_modulos_app
        role = self.role(puede_cxc=True, puede_egresos=True, puede_cxp=True, puede_reportes=True, puede_notas_credito=True, puede_contabilidad=True)
        module, _ = Modulo.objects.get_or_create(codigo="facturacion", defaults={"nombre": "Facturación"})
        EmpresaModulo.objects.create(empresa=self.empresa, modulo=module)
        request = RequestFactory().get("/")
        request.user = Usuario.objects.get(pk=self.actor.pk)
        before = self.snapshot()
        cards = construir_contexto_modulos_app(request, self.empresa)["modulos_app"]
        self.assertFalse({card["key"] for card in cards} & self.HIDDEN_MODULES)
        self.assertEqual(self.client.get(reverse("reporte_cxc", args=[self.empresa.slug]) + "?app=1").status_code, 200)
        self.assertEqual(self.client.get(reverse("egresos_dashboard", args=[self.empresa.slug]) + "?app=1").status_code, 200)
        role.refresh_from_db()
        self.assertTrue(role.puede_cxc and role.puede_egresos and role.puede_contabilidad)
        self.assertEqual(self.snapshot(), before)

    def test_users_card_uses_mobile_route_without_granting_agenda_or_global_control(self):
        self.role()
        before = self.snapshot()
        hub = self.client.get(reverse("agenda_mobile_modules_screen", args=[self.empresa.slug]))
        self.assertEqual(hub.status_code, 200)
        card = next(card for card in hub.context["modulos_app"] if card["key"] == "usuarios")
        self.assertEqual(card["url"], self.url())
        metadata = self.client.get(reverse("agenda_mobile_modules", args=[self.empresa.slug]))
        self.assertEqual(metadata.status_code, 200)
        keys = {card["key"] for card in metadata.json()["modulos"]}
        self.assertIn("usuarios", keys)
        self.assertFalse(keys & (self.HIDDEN_MODULES | {"agenda", "pacientes", "facturacion"}))
        self.actor.refresh_from_db()
        self.assertFalse(self.actor.es_administrador_empresa or self.actor.is_superuser)
        self.assertEqual(self.snapshot(), before)

    def test_user_flag_only_roundtrip_to_metadata_hub_requires_no_role_module_or_license(self):
        for granular in (True, False, None):
            role = self.role(granular=granular) if granular is not None else None
            for empresa in self.empresas.values():
                UsuarioEmpresaPermiso.objects.update_or_create(
                    usuario=self.actor, empresa=empresa, defaults={"rol_sistema": role, "activo": True},
                )
                for state in ("activa", "suspendida", "vencida"):
                    Empresa.objects.filter(pk=empresa.pk).update(estado_licencia=state)
                    before = self.snapshot()
                    with self.subTest(rol=granular, empresa=empresa.slug, licencia=state), CaptureQueriesContext(connection) as queries:
                        users = self.client.get(self.url(empresa=empresa))
                        self.assertEqual(users.status_code, 200)
                        hub_url = users.context["app_modules_url"]
                        self.assertEqual(hub_url, reverse("agenda_mobile_modules_screen", args=[empresa.slug]))
                        hub = self.client.get(hub_url)
                        self.assertEqual(hub.status_code, 200)
                        self.assertFalse(hub.context["app_home_disponible"])
                        self.assertNotContains(hub, 'rel="manifest"')
                        user_card = next(card for card in hub.context["modulos_app"] if card["key"] == "usuarios")
                        self.assertEqual(user_card["url"], self.url(empresa=empresa))
                        self.assertEqual(self.client.get(user_card["url"]).status_code, 200)
                        metadata = self.client.get(reverse("agenda_mobile_modules", args=[empresa.slug]))
                        self.assertEqual(metadata.status_code, 200)
                        self.assertEqual(metadata.json()["inicio_url"], hub_url)
                        self.assertFalse({"agenda", "pacientes", "facturacion"} & {card["key"] for card in metadata.json()["modulos"]})
                    self.no_domain_queries(queries)
                    self.assertFalse(any(q["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for q in queries))
                    self.assertEqual(self.snapshot(), before)
                    self.assertIn(self.client.get(reverse("agenda_mobile", args=[empresa.slug])).status_code, (302, 403))
                    self.assertIn(self.client.get(reverse("agenda_citas", args=[empresa.slug])).status_code, (302, 403))
        self.assertFalse(EmpresaModulo.objects.exists())

    def test_company_admin_target_keeps_independent_privileges_after_clear_or_inactive_assignment(self):
        Usuario.objects.filter(pk=self.target.pk).update(es_administrador_empresa=True)
        for role in (None, self.inactive_role):
            before = self.snapshot(exclude=("core.usuarioempresapermiso", "core.registroauditoria"))
            response = self.client.post(self.url(self.ROUTES[2]), {
                "app": "1", "rol_sistema": str(role.pk) if role is not None else "",
            })
            self.assertEqual(response.status_code, 302)
            self.target.refresh_from_db()
            self.assertTrue(self.target.es_administrador_empresa)
            self.assertEqual(self.target.rol_para_empresa(self.empresa), role)
            self.assertTrue(self.target.tiene_permiso_erp("puede_ver_facturas", self.empresa))
            self.assertTrue(self.target.tiene_permiso_erp("puede_ver_historia_clinica", self.empresa))
            self.assertEqual(self.snapshot(exclude=("core.usuarioempresapermiso", "core.registroauditoria")), before)
            assignment = self.client.get(self.url(self.ROUTES[2]))
            self.assertTrue(assignment.context["usuario_gestionado"].es_administrador_empresa)
            self.assertContains(assignment, 'data-user-admin-warning')
            listing = self.client.get(self.url())
            self.assertContains(listing, 'data-user-admin-warning')

    def test_calendar_role_with_expired_license_keeps_users_but_uses_standalone_safe_navigation(self):
        for code in ("agenda_citas", "clinica_medica"):
            module, _ = Modulo.objects.get_or_create(codigo=code, defaults={"nombre": code})
            for empresa in self.empresas.values():
                EmpresaModulo.objects.create(empresa=empresa, modulo=module)
        for granular in (True, False):
            role = self.role(granular=granular, puede_clinica=True, **{
                "puede_ver_calendario" if granular else "puede_citas": True,
                "puede_ver_pacientes" if granular else "puede_pacientes": True,
                **({"puede_ver_datos_generales_paciente": True} if granular else {}),
            })
            for empresa in self.empresas.values():
                UsuarioEmpresaPermiso.objects.update_or_create(
                    usuario=self.actor, empresa=empresa, defaults={"rol_sistema": role, "activo": True},
                )
                for state in ("activa", "suspendida", "vencida"):
                    Empresa.objects.filter(pk=empresa.pk).update(estado_licencia=state)
                    before = self.snapshot()
                    with self.subTest(granular=granular, empresa=empresa.slug, licencia=state):
                        home = reverse("agenda_mobile", args=[empresa.slug])
                        patients = reverse("clinica_pacientes", args=[empresa.slug]) + "?app=1"
                        standalone = reverse("agenda_mobile_modules_screen", args=[empresa.slug])
                        for route in self.ROUTES:
                            page = self.client.get(self.url(route, empresa, self.targets[empresa.slug]))
                            self.assertEqual(page.status_code, 200)
                            self.assertEqual(page.context["app_home_disponible"], state == "activa")
                            if state != "activa":
                                self.assertEqual(page.context["app_modules_url"], standalone)
                                self.assertTrue(page.context["app_hide_agenda_navigation"])
                                self.assertTrue(page.context["app_hide_patient_navigation"])
                                self.assertNotContains(page, 'rel="manifest"')
                                self.assertNotContains(page, f'href="{home}"')
                                self.assertNotContains(page, f'href="{home}#agenda-app"')
                                self.assertNotContains(page, f'href="{patients}"')
                            else:
                                self.assertContains(page, f'href="{patients}"')
                        hub = self.client.get(standalone)
                        self.assertEqual(hub.status_code, 200)
                        self.assertEqual(hub.context["app_home_disponible"], state == "activa")
                        metadata = self.client.get(reverse("agenda_mobile_modules", args=[empresa.slug]))
                        self.assertEqual(metadata.status_code, 200)
                        self.assertEqual(metadata.json()["inicio_url"], home if state == "activa" else standalone)
                        if state != "activa":
                            self.assertTrue(hub.context["app_hide_agenda_navigation"])
                            self.assertTrue(hub.context["app_hide_patient_navigation"])
                            self.assertNotContains(hub, 'rel="manifest"')
                            self.assertNotContains(hub, f'href="{home}#agenda-app"')
                            self.assertNotContains(hub, f'href="{patients}"')
                            self.assertEqual({card["key"] for card in metadata.json()["modulos"]}, {"usuarios"})
                        else:
                            self.assertContains(hub, f'href="{patients}"')
                    self.assertEqual(self.snapshot(), before)

    def test_html_escapes_names_role_descriptions_and_search_without_exposing_credentials(self):
        name = '<script>synthetic_user()</script>'
        Usuario.objects.filter(pk=self.target.pk).update(first_name=name)
        RolSistema.objects.filter(pk=self.role_a.pk).update(nombre='<img src=x onerror="synthetic_role()">')
        response = self.client.get(self.url(q=name))
        self.assertNotContains(response, name)
        self.assertContains(response, "&lt;script&gt;synthetic_user()&lt;/script&gt;")
        roles = self.client.get(self.url(self.ROUTES[1]))
        self.assertNotContains(roles, '<img src=x onerror="synthetic_role()">')
        for route in self.ROUTES:
            self.assertNotContains(self.client.get(self.url(route)), self.target.password)
