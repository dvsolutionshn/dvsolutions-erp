"""Control privado de enlaces: sólo lectura, sin visitar URLs públicas.

Identidades y pacientes sintéticos en TestCase. La lista móvil no genera ni
recupera tokens, no registra aperturas y no cambia la autorización privada.
"""
from datetime import datetime, timedelta
from html.parser import HTMLParser
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlsplit
from unittest.mock import patch

from django.apps import apps
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
from core.models import Empresa, EmpresaModulo, Modulo, RolSistema, Usuario, UsuarioEmpresaPermiso
from crm.models import CitaCliente
from facturacion.models import InventarioProducto, Producto
from . import views
from .models import InvitacionRegistroPaciente, Paciente, PreconsultaClinica, ProfesionalSalud, ServicioClinico, TratamientoPaciente
from .tokens import hash_token_preconsulta


class _RegistrationMarkup(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.forms, self.links, self.scripts = [], [], []
        self.current = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.current = {"attrs": attrs, "names": []}
            self.forms.append(self.current)
        if self.current is not None and tag in {"input", "select", "textarea", "button"} and attrs.get("name"):
            self.current["names"].append(attrs["name"])
        if tag == "a" and attrs.get("href"):
            self.links.append(attrs["href"])
        if tag == "script":
            self.scripts.append(attrs)

    def handle_endtag(self, tag):
        if tag == "form":
            self.current = None


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class MobileRegistrationControlTests(TestCase):
    SLUGS = ("hospital_mia", "medical_spa", "luque_aestetic", "serviciosmedicos")
    STATES = ("generado", "abierto", "en_progreso", "validacion", "error_tecnico", "completado", "vencido", "revocado")
    TEMPLATE = "clinica/mobile_registration_control.html"
    NOW = timezone.make_aware(datetime(2026, 10, 13, 12))

    @classmethod
    def setUpTestData(cls):
        cls.modulo, _ = Modulo.objects.get_or_create(codigo="clinica_medica", defaults={"nombre": "Clínica"})
        cls.empresas = {slug: Empresa.objects.create(nombre=f"Registro privado QA {slug}", slug=slug,
            rtn=f"REG-CONTROL-{index}", tipo_solucion="clinica", estado_licencia="activa")
            for index, slug in enumerate(cls.SLUGS, 1)}
        cls.empresa = cls.empresas["hospital_mia"]
        cls.external = Empresa.objects.create(nombre="Registro privado externo QA", slug="registration-control-external-qa",
            rtn="REG-CONTROL-EXT", tipo_solucion="clinica", estado_licencia="activa")
        cls.roles = {name: RolSistema.objects.create(nombre=f"Registro privado {name} QA", codigo=f"registration-control-{name}-qa",
            es_rol_clinico=True, usa_permisos_clinicos_granulares=name == "granular",
            **({"puede_crear_pacientes": True} if name == "granular" else {"puede_pacientes": True}))
            for name in ("legacy", "granular")}
        # The nominal private identity is synthetic in this temporary database.
        cls.owners = {name: Usuario.objects.create_user(username="daniel_varela" if name == "granular" else "registration-control-owner-legacy-qa",
            email="dannyvarela25@gmail.com" if name == "legacy" else "owner@example.invalid", password="test-only",
            empresa=cls.empresa, rol_sistema=role) for name, role in cls.roles.items()}
        cls.owner = cls.owners["granular"]
        for actor in cls.owners.values():
            actor.empresas_acceso.add(*cls.empresas.values())
        cls.super_owner = Usuario.objects.create_superuser(username="registration-control-owner-super-qa", password="test-only",
            email="dannyvarela25@gmail.com", empresa=cls.external)
        cls.supervisor = Usuario.objects.create_superuser(username="registration-control-unlisted-super-qa", password="test-only",
            email="super@example.invalid", empresa=cls.empresa)
        cls.admin = Usuario.objects.create_user(username="registration-control-unlisted-admin-qa", password="test-only",
            empresa=cls.empresa, es_administrador_empresa=True)
        cls.non_owner = Usuario.objects.create_user(username="registration-control-unlisted-doctor-qa", password="test-only",
            empresa=cls.empresa, rol_sistema=cls.roles["granular"])
        cls.foreign_creator = Usuario.objects.create_user(username="registration-control-foreign-creator-qa", password="test-only",
            empresa=cls.external, first_name="Autor histórico", last_name="Multicompañía", email="NEVER-EXPOSE-AUTHOR@example.invalid")
        cls.pacientes, cls.profesionales, cls.servicios, cls.citas = {}, {}, {}, {}
        for index, empresa in enumerate((*cls.empresas.values(), cls.external), 1):
            EmpresaModulo.objects.create(empresa=empresa, modulo=cls.modulo)
            slug = empresa.slug
            cls.pacientes[slug] = Paciente.objects.create(empresa=empresa, nombre=f"Paciente sintético registro privado {index}",
                expediente_codigo=f"REGCTL-{index}", identidad=f"0801202900{index:03d}",
                telefono="NEVER-PATIENT-CONTACT-29", correo=f"PRIVATE-PATIENT-{index}@example.invalid")
            cls.profesionales[slug] = ProfesionalSalud.objects.create(empresa=empresa, nombre=f"Profesional intacto registro {index}")
            cls.servicios[slug] = ServicioClinico.objects.create(empresa=empresa, nombre=f"Servicio intacto registro {index}")
            cls.citas[slug] = CitaCliente.objects.create(empresa=empresa, paciente=cls.pacientes[slug],
                profesional_salud=cls.profesionales[slug], servicio_clinico=cls.servicios[slug],
                titulo=f"Cita intacta registro {index}", fecha_hora=cls.NOW+timedelta(days=2))
            TratamientoPaciente.objects.create(empresa=empresa, paciente=cls.pacientes[slug], nombre=f"Tratamiento intacto registro {index}", fecha_inicio="2026-10-13")
            product = Producto.objects.create(empresa=empresa, nombre=f"Producto intacto registro {index}", codigo=f"REGCTL-P-{index}", precio="18.00")
            InventarioProducto.objects.create(empresa=empresa, producto=product, existencias="17.00")
            CuentaContable.objects.create(empresa=empresa, codigo=f"REGCTL-A-{index}", nombre=f"Cuenta intacta registro {index}", tipo="activo")
        cls.raw_tokens, cls.invitations = {}, {}
        for index, state in enumerate(cls.STATES):
            prefix = f"QA29{index:02d}XY"
            raw = prefix+"-PRIVATE-RAW-TOKEN-NOT-FOR-RENDERING"
            cls.raw_tokens[state] = raw
            values = {"empresa": cls.empresa, "token_hash": hash_token_preconsulta(raw),
                      "token_preview": prefix+"TAILDATA", "fecha_expiracion": cls.NOW+timedelta(days=7),
                      "creada_por": cls.owner, "ip_completada": "192.0.2.29"}
            if state in {"abierto", "en_progreso"}:
                values.update(fecha_primera_apertura=cls.NOW-timedelta(hours=4), fecha_ultima_actividad=cls.NOW-timedelta(hours=1),
                              cantidad_aperturas=2, paso_maximo=2 if state == "en_progreso" else 1)
            elif state in {"validacion", "error_tecnico"}:
                values.update(ultimo_resultado=state, intentos_envio=1, fecha_ultimo_intento=cls.NOW-timedelta(minutes=5))
            elif state == "completado":
                values.update(paciente=cls.pacientes[cls.empresa.slug], fecha_completada=cls.NOW-timedelta(hours=1),
                              ultimo_resultado="completado", paso_maximo=3)
            elif state == "vencido":
                values["fecha_expiracion"] = cls.NOW-timedelta(seconds=1)
            elif state == "revocado":
                values["estado"] = "revocada"
            invitation = InvitacionRegistroPaciente.objects.create(**values)
            InvitacionRegistroPaciente.objects.filter(pk=invitation.pk).update(fecha_creacion=cls.NOW-timedelta(days=index))
            invitation.refresh_from_db()
            cls.invitations[state] = invitation
        cls.foreign_invitations = {slug: InvitacionRegistroPaciente.objects.create(empresa=empresa,
            token_hash=hash_token_preconsulta(f"PRIVATE-FOREIGN-INVITATION-{slug}"), token_preview="FOREIGN-CODE-QA",
            fecha_expiracion=cls.NOW+timedelta(days=7), paciente=cls.pacientes[slug], creada_por=cls.foreign_creator)
            for slug, empresa in cls.empresas.items() if empresa.pk != cls.empresa.pk}
        cls.preconsulta = PreconsultaClinica.objects.create(empresa=cls.external, paciente=cls.pacientes[cls.external.slug],
            token_hash=hash_token_preconsulta("PRIVATE-PRECONSULTA-29"), token_preview="PRIV29", fecha_expiracion=cls.NOW+timedelta(days=7),
            datos_generales={"NO-MOBILE-RESPONSES": "PRIVATE-PRECONSULTA-DATA-29"}, motivo_consulta="NEVER-PRECONSULTA-29")

    def setUp(self):
        now = patch("clinica.views.timezone.now", return_value=self.NOW)
        now.start()
        self.addCleanup(now.stop)
        self.client.force_login(self.owner)
        for target in ("clinica.views.registro_paciente_publico", "clinica.views._registrar_apertura_invitacion",
                       "clinica.views._marcar_intento_invitacion", "clinica.views.generar_token_preconsulta",
                       "clinica.views.PreconsultaClinicaPublicaForm", "clinica.views._configuracion_clinica",
                       "crm.views.CitaClienteForm", "crm.views._contexto_calendario", "crm.views.urlopen",
                       "clinica.views.enviar_documento_whatsapp", "django.core.mail.EmailMessage.send"):
            blocker = patch(target, side_effect=AssertionError("No formularios públicos, inicializadores, tokens ni transportes"))
            mock = blocker.start()
            self.addCleanup(blocker.stop)
            self.addCleanup(mock.assert_not_called)

    def url(self, empresa=None, *, mobile=True, **parameters):
        path = reverse("clinica_control_enlaces_registro_paciente", args=[(empresa or self.empresa).slug])
        query = {"app": "1"} if mobile else {}
        query.update(parameters)
        return path+("?"+urlencode(query, doseq=True) if query else "")

    def direct(self, url=None, *, actor=None, method="get", data=None):
        url = url or self.url()
        factory = RequestFactory()
        request = factory.post(url, data or {}) if method == "post" else factory.generic(method.upper(), url)
        request.user = Usuario.objects.get(pk=(actor or self.owner).pk)
        request.session = {}
        request._messages = FallbackStorage(request)
        request._dont_enforce_csrf_checks = True
        match = resolve(urlsplit(url).path)
        request.resolver_match = match
        return match.func(request, **match.kwargs)

    def snapshot(self, exclude=()):
        return tuple((model._meta.label_lower, list(model.objects.order_by("pk").values()))
            for model in apps.get_models(include_auto_created=True) if model._meta.label_lower not in exclude)

    def no_writes(self, queries):
        self.assertFalse(any(row["sql"].lstrip().upper().startswith(("INSERT ", "UPDATE ", "DELETE ", "REPLACE ")) for row in queries))

    def no_private_queries(self, queries):
        for model in (PreconsultaClinica, ProfesionalSalud, ServicioClinico, TratamientoPaciente, CitaCliente):
            self.assertFalse(any(model._meta.db_table in row["sql"] and row["sql"].lstrip().upper().startswith("SELECT")
                                 for row in queries), model._meta.label)

    def denied(self, url=None, *, status=403, **kwargs):
        before = self.snapshot()
        try:
            with CaptureQueriesContext(connection) as queries:
                response = self.direct(url, **kwargs)
        except PermissionDenied:
            self.assertEqual(status, 403)
        except Http404:
            self.assertEqual(status, 404)
        else:
            self.assertEqual(response.status_code, status)
        self.assertEqual(self.snapshot(), before)
        self.no_writes(queries)

    def role(self, *, actor=None, empresa=None, granular=True, **flags):
        role = RolSistema.objects.create(nombre="Registro privado rol restringido QA", codigo=f"registration-control-r-{RolSistema.objects.count()}",
            es_rol_clinico=True, usa_permisos_clinicos_granulares=granular, **flags)
        UsuarioEmpresaPermiso.objects.update_or_create(usuario=actor or self.owner, empresa=empresa or self.empresa,
            defaults={"rol_sistema": role, "activo": True})
        return role

    def response(self, **parameters):
        response = self.client.get(self.url(**parameters))
        self.assertEqual(response.status_code, 200)
        return response

    def rows(self, response):
        return response.context["mobile_registration_items"]

    def test_original_whitelisted_owner_and_both_role_types_can_read_mobile(self):
        for actor in self.owners.values():
            self.client.force_login(actor)
            response = self.response()
            self.assertTemplateUsed(response, self.TEMPLATE)
            self.assertEqual(response.context["conteos"]["total"], 8)
            self.assertEqual({row["estado_codigo"] for row in self.rows(response)}, set(self.STATES))

    def test_non_owner_super_administrator_or_ordinary_doctor_cannot_use_private_control(self):
        for actor in (self.supervisor, self.admin, self.non_owner):
            self.denied(actor=actor)
            self.denied(actor=actor, method="head")
            self.denied(actor=actor, method="post", data={"app": "1"})

    def test_owner_identity_requires_original_creator_patient_permission_not_clinical_config(self):
        for flags in ({}, {"puede_configuracion_clinica": True}, {"puede_ver_pacientes": True},
                      {"puede_ver_datos_generales_paciente": True}, {"puede_expediente_clinico": True}):
            self.role(**flags)
            self.denied()

    def test_legacy_owner_needs_patient_permission_not_expediente_or_configuration(self):
        actor = self.owners["legacy"]
        for flags in ({}, {"puede_expediente_clinico": True}, {"puede_configuracion_clinica": True}):
            self.role(actor=actor, granular=False, **flags)
            self.denied(actor=actor)
        self.role(actor=actor, granular=False, puede_pacientes=True)
        self.assertEqual(self.direct(actor=actor).status_code, 200)

    def test_private_identity_normalization_preserves_only_the_original_whitelist(self):
        actor = self.owner
        for username in ("DANNYVARELA25", " DanielVarela ", "daniel_varela", "dannyvarela25@gmail.com"):
            Usuario.objects.filter(pk=actor.pk).update(username=username, email="owner@example.invalid")
            self.assertEqual(self.direct(actor=actor).status_code, 200)
        Usuario.objects.filter(pk=actor.pk).update(username="Not-private-owner", email=" DANNYVARELA25@GMAIL.COM ")
        self.assertEqual(self.direct(actor=actor).status_code, 200)
        Usuario.objects.filter(pk=actor.pk).update(email="dannyvarela25@example.invalid")
        self.denied(actor=actor)

    def test_get_head_all_models_through_and_sql_are_unchanged(self):
        before = self.snapshot()
        for method in ("get", "head"):
            with CaptureQueriesContext(connection) as queries:
                response = getattr(self.client, method)(self.url())
            self.assertEqual(response.status_code, 200)
            self.assertIn("no-store", response["Cache-Control"])
            if method == "head":
                self.assertEqual(response.content, b"")
            self.no_writes(queries)
            self.no_private_queries(queries)
        self.assertEqual(self.snapshot(), before)

    def test_logged_out_has_original_redirect_without_tracking_or_seeding(self):
        self.client.logout()
        before = self.snapshot()
        for method in ("get", "head"):
            response = getattr(self.client, method)(self.url())
            self.assertEqual(response.status_code, 302)
            self.assertIn("next=", response.url)
        self.assertEqual(self.snapshot(), before)

    def test_other_three_clinics_and_external_company_remain_private_denials(self):
        for empresa in (*[value for key, value in self.empresas.items() if key != "hospital_mia"], self.external):
            self.denied(self.url(empresa), actor=self.super_owner)
            self.denied(self.url(empresa), actor=self.owner)

    def test_hospital_must_be_active_even_for_whitelisted_super(self):
        Empresa.objects.filter(pk=self.empresa.pk).update(activa=False)
        self.denied(actor=self.super_owner, status=404)

    def test_app_requires_clinical_profile_and_active_clinical_module(self):
        Empresa.objects.filter(pk=self.empresa.pk).update(tipo_solucion="erp")
        self.denied(actor=self.super_owner)
        Empresa.objects.filter(pk=self.empresa.pk).update(tipo_solucion="clinica")
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.modulo).update(activo=False)
        self.denied(actor=self.super_owner)

    def test_membership_is_required_even_for_matching_private_identity(self):
        Usuario.objects.filter(pk=self.owner.pk).update(empresa=self.external)
        self.owner.empresas_acceso.remove(self.empresa)
        self.denied()

    def test_license_preserves_only_original_whitelisted_super_exception(self):
        for state in ("suspendida", "vencida"):
            Empresa.objects.filter(pk=self.empresa.pk).update(estado_licencia=state)
            self.denied()
            self.denied(actor=self.owners["legacy"])
            self.assertEqual(self.direct(actor=self.super_owner).status_code, 200)
            self.denied(actor=self.supervisor)  # Super does not replace the private identity.

    def test_original_company_admin_bypass_still_requires_owner_identity(self):
        Usuario.objects.filter(pk=self.owner.pk).update(es_administrador_empresa=True, rol_sistema=None)
        self.assertEqual(self.direct().status_code, 200)
        Empresa.objects.filter(pk=self.empresa.pk).update(estado_licencia="suspendida")
        self.denied()
        self.denied(actor=self.admin)

    def test_active_local_role_override_and_none_do_not_fall_back_to_global_permission(self):
        self.role(puede_configuracion_clinica=True)
        self.denied()
        UsuarioEmpresaPermiso.objects.filter(usuario=self.owner, empresa=self.empresa).update(rol_sistema=None)
        self.denied()
        UsuarioEmpresaPermiso.objects.filter(usuario=self.owner, empresa=self.empresa).update(activo=False)
        self.assertEqual(self.direct().status_code, 200)
        RolSistema.objects.filter(pk=self.roles["granular"].pk).update(activo=False)
        self.denied()

    def test_post_and_other_verbs_cannot_generate_revoke_send_or_modify_any_record(self):
        for method in ("post", "put", "patch", "delete", "options"):
            self.denied(method=method, data={"app": "1", "accion": "revocar", "invitacion_id": self.invitations["generado"].pk})

    def test_body_app_opt_in_without_query_cannot_bypass_readonly_guard(self):
        self.denied(self.url(mobile=False), method="post", data={"app": "1"})
        self.denied(self.url(mobile=False), method="post", data={"app": ["0", "1"]})

    def test_any_app_query_key_requires_exact_single_one_not_web_fallback(self):
        for app in ("", "0", "01", "agenda", ["1", "1"], ["0", "1"]):
            self.denied(self.url(app=app))

    def test_duplicate_filter_scalars_are_rejected(self):
        for key in ("q", "estado", "desde", "hasta", "pagina"):
            self.denied(self.url(**{key: ["1", "1"]}))

    def test_unknown_get_parameters_never_enable_public_or_operational_actions(self):
        for key in ("token", "token_hash", "invitacion_id", "paciente_id", "empresa", "accion", "generar", "revocar", "next", "_popup"):
            self.denied(self.url()+"&"+urlencode({key: "1"}))

    def test_files_are_denied_even_in_a_forged_read_request(self):
        request = RequestFactory().post(self.url(), {"app": "1", "archivo": SimpleUploadedFile("qa.txt", b"synthetic")})
        request.user = self.owner
        request.session = {}
        request._messages = FallbackStorage(request)
        self.assertTrue(request.FILES)
        request.method = "GET"
        before = self.snapshot()
        with self.assertRaises(PermissionDenied):
            views.control_enlaces_registro_paciente(request, empresa_slug=self.empresa.slug)
        self.assertEqual(self.snapshot(), before)

    def test_get_head_raw_bodies_are_denied_before_private_ledger_queries(self):
        for method in ("GET", "HEAD"):
            for body, content_type in ((b'{"app":"1"}', "application/json"), (b"app=1", "application/x-www-form-urlencoded")):
                with self.subTest(method=method, content_type=content_type):
                    before = self.snapshot()
                    request = RequestFactory().generic(method, self.url(), body, content_type=content_type)
                    request.user = self.owner
                    request.session = {}
                    request._messages = FallbackStorage(request)
                    with CaptureQueriesContext(connection) as queries:
                        with self.assertRaises(PermissionDenied):
                            views.control_enlaces_registro_paciente(request, empresa_slug=self.empresa.slug)
                    self.no_writes(queries)
                    self.assertFalse(any(InvitacionRegistroPaciente._meta.db_table in row["sql"] for row in queries))
                    response = self.client.generic(method, self.url(), body, content_type=content_type)
                    self.assertEqual(response.status_code, 403)
                    if method == "HEAD":
                        self.assertEqual(response.content, b"")
                    self.assertEqual(self.snapshot(), before)

    def test_page_metadata_is_ascii_canonical_positive_int64(self):
        for page in ("", "0", "01", "-1", "1.5", "1e2", "１", "abc", str(2**63)):
            self.denied(self.url(pagina=page))
        response = self.response(pagina=str(2**63-1))
        self.assertEqual(response.context["pagina"].number, 1)

    def test_unknown_state_filter_is_rejected_and_all_eight_states_are_supported(self):
        for state in ("pendiente", "completada", "revocada", "todos", "invalid"):
            self.denied(self.url(estado=state))
        for state in self.STATES:
            response = self.response(estado=state)
            self.assertEqual([row["estado_codigo"] for row in self.rows(response)], [state])
        response = self.response(estado=" generado ")
        self.assertEqual(response.context["estado_filtro"], "generado")
        self.assertEqual([row["estado_codigo"] for row in self.rows(response)], ["generado"])

    def test_original_state_precedence_is_reused_not_derived_from_model_state_alone(self):
        invitation = self.invitations["generado"]
        scenarios = (({"estado": "revocada", "ultimo_resultado": "completado"}, "completado"),
                     ({"estado": "revocada", "ultimo_resultado": "error_tecnico"}, "revocado"),
                     ({"estado": "pendiente", "fecha_expiracion": self.NOW-timedelta(seconds=1), "ultimo_resultado": "validacion"}, "vencido"),
                     ({"estado": "pendiente", "fecha_expiracion": self.NOW+timedelta(days=1), "ultimo_resultado": "error_tecnico", "paso_maximo": 3}, "error_tecnico"),
                     ({"ultimo_resultado": "validacion", "paso_maximo": 3}, "validacion"))
        for updates, expected in scenarios:
            InvitacionRegistroPaciente.objects.filter(pk=invitation.pk).update(**updates)
            response = self.response(q=invitation.token_preview)
            self.assertEqual([row["estado_codigo"] for row in self.rows(response)], [expected])

    def test_original_clamped_progress_and_percentage_have_no_new_clinical_rule(self):
        InvitacionRegistroPaciente.objects.filter(pk=self.invitations["en_progreso"].pk).update(paso_maximo=99)
        response = self.response(estado="en_progreso")
        self.assertEqual((self.rows(response)[0]["progreso"], self.rows(response)[0]["progreso_porcentaje"]), (3, 100))
        dummy = SimpleNamespace(fecha_completada=None, ultimo_resultado="sin_intento", estado="pendiente",
            fecha_expiracion=self.NOW+timedelta(days=1), paso_maximo=-2, fecha_primera_apertura=None)
        self.assertEqual(views._estado_control_invitacion(dummy, self.NOW)[0], "generado")

    def test_original_counts_are_after_search_and_dates_but_before_state_filter(self):
        full = self.response()
        state = self.response(estado="validacion")
        self.assertEqual(full.context["conteos"], state.context["conteos"])
        self.assertEqual(state.context["conteos"]["total"], 8)
        self.assertEqual(state.context["requieren_revision"], 2)
        filtered = self.response(q=self.invitations["validacion"].token_preview, estado="generado")
        self.assertEqual(filtered.context["conteos"]["total"], 1)
        self.assertEqual(filtered.context["conteos"]["validacion"], 1)
        self.assertFalse(self.rows(filtered))

    def test_search_reuses_code_patient_identity_creator_username_and_email(self):
        for query, minimum in ((self.invitations["generado"].token_preview, 1), (self.pacientes[self.empresa.slug].nombre, 1),
                               (self.pacientes[self.empresa.slug].identidad, 1), (self.owner.username, 8), (self.owner.email, 8)):
            response = self.response(q=query)
            self.assertEqual(response.context["conteos"]["total"], minimum)
        response = self.response(q=self.pacientes["medical_spa"].nombre)
        self.assertEqual(response.context["conteos"]["total"], 0)
        self.assertFalse(self.rows(response))

    def test_search_is_trimmed_and_unsafe_text_is_escaped_not_executed(self):
        response = self.response(q="  <script>bad()</script>  ")
        self.assertEqual(response.context["consulta"], "<script>bad()</script>")
        self.assertContains(response, "&lt;script&gt;bad()&lt;/script&gt;")
        self.assertNotContains(response, "<script>bad()</script>")
        self.assertEqual(response.context["conteos"]["total"], 0)

    def test_date_filters_use_original_strptime_and_inclusive_local_calendar_dates(self):
        day = timezone.localtime(self.invitations["validacion"].fecha_creacion).date().isoformat()
        response = self.response(desde=day, hasta=day)
        self.assertEqual([row["estado_codigo"] for row in self.rows(response)], ["validacion"])
        self.assertEqual(response.context["conteos"]["total"], 1)

    def test_invalid_dates_clear_original_filter_instead_of_new_denial_rule(self):
        for value in ("not-date", "2026-02-30", "2026-13-01", "2026-10-13T12:00:00"):
            for key in ("desde", "hasta"):
                response = self.response(**{key: value})
                self.assertEqual(response.context["fecha_desde" if key == "desde" else "fecha_hasta"], "")
                self.assertEqual(response.context["conteos"]["total"], 8)

    def test_wrapped_and_non_padded_original_date_input_is_not_new_canonical_validation(self):
        response = self.response(desde=" 2026-10-13 ")
        self.assertEqual(response.context["fecha_desde"], "2026-10-13")
        self.assertEqual(response.context["conteos"]["total"], 1)
        response = self.response(desde="2026-10-6", hasta="2026-10-6")
        self.assertEqual(response.context["conteos"]["total"], 1)
        self.assertEqual((response.context["fecha_desde"], response.context["fecha_hasta"]), ("2026-10-06", "2026-10-06"))
        self.assertContains(response, 'name="desde" value="2026-10-06"')
        self.assertContains(response, 'name="hasta" value="2026-10-06"')

    def test_reversed_date_range_remains_empty_without_swapping(self):
        response = self.response(desde="2026-10-13", hasta="2026-10-01")
        self.assertEqual(response.context["conteos"]["total"], 0)
        self.assertFalse(self.rows(response))

    def test_original_page_size_forty_order_and_summaries_are_for_all_filtered_rows(self):
        for index in range(45):
            InvitacionRegistroPaciente.objects.create(empresa=self.empresa, token_hash=hash_token_preconsulta(f"PAGE-PRIVATE-29-{index}"),
                token_preview=f"PAGE{index:04d}", fecha_expiracion=self.NOW+timedelta(days=1))
        response = self.response()
        self.assertEqual(response.context["pagina"].paginator.per_page, 40)
        self.assertEqual(response.context["conteos"]["total"], 53)
        self.assertEqual(len(self.rows(response)), 40)
        second = self.response(pagina="2")
        self.assertEqual(len(self.rows(second)), 13)
        self.assertEqual(second.context["conteos"], response.context["conteos"])
        expected = list(InvitacionRegistroPaciente.objects.filter(empresa=self.empresa).order_by("-fecha_creacion", "-id").values_list("token_preview", flat=True))
        masked = [row["invitacion"].code_masked for row in self.rows(response)+self.rows(second)]
        self.assertEqual([value[:8] for value in masked], [value[:8] for value in expected])

    def test_patient_foreign_or_orphan_relations_fail_before_q_dates_state_or_page(self):
        invitation = self.invitations["completado"]
        for patient_id in (self.pacientes["medical_spa"].pk, 2**62):
            InvitacionRegistroPaciente.objects.filter(pk=invitation.pk).update(paciente_id=patient_id)
            for query in ({"q": self.invitations["generado"].token_preview}, {"desde": "2099-01-01"}, {"estado": "generado"}, {"pagina": "2"}):
                self.denied(self.url(**query))
            InvitacionRegistroPaciente.objects.filter(pk=invitation.pk).update(paciente=self.pacientes[self.empresa.slug])

    def test_orphan_creator_fails_before_hidden_row_filters(self):
        invitation = self.invitations["vencido"]
        InvitacionRegistroPaciente.objects.filter(pk=invitation.pk).update(creada_por_id=2**62)
        for query in ({"estado": "generado"}, {"q": "no-match"}, {"pagina": "99"}, {"desde": "2099-01-01"}):
            self.denied(self.url(**query))
        InvitacionRegistroPaciente.objects.filter(pk=invitation.pk).update(creada_por=self.owner)

    def test_null_patient_creator_and_inactive_own_patient_are_valid_historical_data(self):
        Paciente.objects.filter(pk=self.pacientes[self.empresa.slug].pk).update(activo=False)
        InvitacionRegistroPaciente.objects.filter(pk=self.invitations["generado"].pk).update(paciente=None, creada_por=None)
        response = self.response()
        self.assertEqual(response.context["conteos"]["total"], 8)

    def test_existing_inactive_multicompany_creator_is_not_restricted_by_primary_company(self):
        Usuario.objects.filter(pk=self.foreign_creator.pk).update(is_active=False)
        InvitacionRegistroPaciente.objects.filter(pk=self.invitations["generado"].pk).update(creada_por=self.foreign_creator)
        response = self.response(q=self.foreign_creator.username)
        self.assertEqual(response.context["conteos"]["total"], 1)
        self.assertNotContains(response, self.foreign_creator.email)

    def test_corrupt_other_company_invitation_does_not_block_or_leak_current_hospital(self):
        invitation = self.foreign_invitations["medical_spa"]
        InvitacionRegistroPaciente.objects.filter(pk=invitation.pk).update(paciente_id=2**62, creada_por_id=2**62)
        response = self.response()
        self.assertEqual(response.context["conteos"]["total"], 8)
        self.assertNotContains(response, invitation.token_preview)
        InvitacionRegistroPaciente.objects.filter(pk=invitation.pk).update(paciente=self.pacientes["medical_spa"], creada_por=self.foreign_creator)

    def test_unused_foreign_or_orphan_preconsulta_is_not_loaded_or_new_scope_restriction(self):
        invitation = self.invitations["generado"]
        for preconsulta_id in (self.preconsulta.pk, 2**62):
            InvitacionRegistroPaciente.objects.filter(pk=invitation.pk).update(preconsulta_id=preconsulta_id)
            with CaptureQueriesContext(connection) as queries:
                response = self.response()
            self.assertEqual(response.context["conteos"]["total"], 8)
            self.no_private_queries(queries)
            self.assertNotContains(response, "NEVER-PRECONSULTA-29")
            InvitacionRegistroPaciente.objects.filter(pk=invitation.pk).update(preconsulta=None)

    def test_safe_projection_has_no_model_objects_or_secret_fields_in_page_context(self):
        with CaptureQueriesContext(connection) as queries:
            response = self.response()
        self.assertEqual(list(response.context["pagina"].object_list), self.rows(response))
        for row in response.context["pagina"].paginator.object_list:
            invitation = row["invitacion"]
            self.assertIsInstance(invitation, SimpleNamespace)
            self.assertNotIsInstance(invitation, InvitacionRegistroPaciente)
            for private in ("token_hash", "token_preview", "preconsulta", "preconsulta_id", "ip_completada", "empresa", "empresa_id"):
                self.assertFalse(hasattr(invitation, private), private)
            patient = invitation.paciente
            if patient is not None:
                self.assertNotIsInstance(patient, Paciente)
            self.assertNotIsInstance(invitation.creada_por, Usuario)
        for query in queries:
            sql = query["sql"]
            if sql.lstrip().upper().startswith("SELECT") and InvitacionRegistroPaciente._meta.db_table in sql:
                selected = sql.split(" FROM ", 1)[0]
                for private in ("token_hash", "ip_completada", "preconsulta_id", "correo", "telefono"):
                    self.assertNotIn(private, selected)

    def test_code_is_always_masked_after_first_eight_characters_without_private_suffix(self):
        response = self.response()
        for row in self.rows(response):
            code = row["invitacion"].code_masked
            self.assertLessEqual(len(code), 11)
            self.assertNotIn("TAILDATA", code)
        self.assertNotContains(response, "TAILDATA")
        for state, invitation in self.invitations.items():
            self.assertNotContains(response, invitation.token_hash)
            self.assertNotContains(response, self.raw_tokens[state])

    def test_html_and_scripts_do_not_expose_contacts_ip_preconsulta_or_raw_tokens(self):
        response = self.response()
        for secret in ("192.0.2.29", "NEVER-PATIENT-CONTACT-29", self.pacientes[self.empresa.slug].correo,
                       "PRIVATE-PRECONSULTA-DATA-29", "NEVER-PRECONSULTA-29"):
            self.assertNotContains(response, secret)
        markup = _RegistrationMarkup(response.content.decode())
        self.assertFalse(any(item.get("type") == "application/json" for item in markup.scripts))

    def test_only_readonly_get_filters_and_safe_mobile_navigation_are_present(self):
        response = self.response()
        markup = _RegistrationMarkup(response.content.decode())
        self.assertTrue(markup.forms)
        for form in markup.forms:
            self.assertEqual(form["attrs"].get("method", "get").lower(), "get")
            self.assertFalse(set(form["names"])-{"app", "q", "estado", "desde", "hasta"})
            self.assertEqual(form["names"].count("app"), 1)
            self.assertEqual(len(form["names"]), len(set(form["names"])))
        self.assertNotContains(response, "<table")
        self.assertNotContains(response, 'rel="manifest"')
        for href in markup.links:
            path = urlsplit(href).path
            self.assertNotIn("/preconsulta/", path)
            self.assertNotIn("/nuevo/", path)
            self.assertNotIn("/generar/", path)
            self.assertNotIn("/pacientes/"+str(self.pacientes[self.empresa.slug].pk)+"/", path)

    def test_projection_names_with_html_are_autoescaped_not_sanitized_or_written(self):
        name = "<img src=x onerror='bad()'> & \"persona\""
        Paciente.objects.filter(pk=self.pacientes[self.empresa.slug].pk).update(nombre=name)
        Usuario.objects.filter(pk=self.owner.pk).update(first_name="<script>bad()</script>", last_name="Autor sintético")
        before = self.snapshot()
        response = self.response()
        self.assertContains(response, "&lt;img")
        self.assertNotContains(response, "<img src=x")
        self.assertNotContains(response, "<script>bad()</script>")
        self.assertEqual(self.snapshot(), before)

    def test_original_web_template_filters_and_legacy_post_loop_are_unchanged(self):
        before = self.snapshot()
        web = self.client.get(self.url(mobile=False, estado="validacion"))
        self.assertEqual(web.status_code, 200)
        self.assertTemplateUsed(web, "clinica/control_enlaces_registro.html")
        self.assertTemplateNotUsed(web, self.TEMPLATE)
        self.assertEqual(web.context["conteos"], self.response(estado="validacion").context["conteos"])
        self.assertIsInstance(web.context["pagina"].object_list[0]["invitacion"], InvitacionRegistroPaciente)
        response = self.client.post(self.url(mobile=False), {})
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "clinica/control_enlaces_registro.html")
        self.assertEqual(self.snapshot(), before)

    def test_pure_capability_does_not_query_invitations_patients_or_grant_other_access(self):
        from .mobile_registration_control import control_enlaces_disponible_app

        request = RequestFactory().get(self.url())
        before = self.snapshot()
        for actor, expected in ((self.owner, True), (self.super_owner, True), (self.supervisor, False), (self.admin, False)):
            request.user = actor
            with CaptureQueriesContext(connection) as queries:
                self.assertEqual(control_enlaces_disponible_app(request, self.empresa), expected)
            self.no_writes(queries)
            self.no_private_queries(queries)
            self.assertFalse(any(InvitacionRegistroPaciente._meta.db_table in row["sql"] or Paciente._meta.db_table in row["sql"] for row in queries))
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.owner.tiene_permiso_erp("puede_ver_calendario", self.empresa))
        self.assertFalse(self.owner.tiene_permiso_erp("puede_ver_facturas", self.empresa))

    def test_configuration_card_requires_real_private_capability_and_correct_mobile_url(self):
        self.role(puede_crear_pacientes=True, puede_configuracion_clinica=True)
        hub = self.client.get(reverse("clinica_configuracion", args=[self.empresa.slug])+"?app=1")
        self.assertEqual(hub.status_code, 200)
        item = next(item for item in hub.context["mobile_configuration_items"] if item["codigo"] == "enlaces_registro")
        self.assertEqual((item["estado"], item["url"]), ("disponible", self.url()))
        self.role(puede_configuracion_clinica=True)
        hub = self.client.get(reverse("clinica_configuracion", args=[self.empresa.slug])+"?app=1")
        self.assertEqual(hub.status_code, 200)
        self.assertFalse(any(item["codigo"] == "enlaces_registro" for item in hub.context["mobile_configuration_items"]))

    def test_post_with_csrf_token_still_cannot_write_or_activate_readonly_control(self):
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.owner)
        strict.cookies["csrftoken"] = "a"*32
        before = self.snapshot()
        response = strict.post(self.url(), {"app": "1", "csrfmiddlewaretoken": "a"*32})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.snapshot(), before)
