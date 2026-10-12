"""Treatments adapt the canonical ten-field form, without creating plans or appointments."""
from datetime import timedelta
from decimal import Decimal
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlencode, urlsplit
from unittest.mock import patch

from django.core.exceptions import PermissionDenied
from django.db import connection
from django.http import Http404, QueryDict
from django.test import Client, RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.models import ConfiguracionAvanzadaEmpresa, Empresa, EmpresaModulo, Modulo, RegistroAuditoria, RolSistema, Usuario, UsuarioEmpresaPermiso
from crm.models import CitaCliente, ConfiguracionCRM, NotificacionCitaWhatsApp, OpcionServicioAgenda
from facturacion.models import Cliente, Factura, MovimientoInventario, MovimientoLoteBodega, PagoFactura, Producto
from . import views
from .forms import TratamientoPacienteForm
from .models import CitaClinica, ConfiguracionClinica, ExpedienteEvento, HistoriaClinicaEspecialidad, Paciente, PlanTratamientoPaciente, PreconsultaClinica, ProfesionalSalud, RecetaMedica, ServicioClinico, TratamientoPaciente


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class MobileTreatmentsIntegrationTests(TestCase):
    SLUGS = ("hospital_mia", "medical_spa", "luque_aestetic", "serviciosmedicos")
    FORM_FIELDS = ("paciente", "servicio", "profesional", "nombre", "fecha_inicio", "fecha_fin_estimada", "estado", "objetivo", "plan_clinico", "monto_estimado")

    @classmethod
    def setUpTestData(cls):
        cls.modulo, _ = Modulo.objects.get_or_create(codigo="clinica_medica", defaults={"nombre": "Clínica"})
        cls.empresas, cls.pacientes, cls.profesionales, cls.servicios, cls.tratamientos = {}, {}, {}, {}, {}
        for index, slug in enumerate(cls.SLUGS, 1):
            empresa = Empresa.objects.create(nombre=f"Tratamientos QA {slug}", slug=slug, rtn=f"MTREAT-{index}", tipo_solucion="clinica", estado_licencia="activa")
            cls.empresas[slug] = empresa
            EmpresaModulo.objects.create(empresa=empresa, modulo=cls.modulo)
            for active in (True, False):
                cls.pacientes[(slug, active)] = Paciente.objects.create(empresa=empresa, nombre=f"Paciente tratamiento {slug} {'activo' if active else 'inactivo'}", expediente_codigo=f"MTREAT-{index}-{active}", activo=active)
                cls.profesionales[(slug, active)] = ProfesionalSalud.objects.create(empresa=empresa, nombre=f"Personal tratamiento {slug} {active}", activo=active)
                cls.servicios[(slug, active)] = ServicioClinico.objects.create(empresa=empresa, nombre=f"Servicio tratamiento {slug} {active}", categoria="tratamiento", duracion_minutos=30, activo=active)
            for estado, _ in TratamientoPaciente.ESTADO_CHOICES:
                cls.tratamientos[(slug, estado)] = TratamientoPaciente.objects.create(empresa=empresa, paciente=cls.pacientes[(slug, True)], servicio=cls.servicios[(slug, True)], profesional=cls.profesionales[(slug, True)], nombre=f"Tratamiento {slug} {estado}", estado=estado, fecha_inicio="2026-10-01", monto_estimado=Decimal("150.50"), objetivo=f"Objetivo sintético {slug}", plan_clinico="Texto de tratamiento original")
        cls.empresa = cls.empresas["hospital_mia"]
        cls.rol = RolSistema.objects.create(nombre="Tratamientos móviles", codigo="mobile-treatment-full", es_rol_clinico=True, usa_permisos_clinicos_granulares=True, puede_ver_planes_tratamiento=True, puede_editar_planes_tratamiento=True)
        cls.usuario = Usuario.objects.create_user(username="mobile-treatments", password="test-only", empresa=cls.empresa, rol_sistema=cls.rol)
        cls.usuario.empresas_acceso.add(*cls.empresas.values())

    def setUp(self):
        self.client.force_login(self.usuario)
        for target in ("clinica.views.EmailMultiAlternatives", "clinica.views.enviar_documento_whatsapp", "clinica.views.enviar_plantilla_preconsulta_whatsapp", "clinica.views.subir_documento_whatsapp"):
            patcher = patch(target)
            transport = patcher.start()
            self.addCleanup(patcher.stop)
            self.addCleanup(transport.assert_not_called)

    def asignar_rol(self, empresa=None, granular=True, **permissions):
        rol = RolSistema.objects.create(nombre="Tratamientos restringidos", codigo=f"mobile-treatment-{RolSistema.objects.count()}", es_rol_clinico=granular, usa_permisos_clinicos_granulares=granular, **permissions)
        UsuarioEmpresaPermiso.objects.update_or_create(usuario=self.usuario, empresa=empresa or self.empresa, defaults={"rol_sistema": rol})
        return rol

    def url(self, empresa=None, create=False, mobile=True, **parameters):
        empresa = empresa or self.empresa
        query = {"app": "1"} if mobile else {}
        query.update(parameters)
        route = reverse("clinica_crear_tratamiento" if create else "clinica_tratamientos", args=[empresa.slug])
        return route+("?"+urlencode(query) if query else "")

    def datos(self, empresa=None, **extra):
        empresa = empresa or self.empresa
        data = {
            "app": "1", "paciente": str(self.pacientes[(empresa.slug, True)].pk),
            "servicio": str(self.servicios[(empresa.slug, True)].pk), "profesional": str(self.profesionales[(empresa.slug, True)].pk),
            "nombre": "Tratamiento capturado móvil", "fecha_inicio": "2026-10-12", "fecha_fin_estimada": "2026-11-02",
            "estado": "en_proceso", "objetivo": "Objetivo escrito conservado", "plan_clinico": "Texto de trabajo conservado", "monto_estimado": "123.45",
        }
        data.update(extra)
        return data

    def side_counts(self):
        return tuple(model.objects.count() for model in (Paciente, ProfesionalSalud, ServicioClinico, PlanTratamientoPaciente, CitaClinica, CitaCliente, OpcionServicioAgenda, NotificacionCitaWhatsApp, ExpedienteEvento, HistoriaClinicaEspecialidad, PreconsultaClinica, RecetaMedica, Cliente, Producto, Factura, PagoFactura, MovimientoInventario, MovimientoLoteBodega, ConfiguracionClinica, ConfiguracionCRM, ConfiguracionAvanzadaEmpresa))

    def snapshot(self):
        return (list(TratamientoPaciente.objects.order_by("pk").values()), self.side_counts(), RegistroAuditoria.objects.count())

    def directo(self, empresa=None, create=False, method="get", data=None, **parameters):
        empresa = empresa or self.empresa
        factory = RequestFactory()
        url = self.url(empresa, create=create, **parameters)
        request = factory.post(url, self.datos(empresa) if data is None else data) if method == "post" else factory.generic(method.upper(), url)
        request.user = Usuario.objects.get(pk=self.usuario.pk)
        view = views.crear_tratamiento if create else views.tratamientos
        return view(request, empresa_slug=empresa.slug)

    def assert_no_history_queries(self, queries):
        for model in (TratamientoPaciente, PlanTratamientoPaciente, PreconsultaClinica, HistoriaClinicaEspecialidad, ExpedienteEvento):
            self.assertFalse(any(f'"{model._meta.db_table}"' in query["sql"] and query["sql"].lstrip().upper().startswith("SELECT") for query in queries), model._meta.label)

    def test_four_company_opt_in_templates_and_passive_get_do_not_seed_or_write(self):
        before = self.snapshot()
        with patch.object(views, "asegurar_profesionales_agenda_base") as seed:
            for empresa in self.empresas.values():
                for create, template in ((False, "clinica/mobile_treatment_list.html"), (True, "clinica/mobile_treatment_form.html")):
                    response = self.client.get(self.url(empresa, create=create))
                    self.assertEqual(response.status_code, 200)
                    self.assertTemplateUsed(response, template)
                    self.assertIn("no-store", response["Cache-Control"])
                    self.assertNotContains(response, "<table")
                seed.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_list_is_scoped_by_company_and_searches_own_treatments(self):
        for empresa in self.empresas.values():
            response = self.client.get(self.url(empresa))
            rows = list(response.context["tratamientos"])
            self.assertEqual(len(rows), 5)
            self.assertTrue(all(row.empresa_id == empresa.pk for row in rows))
            for other in self.empresas.values():
                if other.pk != empresa.pk:
                    self.assertNotContains(response, self.pacientes[(other.slug, True)].nombre)
            result = self.client.get(self.url(empresa, q="completado"))
            self.assertEqual([row.pk for row in result.context["tratamientos"]], [self.tratamientos[(empresa.slug, "completado")].pk])

    def test_state_filters_reuse_all_original_state_choices(self):
        for estado, _ in TratamientoPaciente.ESTADO_CHOICES:
            response = self.client.get(self.url(estado=estado))
            self.assertEqual(response.status_code, 200)
            self.assertEqual([row.estado for row in response.context["tratamientos"]], [estado])
        response = self.client.get(self.url(estado=""))
        self.assertEqual(len(response.context["tratamientos"]), 5)

    def test_server_pagination_covers_all_rows_once_with_stable_tie_order(self):
        records = [TratamientoPaciente.objects.create(empresa=self.empresa, paciente=self.pacientes[(self.empresa.slug, True)], nombre="Empate de tratamiento", fecha_inicio="2026-12-01") for _ in range(27)]
        before = self.snapshot()
        page1 = self.client.get(self.url(page="1"))
        page2 = self.client.get(self.url(page="2"))
        ids1 = [row.pk for row in page1.context["tratamientos"]]
        ids2 = [row.pk for row in page2.context["tratamientos"]]
        self.assertEqual(len(ids1), 20)
        self.assertEqual(len(ids2), 12)
        self.assertEqual(ids1[:20], [record.pk for record in reversed(records)][0:20])
        self.assertEqual(len(set(ids1+ids2)), 32)
        self.assertEqual(set(ids1+ids2), set(TratamientoPaciente.objects.filter(empresa=self.empresa).values_list("pk", flat=True)))
        self.assertEqual([row.pk for row in self.client.get(self.url(page="1")).context["tratamientos"]], ids1)
        self.assertEqual(self.snapshot(), before)

    def test_search_and_state_remain_in_pagination_links(self):
        TratamientoPaciente.objects.bulk_create([TratamientoPaciente(empresa=self.empresa, paciente=self.pacientes[(self.empresa.slug, True)], nombre=f"Filtro persistente {index}", estado="pausado") for index in range(23)])
        response = self.client.get(self.url(q="Filtro persistente", estado="pausado"))
        self.assertEqual(response.context["tratamientos"].paginator.count, 23)
        self.assertContains(response, "q=Filtro+persistente")
        self.assertContains(response, "estado=pausado")
        self.assertContains(response, "page=2")

    def test_mobile_form_has_exact_original_ten_fields_widgets_and_scoped_choices(self):
        for empresa in self.empresas.values():
            response = self.client.get(self.url(empresa, create=True))
            original = self.client.get(self.url(empresa, create=True, mobile=False))
            form, web_form = response.context["form"], original.context["form"]
            self.assertIsInstance(form, TratamientoPacienteForm)
            self.assertEqual(tuple(form.fields), self.FORM_FIELDS)
            self.assertEqual(tuple(form.fields), tuple(web_form.fields))
            for name, field in form.fields.items():
                self.assertContains(response, f'name="{name}"', count=1)
                self.assertIs(type(field.widget), type(web_form.fields[name].widget))
                if hasattr(field, "choices"):
                    self.assertEqual(list(field.choices), list(web_form.fields[name].choices))
            self.assertEqual(list(form.fields["paciente"].queryset), [self.pacientes[(empresa.slug, True)]])
            self.assertEqual(list(form.fields["servicio"].queryset), [self.servicios[(empresa.slug, True)]])
            self.assertEqual(list(form.fields["profesional"].queryset), [self.profesionales[(empresa.slug, True)]])
            self.assertEqual(form["fecha_inicio"].value(), timezone.localdate())
            self.assertContains(response, 'name="app" value="1"', count=1)

    def test_native_original_hidden_initial_fields_are_accepted_without_new_inputs(self):
        class HiddenInputs(HTMLParser):
            def __init__(self):
                super().__init__()
                self.values = {}

            def handle_starttag(self, tag, attributes):
                values = dict(attributes)
                if tag == "input" and values.get("type") == "hidden" and values.get("name"):
                    self.values[values["name"]] = values.get("value", "")

        response = self.client.get(self.url(create=True))
        parser = HiddenInputs()
        parser.feed(response.content.decode())
        form = response.context["form"]
        expected = {"initial-"+name for name, field in form.fields.items() if field.show_hidden_initial}
        self.assertEqual({name for name in parser.values if name.startswith("initial-")}, expected)
        data = self.datos()
        data.update(parser.values)
        self.assertEqual(self.client.post(self.url(create=True), data).status_code, 302)

    def test_reader_only_list_hides_create_and_blocks_backend_creation_before_data_queries(self):
        self.asignar_rol(puede_ver_planes_tratamiento=True)
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["can_create_treatment"])
        self.assertNotContains(response, self.url(create=True))
        before = self.snapshot()
        for method in ("get", "post"):
            with CaptureQueriesContext(connection) as queries:
                with self.assertRaises((PermissionDenied, Http404)):
                    self.directo(create=True, method=method)
            self.assert_no_history_queries(queries)
            self.assertFalse(any(f'"{Paciente._meta.db_table}"' in query["sql"] for query in queries))
        self.assertEqual(self.snapshot(), before)

    def test_writer_only_get_and_invalid_form_do_not_read_treatment_or_plan_history(self):
        self.asignar_rol(puede_editar_planes_tratamiento=True)
        for method in ("get", "post"):
            with CaptureQueriesContext(connection) as queries:
                response = self.client.get(self.url(create=True)) if method == "get" else self.client.post(self.url(create=True), self.datos(nombre=""))
            self.assertEqual(response.status_code, 200)
            self.assert_no_history_queries(queries)
            self.assertFalse(response.context["can_view_treatments"])
            self.assertNotContains(response, 'rel="manifest"')
        with self.assertRaises((PermissionDenied, Http404)):
            self.directo()

    def test_writer_only_valid_save_returns_authorized_composer_not_patient_or_list(self):
        self.asignar_rol(puede_editar_planes_tratamiento=True)
        response = self.client.post(self.url(create=True), self.datos())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(urlsplit(response.url).path, urlsplit(self.url(create=True)).path)
        self.assertEqual(parse_qs(urlsplit(response.url).query)["app"], ["1"])
        self.assertEqual(self.client.get(response.url).status_code, 200)
        self.assertIn(self.client.get(self.url()).status_code, (302, 403, 404))
        patient = reverse("clinica_paciente_detalle", args=[self.empresa.slug, self.pacientes[(self.empresa.slug, True)].pk])+"?app=1"
        self.assertIn(self.client.get(patient).status_code, (302, 403, 404))

    def test_legacy_treatment_permission_preserves_both_without_extra_legacy_grants(self):
        self.asignar_rol(granular=False, puede_tratamientos_clinicos=True)
        self.assertEqual(self.client.get(self.url()).status_code, 200)
        response = self.client.get(self.url(create=True))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["app_home_disponible"])
        self.assertNotContains(response, 'rel="manifest"')
        self.assertEqual(self.client.post(self.url(create=True), self.datos()).status_code, 302)
        self.asignar_rol(granular=False, puede_clinica=True, puede_pacientes=True, puede_expediente_clinico=True)
        for create in (False, True):
            with self.assertRaises((PermissionDenied, Http404)):
                self.directo(create=create)

    def test_granular_roles_do_not_gain_from_legacy_treatment_flag(self):
        self.asignar_rol(puede_tratamientos_clinicos=True)
        before = self.snapshot()
        for create in (False, True):
            with self.assertRaises((PermissionDenied, Http404)):
                self.directo(create=create)
        self.assertEqual(self.snapshot(), before)

    def test_direct_company_module_membership_active_and_license_guards(self):
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.modulo).update(activo=False)
        for create in (False, True):
            with self.assertRaises((PermissionDenied, Http404)):
                self.directo(create=create)
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.modulo).update(activo=True)
        for extra in ({"activa": False}, {"estado_licencia": "suspendida"}, {"fecha_vencimiento_plan": timezone.localdate()-timedelta(days=1)}):
            Empresa.objects.filter(pk=self.empresa.pk).update(**extra)
            for create in (False, True):
                with self.assertRaises((PermissionDenied, Http404)):
                    self.directo(create=create)
            Empresa.objects.filter(pk=self.empresa.pk).update(activa=True, estado_licencia="activa", fecha_vencimiento_plan=None)
        other = self.empresas["medical_spa"]
        self.usuario.empresas_acceso.remove(other)
        for create in (False, True):
            with self.assertRaises((PermissionDenied, Http404)):
                self.directo(other, create=create)

    def test_other_clinic_app_flag_keeps_original_web_templates(self):
        other = Empresa.objects.create(nombre="Otra clínica sintética", slug="other_mobile_treatments", rtn="MTREAT-OTHER", tipo_solucion="clinica", estado_licencia="activa")
        EmpresaModulo.objects.create(empresa=other, modulo=self.modulo)
        self.usuario.empresas_acceso.add(other)
        for create, template in ((False, "clinica/tratamientos.html"), (True, "clinica/form.html")):
            app = self.client.get(self.url(other, create=create))
            web = self.client.get(self.url(other, create=create, mobile=False))
            self.assertEqual(app.status_code, web.status_code)
            self.assertTemplateUsed(app, template)

    def test_mobile_list_rejects_catalogue_post_even_administrator_without_mutation(self):
        self.usuario.es_administrador_empresa = True
        self.usuario.save(update_fields=["es_administrador_empresa"])
        before = self.snapshot()
        with self.assertRaises((PermissionDenied, Http404)):
            self.directo(method="post", data={"app": "1", "accion": "crear_opcion_agenda", "nombre": "No debe crearse"})
        self.assertIn(self.client.post(self.url(), {"app": "1", "accion": "crear_opcion_agenda", "nombre": "No debe crearse"}).status_code, (403, 405))
        self.assertEqual(self.snapshot(), before)

    def test_original_web_catalogue_post_and_listing_remain_unchanged(self):
        self.usuario.es_administrador_empresa = True
        self.usuario.save(update_fields=["es_administrador_empresa"])
        before = TratamientoPaciente.objects.count()
        response = self.client.post(self.url(mobile=False), {"accion": "crear_opcion_agenda", "nombre": "Tratamiento catálogo web original"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(OpcionServicioAgenda.objects.filter(empresa=self.empresa, nombre="Tratamiento catálogo web original", creado_por=self.usuario).exists())
        self.assertEqual(TratamientoPaciente.objects.count(), before)
        original = self.client.get(self.url(mobile=False))
        self.assertTemplateUsed(original, "clinica/tratamientos.html")
        self.assertContains(original, "Tratamiento catálogo web original")

    def test_valid_original_form_saves_exact_ten_fields_only_one_treatment_per_company(self):
        for empresa in self.empresas.values():
            data = self.datos(empresa)
            before, side = TratamientoPaciente.objects.count(), self.side_counts()
            response = self.client.post(self.url(empresa, create=True), data)
            self.assertEqual(response.status_code, 302)
            self.assertEqual(urlsplit(response.url).path, urlsplit(self.url(empresa)).path)
            self.assertEqual(parse_qs(urlsplit(response.url).query)["app"], ["1"])
            treatment = TratamientoPaciente.objects.get(empresa=empresa, nombre=data["nombre"])
            self.assertEqual((treatment.paciente_id, treatment.servicio_id, treatment.profesional_id), tuple(int(data[name]) for name in ("paciente", "servicio", "profesional")))
            for name in ("nombre", "estado", "objetivo", "plan_clinico"):
                self.assertEqual(getattr(treatment, name), data[name])
            self.assertEqual(treatment.fecha_inicio.isoformat(), data["fecha_inicio"])
            self.assertEqual(treatment.fecha_fin_estimada.isoformat(), data["fecha_fin_estimada"])
            self.assertEqual(treatment.monto_estimado, Decimal(data["monto_estimado"]))
            self.assertEqual(TratamientoPaciente.objects.count(), before+1)
            self.assertEqual(self.side_counts(), side)

    def test_global_audit_records_actual_actor_timestamp_and_original_field_snapshot(self):
        started = timezone.now()
        data = self.datos()
        response = self.client.post(self.url(create=True), data)
        self.assertEqual(response.status_code, 302)
        treatment = TratamientoPaciente.objects.get(empresa=self.empresa, nombre=data["nombre"])
        audit = RegistroAuditoria.objects.get(empresa=self.empresa, modelo=treatment._meta.model_name, objeto_id=str(treatment.pk), accion="crear")
        self.assertEqual(audit.usuario_id, self.usuario.pk)
        self.assertEqual(audit.metodo_http, "POST")
        self.assertGreaterEqual(audit.fecha, started)
        self.assertEqual(audit.cambios["paciente"]["nuevo"], treatment.paciente_id)
        self.assertEqual(audit.cambios["monto_estimado"]["nuevo"], "123.45")
        self.assertEqual(audit.cambios["fecha_inicio"]["nuevo"], data["fecha_inicio"])
        self.assertEqual(audit.cambios["plan_clinico"]["nuevo"], data["plan_clinico"])

    def test_app_and_web_create_same_model_fields_with_original_web_patient_redirect(self):
        data = self.datos()
        self.assertEqual(self.client.post(self.url(create=True), data).status_code, 302)
        app = TratamientoPaciente.objects.get(empresa=self.empresa, nombre=data["nombre"])
        del data["app"]
        data["nombre"] = "Creación web equivalente"
        web = self.client.post(self.url(create=True, mobile=False), data)
        self.assertEqual(web.status_code, 302)
        self.assertEqual(web.url, reverse("clinica_paciente_detalle", args=[self.empresa.slug, int(data["paciente"])]))
        original = TratamientoPaciente.objects.get(empresa=self.empresa, nombre=data["nombre"])
        for name in self.FORM_FIELDS:
            if name != "nombre":
                self.assertEqual(getattr(app, name), getattr(original, name))

    def test_optional_service_professional_end_date_and_zero_amount_follow_original_form(self):
        data = self.datos(servicio="", profesional="", fecha_fin_estimada="", monto_estimado="0")
        self.assertTrue(TratamientoPacienteForm(data, empresa=self.empresa).is_valid())
        self.assertEqual(self.client.post(self.url(create=True), data).status_code, 302)
        treatment = TratamientoPaciente.objects.get(empresa=self.empresa, nombre=data["nombre"])
        self.assertIsNone(treatment.servicio_id)
        self.assertIsNone(treatment.profesional_id)
        self.assertIsNone(treatment.fecha_fin_estimada)
        self.assertEqual(treatment.monto_estimado, Decimal("0"))

    def test_no_new_negative_amount_or_reversed_date_medical_rule_is_invented(self):
        data = self.datos(monto_estimado="-3.50", fecha_fin_estimada="2026-09-01")
        original = TratamientoPacienteForm(data, empresa=self.empresa)
        self.assertTrue(original.is_valid(), original.errors)
        self.assertEqual(self.client.post(self.url(create=True), data).status_code, 302)
        treatment = TratamientoPaciente.objects.get(empresa=self.empresa, nombre=data["nombre"])
        self.assertEqual(treatment.monto_estimado, Decimal("-3.50"))
        self.assertLess(treatment.fecha_fin_estimada, treatment.fecha_inicio)

    def test_invalid_bound_form_preserves_all_ten_posted_fields_without_writes(self):
        for extra in ({"nombre": ""}, {"fecha_inicio": "fecha inválida"}, {"estado": "inexistente"}, {"monto_estimado": ""}):
            data = self.datos(**extra)
            before = self.snapshot()
            response = self.client.post(self.url(create=True), data)
            self.assertEqual(response.status_code, 200)
            self.assertTemplateUsed(response, "clinica/mobile_treatment_form.html")
            form = response.context["form"]
            self.assertTrue(form.is_bound)
            self.assertTrue(form.errors)
            for name in self.FORM_FIELDS:
                self.assertEqual(form.data[name], data[name])
            self.assertEqual(self.snapshot(), before)

    def test_decimal_nonfinite_extreme_or_excess_precision_uses_original_form_errors(self):
        for value in ("NaN", "sNaN", "Infinity", "-Infinity", "1e99999", "1e-99999", "99999999999.99", "1.001"):
            before = self.snapshot()
            response = self.client.post(self.url(create=True), self.datos(monto_estimado=value))
            self.assertEqual(response.status_code, 200, value)
            self.assertIn("monto_estimado", response.context["form"].errors)
            self.assertEqual(response.context["form"].data["monto_estimado"], value)
            self.assertEqual(self.snapshot(), before)

    def test_malformed_noncanonical_and_overflow_choice_ids_are_blocked_before_query(self):
        for field in ("paciente", "servicio", "profesional"):
            for value in ("abc", "0", "-1", "00"+str(self.pacientes[(self.empresa.slug, True)].pk), str(2**90)):
                before = self.snapshot()
                with CaptureQueriesContext(connection) as queries:
                    response = self.client.post(self.url(create=True), self.datos(**{field: value}))
                self.assertIn(response.status_code, (403, 404))
                self.assert_no_history_queries(queries)
                self.assertEqual(self.snapshot(), before)

    def test_foreign_and_inactive_model_choices_keep_original_bound_validation(self):
        foreign = self.empresas["medical_spa"]
        for field, records in (("paciente", self.pacientes), ("servicio", self.servicios), ("profesional", self.profesionales)):
            for record in (records[(foreign.slug, True)], records[(self.empresa.slug, False)]):
                before = self.snapshot()
                response = self.client.post(self.url(create=True), self.datos(**{field: str(record.pk)}))
                self.assertEqual(response.status_code, 200)
                self.assertIn(field, response.context["form"].errors)
                self.assertEqual(response.context["form"].data[field], str(record.pk))
                self.assertEqual(self.snapshot(), before)

    def test_patient_initial_is_scoped_but_does_not_lock_original_authorized_post_selection(self):
        own = self.pacientes[(self.empresa.slug, True)]
        response = self.client.get(self.url(create=True, paciente=own.pk))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["form"]["paciente"].value(), own.pk)
        for value in (self.pacientes[("medical_spa", True)].pk, self.pacientes[(self.empresa.slug, False)].pk, "abc", "0", str(2**90), "00"+str(own.pk)):
            self.assertIn(self.client.get(self.url(create=True, paciente=value)).status_code, (403, 404))
        before = self.snapshot()
        for value in ("", str(self.pacientes[("medical_spa", True)].pk)):
            response = self.client.post(self.url(create=True, paciente=own.pk), self.datos(paciente=value))
            self.assertEqual(response.status_code, 200)
            self.assertIn("paciente", response.context["form"].errors)
        self.assertEqual(self.snapshot(), before)
        other = Paciente.objects.create(empresa=self.empresa, nombre="Segundo paciente propio activo", expediente_codigo="MTREAT-SECOND")
        data = self.datos(paciente=str(other.pk))
        self.assertTrue(TratamientoPacienteForm(data, empresa=self.empresa).is_valid())
        response = self.client.post(self.url(create=True, paciente=own.pk), data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(TratamientoPaciente.objects.get(empresa=self.empresa, nombre=data["nombre"]).paciente_id, other.pk)

    def test_closed_create_query_and_post_envelopes_reject_duplicates_and_unknown_fields(self):
        before = self.snapshot()
        for extra in ({"accion": "guardar_plan"}, {"tipo_historia": "capilar"}, {"empresa": self.empresa.pk}, {"creado_por": self.usuario.pk}, {"app": "0"}):
            data = self.datos()
            data.update(extra)
            self.assertIn(self.client.post(self.url(create=True), data).status_code, (403, 404))
        base = QueryDict(mutable=True)
        for key, value in self.datos().items():
            base[key] = value
        for field in ("app", *self.FORM_FIELDS):
            duplicate = base.copy()
            duplicate.setlist(field, [base[field], base[field]])
            self.assertIn(self.client.post(self.url(create=True), duplicate.urlencode(), content_type="application/x-www-form-urlencoded").status_code, (403, 404))
        for extra in ({"q": "incompatible"}, {"page": "1"}, {"servicio": "1"}):
            self.assertIn(self.client.get(self.url(create=True, **extra)).status_code, (403, 404))
        self.assertIn(self.client.get(self.url(create=True)+"&app=1").status_code, (403, 404))
        self.assertIn(self.client.post(self.url(create=True, app="0"), self.datos()).status_code, (403, 404))
        self.assertEqual(self.snapshot(), before)

    def test_list_closed_filters_reject_bad_state_page_ids_duplicate_or_unknown_query(self):
        before = self.snapshot()
        for extra in ({"estado": "no existe"}, {"page": "0"}, {"page": "-1"}, {"page": "01"}, {"page": str(2**90)}, {"paciente": "1"}, {"accion": "crear_opcion_agenda"}):
            self.assertIn(self.client.get(self.url(**extra)).status_code, (403, 404))
        for suffix in ("&app=1", "&q=otra", "&estado=pausado", "&page=1"):
            self.assertIn(self.client.get(self.url(q="base", estado="planificado", page="1")+suffix).status_code, (403, 404))
        self.assertEqual(self.snapshot(), before)

    def test_imported_foreign_relations_off_page_or_hidden_by_search_are_denied_before_leak(self):
        foreign = self.empresas["medical_spa"]
        record = self.tratamientos[(self.empresa.slug, "cancelado")]
        for field, target in (("paciente", self.pacientes[(foreign.slug, True)]), ("servicio", self.servicios[(foreign.slug, True)]), ("profesional", self.profesionales[(foreign.slug, True)])):
            original = getattr(record, field)
            TratamientoPaciente.objects.filter(pk=record.pk).update(**{field: target})
            before = self.snapshot()
            for parameters in ({"q": "no coincide"}, {"estado": "planificado"}, {"page": "2"}):
                response = self.client.get(self.url(**parameters))
                self.assertIn(response.status_code, (403, 404))
                self.assertNotContains(response, target.nombre, status_code=response.status_code)
            self.assertEqual(self.snapshot(), before)
            TratamientoPaciente.objects.filter(pk=record.pk).update(**{field: original})

    def test_orphaned_imported_foreign_keys_are_denied_before_filters_and_restored_in_fixture(self):
        record = self.tratamientos[(self.empresa.slug, "cancelado")]
        for field in ("paciente_id", "servicio_id", "profesional_id"):
            original = getattr(record, field)
            with connection.constraint_checks_disabled():
                TratamientoPaciente.objects.filter(pk=record.pk).update(**{field: 2147483647})
                try:
                    response = self.client.get(self.url(q="no coincide", estado="planificado"))
                    self.assertIn(response.status_code, (403, 404))
                finally:
                    TratamientoPaciente.objects.filter(pk=record.pk).update(**{field: original})

    def test_methods_logout_and_csrf_reject_mutations_without_clinical_writes(self):
        before = self.snapshot()
        for create in (False, True):
            self.assertEqual(self.client.head(self.url(create=create)).status_code, 200)
            self.assertIn(self.client.put(self.url(create=create), "app=1", content_type="application/x-www-form-urlencoded").status_code, (403, 405))
        self.assertEqual(self.snapshot(), before)
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(self.usuario)
        before = self.snapshot()
        self.assertEqual(protected.get(self.url(create=True)).status_code, 200)
        self.assertIn(protected.post(self.url(create=True), self.datos()).status_code, (302, 403))
        self.assertEqual(self.snapshot(), before)
        token = protected.cookies["csrftoken"].value
        self.assertEqual(protected.post(self.url(create=True), self.datos(), HTTP_X_CSRFTOKEN=token).status_code, 302)
        self.client.logout()
        before = self.snapshot()
        self.assertEqual(self.client.get(self.url()).status_code, 302)
        self.assertEqual(self.client.post(self.url(create=True), self.datos()).status_code, 302)
        self.assertEqual(self.snapshot(), before)

    def test_hub_uses_read_list_or_write_only_composer_without_patient_calendar_grants(self):
        hub = reverse("agenda_mobile_modules_screen", args=[self.empresa.slug])
        for permission, create in (("puede_ver_planes_tratamiento", False), ("puede_editar_planes_tratamiento", True)):
            self.asignar_rol(**{permission: True})
            response = self.client.get(hub)
            self.assertEqual(response.status_code, 200)
            card = next(item for item in response.context["modulos_app"] if item["key"] == "tratamientos")
            self.assertEqual(card["estado"], "parcial")
            self.assertEqual(card["url"], self.url(create=create))
            self.assertEqual(self.client.get(card["url"]).status_code, 200)
            self.assertFalse(response.context["app_home_disponible"])
            self.assertFalse(any(item["key"] == "agenda" for item in response.context["modulos_app"]))

    def test_catalog_capability_helper_is_pure_and_does_not_grant_clinic_permissions(self):
        from .mobile_treatments import tratamientos_disponibles_app
        before = self.snapshot()
        request = RequestFactory().get(self.url())
        request.user = self.usuario
        self.assertEqual(tratamientos_disponibles_app(request, self.empresa), {"ver": True, "crear": True})
        self.assertEqual(self.snapshot(), before)
        self.asignar_rol(puede_ver_pacientes=True, puede_clinica=True)
        request.user = Usuario.objects.get(pk=self.usuario.pk)
        self.assertEqual(tratamientos_disponibles_app(request, self.empresa), {"ver": False, "crear": False})

    def test_patient_detail_links_require_the_real_general_data_permission(self):
        patient = self.pacientes[(self.empresa.slug, True)]
        url = reverse("clinica_paciente_detalle", args=[self.empresa.slug, patient.pk])+"?app=1"
        response = self.client.get(self.url())
        self.assertNotContains(response, url)
        self.asignar_rol(puede_ver_planes_tratamiento=True, puede_ver_datos_generales_paciente=True)
        response = self.client.get(self.url())
        self.assertContains(response, url)
