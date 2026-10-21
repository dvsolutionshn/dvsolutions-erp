"""Read-only mobile followups reuse the existing patient reminder records.

All fixtures are synthetic. No calendar initializer, synchronizer, sender,
network or native document renderer is allowed in the APP consultation.
"""
from datetime import datetime, timedelta, timezone as datetime_timezone
from html.parser import HTMLParser
import re
from urllib.parse import parse_qs, urlencode, urlsplit
from unittest.mock import patch

from django.apps import apps
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection, reset_queries
from django.http import Http404, QueryDict
from django.test import RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import resolve, reverse
from django.utils.datastructures import MultiValueDict
from django.utils import timezone

from core.models import Empresa, EmpresaModulo, Modulo, RolSistema, Usuario, UsuarioEmpresaPermiso
from . import models, views
from .models import CitaClinica, Paciente, ProfesionalSalud, ServicioClinico


class _FollowupMarkup(HTMLParser):
    def __init__(self, content):
        super().__init__(convert_charrefs=True)
        self.nodes = []
        self.feed(content)

    def handle_starttag(self, tag, attrs):
        self.nodes.append((tag, dict(attrs)))


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class MobileFollowupsIntegrationTests(TestCase):
    SLUGS = ("hospital_mia", "medical_spa", "luque_aestetic", "serviciosmedicos")
    NOW = datetime(2026, 10, 13, 18, 0, tzinfo=datetime_timezone.utc)
    DATE = datetime(2026, 11, 1, 15, 30, tzinfo=datetime_timezone.utc)
    PRIVATE_TABLES = ("clinica_paciente", "clinica_citaclinica", "clinica_profesionalsalud", "clinica_servicioclinico")
    ROUTE = "clinica_seguimientos_paciente"

    @classmethod
    def setUpTestData(cls):
        with patch("django.utils.timezone.now", return_value=cls.NOW):
            cls.modulo, _ = Modulo.objects.get_or_create(codigo="clinica_medica", defaults={"nombre": "Clínica Followups QA"})
            cls.empresas, cls.pacientes, cls.profesionales, cls.servicios, cls.recordatorios = {}, {}, {}, {}, {}
            for index, slug in enumerate(cls.SLUGS, 1):
                empresa = Empresa.objects.create(nombre=f"Seguimientos QA {slug}", slug=slug, rtn=f"FOLLOWUP-{index}", tipo_solucion="clinica", estado_licencia="activa")
                cls.empresas[slug] = empresa
                EmpresaModulo.objects.create(empresa=empresa, modulo=cls.modulo, activo=True)
                cls.pacientes[slug] = Paciente.objects.create(empresa=empresa, nombre=f"Paciente seguimiento {slug}", expediente_codigo=f"FUP-{index}", identidad=f"08011991000{index:02d}", telefono="CONTACTO-PRIVADO-FUP", correo="private-followup@example.invalid")
                cls.profesionales[slug] = ProfesionalSalud.objects.create(empresa=empresa, nombre=f"Profesional propio {slug}", especialidad="QA sintética")
                cls.servicios[slug] = ServicioClinico.objects.create(empresa=empresa, nombre=f"Servicio propio {slug}")
                for number, state in enumerate(("confirmada", "completada", "cancelada")):
                    cls.recordatorios[(slug, number)] = CitaClinica.objects.create(empresa=empresa, paciente=cls.pacientes[slug], profesional=cls.profesionales[slug] if number != 1 else None, servicio=cls.servicios[slug] if number == 0 else None, fecha_hora=cls.DATE + timedelta(days=number), estado=state, canal="recepcion", motivo=f"Recordatorio: Seguimiento sintético {slug} {number}", es_recordatorio_tratamiento=True, tratamiento_recordatorio=f"Seguimiento sintético {slug} {number}", observaciones=f"Nota completa {slug} {number}\nSegunda línea conservada.")
            cls.empresa = cls.empresas["hospital_mia"]
            cls.paciente = cls.pacientes[cls.empresa.slug]
            cls.rol = RolSistema.objects.create(nombre="Consulta seguimientos QA", codigo="followup-qa-granular", activo=True, es_rol_clinico=True, usa_permisos_clinicos_granulares=True, puede_crear_recordatorios_paciente=True)
            cls.legado = RolSistema.objects.create(nombre="Seguimientos legado QA", codigo="followup-qa-legacy", activo=True, puede_pacientes=True)
            cls.usuario = Usuario.objects.create_user(username="followup-qa", password="test-only", empresa=cls.empresa, rol_sistema=cls.rol)
            cls.usuario.empresas_acceso.add(*cls.empresas.values())
            cls.externa = Empresa.objects.create(nombre="Fuera cuatro followups QA", slug="followup_externa", rtn="FUP-EXT", tipo_solucion="clinica", estado_licencia="activa")
            EmpresaModulo.objects.create(empresa=cls.externa, modulo=cls.modulo)
            cls.usuario.empresas_acceso.add(cls.externa)
            cls.paciente_externo = Paciente.objects.create(empresa=cls.externa, nombre="Paciente ajeno exclusivo QA", expediente_codigo="FUP-EXT", identidad="0801199100099")
            cls.otro_paciente = Paciente.objects.create(empresa=cls.empresa, nombre="Otro paciente local exclusivo QA", expediente_codigo="FUP-OTHER", identidad="0801199100098")
            cls.otra_cita = CitaClinica.objects.create(empresa=cls.empresa, paciente=cls.otro_paciente, fecha_hora=cls.DATE, es_recordatorio_tratamiento=True, tratamiento_recordatorio="NO MOSTRAR OTRO PACIENTE", motivo="Otro paciente")
            cls.cita_normal = CitaClinica.objects.create(empresa=cls.empresa, paciente=cls.paciente, fecha_hora=cls.DATE, es_recordatorio_tratamiento=False, motivo="NO MOSTRAR CITA NORMAL")

    def setUp(self):
        clock = patch("django.utils.timezone.now", return_value=self.NOW)
        clock.start(); self.addCleanup(clock.stop)
        self.client.force_login(self.usuario)
        self.url()  # Resolve route metadata only, before installing blockers.
        self.blocked = {}
        for target in (
            "clinica.views.asegurar_profesionales_agenda_base",
            "clinica.views._sincronizar_agenda_desde_cita_clinica",
            "crm.appointment_notifications.programar_notificaciones_cita",
            "crm.appointment_notifications.procesar_notificacion",
            "crm.services.enviar_plantilla_cita_whatsapp",
            "django.core.mail.EmailMessage.send",
            "socket.create_connection", "socket.socket.connect", "socket.socket.connect_ex",
        ):
            blocker = patch(target, side_effect=AssertionError(f"Forbidden followup side effect: {target}"))
            self.blocked[target] = blocker.start()
            self.addCleanup(blocker.stop)
            self.addCleanup(self.blocked[target].assert_not_called)

    def url(self, empresa=None, paciente=None, mobile=True, **query):
        empresa = empresa or self.empresa
        paciente = paciente or self.pacientes.get(empresa.slug) or self.paciente_externo
        base = reverse(self.ROUTE, args=[empresa.slug, paciente.pk])
        values = {"app": "1", **query} if mobile else query
        return base + ("?" + urlencode(values, doseq=True) if values else "")

    def role(self, granular=True, **flags):
        permissions = {"puede_crear_recordatorios_paciente": True} if not flags and granular else ({"puede_pacientes": True} if not flags else flags)
        role = RolSistema.objects.create(nombre="Rol followup aislado", codigo=f"fup-role-{RolSistema.objects.count()}", activo=True, es_rol_clinico=granular, usa_permisos_clinicos_granulares=granular, **permissions)
        Usuario.objects.filter(pk=self.usuario.pk).update(rol_sistema=role, is_superuser=False, es_administrador_empresa=False)
        UsuarioEmpresaPermiso.objects.filter(usuario=self.usuario).delete()
        self.usuario.refresh_from_db()
        return role

    def snapshot(self):
        return {model._meta.label_lower: list(model._base_manager.order_by("pk").values()) for model in sorted(apps.get_models(include_auto_created=True), key=lambda item: item._meta.label_lower)}

    def assert_no_writes(self, captured):
        self.assertEqual([item["sql"] for item in captured if re.match(r"\s*(INSERT|UPDATE|DELETE|REPLACE|CREATE|ALTER|DROP)\b", item["sql"], re.I)], [])

    def assert_no_private_queries(self, captured):
        self.assertFalse(any(table in item["sql"].lower() for table in self.PRIVATE_TABLES for item in captured))

    def read(self, url=None, method="get", **kwargs):
        before = self.snapshot(); reset_queries()
        with CaptureQueriesContext(connection) as captured:
            if isinstance(kwargs.get("data"), bytes):
                response = self.client.generic(method.upper(), url or self.url(), **kwargs)
            else:
                response = getattr(self.client, method)(url or self.url(), **kwargs)
        self.assert_no_writes(captured)
        self.assertEqual(self.snapshot(), before)
        return response

    def request(self, url=None, method="get", data=None, body=None):
        factory = RequestFactory(); url = url or self.url()
        if body is not None:
            request = factory.generic(method.upper(), url, data=body, content_type="application/json")
        elif method in {"get", "head", "post"}:
            request = getattr(factory, method)(url, data=data or {})
        else:
            request = factory.generic(method.upper(), url)
        request.user = Usuario.objects.get(pk=self.usuario.pk)
        request.session = {}
        return request

    def direct(self, request=None, url=None, method="get", data=None, body=None):
        request = request or self.request(url, method, data, body)
        match = resolve(urlsplit(url or request.get_full_path()).path)
        return views.seguimientos_paciente.__wrapped__(request, **match.kwargs)

    def denied(self, url=None, method="get", data=None, body=None, request=None, early=True, exception=(PermissionDenied, Http404)):
        request = request or self.request(url, method, data, body)
        before = self.snapshot(); reset_queries()
        with CaptureQueriesContext(connection) as captured:
            with self.assertRaises(exception):
                self.direct(request=request, url=url)
        self.assert_no_writes(captured)
        if early:
            self.assert_no_private_queries(captured)
        self.assertEqual(self.snapshot(), before)

    def items(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "clinica/mobile_followup_list.html")
        return response.context["mobile_followup_items"]

    def make_many(self, empresa=None, count=42):
        empresa = empresa or self.empresa
        records = []
        for number in range(count):
            records.append(CitaClinica.objects.create(empresa=empresa, paciente=self.pacientes[empresa.slug], profesional=None, servicio=None, fecha_hora=self.DATE + timedelta(days=20, minutes=number // 2), es_recordatorio_tratamiento=True, tratamiento_recordatorio=f"Más seguimiento {number:03d}", motivo=f"Recordatorio extra {number:03d}", observaciones="Nota extra completa"))
        return records

    def test_four_clinics_both_role_types_use_mobile_get_and_head(self):
        for granular in (True, False):
            self.role(granular=granular)
            for empresa in self.empresas.values():
                with self.subTest(granular=granular, empresa=empresa.slug):
                    response = self.read(self.url(empresa))
                    self.assertEqual(len(self.items(response)), 3)
                    self.assertEqual(response.context["mobile_followup_total"], 3)
                    self.assertIn("no-store", response["Cache-Control"])
                    head = self.read(self.url(empresa), method="head")
                    self.assertEqual(head.status_code, 200); self.assertEqual(head.content, b"")
                    self.assertIn("no-store", head["Cache-Control"])

    def test_unflagged_web_template_and_initializer_remain_original(self):
        with patch.object(views, "asegurar_profesionales_agenda_base", wraps=models.asegurar_profesionales_agenda_base) as seed:
            for empresa in self.empresas.values():
                response = self.client.get(self.url(empresa, mobile=False))
                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, "clinica/seguimientos_paciente.html")
                self.assertTemplateNotUsed(response, "clinica/mobile_followup_list.html")
                self.assertContains(response, 'id="treatmentReminderSubmit"')
                self.assertEqual(set(response.context["recordatorios_tratamiento"].values_list("pk", flat=True)), {self.recordatorios[(empresa.slug, number)].pk for number in range(3)})
                self.assertEqual(ProfesionalSalud.objects.filter(empresa=empresa, nombre__in=[name for name, _ in models.PROFESIONALES_AGENDA_BASE]).count(), 2)
            self.assertEqual(seed.call_count, 4)

    def test_other_company_app_flag_keeps_original_web_instead_of_adapting(self):
        with patch.object(views, "asegurar_profesionales_agenda_base", wraps=models.asegurar_profesionales_agenda_base) as seed:
            response = self.client.get(self.url(self.externa))
            self.assertEqual(response.status_code, 200)
            self.assertTemplateUsed(response, "clinica/seguimientos_paciente.html")
            self.assertNotContains(response, 'id="mobileFollowupSearchForm"')
            seed.assert_called_once_with(self.externa)

    def test_recordatorio_permission_does_not_require_or_grant_other_read_operations(self):
        response = self.read(); self.items(response)
        self.assertNotContains(response, 'rel="manifest"')
        self.assertFalse(response.context["app_home_disponible"])
        for route, args in (("clinica_pacientes", [self.empresa.slug]), ("clinica_paciente_detalle", [self.empresa.slug, self.paciente.pk]), ("clinica_historias_especialidad", [self.empresa.slug, self.paciente.pk]), ("agenda_mobile", [self.empresa.slug])):
            url = reverse(route, args=args)
            self.assertNotContains(response, f'href="{url}?app=1"')
            self.assertIn(self.client.get(url + "?app=1").status_code, (302, 403))

    def test_unrelated_permissions_and_granular_legacy_fields_do_not_grant_followups(self):
        for granular, flags in ((True, {"puede_pacientes": True}), (True, {"puede_ver_pacientes": True}), (True, {"puede_ver_calendario": True}), (True, {"puede_ver_historia_clinica": True}), (True, {"puede_configuracion_clinica": True}), (False, {"puede_expediente_clinico": True}), (False, {"puede_citas": True})):
            self.role(granular=granular, **flags)
            self.denied()

    def test_current_company_assignment_overrides_global_role_and_explicit_none_denies(self):
        override = UsuarioEmpresaPermiso.objects.create(usuario=self.usuario, empresa=self.empresa, rol_sistema=None, activo=True)
        self.denied()
        override.rol_sistema = self.legado; override.save()
        self.assertEqual(len(self.items(self.read())), 3)
        self.assertEqual(len(self.items(self.read(self.url(self.empresas["medical_spa"])))), 3)

    def test_membership_denies_direct_view_before_patient_lookup(self):
        empresa = self.empresas["medical_spa"]
        self.usuario.empresas_acceso.remove(empresa)
        self.denied(url=self.url(empresa))

    def test_inactive_company_denies_without_initializer_or_private_queries(self):
        Empresa.objects.filter(pk=self.empresa.pk).update(activa=False)
        self.denied()

    def test_license_is_original_gate_with_superuser_exception(self):
        for values in ({"estado_licencia": "suspendida"}, {"fecha_vencimiento_plan": self.NOW.date()-timedelta(days=1)}):
            Empresa.objects.filter(pk=self.empresa.pk).update(**values)
            self.denied()
            Empresa.objects.filter(pk=self.empresa.pk).update(estado_licencia="activa", fecha_vencimiento_plan=None)
        Usuario.objects.filter(pk=self.usuario.pk).update(is_superuser=True)
        Empresa.objects.filter(pk=self.empresa.pk).update(estado_licencia="suspendida")
        self.assertEqual(len(self.items(self.read())), 3)

    def test_clinical_module_is_required_even_for_superuser(self):
        Usuario.objects.filter(pk=self.usuario.pk).update(is_superuser=True)
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.modulo).update(activo=False)
        self.denied()

    def test_nonclinical_profile_cannot_fall_through_to_web_with_app_marker(self):
        Empresa.objects.filter(pk=self.empresa.pk).update(tipo_solucion="erp")
        self.denied()

    def test_company_administrator_uses_original_permission_bypass_not_extra_grants(self):
        self.role(granular=True, puede_ver_pacientes=True)
        Usuario.objects.filter(pk=self.usuario.pk).update(es_administrador_empresa=True)
        self.assertEqual(len(self.items(self.read())), 3)

    def test_logout_cannot_read_patient_followup_rows(self):
        self.client.logout()
        response = self.read()
        self.assertEqual(response.status_code, 302)
        self.assertNotContains(response, "Seguimiento sintético", status_code=302)

    def test_pure_capability_queries_no_patient_catalog_calendar_or_followup_data(self):
        from .mobile_followups import seguimientos_disponibles_app
        request = self.request(); before = self.snapshot()
        with CaptureQueriesContext(connection) as captured:
            for empresa in self.empresas.values():
                self.assertTrue(seguimientos_disponibles_app(request, empresa))
            self.assertFalse(seguimientos_disponibles_app(request, self.externa))
        self.assert_no_writes(captured); self.assert_no_private_queries(captured)
        self.assertEqual(self.snapshot(), before)

    def test_get_with_no_professionals_does_not_seed_catalog_or_configuration(self):
        CitaClinica.objects.filter(empresa=self.empresa).update(profesional=None)
        ProfesionalSalud.objects.filter(empresa=self.empresa).delete()
        self.assertEqual(len(self.items(self.read())), 3)
        self.assertFalse(ProfesionalSalud.objects.filter(empresa=self.empresa).exists())

    def test_all_supported_query_values_are_unique_and_closed(self):
        for query in ("app=1&app=1", "app=1&q=a&q=b", "app=1&page=1&page=2", "app=0", "app=", "app=2", "app=1&empresa=1", "app=1&paciente=1", "app=1&accion=editar_recordatorio", "app=1&profesional=1", "app=1&estado=confirmada", "app=1&next=/web/"):
            self.denied(url=self.url().split("?")[0] + "?" + query)

    def test_page_is_canonical_ascii_positive_int64_and_large_page_clamps(self):
        for value in ("0", "-1", "01", "+1", "1.0", "1e2", " 1", "1 ", "１", "9223372036854775808", "a"):
            self.denied(url=self.url(page=value))
        response = self.read(self.url(page="9223372036854775807"))
        self.assertEqual(response.context["mobile_followup_page"], 1)

    def test_search_retains_long_query_without_inventing_a_maximum_length_rule(self):
        query = "x" * 320
        response = self.read(self.url(q=query))
        self.assertEqual(response.context["mobile_followup_total"], 0)
        self.assertEqual(response.context["mobile_followup_query"], query)

    def test_readonly_post_does_not_edit_delete_or_create_any_record(self):
        for action in ("editar_recordatorio", "eliminar_recordatorio", "crear_recordatorio", "", None):
            payload = {"app": "1", "recordatorio_id": self.recordatorios[(self.empresa.slug, 0)].pk, "tratamiento": "Nunca guardar", "fecha": "2027-01-01", "hora": "09:00", "periodo": "AM"}
            if action is not None:
                payload["accion"] = action
            self.denied(method="post", data=payload)
            response = self.read(method="post", data=payload)
            self.assertIn(response.status_code, (403, 405))

    def test_put_patch_delete_and_options_never_reach_initializer_or_records(self):
        for method in ("put", "patch", "delete", "options"):
            self.denied(method=method)

    def test_get_and_head_raw_bodies_are_rejected_before_private_queries(self):
        for method in ("get", "head"):
            for body in (b"{}", b"app=1&accion=editar_recordatorio"):
                self.denied(method=method, body=body)
                response = self.read(method=method, data=body, content_type="application/json")
                self.assertEqual(response.status_code, 403)

    def test_readonly_files_and_post_form_cache_are_rejected(self):
        request = self.request()
        request._files = MultiValueDict({"archivo": [SimpleUploadedFile("qa.txt", b"Synthetic only")]})
        request._post = QueryDict("")
        self.denied(request=request)
        request = self.request()
        request._files = MultiValueDict()
        request._post = QueryDict("app=1&accion=eliminar_recordatorio")
        self.denied(request=request)

    def test_foreign_or_missing_patient_is_404_before_followup_and_catalog_queries(self):
        for identifier in (self.pacientes["medical_spa"].pk, self.paciente_externo.pk, 2147483647):
            url = self.url().replace(f"/pacientes/{self.paciente.pk}/", f"/pacientes/{identifier}/")
            self.denied(url=url, early=False, exception=Http404)
            request = self.request(url)
            with CaptureQueriesContext(connection) as captured:
                with self.assertRaises(Http404):
                    self.direct(request=request, url=url)
            self.assertFalse(any("clinica_citaclinica" in item["sql"].lower() or "clinica_profesionalsalud" in item["sql"].lower() for item in captured))

    def test_patient_identifier_metadata_is_canonical_before_patient_query(self):
        for value in ("0", "01", "9223372036854775808", "999999999999999999999"):
            self.denied(url=self.url().replace(f"/pacientes/{self.paciente.pk}/", f"/pacientes/{value}/"))

    def test_only_current_patient_reminders_are_listed_not_other_appointments(self):
        response = self.read(); items = self.items(response)
        self.assertEqual({item["id"] for item in items}, {self.recordatorios[(self.empresa.slug, number)].pk for number in range(3)})
        self.assertNotContains(response, self.otra_cita.tratamiento_recordatorio)
        self.assertNotContains(response, self.cita_normal.motivo)

    def test_imported_wrong_company_record_cannot_hide_behind_search_or_pagination(self):
        record = self.recordatorios[(self.empresa.slug, 0)]
        CitaClinica.objects.filter(pk=record.pk).update(empresa=self.empresas["medical_spa"])
        for query in ({}, {"q": "sin coincidencias"}, {"page": "2"}):
            self.denied(url=self.url(**query), early=False, exception=PermissionDenied)

    def test_foreign_professional_and_service_are_denied_before_display(self):
        record = self.recordatorios[(self.empresa.slug, 0)]
        for field, invalid in (("profesional_id", self.profesionales["medical_spa"].pk), ("servicio_id", self.servicios["medical_spa"].pk)):
            original = getattr(record, field)
            CitaClinica.objects.filter(pk=record.pk).update(**{field: invalid})
            try:
                self.denied(url=self.url(q="sin coincidencias", page="2"), early=False, exception=PermissionDenied)
            finally:
                CitaClinica.objects.filter(pk=record.pk).update(**{field: original})

    def test_orphan_professional_and_service_fail_closed_and_are_restored_for_teardown(self):
        record = self.recordatorios[(self.empresa.slug, 0)]
        for field in ("profesional_id", "servicio_id"):
            original = getattr(record, field)
            with connection.constraint_checks_disabled():
                CitaClinica.objects.filter(pk=record.pk).update(**{field: 2147483647})
                try:
                    self.denied(url=self.url(q="unmatched"), early=False, exception=PermissionDenied)
                finally:
                    CitaClinica.objects.filter(pk=record.pk).update(**{field: original})

    def test_nullable_professional_and_service_are_valid_historical_records(self):
        CitaClinica.objects.filter(empresa=self.empresa).update(profesional=None, servicio=None)
        items = self.items(self.read())
        self.assertEqual(len(items), 3)
        self.assertTrue(all(not item["profesional"] for item in items))

    def test_inactive_patient_professional_service_and_crosscompany_staff_are_not_reactivated(self):
        staff = Usuario.objects.create_user(username="followup-history-staff", empresa=self.externa, is_active=False)
        ProfesionalSalud.objects.filter(pk=self.profesionales[self.empresa.slug].pk).update(activo=False, usuario=staff)
        ServicioClinico.objects.filter(pk=self.servicios[self.empresa.slug].pk).update(activo=False)
        Paciente.objects.filter(pk=self.paciente.pk).update(activo=False)
        self.assertEqual(len(self.items(self.read())), 3)
        self.assertContains(self.read(), self.profesionales[self.empresa.slug].nombre)

    def test_unrelated_patient_and_other_company_corruption_do_not_poison_current_scope(self):
        CitaClinica.objects.filter(pk=self.otra_cita.pk).update(profesional=self.profesionales["medical_spa"])
        CitaClinica.objects.filter(pk=self.recordatorios[("medical_spa", 0)].pk).update(servicio=self.servicios[self.empresa.slug])
        self.assertEqual(len(self.items(self.read())), 3)

    def test_twenty_row_pagination_preserves_complete_total_and_original_date_id_order(self):
        self.make_many()
        expected = list(CitaClinica.objects.filter(paciente=self.paciente, es_recordatorio_tratamiento=True).order_by("fecha_hora", "id").values_list("id", flat=True))
        first = self.read(); second = self.read(self.url(page="2")); last = self.read(self.url(page="3"))
        self.assertEqual(first.context["mobile_followup_total"], 45)
        self.assertEqual(len(self.items(first)), 20); self.assertEqual(len(self.items(second)), 20); self.assertEqual(len(self.items(last)), 5)
        observed = [item["id"] for page in (first, second, last) for item in self.items(page)]
        self.assertEqual(observed, expected); self.assertEqual(len(set(observed)), 45)

    def test_search_uses_full_scope_treatment_reason_note_and_professional_fields(self):
        extra = self.make_many(count=25)[-1]
        for field, value in (("tratamiento_recordatorio", "NEEDLE tratamiento"), ("motivo", "NEEDLE motivo"), ("observaciones", "NEEDLE nota")):
            values = {"tratamiento_recordatorio": "Otro", "motivo": "Otro", "observaciones": "Otro"}
            values[field] = value
            CitaClinica.objects.filter(pk=extra.pk).update(**values)
            response = self.read(self.url(q="needle"))
            self.assertEqual([item["id"] for item in self.items(response)], [extra.pk])
        response = self.read(self.url(q=self.profesionales[self.empresa.slug].nombre))
        self.assertEqual(response.context["mobile_followup_total"], 2)

    def test_empty_and_foreign_search_echo_query_but_reveal_no_foreign_rows(self):
        needle = self.recordatorios[("medical_spa", 0)].tratamiento_recordatorio
        response = self.read(self.url(q=needle))
        self.assertEqual(self.items(response), [])
        self.assertEqual(response.context["mobile_followup_total"], 0)
        self.assertEqual(response.context["mobile_followup_query"], needle)
        self.assertNotContains(response, self.profesionales["medical_spa"].nombre)

    def test_pager_and_search_urls_are_same_scoped_readonly_route(self):
        self.make_many()
        response = self.read(self.url(q="Seguimiento"))
        self.assertEqual(urlsplit(response.context["app_followups_search_url"]).path, urlsplit(self.url()).path)
        next_url = response.context["mobile_followup_next_url"]
        self.assertEqual(urlsplit(next_url).path, urlsplit(self.url()).path)
        self.assertEqual(parse_qs(urlsplit(next_url).query), {"app": ["1"], "q": ["Seguimiento"], "page": ["2"]})
        second = self.read(next_url)
        self.assertEqual(parse_qs(urlsplit(second.context["mobile_followup_previous_url"]).query), {"app": ["1"], "q": ["Seguimiento"], "page": ["1"]})

    def test_cards_are_readonly_and_native_filter_has_only_app_and_query(self):
        response = self.read(); markup = _FollowupMarkup(response.content.decode())
        forms = [attrs for tag, attrs in markup.nodes if tag == "form"]
        self.assertTrue(forms)
        self.assertTrue(all(attrs.get("method", "get").lower() == "get" for attrs in forms))
        named = [attrs["name"] for tag, attrs in markup.nodes if tag in {"input", "select", "textarea"} and "name" in attrs]
        self.assertEqual(sorted(named), ["app", "q"])
        self.assertNotContains(response, "<table")
        for action in ("editar_recordatorio", "eliminar_recordatorio", "treatmentReminderSubmit", "treatmentReminderForm"):
            self.assertNotContains(response, action)
        self.assertNotContains(response, 'name="csrfmiddlewaretoken"')

    def test_patient_projection_and_cards_exclude_contact_and_mutation_urls(self):
        response = self.read()
        self.assertIsInstance(response.context["paciente"], dict)
        self.assertEqual(set(response.context["paciente"]), {"id", "pk", "nombre", "expediente_codigo"})
        self.assertNotContains(response, "CONTACTO-PRIVADO-FUP")
        self.assertNotContains(response, "private-followup@example.invalid")
        hrefs = [attrs.get("href", "") for tag, attrs in _FollowupMarkup(response.content.decode()).nodes if tag == "a"]
        self.assertFalse(any("recordatorios-tratamiento/crear" in href or "citas/crear" in href or "wa.me" in href or href.startswith("mailto:") for href in hrefs))

    def test_notes_and_search_are_escaped_and_full_text_is_preserved(self):
        record = self.recordatorios[(self.empresa.slug, 0)]
        text = '<img src=x onerror="window.UNSAFE_FOLLOWUP=1"> & QA\n' + "Texto completo " * 120
        CitaClinica.objects.filter(pk=record.pk).update(tratamiento_recordatorio="<script>UNSAFE_TREATMENT</script>", observaciones=text)
        response = self.read()
        self.assertNotContains(response, '<img src=x onerror=')
        self.assertNotContains(response, "<script>UNSAFE_TREATMENT</script>")
        self.assertContains(response, "&lt;img")
        self.assertEqual(next(item for item in self.items(response) if item["id"] == record.pk)["observaciones"], text)
        search = self.read(self.url(q='"><svg onload=UNSAFE_SEARCH>'))
        self.assertNotContains(search, '<svg onload=UNSAFE_SEARCH>')
        self.assertEqual(search.context["mobile_followup_total"], 0)

    def test_original_states_dates_notes_and_nullable_names_are_not_invented_or_recalculated(self):
        items = self.items(self.read())
        for item in items:
            original = CitaClinica.objects.get(pk=item["id"])
            self.assertEqual(item["tratamiento"], original.tratamiento_recordatorio or original.motivo)
            self.assertEqual(item["fecha_hora"], original.fecha_hora)
            self.assertEqual((item["estado"], item["estado_label"]), (original.estado, original.get_estado_display()))
            self.assertEqual(item["observaciones"], original.observaciones or "")
        CitaClinica.objects.filter(pk=self.recordatorios[(self.empresa.slug, 0)].pk).update(tratamiento_recordatorio="", fecha_hora=self.NOW-timedelta(days=10))
        fallback = self.items(self.read())[0]
        self.assertEqual(fallback["tratamiento"], self.recordatorios[(self.empresa.slug, 0)].motivo)

    def test_patient_detail_adds_followup_link_only_with_exact_existing_permission(self):
        for granular in (True, False):
            flags = {"puede_ver_datos_generales_paciente": True} if granular else {"puede_pacientes": True}
            if granular:
                self.role(**flags)
                denied_link = self.client.get(reverse("clinica_paciente_detalle", args=[self.empresa.slug, self.paciente.pk])+"?app=1")
                self.assertEqual(denied_link.status_code, 200)
                self.assertFalse(denied_link.context["can_view_followups"])
                self.assertNotContains(denied_link, self.url())
                flags["puede_crear_recordatorios_paciente"] = True
            self.role(granular=granular, **flags)
            response = self.client.get(reverse("clinica_paciente_detalle", args=[self.empresa.slug, self.paciente.pk])+"?app=1")
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context["can_view_followups"])
            self.assertContains(response, f'href="{self.url()}"')
            self.items(self.read(response.context["app_patient_followups_url"]))

    def test_original_create_endpoint_remains_post_only_and_is_not_linked_from_consultation(self):
        response = self.read()
        create_url = reverse("clinica_crear_recordatorios_tratamiento", args=[self.empresa.slug, self.paciente.pk])
        self.assertNotContains(response, f'action="{create_url}')
        self.assertNotContains(response, f'href="{create_url}')
        before = self.snapshot()
        for method in ("get", "head"):
            result = getattr(self.client, method)(create_url)
            self.assertEqual(result.status_code, 405)
        self.assertEqual(self.snapshot(), before)

    def test_offered_functions_url_roundtrip_is_authorized_without_calendar_or_patient_grants(self):
        for granular in (True, False):
            self.role(granular=granular)
            response = self.read()
            self.assertTrue(response.context["can_view_app_modules"])
            modules_url = response.context["app_modules_url"]
            self.assertContains(response, f'href="{modules_url}"')
            self.assertEqual(self.read(modules_url).status_code, 200)
            self.assertFalse(response.context["app_home_disponible"])
            self.assertNotContains(response, 'rel="manifest"')
            if granular:
                self.assertFalse(response.context["can_view_patient"])
                self.assertFalse(response.context["can_view_patients"])
                patient_url = reverse("clinica_paciente_detalle", args=[self.empresa.slug, self.paciente.pk])+"?app=1"
                self.assertIn(self.client.get(patient_url).status_code, (302, 403))
