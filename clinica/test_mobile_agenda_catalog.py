"""Catálogo móvil de agenda, separado de TratamientoPaciente.

TestCase independiente, datos sintéticos y transportes bloqueados. Las
operaciones se comprueban contra los parsers y guardados originales, sin
inventar un formulario, una relación con citas ni permisos de administración.
"""
from datetime import datetime
from html.parser import HTMLParser
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlsplit
from unittest.mock import patch

from django import forms
from django.apps import apps
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.http import Http404
from django.template.loader import render_to_string
from django.test import Client, RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import resolve, reverse
from django.utils import timezone

from contabilidad.models import CuentaContable
from core.models import Empresa, EmpresaModulo, Modulo, RegistroAuditoria, RolSistema, Usuario, UsuarioEmpresaPermiso
from crm.models import CitaCliente, OpcionServicioAgenda
from facturacion.models import InventarioProducto, Producto
from . import views
from .forms import TratamientoPacienteForm
from .models import Paciente, ProfesionalSalud, ServicioClinico, TratamientoPaciente


class _CatalogMarkup(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.forms, self.links, self.json_options = [], [], ""
        self.current_form = None
        self.capture_json = False
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.current_form = {"attrs": attrs, "names": []}
            self.forms.append(self.current_form)
        if self.current_form is not None and tag in {"input", "select", "textarea", "button"} and attrs.get("name"):
            self.current_form["names"].append(attrs["name"])
        if tag == "a" and attrs.get("href"):
            self.links.append(attrs["href"])
        if tag == "script" and attrs.get("id") == "agendaTreatmentOptions":
            self.capture_json = True

    def handle_data(self, data):
        if self.capture_json:
            self.json_options += data

    def handle_endtag(self, tag):
        if tag == "form":
            self.current_form = None
        elif tag == "script":
            self.capture_json = False


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class MobileAgendaCatalogTests(TestCase):
    SLUGS = ("hospital_mia", "medical_spa", "luque_aestetic", "serviciosmedicos")
    TEMPLATE = "clinica/mobile_agenda_catalog.html"
    PARTIAL = "crm/_detalles_agenda_hospital_mia.html"

    @classmethod
    def setUpTestData(cls):
        cls.modulo, _ = Modulo.objects.get_or_create(codigo="clinica_medica", defaults={"nombre": "Clínica"})
        cls.empresas = {slug: Empresa.objects.create(nombre=f"Catálogo de agenda QA {slug}", slug=slug,
            rtn=f"AGENDA-CATALOG-{index}", tipo_solucion="clinica", estado_licencia="activa")
            for index, slug in enumerate(cls.SLUGS, 1)}
        cls.empresa = cls.empresas["hospital_mia"]
        cls.external = Empresa.objects.create(nombre="Catálogo externo QA", slug="agenda-catalog-external-qa",
            rtn="AGENDA-CATALOG-EXT", tipo_solucion="clinica", estado_licencia="activa")
        cls.roles = {name: RolSistema.objects.create(nombre=f"Catálogo agenda {name} QA", codigo=f"agenda-catalog-{name}-qa",
            es_rol_clinico=True, usa_permisos_clinicos_granulares=name == "granular",
            **({"puede_ver_planes_tratamiento": True, "puede_editar_planes_tratamiento": True}
               if name == "granular" else {"puede_tratamientos_clinicos": True}))
            for name in ("legacy", "granular")}
        cls.users = {name: Usuario.objects.create_user(username=f"agenda-catalog-{name}-qa", password="test-only",
            empresa=cls.empresa, rol_sistema=role) for name, role in cls.roles.items()}
        for user in cls.users.values():
            user.empresas_acceso.add(*cls.empresas.values())
        cls.admin = Usuario.objects.create_user(username="agenda-catalog-admin-qa", password="test-only",
            empresa=cls.empresa, rol_sistema=cls.roles["granular"], es_administrador_empresa=True)
        cls.admin.empresas_acceso.add(*cls.empresas.values())
        cls.supervisor = Usuario.objects.create_superuser(username="agenda-catalog-super-qa", password="test-only",
            email="super@example.invalid", empresa=cls.external)
        cls.foreign_actor = Usuario.objects.create_user(username="agenda-catalog-foreign-qa", password="test-only",
            empresa=cls.external, rol_sistema=cls.roles["granular"])
        cls.none = Usuario.objects.create_user(username="agenda-catalog-none-qa", password="test-only", empresa=cls.empresa)
        cls.opciones, cls.spa_options, cls.pacientes, cls.profesionales, cls.servicios, cls.tratamientos, cls.citas = {}, {}, {}, {}, {}, {}, {}
        for index, empresa in enumerate((*cls.empresas.values(), cls.external), 1):
            EmpresaModulo.objects.create(empresa=empresa, modulo=cls.modulo)
            slug = empresa.slug
            cls.opciones[(slug, "active")] = OpcionServicioAgenda.objects.create(empresa=empresa, categoria="tratamientos",
                nombre=f"A opción propia {slug}", orden=2, creado_por=cls.users["legacy"])
            cls.opciones[(slug, "inactive")] = OpcionServicioAgenda.objects.create(empresa=empresa, categoria="tratamientos",
                nombre=f"B opción inactiva {slug}", orden=5, activo=False)
            cls.spa_options[slug] = OpcionServicioAgenda.objects.create(empresa=empresa, categoria="spa",
                nombre=f"NO CAT TRATAMIENTOS SPA QA {index}")
            cls.pacientes[slug] = Paciente.objects.create(empresa=empresa, nombre=f"Paciente intacto catálogo {index}",
                expediente_codigo=f"ACAT-{index}", identidad=f"0801202700{index:03d}")
            cls.profesionales[slug] = ProfesionalSalud.objects.create(empresa=empresa, nombre=f"Profesional intacto catálogo {index}")
            cls.servicios[slug] = ServicioClinico.objects.create(empresa=empresa, nombre=f"Servicio intacto catálogo {index}")
            cls.tratamientos[slug] = TratamientoPaciente.objects.create(empresa=empresa, paciente=cls.pacientes[slug],
                profesional=cls.profesionales[slug], servicio=cls.servicios[slug], nombre=f"Registro de paciente intacto {index}",
                fecha_inicio="2026-11-01", monto_estimado="17.50", objetivo="Objetivo intacto", plan_clinico="Texto intacto")
            cls.citas[slug] = CitaCliente.objects.create(empresa=empresa, paciente=cls.pacientes[slug],
                profesional_salud=cls.profesionales[slug], servicio_clinico=cls.servicios[slug],
                titulo=f"Cita intacta catálogo {index}", opcion_servicio=cls.opciones[(slug, "active")].nombre,
                fecha_hora=timezone.make_aware(datetime(2026, 11, 18, 10)))
            product = Producto.objects.create(empresa=empresa, nombre=f"Producto intacto catálogo {index}",
                codigo=f"ACAT-P-{index}", precio="25.00")
            InventarioProducto.objects.create(empresa=empresa, producto=product, existencias="12.00")
            CuentaContable.objects.create(empresa=empresa, codigo=f"ACAT-A-{index}", nombre=f"Cuenta intacta catálogo {index}", tipo="activo")
        cls.opcion = cls.opciones[(cls.empresa.slug, "active")]
        cls.inactive_option = cls.opciones[(cls.empresa.slug, "inactive")]
        cls.foreign_option = cls.opciones[(cls.external.slug, "active")]

    def setUp(self):
        self.client.force_login(self.admin)
        for target in ("clinica.views.enviar_documento_whatsapp", "clinica.views.enviar_plantilla_preconsulta_whatsapp",
                       "clinica.views.subir_documento_whatsapp", "django.core.mail.EmailMessage.send",
                       "crm.views.urlopen", "crm.views.programar_notificaciones_cita", "crm.views.procesar_notificacion"):
            patcher = patch(target, side_effect=AssertionError("No mensajes, OAuth ni transportes reales"))
            mocked = patcher.start()
            self.addCleanup(patcher.stop)
            self.addCleanup(mocked.assert_not_called)

    def url(self, empresa=None, *, option=None, delete=False, mobile=True, **parameters):
        empresa = empresa or self.empresa
        route = "clinica_tratamientos"
        args = [empresa.slug]
        if option is not None:
            route = "clinica_tratamiento_agenda_eliminar" if delete else "clinica_tratamiento_agenda_editar"
            args.append(getattr(option, "pk", option))
        query = {"app": "1", "catalogo": "agenda"} if mobile else {}
        query.update(parameters)
        return reverse(route, args=args)+("?"+urlencode(query, doseq=True) if query else "")

    def data(self, *, edit=False, delete=False, **updates):
        data = {"app": "1"}
        if not delete:
            data["nombre"] = "Nombre nuevo catálogo sintético"
            if edit:
                data["activo"] = "on"
            else:
                data["accion"] = "crear_opcion_agenda"
        data.update(updates)
        return data

    def direct(self, url=None, *, actor=None, method="get", data=None):
        url = url or self.url()
        factory = RequestFactory()
        request = factory.post(url, self.data() if data is None else data) if method == "post" else factory.generic(method.upper(), url)
        request.user = Usuario.objects.get(pk=(actor or self.admin).pk)
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
        self.assertFalse(any(query["sql"].lstrip().upper().startswith(("INSERT ", "UPDATE ", "DELETE ", "REPLACE ")) for query in queries))

    def no_patient_queries(self, queries):
        for model in (Paciente, ProfesionalSalud, ServicioClinico, TratamientoPaciente, CitaCliente):
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

    def role(self, actor=None, empresa=None, *, granular=True, **flags):
        role = RolSistema.objects.create(nombre="Rol catálogo restringido QA", codigo=f"agenda-catalog-r-{RolSistema.objects.count()}",
            es_rol_clinico=True, usa_permisos_clinicos_granulares=granular, **flags)
        UsuarioEmpresaPermiso.objects.update_or_create(usuario=actor or self.users["granular"], empresa=empresa or self.empresa,
            defaults={"rol_sistema": role, "activo": True})
        return role

    def non_catalog_snapshot(self):
        return self.snapshot(exclude=("crm.opcionservicioagenda", "core.registroauditoria"))

    def test_four_clinics_and_both_role_types_read_own_mobile_catalog_not_central_copy(self):
        for actor in self.users.values():
            self.client.force_login(actor)
            for empresa in self.empresas.values():
                response = self.client.get(self.url(empresa))
                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, self.TEMPLATE)
                self.assertEqual({item["opcion"].pk for item in response.context["mobile_agenda_catalog_items"]},
                    {self.opciones[(empresa.slug, "active")].pk, self.opciones[(empresa.slug, "inactive")].pk})
                self.assertEqual(response.context["empresa"].pk, empresa.pk)
                self.assertNotContains(response, self.foreign_option.nombre)
                self.assertNotContains(response, self.spa_options[empresa.slug].nombre)

    def test_get_head_are_select_only_without_calendar_or_clinical_initializers(self):
        before = self.snapshot()
        with patch("crm.views.CitaClienteForm", side_effect=AssertionError("No agenda initialization")), patch("crm.views._contexto_calendario", side_effect=AssertionError("No agenda context")), patch("clinica.views._configuracion_clinica", side_effect=AssertionError("No config seed")):
            for empresa in self.empresas.values():
                for method in ("get", "head"):
                    with CaptureQueriesContext(connection) as queries:
                        response = getattr(self.client, method)(self.url(empresa))
                    self.assertEqual(response.status_code, 200)
                    self.assertIn("no-store", response["Cache-Control"])
                    if method == "head":
                        self.assertEqual(response.content, b"")
                    self.no_writes(queries)
                    self.no_patient_queries(queries)
        self.assertEqual(self.snapshot(), before)

    def test_logout_blocks_list_and_crud_without_writes(self):
        self.client.logout()
        before = self.snapshot()
        for url, data in ((self.url(), None), (self.url(), self.data()), (self.url(option=self.opcion), self.data(edit=True)),
                          (self.url(option=self.opcion, delete=True), self.data(delete=True))):
            response = self.client.get(url) if data is None else self.client.post(url, data)
            self.assertEqual(response.status_code, 302)
            self.assertIn("next=", response.url)
        self.assertEqual(self.snapshot(), before)

    def test_roles_without_original_route_permission_do_not_gain_catalog_access(self):
        for flags in ({}, {"puede_configuracion_clinica": True}, {"puede_ver_calendario": True}, {"puede_citas": True}):
            self.role(**flags)
            self.denied(actor=self.users["granular"])
            self.denied(actor=self.users["granular"], method="post", data=self.data())

    def test_granular_reader_can_get_but_original_head_requires_edit(self):
        self.role(puede_ver_planes_tratamiento=True)
        self.client.force_login(self.users["granular"])
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["can_manage_agenda_catalog"])
        self.denied(actor=self.users["granular"], method="head")
        self.denied(actor=self.users["granular"], method="post", data=self.data())

    def test_granular_writer_only_head_does_not_require_or_grant_get_read_permission(self):
        self.role(puede_editar_planes_tratamiento=True)
        self.denied(actor=self.users["granular"])
        self.client.force_login(self.users["granular"])
        before = self.snapshot()
        with CaptureQueriesContext(connection) as queries:
            response = self.client.head(self.url())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"")
        self.assertFalse(response.context["can_view_agenda_catalog"])
        self.assertFalse(response.context["can_manage_agenda_catalog"])
        self.no_writes(queries)
        self.assertEqual(self.snapshot(), before)

    def test_create_edit_and_delete_also_require_original_administrator_helper(self):
        for actor in self.users.values():
            self.denied(actor=actor, method="post", data=self.data())
            self.denied(self.url(option=self.opcion), actor=actor, method="post", data=self.data(edit=True))
            self.denied(self.url(option=self.opcion, delete=True), actor=actor, method="post", data=self.data(delete=True))
        self.role(puede_configuracion_clinica=True, puede_editar_planes_tratamiento=True, puede_ver_planes_tratamiento=True)
        self.denied(actor=self.users["granular"], method="post", data=self.data())

    def test_active_local_role_override_none_or_inactive_retains_original_effective_permissions(self):
        actor = self.users["granular"]
        self.role(actor=actor, puede_ver_calendario=True)
        self.denied(actor=actor)
        UsuarioEmpresaPermiso.objects.filter(usuario=actor, empresa=self.empresa).update(rol_sistema=None)
        self.denied(actor=actor)
        UsuarioEmpresaPermiso.objects.filter(usuario=actor, empresa=self.empresa).update(activo=False)
        self.assertEqual(self.direct(actor=actor).status_code, 200)
        RolSistema.objects.filter(pk=self.roles["granular"].pk).update(activo=False)
        self.denied(actor=actor)

    def test_membership_active_solution_and_clinical_module_are_not_bypassed(self):
        self.denied(actor=self.foreign_actor)
        Empresa.objects.filter(pk=self.empresa.pk).update(tipo_solucion="erp")
        self.denied(actor=self.supervisor)
        Empresa.objects.filter(pk=self.empresa.pk).update(tipo_solucion="clinica", activa=False)
        self.denied(actor=self.supervisor, status=404)
        Empresa.objects.filter(pk=self.empresa.pk).update(activa=True)
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.modulo).update(activo=False)
        self.denied(actor=self.supervisor)
        self.denied(actor=self.admin, method="post", data=self.data())

    def test_license_blocks_non_super_but_preserves_original_super_exception(self):
        for state in ("suspendida", "vencida"):
            Empresa.objects.filter(pk=self.empresa.pk).update(estado_licencia=state)
            for actor in (*self.users.values(), self.admin):
                self.denied(actor=actor)
                self.denied(actor=actor, method="post", data=self.data())
            self.assertEqual(self.direct(actor=self.supervisor).status_code, 200)

    def test_other_slugs_with_explicit_catalog_marker_never_fall_through_to_web(self):
        self.denied(self.url(self.external), actor=self.supervisor)
        self.denied(self.url(self.external), actor=self.supervisor, method="post", data=self.data())
        self.denied(self.url(self.external, mobile=False, catalogo="agenda"), actor=self.supervisor)

    def test_missing_wrong_or_duplicate_opt_in_queries_are_closed(self):
        path = self.url(mobile=False)
        for query in ("catalogo=agenda", "app=1&catalogo=other", "app=0&catalogo=agenda", "app=1&catalogo=agenda&app=1", "app=1&catalogo=agenda&catalogo=agenda"):
            self.denied(path+"?"+query)
        for key in ("q", "estado", "page"):
            self.denied(self.url(**{key: ["1", "1"]}))

    def test_unknown_query_parameters_and_invalid_state_are_rejected(self):
        for key in ("empresa", "nombre", "activo", "opcion_id", "accion", "next", "_popup", "paciente"):
            self.denied(self.url()+"&"+urlencode({key: "1"}))
        for state in ("activo", "inactivo", "todos", "1", "on"):
            self.denied(self.url(estado=state))

    def test_page_is_canonical_bounded_and_large_valid_page_clamps(self):
        for page in ("", "0", "01", "-1", "1.5", "1e2", "１", "abc", str(2**63)):
            self.denied(self.url(page=page))
        response = self.client.get(self.url(page=str(2**63-1)))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["mobile_agenda_catalog_page"], 1)

    def test_search_and_state_filters_apply_to_all_current_company_options(self):
        response = self.client.get(self.url(q=" opción inactiva ", estado="inactivos"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["mobile_agenda_catalog_total"], 1)
        self.assertEqual(response.context["mobile_agenda_catalog_items"][0]["opcion"].pk, self.inactive_option.pk)
        self.assertEqual(response.context["mobile_agenda_catalog_query"], "opción inactiva")
        response = self.client.get(self.url(q=self.foreign_option.nombre))
        self.assertEqual(response.context["mobile_agenda_catalog_total"], 0)
        self.assertFalse(response.context["mobile_agenda_catalog_items"])

    def test_server_pagination_stable_order_and_totals_preserve_all_rows(self):
        extras = [OpcionServicioAgenda.objects.create(empresa=self.empresa, categoria="tratamientos", nombre=f"C opción paginada {i:02d}",
            orden=2 if i % 2 else 7, activo=i % 3 != 0) for i in range(27)]
        expected = list(OpcionServicioAgenda.objects.filter(empresa=self.empresa, categoria="tratamientos").order_by("orden", "nombre", "id").values_list("pk", flat=True))
        first = self.client.get(self.url())
        second = self.client.get(self.url(page="2"))
        self.assertEqual(first.context["mobile_agenda_catalog_total"], 29)
        self.assertEqual(len(first.context["opciones_agenda"]), 20)
        self.assertEqual([row.pk for row in first.context["opciones_agenda"]]+[row.pk for row in second.context["opciones_agenda"]], expected)
        self.assertEqual(parse_qs(urlsplit(first.context["mobile_agenda_catalog_next_url"]).query, keep_blank_values=True), {"app": ["1"], "catalogo": ["agenda"], "q": [""], "estado": [""], "page": ["2"]})
        self.assertTrue(extras)

    def test_all_own_creator_relations_are_validated_before_filter_and_pagination(self):
        OpcionServicioAgenda.objects.filter(pk=self.inactive_option.pk).update(creado_por_id=2**62)
        for params in ({"q": self.opcion.nombre}, {"estado": "activos"}, {"page": "99"}):
            self.denied(self.url(**params))
        self.denied(method="post", data=self.data())
        self.denied(self.url(option=self.opcion), method="post", data=self.data(edit=True))
        OpcionServicioAgenda.objects.filter(pk=self.inactive_option.pk).update(creado_por=None)

    def test_null_inactive_multicompany_creator_and_other_category_corruption_are_legitimate(self):
        Usuario.objects.filter(pk=self.foreign_actor.pk).update(is_active=False)
        OpcionServicioAgenda.objects.filter(pk=self.opcion.pk).update(creado_por=self.foreign_actor)
        OpcionServicioAgenda.objects.filter(pk=self.spa_options[self.empresa.slug].pk).update(creado_por_id=2**62)
        OpcionServicioAgenda.objects.filter(pk=self.foreign_option.pk).update(creado_por_id=2**62)
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["mobile_agenda_catalog_total"], 2)
        OpcionServicioAgenda.objects.filter(pk__in=[self.spa_options[self.empresa.slug].pk, self.foreign_option.pk]).update(creado_por=None)

    def test_foreign_missing_and_wrong_category_selection_are_404_before_all_catalog_query(self):
        for option in (self.foreign_option, self.spa_options[self.empresa.slug], 2**62):
            for delete in (False, True):
                with patch("clinica.mobile_agenda_catalog.validar_relaciones_catalogo_agenda_app", side_effect=AssertionError("No other catalog before invalid selection")):
                    self.denied(self.url(option=option, delete=delete), status=404, method="post", data=self.data(edit=not delete, delete=delete))

    def test_path_ids_are_checked_raw_canonical_int64_without_fallback(self):
        for raw in ("0", "00"+str(self.opcion.pk), str(2**63)):
            for delete in (False, True):
                self.denied(self.url(option=raw, delete=delete), method="post", data=self.data(edit=not delete, delete=delete))

    def test_post_requires_query_and_body_opt_in_without_mixed_navigation_metadata(self):
        for url in (self.url(mobile=False), self.url(mobile=False, app="1"), self.url(mobile=False, catalogo="agenda"),
                    self.url(app="0"), self.url(catalogo="other"), self.url(q="filter"), self.url(page="1")):
            self.denied(url, method="post", data=self.data())
        for data in (self.data(app="0"), {key: value for key, value in self.data().items() if key != "app"}, self.data(accion="edit")):
            self.denied(method="post", data=data)

    def test_create_post_has_only_original_action_name_and_closed_scalar_fields(self):
        for key in ("empresa", "categoria", "orden", "activo", "creado_por", "opcion_id", "catalogo", "confirmacion", "next"):
            self.denied(method="post", data={**self.data(), key: "1"})
        for key in ("app", "accion", "nombre", "csrfmiddlewaretoken"):
            self.denied(method="post", data={**self.data(), key: ["1", "1"]})
        self.denied(method="post", data={**self.data(), "archivo": SimpleUploadedFile("qa.txt", b"synthetic")})

    def test_edit_post_keeps_original_activo_scalar_but_no_other_metadata(self):
        for key in ("accion", "empresa", "categoria", "orden", "creado_por", "opcion_id", "catalogo", "confirmacion"):
            self.denied(self.url(option=self.opcion), method="post", data={**self.data(edit=True), key: "1"})
        for key in ("app", "nombre", "activo", "csrfmiddlewaretoken"):
            self.denied(self.url(option=self.opcion), method="post", data={**self.data(edit=True), key: ["1", "1"]})

    def test_delete_envelope_is_only_app_and_csrf(self):
        for key in ("nombre", "activo", "accion", "empresa", "categoria", "catalogo", "confirmacion", "opcion_id"):
            self.denied(self.url(option=self.opcion, delete=True), method="post", data={**self.data(delete=True), key: "1"})
        for key in ("app", "csrfmiddlewaretoken"):
            self.denied(self.url(option=self.opcion, delete=True), method="post", data={**self.data(delete=True), key: ["1", "1"]})
        self.denied(self.url(option=self.opcion, delete=True), method="post", data={**self.data(delete=True), "archivo": SimpleUploadedFile("x", b"x")})

    def test_original_edit_and_delete_get_head_remain_405_without_any_queries_or_writes(self):
        for option in (self.opcion, self.foreign_option, 2**62):
            for delete in (False, True):
                for method in ("get", "head"):
                    self.denied(self.url(option=option, delete=delete), method=method, status=405)
        for method in ("put", "patch", "delete", "options"):
            self.denied(method=method)

    def test_create_whitespace_normalization_defaults_and_original_audit(self):
        for index, empresa in enumerate(self.empresas.values()):
            before = self.non_catalog_snapshot()
            response = self.client.post(self.url(empresa), self.data(nombre=f"  Nuevo\n  catálogo\t {index}  "))
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.url, self.url(empresa))
            option = OpcionServicioAgenda.objects.get(empresa=empresa, categoria="tratamientos", nombre=f"Nuevo catálogo {index}")
            self.assertEqual((option.orden, option.activo, option.creado_por_id), (0, True, self.admin.pk))
            audit = RegistroAuditoria.objects.get(modelo="opcionservicioagenda", objeto_id=str(option.pk), accion="crear")
            self.assertEqual((audit.usuario_id, audit.empresa_id, audit.metodo_http), (self.admin.pk, empresa.pk, "POST"))
            self.assertTrue(audit.fecha)
            self.assertEqual(self.non_catalog_snapshot(), before)

    def test_create_name_length_and_case_insensitive_duplicates_reuse_original_validation(self):
        # SQLite's original iexact does not fold accented Unicode. Change
        # ASCII letters only, preserving the backend's actual duplicate rule.
        for name in ("", "a", "  ab  ", "x"*181, self.opcion.nombre.replace("propia", "PROPIA"),
                     self.inactive_option.nombre.replace("inactiva", "INACTIVA")):
            before = self.snapshot()
            response = self.client.post(self.url(), self.data(nombre=name))
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.context["mobile_agenda_catalog_values"]["nombre"], name)
            self.assertEqual(self.snapshot(), before)
        for name in ("abc", "x"*180):
            response = self.client.post(self.url(), self.data(nombre=name))
            self.assertEqual(response.status_code, 302)
            self.assertTrue(OpcionServicioAgenda.objects.filter(empresa=self.empresa, nombre=name).exists())

    def test_same_name_in_another_company_or_spa_category_is_not_new_uniqueness_rule(self):
        for name in (self.foreign_option.nombre, self.spa_options[self.empresa.slug].nombre):
            response = self.client.post(self.url(), self.data(nombre=name))
            self.assertEqual(response.status_code, 302)
            self.assertTrue(OpcionServicioAgenda.objects.filter(empresa=self.empresa, categoria="tratamientos", nombre=name).exists())

    def test_edit_preserves_creator_order_category_and_original_active_on_policy(self):
        original_creator = self.opcion.creado_por_id
        for index, value in enumerate(("on", "yes", "1", "", "off")):
            before = self.non_catalog_snapshot()
            response = self.client.post(self.url(option=self.opcion), self.data(edit=True, nombre=f" Editada  opción\t{index} ", activo=value))
            self.assertEqual(response.status_code, 302)
            self.opcion.refresh_from_db()
            self.assertEqual((self.opcion.nombre, self.opcion.activo, self.opcion.orden, self.opcion.creado_por_id, self.opcion.categoria),
                (f"Editada opción {index}", value == "on", 2, original_creator, "tratamientos"))
            self.assertEqual(self.non_catalog_snapshot(), before)

    def test_edit_audit_has_original_actor_old_new_values_without_touching_cita_snapshot(self):
        response = self.client.post(self.url(option=self.opcion), self.data(edit=True, nombre="Nombre corregido QA", activo="off"))
        self.assertEqual(response.status_code, 302)
        audit = RegistroAuditoria.objects.filter(modelo="opcionservicioagenda", objeto_id=str(self.opcion.pk), accion="modificar").latest("pk")
        self.assertEqual(audit.cambios["nombre"], {"anterior": self.opcion.nombre, "nuevo": "Nombre corregido QA"})
        self.assertEqual(audit.cambios["activo"], {"anterior": True, "nuevo": False})
        self.assertEqual((audit.usuario_id, audit.empresa_id), (self.admin.pk, self.empresa.pk))
        self.citas[self.empresa.slug].refresh_from_db()
        self.assertEqual(self.citas[self.empresa.slug].opcion_servicio, self.opcion.nombre)

    def test_invalid_edit_keeps_raw_work_active_boolean_and_scoped_item_even_off_page(self):
        for index in range(25):
            OpcionServicioAgenda.objects.create(empresa=self.empresa, categoria="tratamientos", nombre=f"Anterior página {index:02d}", orden=0)
        before = self.snapshot()
        raw = "  x\n "
        response = self.client.post(self.url(option=self.opcion), self.data(edit=True, nombre=raw, activo="yes"))
        self.assertEqual(response.status_code, 400)
        self.assertTemplateUsed(response, self.TEMPLATE)
        self.assertEqual(response.context["mobile_agenda_catalog_values"], {"nombre": raw, "activo": False})
        self.assertEqual(response.context["mobile_agenda_catalog_edit_item"]["opcion"].pk, self.opcion.pk)
        self.assertEqual(response.context["app_agenda_catalog_form_url"], self.url(option=self.opcion))
        self.assertFalse(any(item["opcion"].pk == self.opcion.pk for item in response.context["mobile_agenda_catalog_items"]))
        self.assertEqual(self.snapshot(), before)

    def test_invalid_edit_duplicate_or_length_never_changes_previous_record(self):
        for name in ("", "ab", "x"*181, self.inactive_option.nombre.replace("inactiva", "INACTIVA")):
            before = self.snapshot()
            response = self.client.post(self.url(option=self.opcion), self.data(edit=True, nombre=name))
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.context["mobile_agenda_catalog_values"]["nombre"], name)
            self.assertEqual(self.snapshot(), before)
        response = self.client.post(self.url(option=self.opcion), self.data(edit=True, nombre=self.opcion.nombre.replace("propia", "PROPIA")))
        self.assertEqual(response.status_code, 302)  # Existing option is excluded by the original duplicate query.

    def test_delete_original_option_only_audit_and_historical_strings_remain(self):
        before = self.non_catalog_snapshot()
        response = self.client.post(self.url(option=self.opcion, delete=True), self.data(delete=True))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, self.url())
        self.assertFalse(OpcionServicioAgenda.objects.filter(pk=self.opcion.pk).exists())
        self.assertTrue(OpcionServicioAgenda.objects.filter(pk=self.inactive_option.pk).exists())
        audit = RegistroAuditoria.objects.get(modelo="opcionservicioagenda", objeto_id=str(self.opcion.pk), accion="eliminar")
        self.assertEqual((audit.usuario_id, audit.empresa_id), (self.admin.pk, self.empresa.pk))
        self.assertEqual(audit.cambios["registro_eliminado"]["anterior"]["nombre"], self.opcion.nombre)
        self.assertEqual(self.non_catalog_snapshot(), before)

    def test_invalid_post_has_no_business_or_patient_queries_and_retains_name(self):
        before = self.snapshot()
        raw = "\n  ab \t"
        with CaptureQueriesContext(connection) as queries, patch("crm.views.CitaClienteForm", side_effect=AssertionError("No CitaForm")):
            response = self.client.post(self.url(), self.data(nombre=raw))
        self.assertEqual(response.status_code, 400)
        self.assertTrue(response.context["mobile_agenda_catalog_bound"])
        self.assertEqual(response.context["mobile_agenda_catalog_mode"], "crear")
        self.assertEqual(response.context["mobile_agenda_catalog_values"]["nombre"], raw)
        self.assertEqual(self.snapshot(), before)
        self.no_writes(queries)
        self.no_patient_queries(queries)

    def test_original_names_with_html_quotes_are_not_rejected_but_mobile_escapes_output(self):
        name = "<img src=x onerror='alert(1)'> & \"nombre\" `texto`"
        response = self.client.post(self.url(), self.data(nombre=name))
        self.assertEqual(response.status_code, 302)
        option = OpcionServicioAgenda.objects.get(empresa=self.empresa, nombre=name)
        response = self.client.get(self.url(q="onerror"))
        self.assertEqual(response.context["mobile_agenda_catalog_items"][0]["opcion"].pk, option.pk)
        self.assertContains(response, "&lt;img")
        self.assertNotContains(response, "<img src=x onerror=")
        self.assertEqual(option.nombre, name)

    def test_readonly_mobile_has_no_post_forms_creator_data_or_operational_web_links(self):
        self.client.force_login(self.users["granular"])
        response = self.client.get(self.url())
        markup = _CatalogMarkup(response.content.decode())
        self.assertFalse(any(form["attrs"].get("method", "get").lower() == "post" for form in markup.forms))
        self.assertFalse(response.context["can_manage_agenda_catalog"])
        self.assertNotContains(response, self.users["legacy"].username)
        self.assertNotContains(response, 'rel="manifest"')
        self.assertNotContains(response, "<table")
        self.assertFalse(any("/editar/" in urlsplit(href).path or "/eliminar/" in urlsplit(href).path for href in markup.links))

    def test_native_admin_forms_have_only_original_operation_fields_and_fixed_app_query(self):
        response = self.client.get(self.url())
        markup = _CatalogMarkup(response.content.decode())
        post_forms = [form for form in markup.forms if form["attrs"].get("method", "get").lower() == "post"]
        self.assertTrue(post_forms)
        for form in post_forms:
            names = form["names"]
            self.assertEqual(names.count("app"), 1)
            self.assertEqual(names.count("csrfmiddlewaretoken"), 1)
            self.assertEqual(len(names), len(set(names)))
            self.assertFalse(set(names)-{"app", "csrfmiddlewaretoken", "accion", "nombre", "activo"})
            action = form["attrs"].get("action", "")
            # A delete dialog may install its own original URL only on opening.
            if action:
                self.assertEqual(parse_qs(urlsplit(action).query), {"app": ["1"], "catalogo": ["agenda"]})
        self.assertFalse(any(name in {"empresa", "opcion_id", "categoria", "orden", "creado_por"}
                             for form in post_forms for name in form["names"]))

    def test_pure_capability_reads_permissions_without_options_patients_or_writes(self):
        from .mobile_agenda_catalog import catalogo_agenda_disponible_app

        request = RequestFactory().get(self.url())
        before = self.snapshot()
        for actor, expected in ((self.admin, {"ver": True, "administrar": True}),
                                (self.users["granular"], {"ver": True, "administrar": False}),
                                (self.none, {"ver": False, "administrar": False})):
            request.user = actor
            with CaptureQueriesContext(connection) as queries:
                self.assertEqual(catalogo_agenda_disponible_app(request, self.empresa), expected)
            self.no_writes(queries)
            self.no_patient_queries(queries)
            self.assertFalse(any(OpcionServicioAgenda._meta.db_table in row["sql"] for row in queries))
        self.assertEqual(self.snapshot(), before)

    def test_navigation_uses_only_existing_authorized_mobile_destinations_without_calendar(self):
        self.client.force_login(self.users["granular"])
        response = self.client.get(self.url())
        self.assertFalse(response.context["app_home_disponible"])
        self.assertEqual(response.context["app_base_url"], "")
        markup = _CatalogMarkup(response.content.decode())
        self.assertFalse(any(urlsplit(href).path == reverse("agenda_mobile", args=[self.empresa.slug]) for href in markup.links))
        if response.context["can_view_app_modules"]:
            self.assertEqual(self.client.get(response.context["app_modules_url"]).status_code, 200)

    def test_new_module_card_is_current_company_catalog_not_patient_flow_or_new_permission(self):
        from crm.mobile_modules import construir_contexto_modulos_app

        for empresa in self.empresas.values():
            request = RequestFactory().get(self.url(empresa))
            request.user = Usuario.objects.get(pk=self.users["granular"].pk)
            before = self.snapshot()
            with CaptureQueriesContext(connection) as queries:
                context = construir_contexto_modulos_app(request, empresa)
            card = next(item for item in context["modulos_app"] if item["key"] == "catalogo_tratamientos")
            self.assertEqual(card["url"], self.url(empresa))
            self.assertEqual(card["estado"], "disponible")
            self.assertEqual(self.snapshot(), before)
            self.no_writes(queries)
        self.role(puede_editar_planes_tratamiento=True)
        request.user = Usuario.objects.get(pk=self.users["granular"].pk)
        context = construir_contexto_modulos_app(request, self.empresa)
        self.assertFalse(any(item["key"] == "catalogo_tratamientos" for item in context["modulos_app"]))

    def test_existing_patient_treatment_mobile_list_without_catalog_marker_is_unchanged(self):
        self.client.force_login(self.users["granular"])
        url = reverse("clinica_tratamientos", args=[self.empresa.slug])+"?app=1"
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "clinica/mobile_treatment_list.html")
        self.assertTemplateNotUsed(response, self.TEMPLATE)
        self.assertEqual([row.pk for row in response.context["tratamientos"]], [self.tratamientos[self.empresa.slug].pk])
        self.assertFalse(any(OpcionServicioAgenda._meta.db_table in row["sql"] for row in queries))

    def test_existing_patient_treatment_mobile_create_uses_all_ten_original_fields(self):
        self.client.force_login(self.users["granular"])
        url = reverse("clinica_crear_tratamiento", args=[self.empresa.slug])+"?app=1"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "clinica/mobile_treatment_form.html")
        self.assertIsInstance(response.context["form"], TratamientoPacienteForm)
        self.assertEqual(tuple(response.context["form"].fields), tuple(TratamientoPacienteForm.base_fields))
        data = {"app": "1", "paciente": self.pacientes[self.empresa.slug].pk, "profesional": self.profesionales[self.empresa.slug].pk,
                "servicio": self.servicios[self.empresa.slug].pk, "nombre": "Paciente nuevo tratamiento intacto",
                "fecha_inicio": "2026-11-18", "fecha_fin_estimada": "2026-12-01", "estado": "en_proceso",
                "objetivo": "Original", "plan_clinico": "Plan original", "monto_estimado": "21.00"}
        original_options = list(OpcionServicioAgenda.objects.order_by("pk").values())
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(TratamientoPaciente.objects.filter(empresa=self.empresa, nombre=data["nombre"]).exists())
        self.assertEqual(list(OpcionServicioAgenda.objects.order_by("pk").values()), original_options)

    def test_web_templates_create_edit_delete_and_redirects_stay_original(self):
        for index, empresa in enumerate(self.empresas.values()):
            url = self.url(empresa, mobile=False)
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertTemplateUsed(response, "clinica/tratamientos.html")
            self.assertTemplateNotUsed(response, self.TEMPLATE)
            response = self.client.post(url, {"accion": "crear_opcion_agenda", "nombre": f" Web  nuevo {index} "})
            self.assertEqual(response.url, url)
            option = OpcionServicioAgenda.objects.get(empresa=empresa, nombre=f"Web nuevo {index}")
            response = self.client.post(self.url(empresa, option=option, mobile=False), {"nombre": f"Web cambio {index}", "activo": "on"})
            self.assertEqual(response.url, url)
            option.refresh_from_db()
            self.assertEqual((option.nombre, option.activo), (f"Web cambio {index}", True))
            response = self.client.post(self.url(empresa, option=option, delete=True, mobile=False), {})
            self.assertEqual(response.url, url)
            self.assertFalse(OpcionServicioAgenda.objects.filter(pk=option.pk).exists())

    def test_web_validation_errors_keep_original_redirect_not_mobile_dialog(self):
        for name in ("ab", self.opcion.nombre.replace("propia", "PROPIA")):
            before = self.snapshot(exclude=("sessions.session",))
            response = self.client.post(self.url(mobile=False), {"accion": "crear_opcion_agenda", "nombre": name})
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.url, self.url(mobile=False))
            self.assertEqual(self.snapshot(exclude=("sessions.session",)), before)

    def render_partial(self, *, secure=None, name="Tratamiento de prueba"):
        class SyntheticAppointmentFields(forms.Form):
            detalles_agenda = forms.CharField(required=False, widget=forms.HiddenInput)

        context = {"detalles_agenda_hospital_mia": True, "empresa": self.empresa,
                   "form": SyntheticAppointmentFields(), "opciones_tratamiento_agenda": [SimpleNamespace(id=31, nombre=name)]}
        if secure is not None:
            context["detalles_agenda_app_segura"] = secure
        return render_to_string(self.PARTIAL, context)

    def test_shared_partial_app_json_preserves_arbitrary_original_names_safely(self):
        name = "<img src=x onerror=\"bad()\"> '&quoted; &quot; `literal` </script><script>bad()</script>"
        before = self.snapshot()
        with patch("crm.views.CitaClienteForm", side_effect=AssertionError("No original seeding form")):
            html = self.render_partial(secure=True, name=name)
        markup = _CatalogMarkup(html)
        self.assertEqual(json.loads(markup.json_options), [{"id": "31", "nombre": name}])
        self.assertNotIn("<script>bad()</script>", html)
        self.assertNotIn("<img src=x", html)
        self.assertEqual(self.snapshot(), before)

    def test_shared_partial_secure_dynamic_encoding_and_web_branch_are_separate(self):
        secure = self.render_partial(secure=True)
        web = self.render_partial(secure=False)
        missing = self.render_partial()
        self.assertEqual(web, missing)
        unsafe_choice = "JSON.stringify(i).replace(/'/g,'&#39;')"
        self.assertIn(unsafe_choice, web)
        self.assertNotIn(unsafe_choice, secure)
        self.assertIn("${i.label}${i.done?", web)
        self.assertNotIn("${i.label}${i.done?", secure)
        self.assertIn("${optionLabel(s)}", web)
        self.assertNotIn("${optionLabel(s)}", secure)
        # Static contract, not a substitute for the parent's isolated Chrome
        # round-trip checks of the native dynamic data-agenda-choice attribute.
        for entity in ("&amp;", "&lt;", "&gt;", "&quot;", "&#39;"):
            self.assertIn(entity, secure)
        self.assertRegex(secure, r"\$\{[^{}]*JSON\.stringify\(i\)[^{}]*\}")

    def test_csrf_uses_original_failure_handler_and_valid_token_keeps_original_create(self):
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.admin)
        before = self.snapshot(exclude=("sessions.session",))
        response = strict.post(self.url(), self.data())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("empresa_login", kwargs={"slug": self.empresa.slug}))
        self.assertEqual(self.snapshot(exclude=("sessions.session",)), before)
        strict.cookies["csrftoken"] = "a"*32
        response = strict.post(self.url(), {**self.data(), "csrfmiddlewaretoken": "a"*32})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, self.url())
        self.assertTrue(OpcionServicioAgenda.objects.filter(empresa=self.empresa, nombre=self.data()["nombre"]).exists())
