"""Private preconsulta consultation: original sections, no public wizard.

Independent synthetic fixtures exercise the original GET/HEAD permission
asymmetry and keep every model, through row and transport unchanged.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone as datetime_timezone
from html.parser import HTMLParser
import re
from urllib.parse import parse_qs, urlencode, urlsplit
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection, reset_queries
from django.http import Http404
from django.test import Client, RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import resolve, reverse
from django.utils.datastructures import MultiValueDict
from django.utils import timezone

from core.models import Empresa, EmpresaModulo, Modulo, RolSistema, Usuario, UsuarioEmpresaPermiso
from . import views
from .models import Paciente, PreconsultaClinica


class _PreconsultaMarkup(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.nodes = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.nodes.append((tag, dict(attrs)))


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class MobilePreconsultasIntegrationTests(TestCase):
    SLUGS = ("hospital_mia", "medical_spa", "luque_aestetic", "serviciosmedicos")
    NOW = datetime(2026, 10, 13, 18, 0, tzinfo=datetime_timezone.utc)
    ROUTE = "clinica_preconsulta_detalle"
    PRIVATE = ("PRIVATE-TOKEN-PRECONSULTA", "PRIVATE-PREVIEW", "198.51.100.77", "PRIVATE-CONTACT", "private-preconsulta@example.invalid", "PRIVATE-PHOTO", "UNUSED-JSON-PRIVATE", "PRIVATE-AUTHOR")
    # erp_access's original context processor SELECTs existing advanced config.
    # Preserve that read; ALL-model snapshots and SQL0 still forbid its seed.
    FORBIDDEN_TABLES = ("clinica_profesionalsalud", "clinica_servicioclinico", "clinica_configuracionclinica", "crm_configuracioncrm", "crm_citacliente", "crm_notificacionprogramadacita", "facturacion_factura", "facturacion_pagofactura", "facturacion_inventarioproducto")
    DATA = {"formulario_general": {
        "motivo_categoria": ["motivo_historico"], "procedimientos_interes": ["procedimiento_historico", "procedimiento_historico"],
        "historia_mejorar": "Texto médico sintético registrado", "funcion_apetito": "normal",
        "diagnostico_medico": "si", "alergias_otras": "Texto sintético de alergia",
        "tabaco_frecuencia": ["frecuencia_historica"], "gine_gestas": 0,
        "expectativas_realistas": False, "decision_cirugia": "decision_historica",
        "unused_nested": {"secret": "UNUSED-JSON-PRIVATE"}},
        "unused_private": {"token": "UNUSED-JSON-PRIVATE"}}

    @classmethod
    def setUpTestData(cls):
        with patch("django.utils.timezone.now", return_value=cls.NOW):
            cls.modulo, _ = Modulo.objects.get_or_create(codigo="clinica_medica", defaults={"nombre": "Clínica preconsulta QA"})
            cls.empresas, cls.pacientes, cls.preconsultas = {}, {}, {}
            cls.reader = RolSistema.objects.create(nombre="Leer preconsulta QA", codigo="preconsulta-reader", activo=True, es_rol_clinico=True, usa_permisos_clinicos_granulares=True, puede_ver_historia_clinica=True)
            cls.editor = RolSistema.objects.create(nombre="HEAD preconsulta QA", codigo="preconsulta-editor", activo=True, es_rol_clinico=True, usa_permisos_clinicos_granulares=True, puede_editar_historia_clinica=True)
            cls.rol = RolSistema.objects.create(nombre="Consulta preconsulta QA", codigo="preconsulta-both", activo=True, es_rol_clinico=True, usa_permisos_clinicos_granulares=True, puede_ver_historia_clinica=True, puede_editar_historia_clinica=True)
            cls.legado = RolSistema.objects.create(nombre="Preconsulta expediente QA", codigo="preconsulta-legacy", activo=True, puede_expediente_clinico=True)
            for index, slug in enumerate(cls.SLUGS, 1):
                company = Empresa.objects.create(nombre=f"Preconsulta QA {slug}", slug=slug, rtn=f"PRECONSULTA-{index}", tipo_solucion="clinica", estado_licencia="activa")
                cls.empresas[slug] = company
                EmpresaModulo.objects.create(empresa=company, modulo=cls.modulo, activo=True)
                patient = Paciente.objects.create(empresa=company, nombre=f"Paciente privado sintético {slug}", identidad=f"08011992000{index:02d}", expediente_codigo=f"PRE-QA-{index}", telefono="PRIVATE-CONTACT", correo="private-preconsulta@example.invalid", foto_perfil="pacientes/PRIVATE-PHOTO.jpg")
                cls.pacientes[slug] = patient
                for key, state in (("filled", "completada"), ("empty", "completada"), ("pending", "pendiente"), ("revoked", "revocada")):
                    cls.preconsultas[(slug, key)] = PreconsultaClinica.objects.create(empresa=company, paciente=patient, tipo="general", token_hash=f"PRIVATE-TOKEN-PRECONSULTA-{index}-{key}", token_preview="PRIVATE-PREVIEW", estado=state, fecha_expiracion=cls.NOW - timedelta(days=1), fecha_completada=cls.NOW - timedelta(hours=1) if state == "completada" else None, datos_generales=deepcopy(cls.DATA) if key == "filled" else {} if key == "empty" else {"formulario_general": ["Corrupto sólo en registro no completado"]}, antecedentes_personales=["antecedente_historico"] if key == "filled" else [] if key == "empty" else [{"secret": "NO-RESPUESTAS-NO-COMPLETADA"}], medicamentos_habituales=["medicamento_historico"] if key == "filled" else [], antecedentes_familiares=["familiar_historico"] if key == "filled" else [], antecedentes_infecciosos="Texto antecedente infeccioso sintético" if key == "filled" else "", ip_completada="198.51.100.77")
            cls.empresa = cls.empresas["hospital_mia"]
            cls.paciente = cls.pacientes[cls.empresa.slug]
            cls.preconsulta = cls.preconsultas[(cls.empresa.slug, "filled")]
            cls.usuario = Usuario.objects.create_user(username="preconsulta-qa", password="test-only", empresa=cls.empresa, rol_sistema=cls.rol)
            cls.usuario.empresas_acceso.add(*cls.empresas.values())
            cls.externa = Empresa.objects.create(nombre="Preconsulta fuera cuatro QA", slug="preconsulta_externa", rtn="PRE-QA-EXT", tipo_solucion="clinica", estado_licencia="activa")
            EmpresaModulo.objects.create(empresa=cls.externa, modulo=cls.modulo)
            cls.usuario.empresas_acceso.add(cls.externa)
            cls.paciente_externo = Paciente.objects.create(empresa=cls.externa, nombre="Paciente externo sintético", identidad="0801199200099", expediente_codigo="PRE-EXT")
            cls.preconsulta_externa = PreconsultaClinica.objects.create(empresa=cls.externa, paciente=cls.paciente_externo, token_hash="PRIVATE-TOKEN-PRECONSULTA-EXT", token_preview="PRIVATE-PREVIEW", estado="completada", fecha_expiracion=cls.NOW, datos_generales=deepcopy(cls.DATA))
            cls.otro_paciente = Paciente.objects.create(empresa=cls.empresa, nombre="Paciente local diferente", identidad="0801199200098", expediente_codigo="PRE-OTHER")

    def setUp(self):
        clock = patch("django.utils.timezone.now", return_value=self.NOW)
        clock.start(); self.addCleanup(clock.stop)
        self.client.force_login(self.usuario)
        self.url()  # URL metadata only, before any mocked initializer is installed.
        self.blocked = {}
        for target in ("clinica.views.asegurar_profesionales_agenda_base", "clinica.views._configuracion_clinica", "clinica.views.PreconsultaClinicaPublicaForm.__init__", "clinica.views._resumen_preconsulta", "clinica.views._sincronizar_agenda_desde_cita_clinica", "crm.appointment_notifications.programar_notificaciones_cita", "crm.appointment_notifications.procesar_notificacion", "crm.services.enviar_plantilla_cita_whatsapp", "django.core.mail.EmailMessage.send", "socket.create_connection", "socket.socket.connect", "socket.socket.connect_ex"):
            blocker = patch(target, side_effect=AssertionError(f"Forbidden private preconsulta effect: {target}"))
            self.blocked[target] = blocker.start()
            self.addCleanup(blocker.stop); self.addCleanup(self.blocked[target].assert_not_called)

    def url(self, empresa=None, paciente=None, preconsulta=None, mobile=True, **query):
        company = empresa or self.empresa
        patient = paciente or self.pacientes.get(company.slug) or self.paciente_externo
        record = preconsulta or self.preconsultas.get((company.slug, "filled")) or self.preconsulta_externa
        parameters = {"app": "1", **query} if mobile else query
        base = reverse(self.ROUTE, args=[company.slug, patient.pk, record.pk])
        return base + ("?" + urlencode(parameters, doseq=True) if parameters else "")

    def use_role(self, role):
        Usuario.objects.filter(pk=self.usuario.pk).update(rol_sistema=role, is_superuser=False, es_administrador_empresa=False)
        UsuarioEmpresaPermiso.objects.filter(usuario=self.usuario).delete()
        self.usuario.refresh_from_db()

    def role(self, granular=True, **flags):
        return RolSistema.objects.create(nombre="Preconsulta rol aislado", codigo=f"preconsulta-role-{RolSistema.objects.count()}", activo=True, es_rol_clinico=granular, usa_permisos_clinicos_granulares=granular, **flags)

    def snapshot(self):
        return {model._meta.label_lower: list(model._base_manager.order_by("pk").values()) for model in sorted(apps.get_models(include_auto_created=True), key=lambda model: model._meta.label_lower)}

    def assert_sql_safe(self, captured, private=False, history=False):
        self.assertEqual([row["sql"] for row in captured if re.match(r"\s*(INSERT|UPDATE|DELETE|REPLACE|CREATE|ALTER|DROP)\b", row["sql"], re.I)], [])
        # Existing history cards still read their original notes/professionals;
        # only the new selected-detail consultation forbids those catalogues.
        forbidden = tuple(table for table in self.FORBIDDEN_TABLES if not history or table not in {"clinica_profesionalsalud", "clinica_servicioclinico"})
        self.assertFalse(any(table in row["sql"].lower() for table in forbidden for row in captured))
        if private:
            self.assertFalse(any(table in row["sql"].lower() for table in ("clinica_paciente", "clinica_preconsultaclinica") for row in captured))

    def read(self, target=None, method="get", history=False, **kwargs):
        before = self.snapshot(); reset_queries()
        with CaptureQueriesContext(connection) as captured:
            response = self.client.generic(method.upper(), target or self.url(), **kwargs) if isinstance(kwargs.get("data"), bytes) else getattr(self.client, method)(target or self.url(), **kwargs)
        self.assert_sql_safe(captured, history=history); self.assertEqual(self.snapshot(), before)
        return response

    def request(self, target=None, method="get", data=None, body=None):
        factory = RequestFactory(); target = target or self.url()
        if body is not None:
            request = factory.generic(method.upper(), target, data=body, content_type="application/json")
        elif method in {"get", "head", "post"}:
            request = getattr(factory, method)(target, data=data or {})
        else:
            request = factory.generic(method.upper(), target)
        request.user = Usuario.objects.get(pk=self.usuario.pk)
        return request

    def direct(self, request=None, target=None):
        request = request or self.request(target)
        match = resolve(urlsplit(target or request.get_full_path()).path)
        return views.preconsulta_detalle.__wrapped__(request, **match.kwargs)

    def denied(self, target=None, method="get", data=None, body=None, request=None, early=True, exception=(PermissionDenied, Http404)):
        request = request or self.request(target, method, data, body)
        before = self.snapshot(); reset_queries()
        with CaptureQueriesContext(connection) as captured:
            with self.assertRaises(exception): self.direct(request=request, target=target)
        self.assert_sql_safe(captured, private=early); self.assertEqual(self.snapshot(), before)

    def sections(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "clinica/mobile_preconsulta_detail.html")
        return response.context["secciones_preconsulta"]

    def filled(self, **updates):
        PreconsultaClinica.objects.filter(pk=self.preconsulta.pk).update(**updates)
        return PreconsultaClinica.objects.get(pk=self.preconsulta.pk)

    def test_four_companies_both_authorized_roles_read_private_app_and_head(self):
        for role in (self.rol, self.legado):
            self.use_role(role)
            for company in self.empresas.values():
                response = self.read(self.url(company)); self.assertTrue(self.sections(response))
                head = self.read(self.url(company), "head")
                self.assertEqual(head.status_code, 200); self.assertEqual(head.content, b"")
                self.assertIn("no-store", response["Cache-Control"]); self.assertIn("no-store", head["Cache-Control"])

    def test_granular_get_read_and_head_edit_permissions_remain_asymmetric(self):
        for company in self.empresas.values():
            self.use_role(self.reader)
            self.assertEqual(self.read(self.url(company)).status_code, 200)
            self.denied(self.url(company), method="head", exception=PermissionDenied)
            self.assertIn(self.client.head(self.url(company)).status_code, (302, 403))
            self.use_role(self.editor)
            self.denied(self.url(company), exception=PermissionDenied)
            head = self.read(self.url(company), "head"); self.assertEqual(head.status_code, 200); self.assertEqual(head.content, b"")

    def test_legacy_expediente_does_not_require_patients_calendar_or_clinical_config(self):
        self.use_role(self.legado)
        self.assertFalse(self.usuario.tiene_permiso_erp("puede_pacientes", self.empresa))
        self.assertFalse(self.usuario.tiene_permiso_erp("puede_citas", self.empresa))
        self.assertEqual(self.read().status_code, 200)

    def test_other_flags_cannot_replace_private_history_permission(self):
        for granular, flags in ((True, {"puede_ver_pacientes": True}), (True, {"puede_configuracion_clinica": True}), (True, {"puede_crear_historia_clinica": True}), (True, {"puede_ver_calendario": True}), (False, {"puede_pacientes": True})):
            self.use_role(self.role(granular, **flags)); self.denied()

    def test_local_company_override_none_does_not_fall_back_global_history_role(self):
        UsuarioEmpresaPermiso.objects.create(usuario=self.usuario, empresa=self.empresa, rol_sistema=None)
        self.denied()

    def test_local_role_authorizes_only_requested_company(self):
        self.use_role(self.role(puede_ver_pacientes=True))
        company = self.empresas["medical_spa"]
        UsuarioEmpresaPermiso.objects.create(usuario=self.usuario, empresa=company, rol_sistema=self.rol)
        self.denied(); self.assertEqual(self.read(self.url(company)).status_code, 200)

    def test_company_membership_profile_active_module_and_license_direct_guards(self):
        for changes in ({"tipo_solucion": "erp"}, {"activa": False}, {"estado_licencia": "suspendida"}, {"fecha_vencimiento_plan": timezone.localdate()-timedelta(days=1)}):
            Empresa.objects.filter(pk=self.empresa.pk).update(**changes); self.denied()
            Empresa.objects.filter(pk=self.empresa.pk).update(tipo_solucion="clinica", activa=True, estado_licencia="activa", fecha_vencimiento_plan=None)
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.modulo).update(activo=False); self.denied()
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.modulo).update(activo=True)
        self.usuario.empresas_acceso.remove(self.empresas["medical_spa"]); self.denied(self.url(self.empresas["medical_spa"]))

    def test_superuser_license_exception_does_not_bypass_active_profile_or_module(self):
        supervisor = Usuario.objects.create_superuser(username="preconsulta-super", password="test-only")
        request = self.request(); request.user = supervisor
        Empresa.objects.filter(pk=self.empresa.pk).update(estado_licencia="suspendida")
        self.assertEqual(self.direct(request=request).status_code, 200)
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.modulo).update(activo=False)
        self.denied(request=request)

    def test_company_admin_original_bypass_does_not_create_records_or_roles(self):
        self.use_role(self.role(puede_ver_pacientes=True))
        Usuario.objects.filter(pk=self.usuario.pk).update(es_administrador_empresa=True)
        self.assertEqual(self.read().status_code, 200)

    def test_logged_out_capability_and_view_have_no_private_queries_or_seed(self):
        from .mobile_preconsultas import preconsultas_disponibles_app
        request = self.request(); request.user = AnonymousUser()
        before = self.snapshot(); self.assertFalse(preconsultas_disponibles_app(request, self.empresa))
        self.assertEqual(Client().get(self.url()).status_code, 302); self.assertEqual(self.snapshot(), before)

    def test_pure_capability_matches_original_method_without_querying_patient_or_preconsulta(self):
        from .mobile_preconsultas import preconsultas_disponibles_app
        for role, get, head in ((self.reader, True, False), (self.editor, False, True), (self.legado, True, True)):
            self.use_role(role); request = self.request(self.url(mobile=False)); before = self.snapshot()
            with CaptureQueriesContext(connection) as captured:
                self.assertEqual(preconsultas_disponibles_app(request, self.empresa, method="GET"), get)
                self.assertEqual(preconsultas_disponibles_app(request, self.empresa, method="HEAD"), head)
            self.assert_sql_safe(captured, private=True); self.assertEqual(self.snapshot(), before)

    def test_any_invalid_or_duplicate_app_marker_fails_closed_within_four(self):
        for value in ("", "0", "true", "01", ["1", "1"], ["0", "1"]): self.denied(self.url(app=value))

    def test_query_closed_no_filters_write_metadata_or_return_urls(self):
        for key in ("q", "estado", "tipo", "page", "empresa", "paciente", "preconsulta", "token", "next", "accion", "formulario"):
            self.denied(self.url() + "&" + urlencode({key: "1"}))

    def test_post_and_other_methods_cannot_reach_public_form_or_original_reader(self):
        for method in ("post", "put", "patch", "delete", "options"):
            self.denied(method=method, data={"app": "1", "accion": "completar"})
        self.denied(self.url(mobile=False), method="post", data={"app": "1"})

    def test_get_head_raw_bodies_and_files_are_rejected_before_private_sql(self):
        for method in ("get", "head"):
            self.denied(method=method, body=b"{}")
            self.assertEqual(self.read(method=method, data=b"{}", content_type="application/json").status_code, 403)
        request = self.request(); request.POST
        request._files = MultiValueDict({"foto": [SimpleUploadedFile("qa.txt", b"synthetic")]})
        self.denied(request=request)

    def test_both_raw_path_ids_are_canonical_ascii_positive_int64_before_sql(self):
        base = reverse(self.ROUTE, args=[self.empresa.slug, self.paciente.pk, self.preconsulta.pk])
        for value in ("0", "01", "9223372036854775808", "999999999999999999999999999"):
            for original in (f"/pacientes/{self.paciente.pk}/", f"/preconsulta/{self.preconsulta.pk}/"):
                target = base.replace(original, original.split(str(self.paciente.pk if "pacientes" in original else self.preconsulta.pk))[0] + value + "/") + "?app=1"
                self.denied(target)

    def test_foreign_or_missing_patient_and_record_do_not_render_private_data(self):
        self.denied(self.url(paciente=self.pacientes["medical_spa"]), early=False, exception=Http404)
        self.denied(self.url(preconsulta=self.preconsultas[("medical_spa", "filled")]), early=False, exception=Http404)
        target = self.url().replace(f"/preconsulta/{self.preconsulta.pk}/", "/preconsulta/9223372036854775807/")
        self.denied(target, early=False, exception=Http404)

    def test_selected_record_wrong_company_or_other_local_patient_is_not_found(self):
        original = {"empresa_id": self.empresa.pk, "paciente_id": self.paciente.pk}
        for updates in ({"empresa_id": self.empresas["medical_spa"].pk}, {"paciente_id": self.otro_paciente.pk}):
            try:
                self.filled(**updates); self.denied(early=False, exception=Http404)
            finally: self.filled(**original)

    def test_inactive_patient_and_expired_completed_record_still_read_original_snapshot(self):
        Paciente.objects.filter(pk=self.paciente.pk).update(activo=False)
        response = self.read(); self.assertTrue(self.sections(response))
        self.assertEqual(response.context["preconsulta"].estado, "completada")

    def test_original_six_sections_parity_order_labels_types_values_and_no_summary_call(self):
        expected = self.original_sections(PreconsultaClinica.objects.get(pk=self.preconsulta.pk))
        actual = self.sections(self.read())
        self.assertEqual(actual, expected)
        self.assertEqual(len(actual), 6)
        self.blocked["clinica.views._resumen_preconsulta"].assert_not_called()

    def test_unknown_historical_codes_duplicates_zero_bool_and_string_choices_preserved(self):
        data = deepcopy(self.DATA); general = data["formulario_general"]
        general.update(procedimientos_interes=["histórico-X", "histórico-X", ""], funcion_apetito="HISTORIC-UNKNOWN", gine_gestas=0, expectativas_realistas=False)
        record = self.filled(datos_generales=data, antecedentes_personales="HISTORIC-STRING")
        sections = self.sections(self.read()); self.assertEqual(sections, self.original_sections(record))
        values = {field["label"]: field["value"] for section in sections for field in section["campos"]}
        self.assertEqual(values["Procedimientos seleccionados"], ["histórico-X", "histórico-X", ""])
        self.assertEqual(values["Antecedentes personales"], ["HISTORIC-STRING"])
        self.assertEqual(values["Gestas"], 0); self.assertEqual(values["Expectativas realistas"], [False])

    def test_empty_completed_record_has_no_sections_or_invented_clinical_values(self):
        response = self.read(self.url(preconsulta=self.preconsultas[(self.empresa.slug, "empty")]))
        self.assertEqual(self.sections(response), [])
        self.assertNotContains(response, "Texto médico sintético registrado")

    def test_non_dict_top_level_json_preserves_original_ignored_behavior(self):
        for value in ([], ["historical"], "historical", 5, True):
            record = self.filled(datos_generales=value)
            self.assertEqual(self.sections(self.read()), self.original_sections(record))

    def test_falsey_formulario_general_keeps_original_empty_mapping_behavior(self):
        for value in (None, "", [], False, 0, {}):
            record = self.filled(datos_generales={"formulario_general": value})
            self.assertEqual(self.sections(self.read()), self.original_sections(record))

    def test_truthy_non_dict_general_is_denied_before_original_section_helper(self):
        for value in (["used"], "used", 5, True):
            self.filled(datos_generales={"formulario_general": value})
            with patch.object(views, "_secciones_preconsulta", side_effect=AssertionError("Invalid shape must precede original helper")) as helper:
                self.denied(early=False, exception=PermissionDenied); helper.assert_not_called()

    def test_nested_used_choice_and_text_containers_denied_not_serialized(self):
        for key, value in (("procedimientos_interes", [{}]), ("procedimientos_interes", [["nested"]]), ("motivo_categoria", {"secret": "UNUSED-JSON-PRIVATE"}), ("historia_mejorar", {"secret": "UNUSED-JSON-PRIVATE"}), ("historia_mejorar", [["nested"]])):
            self.filled(datos_generales={"formulario_general": {key: value}})
            # The original helper alone selects USED keys. Its guarded get()
            # rejects bad values when read, rather than duplicating its schema.
            with patch.object(views, "_secciones_preconsulta", wraps=self.original_sections) as helper:
                self.denied(early=False, exception=PermissionDenied); helper.assert_called_once()

    def test_direct_used_json_choice_arrays_reject_unhashable_nested_values(self):
        for key in ("antecedentes_personales", "medicamentos_habituales", "antecedentes_familiares"):
            original = getattr(self.preconsulta, key)
            for value in ({"secret": "UNUSED-JSON-PRIVATE"}, [["nested"]], [{"secret": "UNUSED-JSON-PRIVATE"}]):
                self.filled(**{key: value}); self.denied(early=False, exception=PermissionDenied)
            self.filled(**{key: original})

    def test_unused_nested_json_keys_are_ignored_and_never_exported(self):
        response = self.read(); self.assertTrue(self.sections(response))
        self.assertNotContains(response, "UNUSED-JSON-PRIVATE")
        self.assertNotIn("unused_private", str(response.context["secciones_preconsulta"]))

    def test_pending_revoked_corrupt_responses_never_parse_or_select_answers(self):
        for key in ("pending", "revoked"):
            record = self.preconsultas[(self.empresa.slug, key)]
            with patch.object(views, "_secciones_preconsulta", side_effect=AssertionError("Noncompleted answers must remain unread")) as helper:
                before = self.snapshot(); reset_queries()
                with CaptureQueriesContext(connection) as captured: response = self.client.get(self.url(preconsulta=record))
                self.assertEqual(self.sections(response), []); helper.assert_not_called()
                self.assert_sql_safe(captured); self.assertEqual(self.snapshot(), before)
                selections = [row["sql"].lower().split(" from ", 1)[0] for row in captured if '"clinica_preconsultaclinica"' in row["sql"].lower() and row["sql"].lstrip().lower().startswith("select")]
                for sql in selections:
                    for field in ("datos_generales", "antecedentes_personales", "medicamentos_habituales", "antecedentes_familiares", "token_hash", "token_preview", "ip_completada", "fecha_expiracion", "creada_por"):
                        self.assertNotIn(field, sql)
                self.assertNotContains(response, "NO-RESPUESTAS-NO-COMPLETADA")

    def test_creator_orphan_is_unused_and_does_not_block_or_leak(self):
        try:
            self.filled(creada_por_id=9223372036854775807)
            self.assertTrue(self.sections(self.read()))
        finally: self.filled(creada_por_id=None)

    def test_existing_foreign_inactive_creator_is_not_exposed_or_required_local(self):
        author = Usuario.objects.create_user(username="PRIVATE-AUTHOR", empresa=self.externa, is_active=False)
        self.filled(creada_por=author)
        response = self.read(); self.assertTrue(self.sections(response)); self.assertNotContains(response, "PRIVATE-AUTHOR")

    def test_minimal_metadata_namespace_and_no_raw_model_or_response_json_context(self):
        response = self.read(); self.sections(response)
        record, patient = response.context["preconsulta"], response.context["paciente"]
        self.assertFalse(hasattr(record, "_meta")); self.assertFalse(hasattr(patient, "_meta"))
        self.assertEqual(set(vars(record)), {"id", "tipo", "tipo_label", "estado", "estado_label", "fecha_completada"})
        for key in ("form", "resumen", "datos_generales", "token", "token_hash", "preconsulta_model", "usuario_creador"):
            self.assertNotIn(key, response.context)
        for sentinel in self.PRIVATE: self.assertNotContains(response, sentinel)

    def test_private_sql_does_not_fetch_token_ip_expiry_contact_photo_or_creator(self):
        reset_queries()
        with CaptureQueriesContext(connection) as captured: response = self.client.get(self.url())
        self.assertTrue(self.sections(response)); self.assert_sql_safe(captured)
        for row in captured:
            sql = row["sql"].lower()
            if not sql.lstrip().startswith("select"): continue
            selected = sql.split(" from ", 1)[0]
            if '"clinica_preconsultaclinica"' in sql:
                for name in ("token_hash", "token_preview", "ip_completada", "fecha_expiracion", "creada_por"):
                    self.assertNotIn(name, selected)
            if '"clinica_paciente"' in sql:
                for name in ("telefono", "correo", "foto_perfil", "direccion"):
                    self.assertNotIn(name, selected)

    def test_readonly_markup_has_no_forms_manifest_public_or_mutation_links(self):
        response = self.read(); nodes = _PreconsultaMarkup(response.content.decode()).nodes
        self.assertFalse(any(tag in {"form", "input", "textarea", "select", "iframe", "table"} for tag, attrs in nodes))
        self.assertNotContains(response, 'rel="manifest"')
        for tag, attrs in nodes:
            if tag == "a":
                href = attrs.get("href", ""); self.assertTrue(href)
                self.assertFalse(any(value in href for value in ("/registro/", "/preconsulta/publica/", "/completar/", "/generar/", "enviar", "token=", "/media/", "/pdf/")))

    def test_xss_long_answer_and_multiline_are_escaped_without_clinical_changes(self):
        data = deepcopy(self.DATA); text = '<script>PRIVATE-XSS</script> & "comillas"\n' + "Texto médico original sin corte. " * 100
        data["formulario_general"]["historia_mejorar"] = text
        self.filled(datos_generales=data)
        response = self.read(); self.assertNotContains(response, "<script>PRIVATE-XSS</script>")
        self.assertContains(response, "&lt;script&gt;PRIVATE-XSS&lt;/script&gt;")
        values = {field["label"]: field["value"] for section in self.sections(response) for field in section["campos"]}
        self.assertEqual(values["Qué le gustaría mejorar"], text)

    def test_back_links_require_real_get_history_permission_no_calendar_or_patient_grant(self):
        self.use_role(self.reader); response = self.read()
        self.assertTrue(response.context["can_view_history"])
        self.assertEqual(parse_qs(urlsplit(response.context["app_patient_histories_url"]).query), {"app": ["1"]})
        self.use_role(self.editor)
        request = self.request(method="head"); response = self.direct(request=request)
        # Direct HEAD rendering is inspectable, but must not offer a GET
        # history URL to an actor whose original GET history permission fails.
        self.assertNotIn(f'href="{reverse("clinica_historias_especialidad", args=[self.empresa.slug, self.paciente.pk])}?app=1"', response.content.decode())

    def test_history_cards_add_private_detail_url_without_public_tokens_or_contacts(self):
        target = reverse("clinica_historias_especialidad", args=[self.empresa.slug, self.paciente.pk]) + "?app=1"
        response = self.read(target, history=True); self.assertContains(response, self.url())
        for sentinel in self.PRIVATE: self.assertNotContains(response, sentinel)
        self.assertFalse(any(hasattr(item, "_meta") for item in response.context["mobile_preconsulta_items"]))
        for item in response.context["mobile_preconsulta_items"]:
            self.assertEqual(set(item), {"id", "tipo_label", "fecha_completada", "motivo_consulta", "url"})

    def test_completed_history_scope_before_original_limit_thirty_and_counts(self):
        for number in range(32):
            PreconsultaClinica.objects.create(empresa=self.empresa, paciente=self.paciente, tipo="capilar" if number % 2 else "general", token_hash=f"PRE-QA-MANY-{number}", token_preview="PRIVATE-PREVIEW", estado="completada", fecha_expiracion=self.NOW, fecha_completada=self.NOW-timedelta(hours=number), motivo_consulta=f"Motivo original sintético {number}", datos_generales={})
        target = reverse("clinica_historias_especialidad", args=[self.empresa.slug, self.paciente.pk]) + "?app=1"
        response = self.read(target, history=True)
        self.assertEqual(len(response.context["mobile_preconsulta_items"]), 30)
        labels = dict(PreconsultaClinica.TIPO_CHOICES)
        expected = [{"id": row["id"], "tipo_label": labels.get(row["tipo"], row["tipo"]), "fecha_completada": row["fecha_completada"], "motivo_consulta": row["motivo_consulta"],
                     "url": reverse(self.ROUTE, args=[self.empresa.slug, self.paciente.pk, row["id"]]) + "?app=1"}
                    for row in PreconsultaClinica.objects.filter(paciente=self.paciente, estado="completada").order_by("-fecha_creacion", "-id").values("id", "tipo", "fecha_completada", "motivo_consulta")[:30]]
        self.assertEqual(response.context["mobile_preconsulta_items"], expected)
        # Existing oldest row is outside the first 30, yet its tenant must be
        # checked before limiting/counting/joining the history presentation.
        self.filled(empresa=self.empresas["medical_spa"])
        before = self.snapshot(); response = self.client.get(target)
        self.assertEqual(response.status_code, 403); self.assertEqual(self.snapshot(), before)

    def test_other_patient_and_noncompleted_foreign_rows_do_not_poison_completed_history(self):
        PreconsultaClinica.objects.create(empresa=self.empresas["medical_spa"], paciente=self.otro_paciente, token_hash="PRE-QA-UNRELATED", token_preview="PRIVATE-PREVIEW", estado="completada", fecha_expiracion=self.NOW, datos_generales={})
        PreconsultaClinica.objects.filter(pk=self.preconsultas[(self.empresa.slug, "pending")].pk).update(empresa=self.empresas["medical_spa"])
        target = reverse("clinica_historias_especialidad", args=[self.empresa.slug, self.paciente.pk]) + "?app=1"
        self.assertEqual(self.read(target, history=True).status_code, 200)

    def test_web_without_app_keeps_original_template_sections_and_summary(self):
        expected = self.original_sections(PreconsultaClinica.objects.get(pk=self.preconsulta.pk))
        before = self.snapshot()
        with patch.object(views, "_resumen_preconsulta", wraps=self.original_summary) as summary:
            response = self.client.get(self.url(mobile=False))
        self.assertEqual(response.status_code, 200); self.assertTemplateUsed(response, "clinica/preconsulta_detalle.html")
        self.assertEqual(response.context["secciones_preconsulta"], expected); summary.assert_called_once()
        self.assertEqual(self.snapshot(), before)

    def test_outside_four_app_flag_keeps_original_desktop_not_new_private_adapter(self):
        before = self.snapshot()
        with patch.object(views, "_resumen_preconsulta", wraps=self.original_summary): response = self.client.get(self.url(self.externa))
        self.assertEqual(response.status_code, 200); self.assertTemplateUsed(response, "clinica/preconsulta_detalle.html")
        self.assertTrue(hasattr(response.context["preconsulta"], "_meta")); self.assertEqual(self.snapshot(), before)

    def test_unflagged_web_post_reader_remains_original_and_valid_csrf_cannot_write_app(self):
        before = self.snapshot()
        with patch.object(views, "_resumen_preconsulta", wraps=self.original_summary): response = self.client.post(self.url(mobile=False), {"accion": "no-op"})
        self.assertEqual(response.status_code, 200); self.assertTemplateUsed(response, "clinica/preconsulta_detalle.html")
        self.assertEqual(self.snapshot(), before)
        strict = Client(enforce_csrf_checks=True); strict.force_login(self.usuario); strict.cookies["csrftoken"] = "a" * 32
        before = self.snapshot()
        reset_queries()
        with CaptureQueriesContext(connection) as captured: response = strict.post(self.url(), {"app": "1", "csrfmiddlewaretoken": "a" * 32})
        self.assertEqual(response.status_code, 403); self.assert_sql_safe(captured, private=True); self.assertEqual(self.snapshot(), before)

    original_sections = staticmethod(views._secciones_preconsulta)
    original_summary = staticmethod(views._resumen_preconsulta)
