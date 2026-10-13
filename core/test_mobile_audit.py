"""Bitácora móvil de metadatos sobre datos sintéticos y permisos originales.

Nunca se abre una URL de objeto, formulario público, PDF o servicio externo.
Los snapshots originales permanecen exclusivamente en el servidor.
"""
from datetime import datetime, timedelta, timezone as datetime_timezone
from html.parser import HTMLParser
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

from clinica.models import Paciente, ProfesionalSalud, ServicioClinico, TratamientoPaciente
from contabilidad.models import CuentaContable
from crm.models import CitaCliente
from facturacion.models import InventarioProducto, Producto
from . import views
from .models import Empresa, EmpresaModulo, Modulo, RegistroAuditoria, RolSistema, Usuario, UsuarioEmpresaPermiso


class _AuditMarkup(HTMLParser):
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
class MobileAuditIntegrationTests(TestCase):
    SLUGS = ("hospital_mia", "medical_spa", "luque_aestetic", "serviciosmedicos")
    TEMPLATE = "core/mobile_audit_list.html"
    NOW = timezone.make_aware(datetime(2026, 10, 13, 12)).astimezone(datetime_timezone.utc)
    ACTIONS = ("crear", "modificar", "eliminar", "relacion")
    EXPECTED_MODELS = {
        "core": {"empresa", "usuario", "usuarioempresapermiso", "empresamodulo", "configuracionavanzadaempresa"},
        "clinica": {"configuracionclinica", "profesionalsalud", "paciente", "pacientefotoevolucion", "servicioclinico",
            "citaclinica", "tratamientopaciente", "plantratamientopaciente", "antecedenteadicionalpaciente",
            "antecedenteadicionalhistorial", "clasificacionalopecia", "expedienteevento", "historiaclinicaespecialidad",
            "preconsultaclinica", "invitacionregistropaciente", "medicamentoprescrito", "consentimientoclinico",
            "examenpaciente", "documentoclinicopaciente", "manualreceta", "enviomanualpdf", "recetamedica",
            "recetamedicadetalle", "plantillareceta", "plantillarecetadetalle", "seguimientopostoperatorio"},
        "crm": {"configuracioncrm", "bloqueodisponibilidadmedica", "citacliente", "programacamarahiperbarica",
            "sesioncamarahiperbarica", "programaterapiapostquirurgica", "sesionterapiapostquirurgica",
            "opcionservicioagenda", "citacirugiafoto"},
        "facturacion": {"producto", "inventarioproducto", "bodegainventario", "loteinventario", "existencialotebodega",
            "movimientolotebodega", "movimientoinventario", "entradainventariodocumento", "lineaentradainventario",
            "bitacoraproductoeliminado", "historialcostorealproducto", "perfilfarmaceuticoproducto",
            "categoriaproductofarmaceutico"},
    }
    PRIVATE = ("PRIVATE-SNAPSHOT-30", "PRIVATE-OBJECT-REP-30", "PRIVATE-REASON-30",
               "PRIVATE-ROUTE-30", "PRIVATE-AGENT-30", "192.0.2.30",
               "38c32126-7d0d-4044-a78a-a5ea11112222", "PRIVATE-CONTACT-30")

    @classmethod
    def setUpTestData(cls):
        cls.empresas = {slug: Empresa.objects.create(nombre=f"Bitácora clínica QA {slug}", slug=slug,
            rtn=f"AUDIT-MOBILE-{index}", tipo_solucion="clinica", estado_licencia="activa")
            for index, slug in enumerate(cls.SLUGS, 1)}
        cls.empresa = cls.empresas["hospital_mia"]
        cls.external = Empresa.objects.create(nombre="Bitácora externa QA", slug="audit-mobile-external",
            rtn="AUDIT-MOBILE-EXT", tipo_solucion="clinica", estado_licencia="activa")
        cls.roles = {name: RolSistema.objects.create(nombre=f"Bitácora {name} QA", codigo=f"audit-mobile-{name}",
            es_rol_clinico=True, usa_permisos_clinicos_granulares=name == "granular") for name in ("legacy", "granular")}
        cls.admins = {name: Usuario.objects.create_user(username=f"audit-mobile-admin-{name}", password="test-only",
            empresa=cls.empresa, rol_sistema=role, es_administrador_empresa=True,
            first_name=f"Administrador {name}", email=f"PRIVATE-AUTHOR-{name}@example.invalid")
            for name, role in cls.roles.items()}
        for actor in cls.admins.values():
            actor.empresas_acceso.add(*cls.empresas.values(), cls.external)
        cls.actor = cls.admins["granular"]
        cls.supervisor = Usuario.objects.create_superuser(username="audit-mobile-super", password="test-only",
            email="super@example.invalid", empresa=cls.external)
        cls.user_admin = Usuario.objects.create_user(username="audit-mobile-users-only", password="test-only",
            empresa=cls.empresa, puede_administrar_usuarios_clinicos=True)
        cls.doctor = Usuario.objects.create_user(username="audit-mobile-doctor", password="test-only",
            empresa=cls.empresa, rol_sistema=cls.roles["granular"])
        cls.foreign_author = Usuario.objects.create_user(username="audit-mobile-foreign-author", password="test-only",
            empresa=cls.external, first_name="Autor histórico", last_name="Multiempresa", email="PRIVATE-AUTHOR@example.invalid")
        cls.pacientes, cls.products, cls.citas = {}, {}, {}
        for index, empresa in enumerate(cls.empresas.values(), 1):
            cls.pacientes[empresa.slug] = Paciente.objects.create(empresa=empresa, nombre=f"Paciente intacto audit {index}",
                identidad=f"0801203000{index:03d}", expediente_codigo=f"AUDM-{index}", telefono="PRIVATE-CONTACT-30")
            profesional = ProfesionalSalud.objects.create(empresa=empresa, nombre=f"Profesional intacto audit {index}")
            servicio = ServicioClinico.objects.create(empresa=empresa, nombre=f"Servicio intacto audit {index}")
            cls.citas[empresa.slug] = CitaCliente.objects.create(empresa=empresa, paciente=cls.pacientes[empresa.slug],
                profesional_salud=profesional, servicio_clinico=servicio, titulo=f"Cita intacta audit {index}",
                fecha_hora=cls.NOW+timedelta(days=2))
            TratamientoPaciente.objects.create(empresa=empresa, paciente=cls.pacientes[empresa.slug],
                nombre=f"Tratamiento intacto audit {index}", fecha_inicio="2026-10-13")
            cls.products[empresa.slug] = Producto.objects.create(empresa=empresa, nombre=f"Producto intacto audit {index}",
                codigo=f"AUDM-P-{index}", precio="15.00")
            InventarioProducto.objects.create(empresa=empresa, producto=cls.products[empresa.slug], existencias="21.00")
            CuentaContable.objects.create(empresa=empresa, codigo=f"AUDM-A-{index}", nombre=f"Cuenta intacta audit {index}", tipo="activo")
        # Preparation only: remove automatically generated synthetic setup
        # events so the fixtures below have exact, independent counters.
        RegistroAuditoria.objects.filter(empresa__in=cls.empresas.values()).delete()
        cls.entries = {}
        pairs = (("clinica", "paciente", "Clinica"), ("core", "empresa", "Configuracion"),
                 ("crm", "citacliente", "CRM y Agenda"), ("facturacion", "producto", "Facturacion"))
        for slug, empresa in cls.empresas.items():
            cls.entries[slug] = []
            for index, (app, model, module) in enumerate(pairs):
                for offset, action in enumerate(cls.ACTIONS):
                    entry = cls.make_entry(empresa, app_label=app, modelo=model, modulo=module, accion=action,
                        objeto_id=f"SAFE-{index}-{offset}", usuario=cls.actor)
                    RegistroAuditoria.objects.filter(pk=entry.pk).update(fecha=cls.NOW-timedelta(days=index, minutes=offset))
                    entry.refresh_from_db()
                    cls.entries[slug].append(entry)
        cls.entry = cls.entries[cls.empresa.slug][0]
        excluded = (("facturacion", "factura"), ("facturacion", "pagofactura"), ("facturacion", "recibopago"),
                    ("clinica", "gastoadicional"), ("clinica", "lineagastoadicional"), ("core", "rolsistema"),
                    ("crm", "campaniamarketing"), ("crm", "enviocampania"), ("contabilidad", "cuentacontable"),
                    ("rrhh", "empleado"), ("clinica", "unknownfuturemodel"))
        cls.excluded = [cls.make_entry(cls.empresa, app_label=app, modelo=model, objeto_id=f"EXCLUDED-{index}")
                        for index, (app, model) in enumerate(excluded)]
        cls.foreign_entry = cls.make_entry(cls.external, objeto_id="OTHER-COMPANY-AUDIT-ID")
        cls.global_entry = cls.make_entry(None, objeto_id="NULL-COMPANY-AUDIT-ID")
        cls.account_ids = [actor.pk for actor in (*cls.admins.values(), cls.supervisor, cls.user_admin, cls.doctor, cls.foreign_author)]
        # Frozen login timestamps keep auth preparation from adding a new
        # Usuario event to this read-only report's deterministic fixtures.
        Usuario.objects.filter(pk__in=cls.account_ids).update(last_login=cls.NOW)

    @classmethod
    def make_entry(cls, empresa, **values):
        data = dict(empresa=empresa, usuario=None, accion="crear", modulo="Clinica", app_label="clinica", modelo="paciente",
            objeto_id="SAFE-SYNTHETIC-ID", objeto_representacion="PRIVATE-OBJECT-REP-30", motivo="PRIVATE-REASON-30",
            cambios={"texto": {"anterior": "PRIVATE-SNAPSHOT-30", "nuevo": "<script>PRIVATE-CONTACT-30</script>"}},
            ruta="/PRIVATE-ROUTE-30/publico/?token=PRIVATE-SNAPSHOT-30", metodo_http="POST", direccion_ip="192.0.2.30",
            agente_usuario="PRIVATE-AGENT-30", identificador_solicitud="38c32126-7d0d-4044-a78a-a5ea11112222")
        data.update(values)
        return RegistroAuditoria.objects.create(**data)

    def setUp(self):
        now = patch("django.utils.timezone.now", return_value=self.NOW)
        now.start()
        self.addCleanup(now.stop)
        self.login(self.actor)
        # Resolve URL metadata before mocks; this does not invoke any view.
        self.url()
        for target in ("core.views.enviar_correo_acceso", "core.views.emitir_token_acceso", "crm.views.urlopen",
                       "crm.views.CitaClienteForm", "crm.views._contexto_calendario", "clinica.views._configuracion_clinica",
                       "clinica.views.registro_paciente_publico", "clinica.views.enviar_documento_whatsapp", "django.core.mail.EmailMessage.send"):
            blocker = patch(target, side_effect=AssertionError("No inicializadores, objetos públicos ni transportes"))
            mocked = blocker.start()
            self.addCleanup(blocker.stop)
            self.addCleanup(mocked.assert_not_called)

    def login(self, actor):
        self.client.force_login(Usuario.objects.get(pk=actor.pk))

    def url(self, empresa=None, *, mobile=True, **parameters):
        query = {"app": "1"} if mobile else {}
        query.update(parameters)
        path = reverse("auditoria_empresa", args=[(empresa or self.empresa).slug])
        return path+("?"+urlencode(query, doseq=True) if query else "")

    def direct(self, url=None, *, actor=None, method="GET", data=None):
        path = url or self.url()
        factory = RequestFactory()
        request = factory.post(path, data or {}) if method == "POST" else factory.generic(method, path)
        request.user = Usuario.objects.get(pk=(actor or self.actor).pk)
        request.session = {}
        request._messages = FallbackStorage(request)
        request._dont_enforce_csrf_checks = True
        match = resolve(urlsplit(path).path)
        request.resolver_match = match
        return match.func(request, **match.kwargs)

    def snapshot(self):
        return tuple((model._meta.label_lower, list(model.objects.order_by("pk").values()))
                     for model in apps.get_models(include_auto_created=True))

    def no_writes(self, queries):
        self.assertFalse(any(row["sql"].lstrip().upper().startswith(("INSERT ", "UPDATE ", "DELETE ", "REPLACE ")) for row in queries))

    def deny(self, url=None, *, status=403, before_audit=False, **kwargs):
        before = self.snapshot()
        with CaptureQueriesContext(connection) as queries:
            try:
                response = self.direct(url, **kwargs)
            except PermissionDenied:
                self.assertEqual(status, 403)
            except Http404:
                self.assertEqual(status, 404)
            else:
                self.assertEqual(response.status_code, status)
        self.assertEqual(self.snapshot(), before)
        self.no_writes(queries)
        if before_audit:
            self.assertFalse(any(RegistroAuditoria._meta.db_table in row["sql"] for row in queries))

    def response(self, **parameters):
        response = self.client.get(self.url(**parameters))
        self.assertEqual(response.status_code, 200)
        return response

    @staticmethod
    def value(item, name):
        return item[name] if isinstance(item, dict) else getattr(item, name)

    def ids(self, response):
        return [self.value(item, "id") for item in response.context["mobile_audit_items"]]

    def test_four_clinics_and_both_role_types_use_original_company_admin_rights(self):
        for actor in self.admins.values():
            self.login(actor)
            for empresa in self.empresas.values():
                response = self.response(empresa=empresa)
                self.assertTemplateUsed(response, self.TEMPLATE)
                self.assertEqual(response.context["total_registros"], 16)
                self.assertEqual(set(self.ids(response)), {entry.pk for entry in self.entries[empresa.slug]})

    def test_superuser_can_query_without_membership_or_other_operational_permissions(self):
        self.login(self.supervisor)
        response = self.response()
        self.assertEqual(response.context["total_registros"], 16)

    def test_clinical_config_or_user_administration_flags_do_not_authorize_audit(self):
        role = RolSistema.objects.create(nombre="Audit config only", codigo="audit-mobile-config-only", es_rol_clinico=True,
            usa_permisos_clinicos_granulares=True, puede_configuracion_clinica=True, puede_ver_historia_clinica=True)
        Usuario.objects.filter(pk=self.doctor.pk).update(rol_sistema=role)
        for actor in (self.doctor, self.user_admin):
            self.deny(actor=actor, before_audit=True)

    def test_company_admin_membership_is_required_even_with_global_admin_flag(self):
        Usuario.objects.filter(pk=self.actor.pk).update(empresa=self.external)
        self.actor.empresas_acceso.remove(self.empresa)
        self.deny(before_audit=True)

    def test_active_local_role_none_or_inactive_does_not_remove_original_account_admin_right(self):
        UsuarioEmpresaPermiso.objects.create(usuario=self.actor, empresa=self.empresa, rol_sistema=None)
        self.assertEqual(self.direct().status_code, 200)
        UsuarioEmpresaPermiso.objects.filter(usuario=self.actor, empresa=self.empresa).update(rol_sistema=self.roles["granular"])
        RolSistema.objects.filter(pk=self.roles["granular"].pk).update(activo=False)
        self.assertEqual(self.direct().status_code, 200)

    def test_expired_or_suspended_license_does_not_add_an_operational_requirement(self):
        for state in ("vencida", "suspendida"):
            Empresa.objects.filter(pk=self.empresa.pk).update(estado_licencia=state)
            self.assertEqual(self.direct().status_code, 200)
            self.assertEqual(self.direct(actor=self.supervisor).status_code, 200)

    def test_no_modules_or_configuration_are_required_or_initialized(self):
        self.assertFalse(EmpresaModulo.objects.filter(empresa__in=self.empresas.values()).exists())
        before = self.snapshot()
        with CaptureQueriesContext(connection) as queries:
            response = self.response()
        self.assertEqual(response.context["total_registros"], 16)
        self.no_writes(queries)
        self.assertEqual(self.snapshot(), before)

    def test_inactive_company_is_original_not_found_even_for_superuser(self):
        Empresa.objects.filter(pk=self.empresa.pk).update(activa=False)
        self.deny(actor=self.supervisor, status=404, before_audit=True)

    def test_nonclinical_profile_and_other_slug_do_not_fall_through_to_desktop(self):
        self.deny(self.url(self.external), actor=self.supervisor, before_audit=True)
        Empresa.objects.filter(pk=self.empresa.pk).update(tipo_solucion="erp")
        self.deny(actor=self.supervisor, before_audit=True)

    def test_logged_out_keeps_original_login_redirect_without_writes(self):
        self.client.logout()
        before = self.snapshot()
        for method in ("get", "head"):
            response = getattr(self.client, method)(self.url())
            self.assertEqual(response.status_code, 302)
            self.assertIn("next=", response.url)
        self.assertEqual(self.snapshot(), before)

    def test_get_head_all_models_auto_through_sql_and_no_store_are_unchanged(self):
        before = self.snapshot()
        for method in ("get", "head"):
            with CaptureQueriesContext(connection) as queries:
                response = getattr(self.client, method)(self.url())
            self.assertEqual(response.status_code, 200)
            self.assertIn("no-store", response["Cache-Control"])
            if method == "head":
                self.assertEqual(response.content, b"")
            self.no_writes(queries)
        self.assertEqual(self.snapshot(), before)

    def test_all_writing_methods_are_denied_before_audit_queries(self):
        for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
            self.deny(method=method, data={"app": "1", "accion": "eliminar"}, before_audit=True)

    def test_body_opt_in_alone_cannot_enable_original_desktop_post_loop(self):
        self.deny(self.url(mobile=False), method="POST", data={"app": "1"}, before_audit=True)
        self.deny(self.url(mobile=False), method="POST", data={"app": ["0", "1"]}, before_audit=True)

    def test_wrong_or_duplicate_explicit_app_keys_never_render_desktop(self):
        for value in ("", "0", "01", "agenda", ["1", "1"], ["0", "1"]):
            self.deny(self.url(app=value), before_audit=True)

    def test_duplicate_filter_scalars_are_rejected_before_audit_queries(self):
        for key in ("q", "accion", "modulo", "usuario", "desde", "hasta", "page"):
            self.deny(self.url(**{key: ["1", "1"]}), before_audit=True)

    def test_unknown_object_secret_and_global_parameters_are_closed(self):
        for key in ("empresa", "objeto_id", "modelo", "token", "hash", "snapshot", "ruta", "usuario_id", "_popup", "next"):
            self.deny(self.url()+"&"+urlencode({key: "1"}), before_audit=True)

    def test_get_head_json_and_form_bodies_are_denied_before_private_queries(self):
        for method in ("GET", "HEAD"):
            for body, content_type in ((b'{"app":"1"}', "application/json"), (b"app=1", "application/x-www-form-urlencoded")):
                request = RequestFactory().generic(method, self.url(), body, content_type=content_type)
                request.user = self.actor
                request.session = {}
                request._messages = FallbackStorage(request)
                before = self.snapshot()
                with CaptureQueriesContext(connection) as queries:
                    with self.assertRaises(PermissionDenied):
                        views.auditoria_empresa(request, slug=self.empresa.slug)
                self.assertFalse(any(RegistroAuditoria._meta.db_table in row["sql"] for row in queries))
                self.no_writes(queries)
                response = self.client.generic(method, self.url(), body, content_type=content_type)
                self.assertEqual(response.status_code, 403)
                if method == "HEAD":
                    self.assertEqual(response.content, b"")
                self.assertEqual(self.snapshot(), before)

    def test_files_cannot_be_smuggled_into_a_readonly_request(self):
        request = RequestFactory().post(self.url(), {"app": "1", "logo": SimpleUploadedFile("qa.txt", b"synthetic")})
        request.user = self.actor
        request.session = {}
        request._messages = FallbackStorage(request)
        self.assertTrue(request.FILES)
        request.method = "GET"
        before = self.snapshot()
        with self.assertRaises(PermissionDenied):
            views.auditoria_empresa(request, slug=self.empresa.slug)
        self.assertEqual(self.snapshot(), before)

    def test_page_ascii_canonical_positive_int64_and_original_clamping(self):
        for value in ("", "0", "01", "-1", "1.5", "1e2", "１", " 1 ", "abc", str(2**63)):
            self.deny(self.url(page=value), before_audit=True)
        response = self.response(page=str(2**63-1))
        self.assertEqual(response.context["registros"].number, 1)

    def test_user_filter_is_blank_or_canonical_positive_int64_not_a_new_membership_rule(self):
        for value in ("0", "01", "-1", "1e2", "１", " 1 ", "abc", str(2**63)):
            self.deny(self.url(usuario=value), before_audit=True)
        self.assertEqual(self.response(usuario="").context["total_registros"], 16)
        self.assertEqual(self.response(usuario=str(2**63-1)).context["total_registros"], 0)

    def test_action_filters_are_original_four_values_and_whitespace_is_originally_trimmed(self):
        for action in self.ACTIONS:
            response = self.response(accion=action)
            self.assertEqual(response.context["total_registros"], 4)
            self.assertTrue(all(self.value(item, "accion") == action for item in response.context["mobile_audit_items"]))
        self.assertEqual(self.response(accion=" crear ").context["total_registros"], 4)
        for action in ("delete", "todos", "invalid"):
            self.deny(self.url(accion=action), before_audit=True)

    def test_module_filter_reuses_original_labels_without_expanding_model_scope(self):
        for module in ("Clinica", "Configuracion", "CRM y Agenda", "Facturacion"):
            response = self.response(modulo=module)
            self.assertEqual(response.context["total_registros"], 4)
        self.assertEqual(self.response(modulo="Contabilidad").context["total_registros"], 0)

    def test_original_q_searches_id_model_username_and_private_fields_only_in_where(self):
        for query, count in (("SAFE-0-0", 1), ("paciente", 4), (self.actor.username, 16),
                             (self.actor.email, 16), ("PRIVATE-REASON-30", 16), ("PRIVATE-OBJECT-REP-30", 16)):
            response = self.response(q=query)
            self.assertEqual(response.context["total_registros"], count)
        self.assertEqual(self.response(q="PRIVATE-SNAPSHOT-30").context["total_registros"], 0)

    def test_search_trim_and_html_echo_are_escaped_and_do_not_write(self):
        before = self.snapshot()
        response = self.response(q="  <script>bad()</script>  ")
        self.assertEqual(response.context["filtros"]["q"], "<script>bad()</script>")
        self.assertContains(response, "&lt;script&gt;bad()&lt;/script&gt;")
        self.assertNotContains(response, "<script>bad()</script>")
        self.assertEqual(self.snapshot(), before)

    def test_real_iso_dates_filter_original_inclusive_local_day(self):
        response = self.response(desde="2026-10-13", hasta="2026-10-13")
        self.assertEqual(response.context["total_registros"], 4)
        self.assertEqual({self.value(item, "modelo") for item in response.context["mobile_audit_items"]}, {"paciente"})

    def test_bad_date_format_preserves_original_none_filter_and_clears_native_display(self):
        for value in ("not-a-date", "2026-10-13T12:00:00", "13/10/2026"):
            for key in ("desde", "hasta"):
                response = self.response(**{key: value})
                self.assertEqual(response.context["total_registros"], 16)
                self.assertEqual(response.context["filtros"][key], "")

    def test_impossible_dates_raise_denial_before_audit_sql_not_server_error(self):
        for value in ("2026-02-30", "2026-13-01"):
            for key in ("desde", "hasta"):
                self.deny(self.url(**{key: value}), before_audit=True)

    def test_nonpadded_and_trimmed_dates_use_original_parser_with_iso_display_only(self):
        response = self.response(desde=" 2026-10-10 ", hasta="2026-10-10")
        self.assertEqual(response.context["total_registros"], 4)
        self.assertEqual(response.context["filtros"]["desde"], "2026-10-10")
        response = self.response(desde="2026-10-9")
        self.assertEqual(response.context["total_registros"], 16)
        self.assertEqual(response.context["filtros"]["desde"], "2026-10-09")

    def test_reversed_dates_remain_empty_without_swap_or_financial_rule(self):
        response = self.response(desde="2026-10-13", hasta="2026-10-10")
        self.assertEqual(response.context["total_registros"], 0)
        self.assertFalse(response.context["mobile_audit_items"])

    def test_pagination_fifty_is_stable_and_count_is_all_filtered_events(self):
        for index in range(45):
            self.make_entry(self.empresa, objeto_id=f"PAGE-AUDIT-{index:02d}")
        response, second = self.response(), self.response(page="2")
        self.assertEqual(response.context["registros"].paginator.per_page, 50)
        self.assertEqual(response.context["total_registros"], 61)
        self.assertEqual(second.context["total_registros"], 61)
        self.assertEqual((len(self.ids(response)), len(self.ids(second))), (50, 11))
        expected = [row.pk for row in RegistroAuditoria.objects.filter(empresa=self.empresa).exclude(pk__in=[row.pk for row in self.excluded]).order_by("-fecha", "-id")]
        self.assertEqual(self.ids(response)+self.ids(second), expected)
        self.assertEqual(len(set(self.ids(response)+self.ids(second))), 61)

    def test_search_is_server_side_including_matching_row_outside_first_page(self):
        for index in range(55):
            self.make_entry(self.empresa, objeto_id=f"FILL-AUDIT-{index:02d}")
        response = self.response(q=self.entry.objeto_id)
        self.assertEqual(self.ids(response), [self.entry.pk])
        self.assertEqual(response.context["total_registros"], 1)

    def test_pager_links_preserve_closed_original_filters_and_optin(self):
        for index in range(51):
            self.make_entry(self.empresa, objeto_id=f"PAGER-AUDIT-{index:02d}")
        response = self.response(q="PAGER-AUDIT", accion="crear", modulo="Clinica", usuario="", desde="2026-10-13", hasta="2026-10-13")
        url = response.context["mobile_audit_next_url"]
        self.assertEqual(urlsplit(url).path, urlsplit(self.url()).path)
        self.assertEqual(parse_qs(urlsplit(url).query)["page"], ["2"])
        self.assertEqual(parse_qs(urlsplit(url).query)["app"], ["1"])
        second = self.client.get(url)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.context["total_registros"], 51)

    def test_financial_campaign_global_role_and_unknown_logs_are_not_part_of_totals_or_options(self):
        RegistroAuditoria.objects.filter(pk=self.excluded[0].pk).update(usuario=self.foreign_author)
        response = self.response()
        self.assertEqual(response.context["total_registros"], 16)
        self.assertTrue(set(self.ids(response)).isdisjoint({row.pk for row in self.excluded}))
        for row in self.excluded:
            self.assertNotContains(response, row.objeto_id)
        self.assertEqual({self.value(item, "id") for item in response.context["usuarios_auditoria"]}, {self.actor.pk})
        self.assertEqual(self.response(q="EXCLUDED").context["total_registros"], 0)

    def test_foreign_and_null_company_events_are_never_in_current_scope(self):
        response = self.response()
        self.assertNotContains(response, self.foreign_entry.objeto_id)
        self.assertNotContains(response, self.global_entry.objeto_id)
        self.assertEqual(self.response(q="OTHER-COMPANY").context["total_registros"], 0)

    def test_orphan_visible_author_fails_before_search_date_module_action_or_page(self):
        RegistroAuditoria.objects.filter(pk=self.entry.pk).update(usuario_id=2**62)
        try:
            for query in ({"q": "no-match"}, {"desde": "2099-01-01"}, {"accion": "eliminar"}, {"modulo": "Configuracion"}, {"page": "99"}):
                self.deny(self.url(**query))
        finally:
            RegistroAuditoria.objects.filter(pk=self.entry.pk).update(usuario=self.actor)

    def test_orphan_excluded_or_other_company_author_does_not_poison_visible_scope(self):
        for row in (self.excluded[0], self.foreign_entry, self.global_entry):
            original = row.usuario_id
            RegistroAuditoria.objects.filter(pk=row.pk).update(usuario_id=2**62)
            try:
                self.assertEqual(self.response().context["total_registros"], 16)
            finally:
                RegistroAuditoria.objects.filter(pk=row.pk).update(usuario_id=original)

    def test_null_author_is_system_and_existing_inactive_multicompany_author_is_valid(self):
        RegistroAuditoria.objects.filter(pk=self.entry.pk).update(usuario=None)
        response = self.response(q=self.entry.objeto_id)
        self.assertEqual(self.ids(response), [self.entry.pk])
        self.assertTrue(self.value(response.context["mobile_audit_items"][0], "usuario_nombre"))
        Usuario.objects.filter(pk=self.foreign_author.pk).update(is_active=False)
        RegistroAuditoria.objects.filter(pk=self.entry.pk).update(usuario=self.foreign_author)
        response = self.response(usuario=str(self.foreign_author.pk))
        self.assertEqual(self.ids(response), [self.entry.pk])
        self.assertNotContains(response, self.foreign_author.email)

    def test_values_queries_do_not_select_snapshots_free_text_routes_ip_uuid_or_email(self):
        with CaptureQueriesContext(connection) as queries:
            response = self.response()
        self.assertEqual(response.context["total_registros"], 16)
        for query in queries:
            sql = query["sql"]
            if sql.lstrip().upper().startswith("SELECT") and RegistroAuditoria._meta.db_table in sql:
                selected = sql.split(" FROM ", 1)[0]
                for private in ("cambios", "objeto_representacion", "motivo", "ruta", "metodo_http", "direccion_ip", "agente_usuario", "identificador_solicitud", "email", "password"):
                    self.assertNotIn(f'"{private}"', selected)

    def test_page_and_paginator_projection_have_no_models_or_private_payload(self):
        response = self.response()
        safe = {"id", "empresa_id", "usuario_id", "fecha", "accion", "accion_label", "modelo_label", "objeto_id", "usuario_nombre", "modulo", "app_label", "modelo"}
        for collection in (response.context["mobile_audit_items"], response.context["registros"].object_list):
            for item in collection:
                self.assertNotIsInstance(item, RegistroAuditoria)
                data = item if isinstance(item, dict) else vars(item)
                self.assertFalse(set(data)-safe, set(data)-safe)
                self.assertFalse(any(isinstance(value, (RegistroAuditoria, Usuario)) for value in data.values()))
        paginator_fields = {"id", "empresa_id", "usuario_id", "accion", "modulo", "app_label", "modelo", "objeto_id", "fecha",
                            "usuario__id", "usuario__username", "usuario__first_name", "usuario__last_name"}
        for item in response.context["registros"].paginator.object_list:
            self.assertIsInstance(item, dict)
            self.assertEqual(set(item), paginator_fields)
            self.assertFalse(any(isinstance(value, (RegistroAuditoria, Usuario)) for value in item.values()))
        for actor in response.context["usuarios_auditoria"]:
            self.assertNotIsInstance(actor, Usuario)
            data = actor if isinstance(actor, dict) else vars(actor)
            self.assertFalse(set(data)-{"id", "username"})

    def test_html_has_no_private_snapshot_object_representation_contact_or_transport_payload(self):
        response = self.response()
        for value in (*self.PRIVATE, self.actor.email, self.foreign_author.email):
            self.assertNotContains(response, value)
        self.assertFalse(any(item.get("type") == "application/json" for item in _AuditMarkup(response.content.decode()).scripts))

    def test_unsafe_and_long_identifiers_and_author_names_are_literal_escaped_metadata(self):
        value = '<img src=x onerror="bad()"> & '+"Z"*55
        RegistroAuditoria.objects.filter(pk=self.entry.pk).update(objeto_id=value)
        Usuario.objects.filter(pk=self.actor.pk).update(first_name="<script>bad()</script>")
        before = self.snapshot()
        response = self.response()
        self.assertContains(response, "&lt;img")
        self.assertNotContains(response, '<img src=x onerror="bad()">')
        self.assertNotContains(response, "<script>bad()</script>")
        self.assertEqual(self.snapshot(), before)

    def test_mobile_ui_only_has_get_filters_and_no_object_public_or_financial_links(self):
        response = self.response()
        markup = _AuditMarkup(response.content.decode())
        self.assertTrue(markup.forms)
        for form in markup.forms:
            self.assertEqual(form["attrs"].get("method", "get").lower(), "get")
            self.assertEqual(set(form["names"]), {"app", "q", "accion", "modulo", "usuario", "desde", "hasta"})
            self.assertEqual(len(form["names"]), len(set(form["names"])))
        self.assertNotContains(response, "<table")
        self.assertNotContains(response, 'rel="manifest"')
        for href in markup.links:
            path = urlsplit(href).path
            for forbidden in ("/auditoria/clinica/", "/nuevo/", "/pdf/", "/facturacion/", "/pacientes/", "/control/"):
                self.assertNotIn(forbidden, path)

    def test_original_web_template_scope_filters_and_post_read_loop_are_unchanged(self):
        before = self.snapshot()
        response = self.client.get(self.url(mobile=False))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "core/auditoria_empresa.html")
        self.assertTemplateNotUsed(response, self.TEMPLATE)
        self.assertEqual(response.context["total_registros"], 16+len(self.excluded))
        self.assertIsInstance(response.context["registros"].object_list[0], RegistroAuditoria)
        response = self.client.post(self.url(mobile=False), {})
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "core/auditoria_empresa.html")
        self.assertEqual(self.snapshot(), before)

    def test_original_web_other_company_remains_available_to_original_superuser(self):
        self.login(self.supervisor)
        response = self.client.get(self.url(self.external, mobile=False))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "core/auditoria_empresa.html")
        self.assertContains(response, self.foreign_entry.objeto_id)

    def test_csrf_valid_post_still_cannot_turn_readonly_audit_into_an_action(self):
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(Usuario.objects.get(pk=self.actor.pk))
        strict.cookies["csrftoken"] = "a"*32
        before = self.snapshot()
        response = strict.post(self.url(), {"app": "1", "csrfmiddlewaretoken": "a"*32, "accion": "eliminar"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.snapshot(), before)

    def test_pure_capability_queries_no_audit_or_business_data_and_grants_no_new_permission(self):
        from .mobile_audit import auditoria_disponible_app

        request = RequestFactory().get(self.url())
        before = self.snapshot()
        for actor, expected in ((self.actor, True), (self.supervisor, True), (self.doctor, False), (self.user_admin, False)):
            request.user = actor
            with CaptureQueriesContext(connection) as queries:
                self.assertEqual(auditoria_disponible_app(request, self.empresa), expected)
            self.no_writes(queries)
            for model in (RegistroAuditoria, Paciente, CitaCliente, InventarioProducto, CuentaContable):
                self.assertFalse(any(model._meta.db_table in row["sql"] for row in queries))
        self.assertEqual(self.snapshot(), before)

    def test_exact_positive_model_pairs_admit_current_metadata_not_future_models_or_cross_app_names(self):
        from .mobile_audit import MODELOS_AUDITORIA_APP

        self.assertEqual({app: set(models) for app, models in MODELOS_AUDITORIA_APP.items()}, self.EXPECTED_MODELS)
        self.assertEqual({app: len(models) for app, models in MODELOS_AUDITORIA_APP.items()},
                         {"core": 5, "clinica": 26, "crm": 9, "facturacion": 13})
        expected_ids = set()
        for app, models in self.EXPECTED_MODELS.items():
            for model in models:
                entry = self.make_entry(self.empresa, app_label=app, modelo=model, objeto_id=f"ALLOW-{app}-{model}")
                expected_ids.add(entry.pk)
        mismatches = [self.make_entry(self.empresa, app_label=app, modelo=model, objeto_id="ALLOW-NOT-A-PAIR")
                      for app, model in (("core", "paciente"), ("clinica", "empresa"), ("facturacion", "citacliente"), ("crm", "producto"))]
        first, second = self.response(q="ALLOW"), self.response(q="ALLOW", page="2")
        self.assertEqual(first.context["total_registros"], 53)
        self.assertEqual(set(self.ids(first)+self.ids(second)), expected_ids)
        self.assertTrue(expected_ids.isdisjoint({item.pk for item in mismatches}))

    def test_configuration_card_requires_full_original_audit_capability_and_mobile_route(self):
        module, _ = Modulo.objects.get_or_create(codigo="clinica_medica", defaults={"nombre": "Clínica"})
        EmpresaModulo.objects.create(empresa=self.empresa, modulo=module)
        before = self.snapshot()
        response = self.client.get(reverse("clinica_configuracion", args=[self.empresa.slug])+"?app=1")
        self.assertEqual(response.status_code, 200)
        item = next(item for item in response.context["mobile_configuration_items"] if item["codigo"] == "bitacora_clinica")
        self.assertEqual((item["estado"], item["url"]), ("disponible", self.url()))
        self.assertEqual(self.snapshot(), before)
        role = RolSistema.objects.create(nombre="Audit nonadmin configuration", codigo="audit-mobile-nonadmin-config",
            es_rol_clinico=True, usa_permisos_clinicos_granulares=True, puede_configuracion_clinica=True)
        Usuario.objects.filter(pk=self.doctor.pk).update(rol_sistema=role)
        self.login(self.doctor)
        response = self.client.get(reverse("clinica_configuracion", args=[self.empresa.slug])+"?app=1")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(any(item["codigo"] == "bitacora_clinica" for item in response.context["mobile_configuration_items"]))

    def test_expired_admin_only_audit_has_safe_self_return_without_calendar_or_module_grant(self):
        Empresa.objects.filter(pk=self.empresa.pk).update(estado_licencia="suspendida")
        before = self.snapshot()
        response = self.response()
        self.assertFalse(response.context["app_home_disponible"])
        self.assertFalse(response.context["can_view_app_modules"])
        self.assertFalse(response.context["can_view_clinical_configuration"])
        self.assertEqual(response.context["app_audit_back_url"], self.url())
        markup = _AuditMarkup(response.content.decode())
        for href in markup.links:
            self.assertEqual(urlsplit(href).path, urlsplit(self.url()).path)
        self.assertNotContains(response, 'rel="manifest"')
        self.assertEqual(self.snapshot(), before)
