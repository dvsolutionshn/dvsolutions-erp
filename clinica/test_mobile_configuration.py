"""Clinical configuration uses its canonical catalog forms and action guards."""
from collections import Counter
from datetime import timedelta
from decimal import Decimal
from html.parser import HTMLParser
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.http import Http404, QueryDict
from django.test import Client, RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import resolve, reverse
from django.utils import timezone

from core.models import ConfiguracionAvanzadaEmpresa, Empresa, EmpresaModulo, Modulo, RegistroAuditoria, RolSistema, Usuario, UsuarioEmpresaPermiso
from crm.models import CitaCliente, ConfiguracionCRM
from facturacion.models import Cliente, Factura, MovimientoInventario, MovimientoLoteBodega, PagoFactura, Producto, ReciboPago
from . import views
from .forms import ProfesionalSaludForm, ServicioClinicoForm
from .models import CitaClinica, ConfiguracionClinica, Paciente, ProfesionalSalud, ServicioClinico, TratamientoPaciente


class ConfigurationControlNames(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.inside = False
        self.names = []
        self.controls = []
        self.feed(html)

    def handle_starttag(self, tag, attributes):
        original_attributes = attributes
        attributes = dict(attributes)
        if tag == "form":
            self.inside = attributes.get("id") == "mobileConfigurationForm"
        if self.inside and tag in {"input", "select", "textarea", "button"} and attributes.get("name"):
            self.names.append(attributes["name"])
            self.controls.append((tag, original_attributes))

    def handle_endtag(self, tag):
        if tag == "form":
            self.inside = False


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class MobileConfigurationIntegrationTests(TestCase):
    SLUGS = ("hospital_mia", "medical_spa", "luque_aestetic", "serviciosmedicos")
    PROFESSIONAL_FIELDS = ("nombre", "especialidad", "colegiacion", "telefono", "activo")
    SERVICE_FIELDS = ("nombre", "categoria", "duracion_minutos", "color_calendario", "precio_referencia", "requiere_consentimiento", "activo")
    ROUTES = ("clinica_configuracion", "clinica_profesionales", "clinica_profesional_editar", "clinica_servicios", "clinica_servicio_editar", "clinica_servicio_eliminar")

    @classmethod
    def setUpTestData(cls):
        cls.module, _ = Modulo.objects.get_or_create(codigo="clinica_medica", defaults={"nombre": "Clínica"})
        cls.default_role = RolSistema.objects.create(nombre="Configuración clínica QA", codigo="mobile-config-all", es_rol_clinico=True, usa_permisos_clinicos_granulares=True, puede_configuracion_clinica=True)
        cls.empresas, cls.profesionales, cls.servicios = {}, {}, {}
        for index, slug in enumerate(cls.SLUGS, 1):
            empresa = Empresa.objects.create(nombre=f"Clínica configuración QA {slug}", slug=slug, rtn=f"MCFG-{index}", tipo_solucion="clinica", estado_licencia="activa")
            EmpresaModulo.objects.create(empresa=empresa, modulo=cls.module)
            cls.empresas[slug] = empresa
            cls.profesionales[slug] = ProfesionalSalud.objects.create(empresa=empresa, nombre=f"Profesional sintético {slug}", especialidad="Especialidad QA", colegiacion="COLEG-001", telefono="9999-0011")
            cls.servicios[slug] = ServicioClinico.objects.create(empresa=empresa, nombre=f"Servicio sintético {slug}", categoria="consulta", duracion_minutos=60, color_calendario="#123abc", precio_referencia=Decimal("75.50"))
        cls.empresa = cls.empresas["hospital_mia"]
        cls.usuario = Usuario.objects.create_user(username="mobile-config-qa", password="test-only", empresa=cls.empresa, rol_sistema=cls.default_role)
        cls.admin = Usuario.objects.create_user(username="mobile-config-admin", password="test-only", empresa=cls.empresa, rol_sistema=cls.default_role, es_administrador_empresa=True)
        cls.owner = Usuario.objects.create_user(username="daniel_varela", password="test-only", empresa=cls.empresa, rol_sistema=cls.default_role)
        cls.supervisor = Usuario.objects.create_superuser(username="mobile-config-super", password="test-only", empresa=cls.empresa)
        for actor in (cls.usuario, cls.admin, cls.owner):
            actor.empresas_acceso.add(*cls.empresas.values())

    def setUp(self):
        self.actor = self.usuario
        self.client.force_login(self.actor)
        for target in ("facturacion.views.enviar_documento_whatsapp", "facturacion.views.subir_documento_whatsapp", "django.core.mail.EmailMessage.send"):
            transport = patch(target)
            mock = transport.start()
            self.addCleanup(transport.stop)
            self.addCleanup(mock.assert_not_called)

    def use_user(self, user):
        self.actor = user
        self.client.force_login(user)

    def role(self, granular=True, **permissions):
        role = RolSistema.objects.create(nombre="Configuración restringida QA", codigo=f"mobile-config-role-{RolSistema.objects.count()}", es_rol_clinico=granular, usa_permisos_clinicos_granulares=granular, **permissions)
        UsuarioEmpresaPermiso.objects.update_or_create(usuario=self.actor, empresa=self.empresa, defaults={"rol_sistema": role})
        return role

    def url(self, route="clinica_configuracion", empresa=None, record=None, mobile=True, **query):
        empresa = empresa or self.empresa
        arguments = [empresa.slug]
        if route == "clinica_profesional_editar":
            arguments.append((record or self.profesionales[empresa.slug]).pk)
        elif route in {"clinica_servicio_editar", "clinica_servicio_eliminar"}:
            arguments.append((record or self.servicios[empresa.slug]).pk)
        parameters = {"app": "1"} if mobile else {}
        parameters.update(query)
        return reverse(route, args=arguments)+("?"+urlencode(parameters) if parameters else "")

    def data(self, service=False, **extra):
        values = {"app": "1", "nombre": "Profesional móvil nuevo", "especialidad": "Especialidad nueva", "colegiacion": "COLEG-NUEVA", "telefono": "9999-8888", "activo": "on"}
        if service:
            values = {"app": "1", "nombre": "Servicio móvil nuevo", "categoria": "spa", "duracion_minutos": "45", "color_calendario": "#abcdef", "precio_referencia": "123.45", "requiere_consentimiento": "on", "activo": "on"}
        values.update(extra)
        return values

    def direct(self, route="clinica_configuracion", method="get", data=None, url=None, empresa=None, record=None):
        target = url or self.url(route, empresa, record)
        data = self.data(service="servicio" in route) if data is None else data
        if isinstance(data, QueryDict):
            data = {key: data.getlist(key) for key in data}
        request = RequestFactory().post(target, data) if method == "post" else RequestFactory().generic(method.upper(), target)
        request.user = Usuario.objects.get(pk=self.actor.pk)
        match = resolve(urlsplit(target).path)
        return match.func(request, **match.kwargs)

    def denied(self, callback):
        try:
            response = callback()
        except (PermissionDenied, Http404):
            return
        self.assertIn(response.status_code, (403, 404, 405))

    def business_snapshot(self):
        return tuple(list(model.objects.order_by("pk").values()) for model in (Paciente, CitaClinica, CitaCliente, TratamientoPaciente, Cliente, Producto, Factura, PagoFactura, ReciboPago, MovimientoInventario, MovimientoLoteBodega, ConfiguracionClinica, ConfiguracionCRM, ConfiguracionAvanzadaEmpresa))

    def snapshot(self):
        return list(ProfesionalSalud.objects.order_by("pk").values()), list(ServicioClinico.objects.order_by("pk").values()), self.business_snapshot(), RegistroAuditoria.objects.count()

    def no_catalog_queries(self, captured, exclude=()):
        models = (ProfesionalSalud, ServicioClinico, Paciente, CitaClinica, CitaCliente, TratamientoPaciente, Factura, PagoFactura, ReciboPago)
        sql = [row["sql"] for row in captured if row["sql"].lstrip().upper().startswith("SELECT")]
        self.assertFalse(any(f'"{model._meta.db_table}"' in statement for model in models if model not in exclude for statement in sql))

    def test_config_hub_mobile_four_companies_is_read_only_and_web_template_is_unchanged(self):
        before = self.snapshot()
        for empresa in self.empresas.values():
            with CaptureQueriesContext(connection) as queries:
                app = self.client.get(self.url(empresa=empresa))
            self.assertEqual(app.status_code, 200)
            self.assertTemplateUsed(app, "clinica/mobile_configuration_hub.html")
            self.assertNotContains(app, "<table")
            self.assertIn("no-store", app["Cache-Control"])
            self.no_catalog_queries(queries)
            web = self.client.get(self.url(empresa=empresa, mobile=False))
            self.assertTemplateUsed(web, "clinica/configuracion.html")
            self.assertTemplateNotUsed(web, "clinica/mobile_configuration_hub.html")
        self.assertEqual(self.snapshot(), before)

    def test_legacy_empty_hub_keeps_original_none_permission_without_catalogue_or_navigation_grants(self):
        self.role(granular=False)
        before = self.snapshot()
        with CaptureQueriesContext(connection) as queries:
            hub = self.client.get(self.url())
        self.assertEqual(hub.status_code, 200)
        self.no_catalog_queries(queries)
        self.assertNotContains(hub, self.url("clinica_profesionales"))
        self.assertNotContains(hub, self.url("clinica_servicios"))
        self.assertNotContains(hub, reverse("agenda_mobile_modules_screen", args=[self.empresa.slug]))
        self.assertNotContains(hub, 'rel="manifest"')
        self.assertFalse(hub.context["app_modules_disponible"])
        self.assertFalse(hub.context["app_home_disponible"])
        self.assertEqual(hub.context["app_modules_url"], self.url())
        for route in self.ROUTES[1:]:
            self.denied(lambda: self.direct(route, method="post" if route == self.ROUTES[-1] else "get"))
        self.assertEqual(self.snapshot(), before)

    def test_granular_hub_requires_configuration_or_existing_manual_admin_exception_only(self):
        self.role()
        self.denied(lambda: self.direct())
        self.role(puede_administrar_manuales_pdf=True)
        hub = self.client.get(self.url())
        self.assertEqual(hub.status_code, 200)
        for route in ("clinica_profesionales", "clinica_servicios"):
            self.denied(lambda: self.direct(route))
            self.assertNotContains(hub, self.url(route))

    def test_catalog_configuration_permission_works_for_both_role_types_without_agenda_or_history(self):
        for granular in (True, False):
            self.role(granular=granular, puede_configuracion_clinica=True)
            for route in ("clinica_profesionales", "clinica_servicios"):
                app = self.client.get(self.url(route))
                self.assertEqual(app.status_code, 200)
                self.assertNotContains(app, 'rel="manifest"')
                self.assertFalse(app.context["app_home_disponible"])
                self.assertEqual(self.client.post(self.url(route), self.data(service=route == "clinica_servicios", nombre=f"Catálogo {route} {granular}")).status_code, 302)
            for route in ("clinica_profesional_editar", "clinica_servicio_editar"):
                self.denied(lambda: self.direct(route))

    def test_configuration_capabilities_helper_is_pure_and_respects_existing_role_and_owner_rules(self):
        from .mobile_configuration import config_disponible_app
        for actor in (self.usuario, self.admin, self.owner, self.supervisor):
            self.use_user(actor)
            request = RequestFactory().get("/")
            request.user = Usuario.objects.get(pk=actor.pk)
            with CaptureQueriesContext(connection) as queries:
                flags = config_disponible_app(request, self.empresa)
            self.no_catalog_queries(queries)
            self.assertTrue(flags["hub"])
            self.assertTrue(flags["profesionales"])
            self.assertTrue(flags["servicios"])
            self.assertEqual(flags["editar_profesionales"], actor in (self.admin, self.supervisor))
            self.assertEqual(flags["editar_servicios"], actor == self.owner)
            self.assertEqual(flags["eliminar_servicios"], actor == self.owner)

    def test_unrelated_clinical_permissions_and_missing_configuration_role_do_not_grant_catalogues(self):
        for permissions in ({}, {"puede_ver_pacientes": True}, {"puede_expediente_clinico": True}, {"puede_editar_historia_clinica": True}, {"puede_ver_calendario": True}, {"puede_administrar_manuales_pdf": True}):
            self.role(**permissions)
            before = self.snapshot()
            for route in ("clinica_profesionales", "clinica_servicios"):
                self.denied(lambda: self.direct(route))
                self.denied(lambda: self.direct(route, method="post"))
            self.assertEqual(self.snapshot(), before)

    def test_direct_guards_check_clinical_module_membership_activity_and_license(self):
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.module).update(activo=False)
        for route in self.ROUTES[:-1]:
            self.denied(lambda: self.direct(route))
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.module).update(activo=True)
        for values in ({"activa": False}, {"estado_licencia": "suspendida"}, {"fecha_vencimiento_plan": timezone.localdate()-timedelta(days=1)}):
            Empresa.objects.filter(pk=self.empresa.pk).update(**values)
            self.denied(lambda: self.direct("clinica_profesionales"))
            Empresa.objects.filter(pk=self.empresa.pk).update(activa=True, estado_licencia="activa", fecha_vencimiento_plan=None)
        self.usuario.empresas_acceso.remove(self.empresas["medical_spa"])
        self.denied(lambda: self.direct("clinica_servicios", empresa=self.empresas["medical_spa"]))

    def test_other_clinic_app_opt_in_keeps_original_web_configuration_and_catalogues(self):
        empresa = Empresa.objects.create(nombre="Otra clínica configuración QA", slug="otra_configuracion_qa", rtn="MCFG-OTHER", tipo_solucion="clinica", estado_licencia="activa")
        EmpresaModulo.objects.create(empresa=empresa, modulo=self.module)
        self.usuario.empresas_acceso.add(empresa)
        for route, template in ((self.ROUTES[0], "clinica/configuracion.html"), (self.ROUTES[1], "clinica/catalogo.html"), (self.ROUTES[3], "clinica/catalogo.html")):
            response = self.client.get(self.url(route, empresa=empresa))
            self.assertEqual(response.status_code, 200)
            self.assertTemplateUsed(response, template)

    def test_get_and_head_never_seed_default_professionals_or_configuration_or_audit(self):
        ConfiguracionClinica.objects.all().delete()
        before = self.snapshot()
        with patch.object(views, "_configuracion_clinica") as configuration, patch.object(views, "asegurar_profesionales_agenda_base") as professionals:
            for route in (self.ROUTES[0], self.ROUTES[1], self.ROUTES[3]):
                for method in ("get", "head"):
                    response = getattr(self.client, method)(self.url(route))
                    self.assertEqual(response.status_code, 200)
                    if method == "head":
                        self.assertEqual(response.content, b"")
            configuration.assert_not_called()
            professionals.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_authorized_edit_get_head_only_read_selected_catalog_row_without_seeding_or_mutations(self):
        for actor, route in ((self.admin, self.ROUTES[2]), (self.owner, self.ROUTES[4])):
            self.use_user(actor)
            before = self.snapshot()
            for method in ("get", "head"):
                with CaptureQueriesContext(connection) as queries:
                    response = getattr(self.client, method)(self.url(route))
                self.assertEqual(response.status_code, 200)
                self.assertIn("no-store", response["Cache-Control"])
                self.no_catalog_queries(queries, exclude=(ProfesionalSalud,) if route == self.ROUTES[2] else (ServicioClinico,))
            self.assertEqual(self.snapshot(), before)

    def test_professional_form_five_canonical_fields_are_rendered_once_without_usuario(self):
        self.assertEqual(tuple(ProfesionalSaludForm.base_fields), self.PROFESSIONAL_FIELDS)
        self.use_user(self.admin)
        for route in (self.ROUTES[1], self.ROUTES[2]):
            page = self.client.get(self.url(route))
            self.assertIsInstance(page.context["form"], ProfesionalSaludForm)
            names = Counter(ConfigurationControlNames(page.content.decode()).names)
            self.assertEqual(set(names), set(self.PROFESSIONAL_FIELDS) | {"app", "csrfmiddlewaretoken"})
            self.assertTrue(all(count == 1 for count in names.values()))

    def test_service_form_seven_canonical_fields_and_category_choices_are_unchanged(self):
        self.assertEqual(tuple(ServicioClinicoForm.base_fields), self.SERVICE_FIELDS)
        self.use_user(self.owner)
        for route in (self.ROUTES[3], self.ROUTES[4]):
            page = self.client.get(self.url(route))
            self.assertIsInstance(page.context["form"], ServicioClinicoForm)
            self.assertEqual(list(page.context["form"].fields["categoria"].choices), list(ServicioClinicoForm(empresa=self.empresa).fields["categoria"].choices))
            names = Counter(ConfigurationControlNames(page.content.decode()).names)
            self.assertEqual(set(names), set(self.SERVICE_FIELDS) | {"app", "csrfmiddlewaretoken"})
            self.assertTrue(all(count == 1 for count in names.values()))

    def test_professional_creation_all_four_saves_only_original_fields_and_global_actor_audit(self):
        for empresa in self.empresas.values():
            before = self.business_snapshot()
            data = self.data(nombre=f"Profesional creado {empresa.slug}")
            response = self.client.post(self.url(self.ROUTES[1], empresa=empresa), data)
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.url, self.url(self.ROUTES[1], empresa=empresa))
            saved = ProfesionalSalud.objects.get(empresa=empresa, nombre=data["nombre"])
            for name in self.PROFESSIONAL_FIELDS:
                self.assertEqual(getattr(saved, name), True if name == "activo" else data[name])
            self.assertIsNone(saved.usuario_id)
            self.assertTrue(RegistroAuditoria.objects.filter(modelo="profesionalsalud", objeto_id=str(saved.pk), accion="crear", usuario=self.actor, empresa=empresa).exists())
            self.assertEqual(self.business_snapshot(), before)

    def test_service_creation_all_four_uses_original_values_and_audit_without_other_business_writes(self):
        for empresa in self.empresas.values():
            before = self.business_snapshot()
            data = self.data(service=True, nombre=f"Servicio creado {empresa.slug}")
            response = self.client.post(self.url(self.ROUTES[3], empresa=empresa), data)
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.url, self.url(self.ROUTES[3], empresa=empresa))
            saved = ServicioClinico.objects.get(empresa=empresa, nombre=data["nombre"])
            self.assertEqual((saved.categoria, saved.duracion_minutos, saved.color_calendario, saved.precio_referencia, saved.requiere_consentimiento, saved.activo), ("spa", 45, "#abcdef", Decimal("123.45"), True, True))
            self.assertTrue(RegistroAuditoria.objects.filter(modelo="servicioclinico", objeto_id=str(saved.pk), accion="crear", usuario=self.actor, empresa=empresa).exists())
            self.assertEqual(self.business_snapshot(), before)

    def test_professional_edit_admin_all_four_preserves_hidden_user_and_company(self):
        self.use_user(self.admin)
        actor = Usuario.objects.create_user(username="configuration-multi-actor", empresa=self.empresas["medical_spa"])
        for empresa in self.empresas.values():
            professional = self.profesionales[empresa.slug]
            ProfesionalSalud.objects.filter(pk=professional.pk).update(usuario=actor)
            before = self.business_snapshot()
            self.assertEqual(self.client.post(self.url(self.ROUTES[2], empresa=empresa), self.data()).status_code, 302)
            saved = ProfesionalSalud.objects.get(pk=professional.pk)
            self.assertEqual((saved.empresa_id, saved.usuario_id, saved.nombre), (empresa.pk, actor.pk, self.data()["nombre"]))
            self.assertEqual(self.business_snapshot(), before)

    def test_superuser_may_edit_professional_but_is_not_implicitly_erp_service_owner(self):
        self.use_user(self.supervisor)
        self.assertEqual(self.client.get(self.url(self.ROUTES[2])).status_code, 200)
        self.assertEqual(self.client.post(self.url(self.ROUTES[2]), self.data()).status_code, 302)
        self.denied(lambda: self.direct(self.ROUTES[4]))
        self.denied(lambda: self.direct(self.ROUTES[5], method="post", data={"app": "1"}))

    def test_service_owner_can_edit_only_hospital_without_expanding_other_three_permissions(self):
        self.use_user(self.owner)
        self.assertEqual(self.client.get(self.url(self.ROUTES[4])).status_code, 200)
        self.assertEqual(self.client.post(self.url(self.ROUTES[4]), self.data(service=True)).status_code, 302)
        for slug in self.SLUGS[1:]:
            for route in (self.ROUTES[4], self.ROUTES[5]):
                self.denied(lambda: self.direct(route, method="post", empresa=self.empresas[slug]))
        self.assertFalse(ServicioClinico.objects.exclude(empresa=self.empresa).filter(nombre=self.data(service=True)["nombre"]).exists())

    def test_nonadmin_professional_and_nonowner_service_actions_hidden_and_blocked_before_save(self):
        before = self.snapshot()
        for route in (self.ROUTES[1], self.ROUTES[3]):
            page = self.client.get(self.url(route))
            edit_route = self.ROUTES[2] if route == self.ROUTES[1] else self.ROUTES[4]
            self.assertNotContains(page, self.url(edit_route))
        for route in (self.ROUTES[2], self.ROUTES[4], self.ROUTES[5]):
            self.denied(lambda: self.direct(route, method="post"))
        self.assertEqual(self.snapshot(), before)

    def test_company_admin_without_erp_owner_identity_cannot_edit_or_delete_services(self):
        self.use_user(self.admin)
        before = self.snapshot()
        self.denied(lambda: self.direct(self.ROUTES[4]))
        self.denied(lambda: self.direct(self.ROUTES[4], method="post"))
        self.denied(lambda: self.direct(self.ROUTES[5], method="post", data={"app": "1"}))
        self.assertEqual(self.snapshot(), before)

    def test_duplicate_professional_and_service_names_do_not_gain_new_uniqueness_rules(self):
        for route, model, record in ((self.ROUTES[1], ProfesionalSalud, self.profesionales[self.empresa.slug]), (self.ROUTES[3], ServicioClinico, self.servicios[self.empresa.slug])):
            response = self.client.post(self.url(route), self.data(service=model is ServicioClinico, nombre=record.nombre))
            self.assertEqual(response.status_code, 302)
            self.assertEqual(model.objects.filter(empresa=self.empresa, nombre=record.nombre).count(), 2)

    def test_original_zero_duration_large_duration_negative_price_and_nonhex_color_are_not_newly_restricted(self):
        for index, (duration, color) in enumerate((("0", "abc"), ("721", ""))):
            data = self.data(service=True, nombre=f"Reglas originales servicio {index}", duracion_minutos=duration, precio_referencia="-12.25", color_calendario=color)
            response = self.client.post(self.url(self.ROUTES[3]), data)
            self.assertEqual(response.status_code, 302)
            saved = ServicioClinico.objects.get(nombre=data["nombre"], empresa=self.empresa)
            self.assertEqual((saved.duracion_minutos, saved.precio_referencia, saved.color_calendario), (int(duration), Decimal("-12.25"), color))

    def test_invalid_professional_create_and_edit_preserve_all_fields_without_other_catalog_queries(self):
        self.use_user(self.admin)
        data = self.data(nombre="", especialidad="Especialidad conservada", colegiacion="Colegiación conservada", telefono="Telefono conservado")
        before = self.snapshot()
        for route in (self.ROUTES[1], self.ROUTES[2]):
            with CaptureQueriesContext(connection) as queries:
                response = self.client.post(self.url(route), data)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context["form"].is_bound)
            self.assertTrue(response.context["form"].errors)
            for field in self.PROFESSIONAL_FIELDS:
                self.assertEqual(response.context["form"].data[field], data[field])
            self.no_catalog_queries(queries, exclude=(ProfesionalSalud,) if route == self.ROUTES[2] else ())
        self.assertEqual(self.snapshot(), before)

    def test_invalid_service_create_and_edit_preserve_seven_fields_without_professional_or_history_queries(self):
        self.use_user(self.owner)
        data = self.data(service=True, categoria="invalid-category", duracion_minutos="-1", color_calendario="nothex", precio_referencia="-5.00")
        before = self.snapshot()
        for route in (self.ROUTES[3], self.ROUTES[4]):
            with CaptureQueriesContext(connection) as queries:
                response = self.client.post(self.url(route), data)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context["form"].errors)
            for field in self.SERVICE_FIELDS:
                self.assertEqual(response.context["form"].data[field], data[field])
            self.no_catalog_queries(queries, exclude=(ServicioClinico,) if route == self.ROUTES[4] else ())
        self.assertEqual(self.snapshot(), before)

    def test_original_decimal_and_duration_validation_reject_invalid_values_without_server_errors(self):
        before = self.snapshot()
        for field, values in (("precio_referencia", ("NaN", "Infinity", "1e100", "abc")), ("duracion_minutos", ("-1", "abc", "1.5"))):
            for value in values:
                response = self.client.post(self.url(self.ROUTES[3]), self.data(service=True, **{field: value}))
                self.assertEqual(response.status_code, 200)
                self.assertIn(field, response.context["form"].errors)
                self.assertEqual(response.context["form"].data[field], value)
        self.assertEqual(self.snapshot(), before)

    def test_color_widget_fallback_is_mobile_only_and_preserves_original_char_validation(self):
        self.use_user(self.owner)
        for value in ("nonhex", ""):
            ServicioClinico.objects.filter(pk=self.servicios[self.empresa.slug].pk).update(color_calendario=value)
            app = self.client.get(self.url(self.ROUTES[4]))
            web = self.client.get(self.url(self.ROUTES[4], mobile=False))
            self.assertEqual(app.context["form"]["color_calendario"].value(), value)
            self.assertEqual(app.context["form"].fields["color_calendario"].widget.input_type, "text")
            self.assertEqual(web.context["form"].fields["color_calendario"].widget.input_type, "color")
            inputs = [attributes for tag, attributes in ConfigurationControlNames(app.content.decode()).controls if tag == "input" and dict(attributes).get("name") == "color_calendario"]
            self.assertEqual(len(inputs), 1)
            self.assertEqual([item for key, item in inputs[0] if key == "type"], ["text"])
            self.assertEqual(dict(inputs[0]).get("value", ""), value)
        ServicioClinico.objects.filter(pk=self.servicios[self.empresa.slug].pk).update(color_calendario="#abcdef")
        app = self.client.get(self.url(self.ROUTES[4]))
        self.assertEqual(app.context["form"].fields["color_calendario"].widget.input_type, "color")
        inputs = [attributes for tag, attributes in ConfigurationControlNames(app.content.decode()).controls if tag == "input" and dict(attributes).get("name") == "color_calendario"]
        self.assertEqual([item for key, item in inputs[0] if key == "type"], ["color"])
        self.assertEqual(dict(inputs[0]).get("value"), "#abcdef")

    def test_edit_audit_records_actual_actor_old_new_company_time_and_original_route(self):
        for actor, route, model, original in ((self.admin, self.ROUTES[2], ProfesionalSalud, self.profesionales[self.empresa.slug]), (self.owner, self.ROUTES[4], ServicioClinico, self.servicios[self.empresa.slug])):
            self.use_user(actor)
            start = timezone.now()
            data = self.data(service=model is ServicioClinico)
            self.assertEqual(self.client.post(self.url(route), data).status_code, 302)
            audit = RegistroAuditoria.objects.get(modelo=model._meta.model_name, objeto_id=str(original.pk), accion="modificar")
            self.assertEqual((audit.usuario_id, audit.empresa_id, audit.metodo_http), (actor.pk, self.empresa.pk, "POST"))
            self.assertGreaterEqual(audit.fecha, start)
            self.assertEqual(audit.cambios["nombre"], {"anterior": original.nombre, "nuevo": data["nombre"]})
            self.assertEqual(audit.ruta, urlsplit(self.url(route)).path)

    def test_unflagged_web_creation_and_edit_keep_original_templates_saves_and_redirects(self):
        for route, model in ((self.ROUTES[1], ProfesionalSalud), (self.ROUTES[3], ServicioClinico)):
            data = self.data(service=model is ServicioClinico, nombre=f"Creación web {route}")
            data.pop("app")
            response = self.client.post(self.url(route, mobile=False), data)
            self.assertEqual(response.url, self.url(route, mobile=False))
            self.assertTrue(model.objects.filter(empresa=self.empresa, nombre=data["nombre"]).exists())
        for actor, route, list_route, model in ((self.admin, self.ROUTES[2], self.ROUTES[1], ProfesionalSalud), (self.owner, self.ROUTES[4], self.ROUTES[3], ServicioClinico)):
            self.use_user(actor)
            response = self.client.get(self.url(route, mobile=False))
            self.assertTemplateUsed(response, "clinica/catalogo.html")
            data = self.data(service=model is ServicioClinico)
            data.pop("app")
            self.assertEqual(self.client.post(self.url(route, mobile=False), data).url, self.url(list_route, mobile=False))

    def test_unflagged_web_delete_keeps_original_post_without_new_confirmation_metadata(self):
        self.use_user(self.owner)
        before = self.business_snapshot()
        response = self.client.post(self.url(self.ROUTES[5], mobile=False), {})
        self.assertEqual(response.url, self.url(self.ROUTES[3], mobile=False))
        self.assertFalse(ServicioClinico.objects.filter(pk=self.servicios[self.empresa.slug].pk).exists())
        self.assertEqual(self.business_snapshot(), before)

    def test_professional_search_and_state_filter_apply_to_all_rows_before_server_pagination(self):
        for index in range(23):
            ProfesionalSalud.objects.create(empresa=self.empresa, nombre=f"Filtro profesional {index:02d}", especialidad="Especialidad buscada", activo=True)
        for index in range(21):
            ProfesionalSalud.objects.create(empresa=self.empresa, nombre=f"Otro profesional {index:02d}", activo=False)
        response = self.client.get(self.url(self.ROUTES[1], q="Filtro profesional", estado="activos", page="2"))
        self.assertEqual(response.context["items"].paginator.per_page, 20)
        self.assertEqual(response.context["items"].paginator.count, 23)
        self.assertEqual(len(response.context["items"]), 3)
        self.assertNotContains(response, self.profesionales["medical_spa"].nombre)
        self.assertNotContains(response, "<table")

    def test_services_stable_pagination_filter_links_preserve_app_query_and_state(self):
        records = [ServicioClinico.objects.create(empresa=self.empresa, nombre="Servicio empate", categoria="spa", activo=True) for _ in range(23)]
        response = self.client.get(self.url(self.ROUTES[3], q="Servicio empate", estado="activos"))
        self.assertEqual([item.pk for item in response.context["items"]], [record.pk for record in records[:20]])
        next_url = response.context["mobile_catalog_next_url"]
        self.assertEqual(parse_qs(urlsplit(next_url).query), {"app": ["1"], "q": ["Servicio empate"], "estado": ["activos"], "page": ["2"]})
        page2 = self.client.get(next_url)
        self.assertEqual([item.pk for item in page2.context["items"]], [record.pk for record in records[20:]])

    def test_foreign_record_ids_canonical_overflow_and_missing_ids_deny_before_edit_or_delete(self):
        self.use_user(self.admin)
        self.denied(lambda: self.direct(self.ROUTES[2], record=self.profesionales["medical_spa"]))
        self.use_user(self.owner)
        self.denied(lambda: self.direct(self.ROUTES[4], record=self.servicios["medical_spa"]))
        for route in (self.ROUTES[2], self.ROUTES[4], self.ROUTES[5]):
            self.use_user(self.admin if route == self.ROUTES[2] else self.owner)
            target = self.url(route)
            record = self.profesionales[self.empresa.slug] if route == self.ROUTES[2] else self.servicios[self.empresa.slug]
            for value in ("0", "00"+str(record.pk), "9223372036854775808", "2147483647"):
                malformed = target.replace(f"/{record.pk}/", f"/{value}/")
                self.denied(lambda: self.direct(route, method="post" if route == self.ROUTES[5] else "get", url=malformed))

    def test_closed_query_and_duplicate_filters_or_pages_cannot_enable_pc_fallback(self):
        for route in self.ROUTES[:-1]:
            self.use_user(self.admin if route == self.ROUTES[2] else self.owner if route == self.ROUTES[4] else self.usuario)
            before = self.snapshot()
            for query in ("app=1&app=1", "app=1&next=/", "app=1&empresa=1", "app=1&accion=delete"):
                self.denied(lambda: self.direct(route, url=self.url(route).split("?")[0]+"?"+query))
            self.assertEqual(self.snapshot(), before)
        before = self.snapshot()
        for route in (self.ROUTES[1], self.ROUTES[3]):
            for query in ("app=1&q=a&q=b", "app=1&estado=unknown", "app=1&page=1&page=1"):
                self.denied(lambda: self.direct(route, url=self.url(route).split("?")[0]+"?"+query))
            for page in ("", "0", "01", "-1", "1e2", "١", "9223372036854775808"):
                self.denied(lambda: self.direct(route, url=self.url(route, page=page)))
        self.assertEqual(self.snapshot(), before)

    def test_duplicate_form_fields_unknown_hidden_financial_fields_files_and_mismatched_app_are_denied(self):
        for route in (self.ROUTES[1], self.ROUTES[2], self.ROUTES[3], self.ROUTES[4]):
            self.use_user(self.admin if route == self.ROUTES[2] else self.owner if route == self.ROUTES[4] else self.usuario)
            before = self.snapshot()
            service = route in (self.ROUTES[3], self.ROUTES[4])
            fields = self.SERVICE_FIELDS if service else self.PROFESSIONAL_FIELDS
            for key in ("app", *fields):
                data = QueryDict(mutable=True)
                data.update(self.data(service=service))
                data.setlist(key, [data[key], data[key]])
                self.denied(lambda: self.direct(route, method="post", data=data))
            for key in ("usuario", "empresa", "creado_por", "profesional_id", "servicio_id", "next", "accion", "initial-nombre"):
                self.denied(lambda: self.direct(route, method="post", data=self.data(service=service, **{key: "1"})))
            self.denied(lambda: self.direct(route, method="post", data=self.data(service=service, archivo=SimpleUploadedFile("unexpected.txt", b"unexpected"))))
            self.denied(lambda: self.direct(route, method="post", url=self.url(route, app="0")))
            self.assertEqual(self.snapshot(), before)

    def test_methods_configuration_post_and_catalogue_other_verbs_are_denied_without_writes(self):
        before = self.snapshot()
        self.denied(lambda: self.direct(self.ROUTES[0], method="post"))
        for route in (self.ROUTES[1], self.ROUTES[3]):
            for method in ("put", "patch", "delete"):
                self.denied(lambda: self.direct(route, method=method))
        self.denied(lambda: self.direct(self.ROUTES[5]))
        self.assertEqual(self.snapshot(), before)

    def test_nullable_professional_user_orphan_denied_before_filtered_list_and_edit_but_multicompany_user_is_valid(self):
        professional = self.profesionales[self.empresa.slug]
        self.use_user(self.admin)
        with connection.constraint_checks_disabled():
            try:
                ProfesionalSalud.objects.filter(pk=professional.pk).update(usuario_id=2147483647)
                before = self.snapshot()
                self.denied(lambda: self.direct(self.ROUTES[1], url=self.url(self.ROUTES[1], q="No coincide", page="100")))
                self.denied(lambda: self.direct(self.ROUTES[2]))
                self.denied(lambda: self.direct(self.ROUTES[2], method="post"))
                self.assertEqual(self.snapshot(), before)
            finally:
                ProfesionalSalud.objects.filter(pk=professional.pk).update(usuario=None)
        actor = Usuario.objects.create_user(username="configuration-foreign-primary", empresa=self.empresas["medical_spa"])
        ProfesionalSalud.objects.filter(pk=professional.pk).update(usuario=actor, activo=False)
        before = self.snapshot()
        self.assertEqual(self.client.get(self.url(self.ROUTES[1], estado="inactivos")).status_code, 200)
        self.assertEqual(self.client.get(self.url(self.ROUTES[2])).status_code, 200)
        self.assertEqual(self.snapshot(), before)

    def test_service_delete_accepts_no_extra_metadata_duplicate_app_or_mismatched_query(self):
        self.use_user(self.owner)
        before = self.snapshot()
        for data in ({"app": "1", "confirmacion": "ELIMINAR"}, {"app": "1", "empresa": "1"}, {"app": "1", "nombre": "No debe cambiar"}, {"app": "1", "next": "/"}):
            self.denied(lambda: self.direct(self.ROUTES[5], method="post", data=data))
        data = QueryDict(mutable=True)
        data.setlist("app", ["1", "1"])
        self.denied(lambda: self.direct(self.ROUTES[5], method="post", data=data))
        self.denied(lambda: self.direct(self.ROUTES[5], method="post", data={"app": "1"}, url=self.url(self.ROUTES[5], app="0")))
        self.assertEqual(self.snapshot(), before)

    def related_records(self, empresa=None):
        empresa = empresa or self.empresa
        patient = Paciente.objects.create(empresa=empresa, nombre=f"Paciente configuración sintético {empresa.slug}", identidad=f"CFGP-{empresa.pk}")
        service = self.servicios[self.empresa.slug]
        date = timezone.now()+timedelta(days=7)
        clinical = CitaClinica.objects.create(empresa=empresa, paciente=patient, servicio=service, fecha_hora=date, motivo="Motivo sintético configuración")
        agenda = CitaCliente.objects.create(empresa=empresa, paciente=patient, servicio_clinico=service, titulo="Agenda sintética configuración", fecha_hora=date)
        treatment = TratamientoPaciente.objects.create(empresa=empresa, paciente=patient, servicio=service, nombre="Tratamiento sintético configuración")
        return clinical, agenda, treatment

    def test_confirmed_service_delete_reuses_original_delete_preserves_history_setnull_and_global_audit(self):
        self.use_user(self.owner)
        clinical, agenda, treatment = self.related_records()
        original = self.servicios[self.empresa.slug]
        original_rows = [list(model.objects.filter(pk=record.pk).values())[0] for model, record in ((CitaClinica, clinical), (CitaCliente, agenda), (TratamientoPaciente, treatment))]
        response = self.client.post(self.url(self.ROUTES[5]), {"app": "1"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, self.url(self.ROUTES[3]))
        self.assertFalse(ServicioClinico.objects.filter(pk=original.pk).exists())
        for (model, record, field), previous in zip(((CitaClinica, clinical, "servicio_id"), (CitaCliente, agenda, "servicio_clinico_id"), (TratamientoPaciente, treatment, "servicio_id")), original_rows):
            current = model.objects.filter(pk=record.pk).values().get()
            previous[field] = None
            self.assertEqual(current, previous)
        audit = RegistroAuditoria.objects.get(modelo="servicioclinico", objeto_id=str(original.pk), accion="eliminar")
        self.assertEqual((audit.usuario_id, audit.empresa_id, audit.metodo_http), (self.owner.pk, self.empresa.pk, "POST"))
        self.assertEqual(audit.cambios["registro_eliminado"]["anterior"]["nombre"], original.nombre)

    def test_foreign_imported_reverse_relations_are_denied_before_service_delete_or_any_foreign_setnull(self):
        self.use_user(self.owner)
        clinical, agenda, treatment = self.related_records()
        for model, record in ((CitaClinica, clinical), (CitaCliente, agenda), (TratamientoPaciente, treatment)):
            for company_id in (self.empresas["medical_spa"].pk, 2147483647):
                with connection.constraint_checks_disabled():
                    try:
                        model.objects.filter(pk=record.pk).update(empresa_id=company_id)
                        before = self.snapshot()
                        with patch.object(ServicioClinico, "delete") as delete:
                            self.denied(lambda: self.direct(self.ROUTES[5], method="post", data={"app": "1"}))
                            delete.assert_not_called()
                        self.assertEqual(self.snapshot(), before)
                    finally:
                        model.objects.filter(pk=record.pk).update(empresa=self.empresa)

    def test_mobile_module_configuration_card_respects_original_menu_and_uses_mobile_only_url(self):
        from crm.mobile_modules import construir_contexto_modulos_app
        ConfiguracionAvanzadaEmpresa.objects.create(empresa=self.empresa, manuales_pdf_habilitados=True)
        for granular, permissions in ((True, {"puede_configuracion_clinica": True}), (True, {"puede_administrar_manuales_pdf": True}), (False, {})):
            self.role(granular=granular, **permissions)
            request = RequestFactory().get("/")
            request.user = Usuario.objects.get(pk=self.actor.pk)
            with CaptureQueriesContext(connection) as queries:
                context = construir_contexto_modulos_app(request, self.empresa)
            self.no_catalog_queries(queries)
            cards = {item["key"]: item for item in context["modulos_app"]}
            if permissions:
                self.assertEqual((cards["configuracion"]["estado"], cards["configuracion"]["url"]), ("parcial", self.url()))
            else:
                self.assertNotIn("configuracion", cards)
            hub = self.client.get(self.url())
            self.assertEqual(hub.status_code, 200)
            for route in (self.ROUTES[1], self.ROUTES[3]):
                if permissions.get("puede_configuracion_clinica"):
                    self.assertContains(hub, self.url(route))
                else:
                    self.assertNotContains(hub, self.url(route))

    def test_configuration_hub_pending_administration_cards_have_no_desktop_fallback_actions(self):
        self.use_user(self.supervisor)
        billing, _ = Modulo.objects.get_or_create(codigo="facturacion", defaults={"nombre": "Facturación"})
        EmpresaModulo.objects.create(empresa=self.empresa, modulo=billing)
        before = self.snapshot()
        response = self.client.get(self.url())
        items = {item["codigo"]: item for item in response.context["mobile_configuration_items"]}
        self.assertEqual(items["usuarios_roles"]["estado"], "pendiente")
        self.assertEqual(items["usuarios_roles"]["url"], "")
        self.assertEqual(items["configuracion_facturacion"]["estado"], "pendiente")
        self.assertEqual(items["configuracion_facturacion"]["url"], "")
        for item in items.values():
            if item["estado"] == "pendiente":
                self.assertEqual(item["url"], "")
            else:
                self.assertEqual(parse_qs(urlsplit(item["url"]).query), {"app": ["1"]})
        self.assertEqual(self.snapshot(), before)

    def test_csrf_and_logout_keep_original_protection_for_catalogue_writes(self):
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(self.usuario)
        before = self.snapshot()
        self.assertEqual(protected.get(self.url(self.ROUTES[1])).status_code, 200)
        rejected = protected.post(self.url(self.ROUTES[1]), self.data())
        self.assertEqual(rejected.status_code, 302)
        self.assertEqual(rejected.url, reverse("empresa_login", kwargs={"slug": self.empresa.slug}))
        self.assertEqual(self.snapshot(), before)
        accepted = protected.post(self.url(self.ROUTES[1]), self.data(), HTTP_X_CSRFTOKEN=protected.cookies["csrftoken"].value)
        self.assertEqual((accepted.status_code, accepted.url), (302, self.url(self.ROUTES[1])))
        self.assertEqual(ProfesionalSalud.objects.filter(empresa=self.empresa, nombre=self.data()["nombre"]).count(), 1)
        self.client.logout()
        self.assertEqual(self.client.get(self.url()).status_code, 302)

    def test_escaped_catalogue_text_and_other_company_search_do_not_leak_or_execute(self):
        ProfesionalSalud.objects.filter(pk=self.profesionales[self.empresa.slug].pk).update(nombre="<script>config_synthetic()</script>")
        response = self.client.get(self.url(self.ROUTES[1]))
        self.assertContains(response, "&lt;script&gt;config_synthetic()&lt;/script&gt;")
        self.assertNotContains(response, "<script>config_synthetic()</script>")
        page = self.client.get(self.url(self.ROUTES[3], q=self.servicios["medical_spa"].nombre))
        self.assertEqual(len(page.context["items"]), 0)
        self.assertEqual(page.context["mobile_catalog_total"], 0)
        self.assertEqual(page.context["mobile_catalog_items"], [])
        self.assertNotContains(page, self.url(self.ROUTES[4], empresa=self.empresas["medical_spa"]))
