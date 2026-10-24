"""Synthetic local permission snapshots, never mutations of a reusable role.

The original clinical-user administrator guard remains authoritative. A change
may create one role snapshot, one current-company assignment, and its audit.
No account, other-company assignment, or pre-existing role is rewritten.
"""
from datetime import datetime, timezone as datetime_timezone
from html.parser import HTMLParser
import re
import time
from urllib.parse import urlencode, urlsplit
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser, Group
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.exceptions import PermissionDenied
from django.core import signing
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection, reset_queries
from django.http import Http404, QueryDict
from django.test import Client, RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import resolve, reverse

from . import test_mobile_users as _users_tests
from .clinical_permissions import PERMISOS_CLINICOS
from .models import Empresa, RegistroAuditoria, RolSistema, Usuario, UsuarioEmpresaPermiso


class _ClinicalPermissionMarkup(HTMLParser):
    def __init__(self, content):
        super().__init__(convert_charrefs=True)
        self.nodes, self.controls, self.anchors = [], [], []
        self.in_form = False
        self.feed(content.decode() if isinstance(content, bytes) else content)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        self.nodes.append((tag, values))
        if tag == "form":
            self.in_form = values.get("id") == "mobileClinicalPermissionsForm"
        if self.in_form and tag in {"input", "select", "textarea", "button"}:
            self.controls.append((tag, values))
        if tag == "a":
            self.anchors.append(values)

    def handle_endtag(self, tag):
        if tag == "form": self.in_form = False

    def hidden(self, name):
        return next(attrs["value"] for _, attrs in self.controls if attrs.get("name") == name)

    def checked(self):
        return {attrs["name"] for _, attrs in self.controls if attrs.get("type") == "checkbox" and "checked" in attrs}


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class MobileUserPermissionsEditTests(TestCase):
    SLUGS = _users_tests.MobileClinicalUsersTests.SLUGS
    NOW = datetime(2026, 10, 23, 18, tzinfo=datetime_timezone.utc)
    FIELDS = tuple(name for _, fields in PERMISOS_CLINICOS for name, _ in fields)
    ALLOWED_CHANGES = {"core.rolsistema", "core.usuarioempresapermiso", "core.registroauditoria"}

    @classmethod
    def setUpTestData(cls):
        with patch("django.utils.timezone.now", return_value=cls.NOW):
            _users_tests.MobileClinicalUsersTests.setUpTestData.__func__(cls)
            # Deliberately exercise hidden legacy/non-clinical flags too.
            RolSistema.objects.filter(pk=cls.role_a.pk).update(
                descripcion="Descripción histórica de rol compartido QA",
                puede_contabilidad=True, puede_rrhh=True, puede_campanias=True,
                puede_configuracion_crm=True, puede_pacientes=True,
            )
            Usuario.objects.filter(pk__in=[user.pk for user in cls.targets.values()]).update(rol_sistema=cls.role_a)
            cls.role_a.refresh_from_db()
            cls.other_assignment = UsuarioEmpresaPermiso.objects.create(
                usuario=cls.shared, empresa=cls.empresas["medical_spa"], rol_sistema=cls.role_a,
            )
            cls.shared.empresas_acceso.add(cls.empresas["luque_aestetic"])
            cls.group = Group.objects.create(name="Cuenta sin cambios QA")
            cls.target.groups.add(cls.group)
            cls.admin_target = Usuario.objects.create_user(
                username="target-independent-admin", password="test-only", empresa=cls.empresa,
                es_administrador_empresa=True, rol_sistema=cls.role_a,
            )

    snapshot = _users_tests.MobileClinicalUsersTests.snapshot

    def setUp(self):
        clock = patch("django.utils.timezone.now", return_value=self.NOW)
        clock.start(); self.addCleanup(clock.stop)
        self.client.force_login(self.actor)
        self.url()  # Resolve URL metadata before replacing transport callables.
        for name in ("core.views.enviar_correo_acceso", "core.views.emitir_token_acceso", "facturacion.views.enviar_documento_whatsapp", "facturacion.views.subir_documento_whatsapp", "crm.views.urlopen", "crm.services._post_whatsapp", "django.core.mail.EmailMessage.send", "socket.create_connection", "socket.socket.connect", "socket.socket.connect_ex", "facturacion.views.HTML"):
            blocker = patch(name, side_effect=AssertionError(f"No permission-editor transport: {name}"))
            mock = blocker.start(); self.addCleanup(blocker.stop); self.addCleanup(mock.assert_not_called)

    def url(self, company=None, target=None, mobile=True, **query):
        company = company or self.empresa
        target = target or self.targets.get(company.slug, self.target)
        base = reverse("usuario_clinico_permisos_editar_app", args=[company.slug, target.pk])
        params = {"app": "1", **query} if mobile else query
        return base + ("?"+urlencode(params, doseq=True) if params else "")

    def no_writes(self, queries):
        self.assertEqual([row["sql"] for row in queries if re.match(r"\s*(INSERT|UPDATE|DELETE|REPLACE|CREATE|ALTER|DROP)\b", row["sql"], re.I)], [])

    def immutable(self, url=None, method="get", data=None):
        before = self.snapshot(); reset_queries()
        with CaptureQueriesContext(connection) as queries:
            response = getattr(self.client, method)(url or self.url(), data=data or {})
        self.no_writes(queries)
        self.assertEqual(self.snapshot(), before)
        return response

    def request(self, url=None, method="get", data=None, actor=None, raw=None):
        url = url or self.url(); factory = RequestFactory()
        if raw is not None:
            request = factory.generic(method.upper(), url, data=raw, content_type="application/json")
        elif method == "post":
            request = factory.post(url, data.urlencode(), content_type="application/x-www-form-urlencoded") if isinstance(data, QueryDict) else factory.post(url, data or {})
        else:
            request = factory.generic(method.upper(), url)
        request.user = actor if actor is not None else Usuario.objects.get(pk=self.actor.pk)
        request.session = {}; request._messages = FallbackStorage(request)
        request.resolver_match = resolve(urlsplit(url).path)
        return request

    def direct(self, request):
        match = request.resolver_match
        return match.func(request, **match.kwargs)

    def denied(self, url=None, method="get", data=None, actor=None, raw=None, status=403):
        before = self.snapshot(); request = self.request(url, method, data, actor, raw); reset_queries()
        with CaptureQueriesContext(connection) as queries:
            try:
                response = self.direct(request)
            except (PermissionDenied, Http404) as exc:
                self.assertEqual(404 if isinstance(exc, Http404) else 403, status)
            else:
                self.assertEqual(response.status_code, status)
        self.no_writes(queries); self.assertEqual(self.snapshot(), before)

    def token(self, company=None, target=None):
        response = self.immutable(self.url(company, target))
        self.assertEqual(response.status_code, 200)
        return _ClinicalPermissionMarkup(response.content).hidden("version_permisos")

    def selected(self, company=None, target=None):
        target = Usuario.objects.get(pk=(target or self.targets.get((company or self.empresa).slug, self.target)).pk)
        role = target.rol_para_empresa(company or self.empresa)
        return {name for name in self.FIELDS if role and getattr(role, name)}

    def body(self, company=None, target=None, selected=None, version=None):
        selected = self.selected(company, target) if selected is None else set(selected)
        # Keep the closing acknowledgement last, exactly as native FormData.
        return {"app": "1", "version_permisos": version or self.token(company, target), **{name: "1" for name in self.FIELDS if name in selected}, "confirmar_permisos": "1"}

    def role_values(self):
        return list(RolSistema.objects.order_by("pk").values())

    def assert_snapshot_role(self, old, new, selected):
        self.assertNotEqual(old.pk, new.pk)
        self.assertNotEqual(old.codigo, new.codigo)
        self.assertNotIn(self.target.email, new.nombre)
        self.assertNotIn(self.target.username, new.nombre)
        for field in RolSistema._meta.concrete_fields:
            if field.primary_key or field.name in {"codigo", "nombre"}: continue
            self.assertEqual(getattr(new, field.attname), field.name in selected if field.name in self.FIELDS else getattr(old, field.attname), field.name)

    def assert_only_local_changes(self, before):
        after = dict(self.snapshot())
        changed = {key for key, rows in before if after[key] != rows}
        self.assertLessEqual(changed, self.ALLOWED_CHANGES)

    def test_four_companies_and_both_administrator_role_types_render_immutable_get_head(self):
        for granular in (False, True):
            role = RolSistema.objects.create(nombre="Actor edición individual QA", codigo=f"per-user-actor-{granular}", es_rol_clinico=granular, usa_permisos_clinicos_granulares=granular)
            Usuario.objects.filter(pk=self.actor.pk).update(rol_sistema=role)
            for company in self.empresas.values():
                for method in ("get", "head"):
                    response = self.immutable(self.url(company), method)
                    self.assertEqual(response.status_code, 200)
                    self.assertTemplateUsed(response, "core/mobile_user_permissions_edit.html")
                    self.assertIn("no-store", response["Cache-Control"])
                    if method == "head": self.assertEqual(response.content, b"")

    def test_user_flag_not_company_administration_or_clinical_configuration_authorizes_actor(self):
        Usuario.objects.filter(pk=self.actor.pk).update(puede_administrar_usuarios_clinicos=False, es_administrador_empresa=True, rol_sistema=self.role_a)
        RolSistema.objects.filter(pk=self.role_a.pk).update(puede_configuracion_clinica=True)
        self.denied(); self.denied(method="post", data={"app": "1"})

    def test_superuser_exception_needs_no_user_flag_or_operational_license_modules(self):
        self.client.force_login(self.supervisor)
        for company in self.empresas.values():
            Empresa.objects.filter(pk=company.pk).update(estado_licencia="suspendida")
            self.assertEqual(self.immutable(self.url(company)).status_code, 200)

    def test_user_flag_retains_original_access_without_modules_license_or_calendar(self):
        for state in ("activa", "vencida", "suspendida"):
            Empresa.objects.filter(pk=self.empresa.pk).update(estado_licencia=state)
            self.assertEqual(self.immutable().status_code, 200)
        self.assertFalse(self.actor.is_superuser)

    def test_company_membership_active_profile_and_exact_four_are_required(self):
        outsider = Usuario.objects.create_user(username="outsider-permissions-qa", password="test-only", empresa=self.externa, puede_administrar_usuarios_clinicos=True)
        self.denied(actor=outsider)
        self.denied(self.url(self.externa, self.foreign))
        Empresa.objects.filter(pk=self.empresa.pk).update(tipo_solucion="erp")
        self.denied()
        Empresa.objects.filter(pk=self.empresa.pk).update(tipo_solucion="clinica")
        Empresa.objects.filter(pk=self.empresa.pk).update(activa=False)
        self.denied(status=404)  # The original company resolver only finds active companies.

    def test_foreign_retired_super_and_missing_targets_are_404_without_changes(self):
        for target in (self.foreign, self.retired, self.supervisor):
            self.denied(self.url(target=target), status=404)
        missing = self.url().replace(f"/{self.target.pk}/", "/9223372036854775807/")
        self.denied(missing, status=404)

    def test_shared_primary_other_company_and_inactive_user_remain_eligible(self):
        Usuario.objects.filter(pk=self.inactive_user.pk).update(rol_sistema=self.role_a)
        for target in (self.shared, self.inactive_user):
            page = self.immutable(self.url(target=target))
            self.assertEqual(page.status_code, 200)
            self.assertTrue(_ClinicalPermissionMarkup(page.content).controls)

    def test_none_legacy_inactive_and_nonclinical_roles_explain_without_edit_form_or_seed(self):
        for role in (None, self.base_role, self.inactive_role):
            Usuario.objects.filter(pk=self.target.pk).update(rol_sistema=role)
            page = self.immutable()
            self.assertEqual(page.status_code, 200)
            self.assertFalse(_ClinicalPermissionMarkup(page.content).controls)
        Usuario.objects.filter(pk=self.target.pk).update(rol_sistema=self.role_a)
        UsuarioEmpresaPermiso.objects.create(usuario=self.target, empresa=self.empresa, rol_sistema=None)
        self.assertFalse(_ClinicalPermissionMarkup(self.immutable().content).controls)

    def test_native_form_contains_complete_catalog_once_and_only_explicit_metadata(self):
        page = self.immutable(); parsed = _ClinicalPermissionMarkup(page.content)
        named = [attrs["name"] for _, attrs in parsed.controls if attrs.get("name")]
        self.assertEqual(set(named), set(self.FIELDS) | {"app", "version_permisos", "confirmar_permisos", "csrfmiddlewaretoken"})
        self.assertEqual(len(named), len(set(named)))
        self.assertEqual(named[-1], "confirmar_permisos")
        self.assertEqual(parsed.checked(), self.selected())
        for _, attrs in parsed.controls:
            if attrs.get("type") == "checkbox": self.assertEqual(attrs.get("value"), "1")
        for name in ("puede_crear_facturas", "puede_inventario", "puede_proveedores", "puede_convertir_gastos_adicionales_factura"):
            self.assertIn(name, named)

    def test_unknown_duplicate_query_missing_or_bad_app_markers_are_denied(self):
        for suffix in ("", "?app=0", "?app=1&app=1", "?app=1&empresa=2", "?app=1&next=/", "?app=1&usuario=2"):
            self.denied(self.url().split("?")[0]+suffix)

    def test_noncanonical_target_path_is_denied_before_private_target_lookup(self):
        for value in ("0", "01", "9223372036854775808"):
            target = self.url().replace(f"/{self.target.pk}/", f"/{value}/")
            self.denied(target)

    def test_get_head_raw_bodies_files_and_unsupported_methods_cannot_write(self):
        for method in ("get", "head"):
            self.denied(method=method, raw=b'{"app":"1"}')
        for method in ("put", "patch", "delete", "options"):
            self.denied(method=method)
        request = self.request(); request.POST
        request._files = {"file": SimpleUploadedFile("synthetic.txt", b"not a real upload")}
        before = self.snapshot()
        with self.assertRaises(PermissionDenied): self.direct(request)
        self.assertEqual(self.snapshot(), before)

    def test_post_requires_query_app_and_no_unknown_account_or_role_fields(self):
        body = self.body(selected={"puede_editar_pacientes"})
        self.denied(self.url(mobile=False), "post", body)
        for field in ("empresa", "usuario", "rol_sistema", "codigo", "nombre", "activo", "is_superuser", "is_staff", "es_administrador_empresa", "puede_administrar_usuarios_clinicos", "email", "password", "puede_contabilidad"):
            self.denied(method="post", data={**body, field: "1"})

    def test_post_duplicate_flags_metadata_and_nonliteral_values_are_denied(self):
        body = self.body(selected={"puede_editar_pacientes"})
        for field in ("app", "version_permisos", "confirmar_permisos", "puede_editar_pacientes"):
            duplicate = QueryDict("", mutable=True)
            for name, value in body.items(): duplicate[name] = value
            duplicate.setlist(field, [body[field], body[field]])
            self.denied(method="post", data=duplicate)
        for value in ("0", "true", "on", "", "2"):
            invalid = dict(body); invalid["puede_editar_pacientes"] = value
            self.denied(method="post", data=invalid)

    def test_missing_or_nonclosing_confirmation_and_missing_version_are_denied(self):
        body = self.body(selected={"puede_editar_pacientes"})
        for field in ("version_permisos", "confirmar_permisos", "app"):
            invalid = dict(body); invalid.pop(field)
            self.denied(method="post", data=invalid)
        misplaced = {"confirmar_permisos": "1", **{name: value for name, value in body.items() if name != "confirmar_permisos"}}
        self.denied(method="post", data=misplaced)

    def test_copied_role_changes_only_selected_catalog_fields_for_four_companies(self):
        selected = {"puede_editar_pacientes", "puede_ver_historia_clinica", "puede_crear_recetas", "puede_inventario"}
        for company in self.empresas.values():
            target = self.targets[company.slug]; old = Usuario.objects.get(pk=target.pk).rol_para_empresa(company)
            body = self.body(company, target, selected)
            before = self.snapshot(); existing = self.role_values()
            response = self.client.post(self.url(company, target), body)
            self.assertEqual(response.status_code, 302)
            assignment = UsuarioEmpresaPermiso.objects.get(usuario=target, empresa=company)
            self.assertTrue(assignment.activo)
            self.assert_snapshot_role(old, assignment.rol_sistema, selected)
            self.assertEqual(list(RolSistema.objects.filter(pk__in=[row["id"] for row in existing]).order_by("pk").values()), existing)
            self.assert_only_local_changes(before)

    def test_shared_user_other_assignments_global_role_and_account_through_rows_are_unchanged(self):
        company = self.empresa; target = self.shared
        body = self.body(company, target, {"puede_ver_recetas"})
        account = list(Usuario.objects.order_by("pk").values())
        others = list(UsuarioEmpresaPermiso.objects.exclude(usuario=target, empresa=company).order_by("pk").values())
        before = self.snapshot()
        self.assertEqual(self.client.post(self.url(company, target), body).status_code, 302)
        self.assertEqual(list(Usuario.objects.order_by("pk").values()), account)
        self.assertEqual(list(UsuarioEmpresaPermiso.objects.exclude(usuario=target, empresa=company).order_by("pk").values()), others)
        self.assert_only_local_changes(before)
        target.refresh_from_db(); self.assertEqual(target.rol_sistema_id, self.role_a.pk)
        self.assertEqual(target.rol_para_empresa(self.empresas["medical_spa"]).pk, self.role_a.pk)

    def test_omitted_checkboxes_clear_only_catalog_fields_and_preserve_every_hidden_concrete_field(self):
        old = RolSistema.objects.get(pk=self.role_a.pk)
        body = self.body(selected=set())
        self.assertEqual(self.client.post(self.url(), body).status_code, 302)
        assignment = UsuarioEmpresaPermiso.objects.get(usuario=self.target, empresa=self.empresa)
        self.assert_snapshot_role(old, assignment.rol_sistema, set())
        self.assertTrue(assignment.rol_sistema.puede_contabilidad)
        self.assertTrue(assignment.rol_sistema.puede_configuracion_crm)

    def test_noop_is_exactly_zero_model_and_sql_writes(self):
        body = self.body()
        response = self.immutable(method="post", data=body)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(UsuarioEmpresaPermiso.objects.filter(usuario=self.target, empresa=self.empresa).exists())

    def test_changed_role_flags_and_hidden_state_make_signed_version_stale_without_retokenizing(self):
        for field in ("puede_ver_pacientes", "puede_contabilidad"):
            body = self.body(selected={"puede_editar_pacientes"})
            role = RolSistema.objects.get(pk=self.role_a.pk)
            RolSistema.objects.filter(pk=role.pk).update(**{field: not getattr(role, field)})
            page = self.immutable(method="post", data=body)
            self.assertEqual(page.status_code, 409)
            parsed = _ClinicalPermissionMarkup(page.content)
            self.assertEqual(parsed.hidden("version_permisos"), body["version_permisos"])
            self.assertEqual(parsed.checked(), {"puede_editar_pacientes"})
            self.assertTrue(any(attrs.get("href") == self.url() for attrs in parsed.anchors))

    def test_assignment_and_target_account_state_changes_invalidate_version_without_overwriting(self):
        body = self.body(selected={"puede_crear_recetas"})
        UsuarioEmpresaPermiso.objects.create(usuario=self.target, empresa=self.empresa, rol_sistema=self.role_b)
        self.assertEqual(self.immutable(method="post", data=body).status_code, 409)
        body = self.body(selected={"puede_crear_recetas"})
        Usuario.objects.filter(pk=self.target.pk).update(is_staff=True)
        self.assertEqual(self.immutable(method="post", data=body).status_code, 409)

    def test_a_successful_version_cannot_be_replayed_to_create_duplicate_snapshots(self):
        body = self.body(selected={"puede_ver_recetas"})
        self.assertEqual(self.client.post(self.url(), body).status_code, 302)
        self.assertEqual(self.immutable(method="post", data=body).status_code, 409)

    def test_independent_company_administrator_privilege_is_preserved_not_promised_revoked(self):
        url = self.url(target=self.admin_target)
        page = self.immutable(url)
        self.assertContains(page, "administrador")
        body = self.body(target=self.admin_target, selected=set())
        account = list(Usuario.objects.order_by("pk").values())
        self.assertEqual(self.client.post(url, body).status_code, 302)
        self.admin_target.refresh_from_db()
        self.assertEqual(list(Usuario.objects.order_by("pk").values()), account)
        self.assertTrue(self.admin_target.es_administrador_empresa)
        self.assertTrue(self.admin_target.tiene_permiso_erp("puede_editar_pacientes", self.empresa))

    def test_web_assignment_and_global_role_catalog_remain_original(self):
        for name, args, template in (("usuario_clinico_permisos", [self.empresa.slug, self.target.pk], "core/usuario_clinico_permisos.html"), ("roles_clinicos", [self.empresa.slug], "core/roles_clinicos.html")):
            page = self.client.get(reverse(name, args=args))
            self.assertEqual(page.status_code, 200)
            self.assertTemplateUsed(page, template)
            self.assertTemplateNotUsed(page, "core/mobile_user_permissions_edit.html")
        before = self.role_values()
        self.assertEqual(self.client.post(reverse("usuario_clinico_permisos", args=[self.empresa.slug, self.target.pk]), {"rol_sistema": self.role_b.pk}).status_code, 302)
        self.assertEqual(self.role_values(), before)

    def test_signed_payload_contains_only_scope_ids_and_digest_not_hidden_role_or_account_fields(self):
        from .mobile_user_permissions import VERSION_MAX_AGE, VERSION_SALT
        token = self.token()
        payload = signing.loads(token, salt=VERSION_SALT, max_age=VERSION_MAX_AGE)
        self.assertEqual(set(payload), {"actor", "empresa", "usuario", "estado"})
        self.assertEqual((payload["actor"], payload["empresa"], payload["usuario"]), (self.actor.pk, self.empresa.pk, self.target.pk))
        self.assertRegex(payload["estado"], r"^[0-9a-f]{64}$")
        for value in (self.target.email, self.target.password, "puede_contabilidad", "Descripción histórica"):
            self.assertNotIn(value, str(payload))

    def test_invalid_signature_expired_signature_and_wrong_scope_keep_bound_draft_without_writes(self):
        from .mobile_user_permissions import VERSION_MAX_AGE
        selected = {"puede_editar_pacientes", "puede_enviar_recetas"}
        original = self.body(selected=selected)
        bad = dict(original); bad["version_permisos"] += "altered"
        response = self.immutable(method="post", data=bad)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(_ClinicalPermissionMarkup(response.content).checked(), selected)
        with patch("django.core.signing.time.time", return_value=time.time()+VERSION_MAX_AGE+2):
            expired = self.immutable(method="post", data=original)
        self.assertEqual(expired.status_code, 400)
        self.assertEqual(_ClinicalPermissionMarkup(expired.content).hidden("version_permisos"), original["version_permisos"])
        for company, target in ((self.empresas["medical_spa"], self.targets["medical_spa"]), (self.empresa, self.shared)):
            response = self.immutable(self.url(company, target), "post", original)
            self.assertEqual(response.status_code, 400)

    def test_signature_is_bound_to_the_administrator_not_reusable_by_another_authorized_actor(self):
        body = self.body(selected={"puede_editar_pacientes"})
        actor = Usuario.objects.create_user(username="second-individual-admin-qa", password="test-only", empresa=self.empresa, puede_administrar_usuarios_clinicos=True)
        request = self.request(method="post", data=body, actor=actor)
        before = self.snapshot()
        response = self.direct(request)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(_ClinicalPermissionMarkup(response.content).hidden("version_permisos"), body["version_permisos"])

    def test_previous_inactive_assignment_falls_back_to_effective_global_role_and_only_local_row_updates(self):
        local = UsuarioEmpresaPermiso.objects.create(usuario=self.target, empresa=self.empresa, rol_sistema=self.role_b, activo=False)
        body = self.body(selected={"puede_ver_recetas"})
        before = self.snapshot(); old_roles = self.role_values()
        self.assertEqual(self.client.post(self.url(), body).status_code, 302)
        local.refresh_from_db()
        self.assertTrue(local.activo)
        self.assert_snapshot_role(self.role_a, local.rol_sistema, {"puede_ver_recetas"})
        self.assertEqual(list(RolSistema.objects.filter(pk__in=[row["id"] for row in old_roles]).order_by("pk").values()), old_roles)
        self.assert_only_local_changes(before)

    def test_each_later_edit_creates_another_snapshot_without_mutating_a_reused_local_snapshot(self):
        first = {"puede_crear_recetas"}
        self.assertEqual(self.client.post(self.url(), self.body(selected=first)).status_code, 302)
        assignment = UsuarioEmpresaPermiso.objects.get(usuario=self.target, empresa=self.empresa)
        previous = assignment.rol_sistema
        UsuarioEmpresaPermiso.objects.create(usuario=self.shared, empresa=self.empresa, rol_sistema=previous)
        original = RolSistema.objects.filter(pk=previous.pk).values().get()
        second = {"puede_editar_pacientes", "puede_ver_historia_clinica"}
        self.assertEqual(self.client.post(self.url(), self.body(selected=second)).status_code, 302)
        assignment.refresh_from_db()
        self.assert_snapshot_role(previous, assignment.rol_sistema, second)
        self.assertEqual(RolSistema.objects.filter(pk=previous.pk).values().get(), original)
        self.assertEqual(UsuarioEmpresaPermiso.objects.get(usuario=self.shared, empresa=self.empresa).rol_sistema_id, previous.pk)

    def test_explicit_audit_records_actual_actor_company_assignment_and_exact_before_after_flags(self):
        selected = {"puede_editar_pacientes", "puede_crear_recetas"}
        before_selected = self.selected()
        previous = self.role_a.pk
        response = self.client.post(self.url(), self.body(selected=selected))
        self.assertEqual(response.status_code, 302)
        assignment = UsuarioEmpresaPermiso.objects.get(usuario=self.target, empresa=self.empresa)
        audit = RegistroAuditoria.objects.get(motivo="Edición móvil de permisos clínicos únicamente en esta empresa.")
        self.assertEqual((audit.empresa_id, audit.usuario_id, audit.modelo, audit.objeto_id, audit.metodo_http), (self.empresa.pk, self.actor.pk, "usuarioempresapermiso", str(assignment.pk), "POST"))
        expected = {name: {"anterior": name in before_selected, "nuevo": name in selected} for name in self.FIELDS if (name in before_selected) != (name in selected)}
        expected["rol_sistema"] = {"anterior": previous, "nuevo": assignment.rol_sistema_id}
        self.assertEqual(audit.cambios, expected)
        self.assertEqual(audit.ruta, urlsplit(self.url()).path)

    def test_role_assignment_and_mandatory_audit_failures_roll_back_every_model(self):
        from . import mobile_user_permissions as editor
        body = self.body(selected={"puede_editar_pacientes"})
        for target in ("core.mobile_user_permissions.RolSistema.objects.create", "core.mobile_user_permissions.UsuarioEmpresaPermiso.objects.create", "core.mobile_user_permissions._registrar_cambio_permisos"):
            before = self.snapshot()
            with patch(target, side_effect=RuntimeError("Synthetic transaction failure")) as failing:
                with self.assertRaisesRegex(RuntimeError, "Synthetic transaction failure"):
                    self.direct(self.request(method="post", data=body))
                failing.assert_called_once()
            self.assertEqual(self.snapshot(), before)
        self.assertTrue(callable(editor.gestionar_permisos_usuario_app))

    def test_existing_assignment_save_failure_rolls_back_new_role_snapshot_and_audit(self):
        UsuarioEmpresaPermiso.objects.create(usuario=self.target, empresa=self.empresa, rol_sistema=self.role_a)
        body = self.body(selected={"puede_editar_pacientes"}); before = self.snapshot()
        with patch.object(UsuarioEmpresaPermiso, "save", side_effect=RuntimeError("Synthetic assignment save failure")) as failing:
            with self.assertRaisesRegex(RuntimeError, "Synthetic assignment save failure"):
                self.direct(self.request(method="post", data=body))
            failing.assert_called_once()
        self.assertEqual(self.snapshot(), before)

    def test_ineligible_role_post_cannot_initialize_or_upgrade_none_legacy_or_inactive(self):
        signed = self.token()
        for role in (None, self.base_role, self.inactive_role):
            Usuario.objects.filter(pk=self.target.pk).update(rol_sistema=role)
            response = self.immutable(method="post", data=self.body(selected={"puede_editar_pacientes"}, version=signed))
            self.assertEqual(response.status_code, 409)
            self.assertFalse(response.context["mobile_user_permissions_editable"])
        self.assertFalse(UsuarioEmpresaPermiso.objects.filter(usuario=self.target, empresa=self.empresa).exists())

    def test_capability_is_pure_and_new_entry_links_only_offer_eligible_current_company_users(self):
        from .mobile_user_permissions import permisos_usuario_editables_app
        request = self.request(); reset_queries()
        with CaptureQueriesContext(connection) as queries:
            self.assertTrue(permisos_usuario_editables_app(request, self.empresa, self.role_a))
            for role in (None, self.base_role, self.inactive_role):
                self.assertFalse(permisos_usuario_editables_app(request, self.empresa, role))
        self.assertFalse(any('"core_usuarioempresapermiso"' in row["sql"].lower() or '"core_rolsistema"' in row["sql"].lower() for row in queries))
        listing = self.immutable(reverse("usuarios_clinicos", args=[self.empresa.slug])+"?app=1")
        rows = {row["usuario"].pk: row for row in listing.context["filas"]}
        self.assertEqual(rows[self.target.pk]["permissions_edit_url"], self.url())
        self.assertFalse(rows[self.inactive_user.pk].get("permissions_edit_url"))
        original = self.immutable(reverse("usuario_clinico_permisos", args=[self.empresa.slug, self.target.pk])+"?app=1")
        self.assertTrue(original.context["can_edit_user_clinical_permissions"])
        self.assertEqual(original.context["app_user_permissions_edit_url"], self.url())

    def test_anonymous_and_inactive_administrator_do_not_gain_access_or_seed_records(self):
        self.denied(actor=AnonymousUser(), status=302)
        Usuario.objects.filter(pk=self.actor.pk).update(is_active=False)
        response = self.immutable()
        self.assertEqual(response.status_code, 302)

    def test_csrf_uses_original_failure_notice_and_valid_token_does_not_bypass_new_closed_body(self):
        from .views import SESSION_EXPIRED_MESSAGE_KEY
        strict = Client(enforce_csrf_checks=True); strict.force_login(self.actor)
        page = strict.get(self.url()); parsed = _ClinicalPermissionMarkup(page.content)
        body = self.body(version=parsed.hidden("version_permisos"), selected={"puede_editar_pacientes"})
        before = self.snapshot(exclude={"sessions.session"})
        response = strict.post(self.url(), body)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("empresa_login", kwargs={"slug": self.empresa.slug}))
        self.assertTrue(strict.session.get(SESSION_EXPIRED_MESSAGE_KEY))
        self.assertEqual(self.snapshot(exclude={"sessions.session"}), before)
        body["csrfmiddlewaretoken"] = parsed.hidden("csrfmiddlewaretoken")
        # Unknown field still fails before mutation with a genuine CSRF token.
        body = {**{key: value for key, value in body.items() if key != "confirmar_permisos"}, "email": "forged@example.invalid", "confirmar_permisos": "1"}
        before = self.snapshot()
        self.assertEqual(strict.post(self.url(), body).status_code, 403)
        self.assertEqual(self.snapshot(), before)
