"""Internal general-history APP, preserving the original form and side effects.

All fixtures and transports are synthetic. Missing GET/HEAD/invalid POST may
create the original pending preconsulta; existing reads/errors never write.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone as datetime_timezone
from html.parser import HTMLParser
from itertools import count
import re
from urllib.parse import parse_qs, urlencode, urlsplit
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.models import Session
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection, reset_queries
from django.http import Http404
from django.test import Client, RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import resolve, reverse
from django.utils.datastructures import MultiValueDict

from core.clinical_permissions import permiso_clinica_granular
from core.models import Empresa, EmpresaModulo, Modulo, RegistroAuditoria, RolSistema, Usuario, UsuarioEmpresaPermiso
from facturacion.models import Cliente
from . import views
from .forms import PreconsultaClinicaPublicaForm
from .models import Paciente, PreconsultaClinica


class _GeneralHistoryMarkup(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.nodes = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.nodes.append((tag, dict(attrs)))


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class MobileGeneralHistoryIntegrationTests(TestCase):
    SLUGS = ("hospital_mia", "medical_spa", "luque_aestetic", "serviciosmedicos")
    NOW = datetime(2026, 10, 13, 18, 0, tzinfo=datetime_timezone.utc)
    ROUTE = "clinica_completar_historia_clinica_paciente"
    PRIVATE = ("GENERAL-PRIVATE-TOKEN", "GENERAL-PRIVATE-PREVIEW", "GENERAL-PRIVATE-AUTHOR", "UNUSED-PRIVATE-JSON")
    GROUPS = (
        ("nombres", "apellidos", "identidad", "fecha_nacimiento", "sexo", "estado_civil"),
        ("correo", "telefono_codigo_area", "telefono", "direccion", "lugar_nacimiento", "ocupacion", "lugar_trabajo", "informante", "informante_detalle", "contacto_emergencia_completo", "referido_por", "referido_por_detalle"),
        ("motivo_categoria", "procedimientos_interes", "procedimientos_interes_otros"),
        ("funcion_apetito", "funcion_sueno", "funcion_sed", "funcion_miccion", "funcion_evacuaciones"),
        ("diagnostico_medico", "diagnostico_medico_detalle", "alergias_respuesta", "alergias_detalle", "medicamentos_respuesta", "medicamentos_detalle", "antecedentes_infecciosos"),
        ("antecedentes_hospitalarios", "antecedentes_hospitalarios_detalle", "quirurgicos_operado", "quirurgicos_detalle", "consumo_riesgo", "consumo_riesgo_detalle", "dieta", "ejercicio"),
        ("antecedentes_familiares", "antecedentes_familiares_detalle", "riesgo_tromboembolico", "riesgo_tromboembolico_otros"),
        ("gine_menarca", "gine_gestas", "gine_partos", "gine_cesareas", "gine_abortos", "gine_ultima_menstruacion", "gine_embarazada", "gine_lactancia", "gine_mamografia", "gine_mamografia_fecha"),
        ("evaluacion_psicologica", "evaluacion_psicologica_detalle", "decision_cirugia", "decision_cirugia_otros", "consentimiento_datos"),
    )
    HIDDEN = ("primer_nombre", "segundo_nombre", "primer_apellido", "segundo_apellido", "contacto_emergencia", "telefono_emergencia")
    FIELDS = tuple(name for group in GROUPS for name in group) + HIDDEN
    MULTIPLE = tuple(name for name in FIELDS if name in {
        "motivo_categoria", "procedimientos_interes", "antecedentes_hospitalarios", "quirurgicos_operado", "consumo_riesgo", "dieta", "ejercicio",
        "antecedentes_familiares", "riesgo_tromboembolico", "gine_embarazada", "gine_lactancia", "gine_mamografia", "evaluacion_psicologica", "decision_cirugia"})
    DATA = {
        "formulario_general_pendiente_doctor": True,
        "unknown_top": {"nested": ["UNUSED-PRIVATE-JSON", {"kept": False}]},
        "formulario_general": {"diagnostico_medico": "si", "diagnostico_medico_detalle": "Asma histórica sintética",
            "alergias_respuesta": "no", "medicamentos_respuesta": "si", "medicamentos_detalle": "Uso diario histórico",
            "medicamentos_actuales_seleccion": ["anticonceptivos"], "expectativas_realistas": ["si"],
            "unknown_general": {"nested": [0, False, {"private": "UNUSED-PRIVATE-JSON"}]},
            "consumo_riesgo": ["historico"], "consumo_riesgo_detalle": "Texto anterior", "evaluacion_psicologica": ["codigo_historico"]},
    }
    BUSINESS_ALLOWED = {"clinica.preconsultaclinica", "clinica.paciente", "facturacion.cliente", "contabilidad.cuentacontable", "core.registroauditoria"}
    PRIVATE_TABLES = ("clinica_paciente", "clinica_preconsultaclinica", "facturacion_cliente")
    FORBIDDEN_TABLES = ("clinica_profesionalsalud", "clinica_servicioclinico", "clinica_configuracionclinica", "crm_configuracioncrm", "crm_citacliente", "crm_notificacionprogramadacita", "facturacion_factura", "facturacion_pagofactura", "facturacion_inventarioproducto")

    @classmethod
    def setUpTestData(cls):
        with patch("django.utils.timezone.now", return_value=cls.NOW):
            cls.modulo, _ = Modulo.objects.get_or_create(codigo="clinica_medica", defaults={"nombre": "Clínica historia general QA"})
            cls.rol = RolSistema.objects.create(nombre="Completar historia QA", codigo="general-history-edit", activo=True, es_rol_clinico=True, usa_permisos_clinicos_granulares=True, puede_editar_historia_clinica=True)
            cls.legado = RolSistema.objects.create(nombre="Historia pacientes legado QA", codigo="general-history-legacy", activo=True, puede_pacientes=True)
            cls.empresas, cls.pacientes, cls.preconsultas, cls.nuevos = {}, {}, {}, {}
            for index, slug in enumerate(cls.SLUGS, 1):
                company = Empresa.objects.create(nombre=f"Historia general QA {slug}", slug=slug, rtn=f"GENERAL-QA-{index}", tipo_solucion="clinica", estado_licencia="activa")
                cls.empresas[slug] = company
                EmpresaModulo.objects.create(empresa=company, modulo=cls.modulo, activo=True)
                patient = Paciente.objects.create(empresa=company, nombre=f"Ana General {index}", primer_nombre="Ana", primer_apellido=f"General{index}", identidad=f"08011992003{index:02d}", expediente_codigo=f"GENERAL-QA-{index}", fecha_nacimiento="1992-05-10", sexo="femenino", estado_civil="soltero", telefono="99990001", whatsapp="99990001")
                cls.pacientes[slug] = patient
                cls.preconsultas[slug] = PreconsultaClinica.objects.create(empresa=company, paciente=patient, tipo="general", token_hash=f"GENERAL-PRIVATE-TOKEN-{index}", token_preview="GENERAL-PRIVATE-PREVIEW", fecha_expiracion=cls.NOW+timedelta(days=5), datos_generales=deepcopy(cls.DATA), antecedentes_personales=["asma"], medicamentos_habituales=["historico"], medicamentos_habituales_detalle="Uso diario histórico", alergias="Detalle histórico", funciones_organicas="alterada", funciones_detalle="Texto histórico", dieta="Vegetariana histórica", ejercicio="Ejercicio histórico")
                cls.nuevos[slug] = Paciente.objects.create(empresa=company, nombre=f"Paciente sin general {index}", primer_nombre="Paciente", primer_apellido=f"Nuevo{index}", identidad=f"08011993003{index:02d}", expediente_codigo=f"GENERAL-NEW-{index}", fecha_nacimiento="1993-01-02", sexo="masculino", estado_civil="soltero", telefono="99990002")
            cls.empresa = cls.empresas["hospital_mia"]; cls.paciente = cls.pacientes[cls.empresa.slug]; cls.preconsulta = cls.preconsultas[cls.empresa.slug]
            cls.usuario = Usuario.objects.create_user(username="general-history-qa", password="test-only", empresa=cls.empresa, rol_sistema=cls.rol)
            cls.usuario.empresas_acceso.add(*cls.empresas.values())
            cls.externa = Empresa.objects.create(nombre="Historia general externa QA", slug="general_history_externa", rtn="GENERAL-QA-EXT", tipo_solucion="clinica", estado_licencia="activa")
            EmpresaModulo.objects.create(empresa=cls.externa, modulo=cls.modulo)
            cls.usuario.empresas_acceso.add(cls.externa)
            cls.paciente_externo = Paciente.objects.create(empresa=cls.externa, nombre="Paciente externo general QA", primer_nombre="Externo", primer_apellido="General", identidad="0801199200399", expediente_codigo="GENERAL-EXT", fecha_nacimiento="1992-05-10", sexo="femenino", estado_civil="soltero", telefono="99990003")
            cls.preconsulta_externa = PreconsultaClinica.objects.create(empresa=cls.externa, paciente=cls.paciente_externo, token_hash="GENERAL-PRIVATE-TOKEN-EXT", token_preview="PRIVATE", fecha_expiracion=cls.NOW, datos_generales={})

    def setUp(self):
        clock = patch("django.utils.timezone.now", return_value=self.NOW); clock.start(); self.addCleanup(clock.stop)
        self.client.force_login(self.usuario)
        self.url()  # Resolve URL metadata before blocking public view callables.
        self.blocked = {}
        for target in ("clinica.views.asegurar_profesionales_agenda_base", "clinica.views._configuracion_clinica", "clinica.views._resumen_preconsulta", "clinica.views._sincronizar_agenda_desde_cita_clinica", "crm.appointment_notifications.programar_notificaciones_cita", "crm.appointment_notifications.procesar_notificacion", "crm.services.enviar_plantilla_cita_whatsapp", "django.core.mail.EmailMessage.send", "socket.create_connection", "socket.socket.connect", "socket.socket.connect_ex"):
            blocker = patch(target, side_effect=AssertionError(f"Forbidden general-history effect: {target}"))
            self.blocked[target] = blocker.start(); self.addCleanup(blocker.stop); self.addCleanup(self.blocked[target].assert_not_called)
        sequence = count()
        self.token_generator = patch.object(views, "generar_token_preconsulta", side_effect=lambda: ("GENERAL-PRIVATE-TOKEN-RAW", f"GENERAL-PRIVATE-TOKEN-CREATED-{next(sequence)}", "GENERAL-PRIVATE-PREVIEW"))
        self.token_mock = self.token_generator.start(); self.addCleanup(self.token_generator.stop)

    def url(self, empresa=None, paciente=None, mobile=True, **query):
        company = empresa or self.empresa; patient = paciente or self.pacientes.get(company.slug) or self.paciente_externo
        base = reverse(self.ROUTE, args=[company.slug, patient.pk]); values = {"app": "1", **query} if mobile else query
        return base + ("?" + urlencode(values, doseq=True) if values else "")

    def data(self, paciente=None, mobile=True, **updates):
        patient = paciente or self.paciente
        values = {name: [] if name in self.MULTIPLE else "" for name in self.FIELDS}
        values.update(nombres="Ana", apellidos=f"Actualizada{patient.pk}", identidad=patient.identidad, fecha_nacimiento="1992-05-10", sexo="femenino", estado_civil="soltero", correo="general-qa@example.invalid", telefono_codigo_area="504", telefono="99998888", lugar_nacimiento="Tegucigalpa", ocupacion="Ocupación sintética", lugar_trabajo="No aplica", informante="yo_mismo", contacto_emergencia_completo="Contacto sintético - 9999-9999", referido_por="facebook", motivo_categoria=["no_aplica"], procedimientos_interes=[], procedimientos_interes_otros="No aplica", funcion_apetito="normal", funcion_sueno="normal", funcion_sed="normal", funcion_miccion="normal", funcion_evacuaciones="normal", diagnostico_medico="no", alergias_respuesta="no", medicamentos_respuesta="no", antecedentes_infecciosos="No aplica", antecedentes_hospitalarios=["no"], quirurgicos_operado=["no"], consumo_riesgo=["no"], dieta=["no"], ejercicio=["si"], antecedentes_familiares=["no_aplica"], riesgo_tromboembolico=["ninguno"], evaluacion_psicologica=["ninguna"], consentimiento_datos="on")
        if mobile: values["app"] = "1"
        values.update(updates); return values

    def use_role(self, role=None, **flags):
        if role is None:
            role = RolSistema.objects.create(nombre="General rol aislado", codigo=f"general-role-{RolSistema.objects.count()}", activo=True, es_rol_clinico=True, usa_permisos_clinicos_granulares=True, **flags)
        Usuario.objects.filter(pk=self.usuario.pk).update(rol_sistema=role, is_superuser=False, es_administrador_empresa=False)
        UsuarioEmpresaPermiso.objects.filter(usuario=self.usuario).delete(); self.usuario.refresh_from_db(); return role

    def snapshot(self):
        return {model._meta.label_lower: list(model._base_manager.order_by("pk").values()) for model in sorted(apps.get_models(include_auto_created=True), key=lambda model: model._meta.label_lower)}

    def changed(self, before):
        after = self.snapshot(); return {label for label in before if before[label] != after[label]}

    def assert_no_writes(self, captured):
        self.assertEqual([row["sql"] for row in captured if re.match(r"\s*(INSERT|UPDATE|DELETE|REPLACE|CREATE|ALTER|DROP)\b", row["sql"], re.I)], [])

    def assert_no_catalogues(self, captured):
        self.assertFalse(any(table in row["sql"].lower() for table in self.FORBIDDEN_TABLES for row in captured))

    def request(self, target=None, method="get", data=None, body=None):
        target = target or self.url(); factory = RequestFactory()
        if body is not None: request = factory.generic(method.upper(), target, data=body, content_type="application/json")
        elif method in {"get", "head", "post"}: request = getattr(factory, method)(target, data=data or {})
        else: request = factory.generic(method.upper(), target)
        request.user = Usuario.objects.get(pk=self.usuario.pk)
        return request

    def direct(self, request=None, target=None, paciente_id=None):
        request = request or self.request(target)
        if paciente_id is not None: return views.completar_historia_clinica_paciente.__wrapped__(request, self.empresa.slug, paciente_id)
        match = resolve(urlsplit(target or request.get_full_path()).path)
        return views.completar_historia_clinica_paciente.__wrapped__(request, **match.kwargs)

    def denied(self, target=None, method="get", data=None, body=None, request=None, early=True, exception=(PermissionDenied, Http404), paciente_id=None):
        request = request or self.request(target, method, data, body); before = self.snapshot(); reset_queries()
        with CaptureQueriesContext(connection) as captured:
            with self.assertRaises(exception): self.direct(request=request, target=target, paciente_id=paciente_id)
        self.assert_no_writes(captured); self.assert_no_catalogues(captured); self.assertEqual(self.snapshot(), before)
        if early: self.assertFalse(any(table in row["sql"].lower() for table in self.PRIVATE_TABLES for row in captured))

    def unchanged(self, target=None, method="get", data=None, body=None):
        before = self.snapshot(); reset_queries()
        with CaptureQueriesContext(connection) as captured:
            response = self.client.generic(method.upper(), target or self.url(), data=body, content_type="application/json") if body is not None else getattr(self.client, method)(target or self.url(), data=data or {})
        self.assert_no_writes(captured); self.assert_no_catalogues(captured); self.assertEqual(self.snapshot(), before)
        return response

    def assert_mobile(self, response, bound=False):
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "clinica/mobile_general_history_form.html")
        self.assertIsInstance(response.context["form"], PreconsultaClinicaPublicaForm)
        self.assertEqual(response.context["form"].is_bound, bound)
        self.assertIn("no-store", response["Cache-Control"])

    def assert_seed_only(self, before, patient, response, method="GET"):
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.changed(before), {"clinica.preconsultaclinica", "core.registroauditoria"})
        record = PreconsultaClinica.objects.get(paciente=patient, tipo="general")
        self.assertEqual(record.empresa_id, patient.empresa_id); self.assertEqual(record.creada_por_id, self.usuario.pk)
        self.assertEqual(record.estado, "pendiente"); self.assertEqual(record.fecha_expiracion, self.NOW + timedelta(days=30))
        self.assertEqual(record.datos_generales, {"formulario_general_pendiente_doctor": True})
        self.assertEqual(record.token_preview, "GENERAL-PRIVATE-PREVIEW")
        audit = RegistroAuditoria.objects.filter(app_label="clinica", modelo="preconsultaclinica", objeto_id=str(record.pk)).get()
        self.assertEqual(audit.accion, "crear"); self.assertEqual(audit.usuario_id, self.usuario.pk); self.assertEqual(audit.metodo_http, method)
        if method != "HEAD":
            for sentinel in self.PRIVATE: self.assertNotContains(response, sentinel)

    def test_four_companies_both_roles_existing_get_head_are_immutable(self):
        for role in (self.rol, self.legado):
            self.use_role(role)
            for company in self.empresas.values():
                self.assert_mobile(self.unchanged(self.url(company)))
                head = self.unchanged(self.url(company), "head"); self.assertEqual(head.status_code, 200); self.assertEqual(head.content, b"")
        self.token_mock.assert_not_called()

    def test_get_head_post_mapper_is_edit_history_not_read_or_create(self):
        suffix = f"pacientes/{self.paciente.pk}/historia-clinica/completar/"
        for method in ("GET", "HEAD", "POST"): self.assertEqual(permiso_clinica_granular(suffix, method), "puede_editar_historia_clinica")
        self.assert_mobile(self.unchanged()); self.assert_mobile(self.unchanged(method="post", data=self.data(nombres="")), bound=True)
        self.assertFalse(self.usuario.tiene_permiso_erp("puede_ver_historia_clinica", self.empresa))

    def test_legacy_patients_alone_allowed_but_expediente_alone_denied(self):
        self.use_role(self.legado); self.assert_mobile(self.unchanged())
        self.assertFalse(self.usuario.tiene_permiso_erp("puede_expediente_clinico", self.empresa))
        self.use_role(RolSistema.objects.create(nombre="Sólo expediente QA", codigo="general-only-exp", activo=True, puede_expediente_clinico=True)); self.denied()

    def test_read_create_config_patient_or_calendar_flags_do_not_grant_edit(self):
        for flag in ("puede_ver_historia_clinica", "puede_crear_historia_clinica", "puede_configuracion_clinica", "puede_editar_pacientes", "puede_ver_calendario"):
            self.use_role(**{flag: True}); self.denied(); self.denied(method="post", data=self.data())

    def test_local_override_none_never_falls_back_global_role(self):
        UsuarioEmpresaPermiso.objects.create(usuario=self.usuario, empresa=self.empresa, rol_sistema=None)
        self.denied(); self.denied(method="head"); self.denied(method="post", data=self.data())

    def test_local_override_grants_only_its_company(self):
        self.use_role(puede_ver_historia_clinica=True)
        UsuarioEmpresaPermiso.objects.create(usuario=self.usuario, empresa=self.empresa, rol_sistema=self.rol)
        self.assert_mobile(self.unchanged()); self.denied(self.url(self.empresas["medical_spa"]))

    def test_anonymous_and_inactive_users_cannot_reach_private_or_seed(self):
        request = self.request(self.url(paciente=self.nuevos[self.empresa.slug])); request.user = AnonymousUser(); self.denied(request=request)
        Usuario.objects.filter(pk=self.usuario.pk).update(is_active=False)
        # The original authentication backend excludes inactive accounts; do
        # not invent a second policy for an impossible authenticated RF user.
        response = self.unchanged(self.url(paciente=self.nuevos[self.empresa.slug]))
        self.assertEqual(response.status_code, 302); self.token_mock.assert_not_called()

    def test_inactive_company_wrong_profile_or_missing_clinical_module_denied(self):
        Empresa.objects.filter(pk=self.empresa.pk).update(activa=False); self.denied(exception=Http404)
        Empresa.objects.filter(pk=self.empresa.pk).update(activa=True, tipo_solucion="erp"); self.denied()
        Empresa.objects.filter(pk=self.empresa.pk).update(tipo_solucion="clinica")
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.modulo).update(activo=False); self.denied()

    def test_membership_licence_and_original_admin_super_exceptions(self):
        Usuario.objects.filter(pk=self.usuario.pk).update(empresa=self.externa); self.usuario.empresas_acceso.clear(); self.denied()
        Usuario.objects.filter(pk=self.usuario.pk).update(empresa=self.empresa); Empresa.objects.filter(pk=self.empresa.pk).update(estado_licencia="suspendida"); self.denied()
        Usuario.objects.filter(pk=self.usuario.pk).update(es_administrador_empresa=True); self.denied()
        Usuario.objects.filter(pk=self.usuario.pk).update(is_superuser=True); self.assert_mobile(self.unchanged())
        Empresa.objects.filter(pk=self.empresa.pk).update(estado_licencia="activa")
        Usuario.objects.filter(pk=self.usuario.pk).update(is_superuser=False, es_administrador_empresa=True, rol_sistema=None); self.assert_mobile(self.unchanged())

    def test_inactive_patient_remains_original_authorized_history(self):
        Paciente.objects.filter(pk=self.paciente.pk).update(activo=False); self.assert_mobile(self.unchanged())

    def test_noncanonical_overflow_path_ids_denied_before_patient_or_seed(self):
        base = self.url()
        for value in ("0", "00"+str(self.paciente.pk), "9223372036854775808", "１２", "-1"):
            target = base.replace(f"/{self.paciente.pk}/historia-clinica/", f"/{value}/historia-clinica/")
            self.denied(target, request=self.request(target), paciente_id=value)

    def test_missing_and_foreign_patients_return_404_without_seed(self):
        self.denied(self.url(paciente=self.paciente_externo), early=False, exception=Http404)
        target = self.url().replace(f"/{self.paciente.pk}/historia-clinica/", "/999999999/historia-clinica/")
        self.denied(target, early=False, exception=Http404); self.token_mock.assert_not_called()

    def test_closed_query_duplicates_bad_app_and_unknown_keys_before_getter(self):
        for target in (self.url(app="0"), self.url()+"&app=1", self.url()+"&app=0", self.url()+"&"+urlencode({"empresa": "1"}), self.url()+"&page=1", self.url()+"&preconsulta=1"):
            self.denied(target)

    def test_unsupported_methods_never_seed_or_mutate(self):
        for method in ("put", "patch", "delete", "options", "trace"):
            self.denied(self.url(paciente=self.nuevos[self.empresa.slug]), method=method)

    def test_get_head_bodies_and_files_rejected_before_seed(self):
        for method in ("get", "head"):
            self.denied(method=method, body=b"{}"); request = self.request(method=method); request.POST
            request._files = MultiValueDict({"foto_perfil": [SimpleUploadedFile("synthetic.png", b"not-an-image", content_type="image/png")]})
            self.denied(request=request)

    def test_post_requires_matching_single_app_query_and_body(self):
        for target, body in ((self.url(), self.data(app="0")), (self.url(), self.data(app=["1", "1"])), (self.url(app="0"), self.data()), (self.url(mobile=False), self.data())):
            self.denied(target, method="post", data=body)
        body = self.data(); body.pop("app"); self.denied(method="post", data=body)

    def test_unknown_retired_json_identity_and_photo_body_keys_rejected(self):
        for key in ("empresa", "paciente", "preconsulta", "estado", "creada_por", "datos_generales", "token_hash", "ip_completada", "foto_perfil", "examen_imc", "historia_mejorar", "expectativas_realistas", "accion", "initial-token_hash"):
            self.denied(method="post", data={**self.data(), key: "1"})

    def test_scalar_radio_boolean_and_hidden_duplicates_rejected_before_getter(self):
        for key in ("nombres", "identidad", "funcion_apetito", "diagnostico_medico", "consentimiento_datos", "primer_nombre", "telefono_emergencia", "csrfmiddlewaretoken"):
            self.denied(method="post", data={**self.data(), key: ["on", "on"]})

    def test_legitimate_multichoices_pass_to_original_form_and_invalid_selection_bound(self):
        values = self.data(nombres="", motivo_categoria=["cirugia_mamaria", "contorno_corporal"], antecedentes_familiares=["diabetes", "hipertension"])
        response = self.unchanged(method="post", data=values); self.assert_mobile(response, bound=True)
        self.assertEqual(response.context["form"].data.getlist("motivo_categoria"), values["motivo_categoria"])
        response = self.unchanged(method="post", data=self.data(dieta=["si", "no"])); self.assert_mobile(response, bound=True)
        self.assertIn("dieta", response.context["form"].errors)

    def test_all_files_are_denied_including_original_unused_virtual_photo(self):
        for name in ("foto_perfil", "archivo", "logo"):
            self.denied(method="post", data={**self.data(), name: SimpleUploadedFile("synthetic.jpg", b"not-an-image", content_type="image/jpeg")})

    def test_missing_get_creates_only_original_pending_preconsulta_and_audit(self):
        patient = self.nuevos[self.empresa.slug]; before = self.snapshot(); response = self.client.get(self.url(paciente=patient))
        self.assert_seed_only(before, patient, response); self.assert_mobile(response); self.token_mock.assert_called_once()
        self.assert_mobile(self.unchanged(self.url(paciente=patient))); self.assertEqual(self.token_mock.call_count, 1)

    def test_missing_head_preserves_original_seed_with_empty_http_body(self):
        patient = self.nuevos[self.empresa.slug]; before = self.snapshot(); response = self.client.head(self.url(paciente=patient))
        self.assert_seed_only(before, patient, response, "HEAD"); self.assertEqual(response.content, b""); self.token_mock.assert_called_once()

    def test_missing_invalid_post_seeds_only_original_pending_record_before_validation(self):
        patient = self.nuevos[self.empresa.slug]; before = self.snapshot(); response = self.client.post(self.url(paciente=patient), self.data(patient, nombres=""))
        self.assert_seed_only(before, patient, response, "POST"); self.assert_mobile(response, bound=True); self.assertIn("nombres", response.context["form"].errors)

    def test_existing_invalid_post_retains_all_native_values_and_all_models(self):
        body = self.data(nombres="", historia_mejorar="<script>IGNORED</script>")
        body.pop("historia_mejorar"); body.update(diagnostico_medico="si", diagnostico_medico_detalle="Texto largo \"sintético\" & <b>literal</b>"*12)
        response = self.unchanged(method="post", data=body); self.assert_mobile(response, bound=True)
        for key, value in body.items():
            if isinstance(value, list): self.assertEqual(response.context["form"].data.getlist(key), value)
            else: self.assertEqual(response.context["form"].data.get(key), value)
        self.token_mock.assert_not_called(); self.assertNotContains(response, "<b>literal</b>")

    def test_native_nine_groups_sixty_visible_six_hidden_exact_original_widgets(self):
        response = self.unchanged(); self.assert_mobile(response); markup = _GeneralHistoryMarkup(response.content.decode())
        controls = [(tag, values) for tag, values in markup.nodes if tag in {"input", "textarea", "select"} and values.get("name")]
        names = {values["name"] for _, values in controls}
        self.assertEqual(names, set(self.FIELDS)|{"app", "csrfmiddlewaretoken"}); self.assertEqual(len(set(self.FIELDS)), 66)
        for key in (*self.HIDDEN, "app", "csrfmiddlewaretoken"):
            self.assertEqual(len([values for _, values in controls if values["name"] == key]), 1)
        for name in self.FIELDS:
            original = PreconsultaClinicaPublicaForm.base_fields[name]
            self.assertEqual(response.context["form"].fields[name].__class__, original.__class__)
            self.assertEqual(response.context["form"].fields[name].widget.__class__, original.widget.__class__)
        self.assertEqual([tuple(field.name for field in section["campos"]) for section in response.context["mobile_complete_history_sections"]], list(self.GROUPS))
        self.assertEqual(tuple(field.name for field in response.context["mobile_complete_history_hidden_fields"]), self.HIDDEN)
        self.assertNotIn("foto_perfil", names); self.assertNotIn("examen_imc", names)

    def test_dates_iso_only_presentation_and_raw_invalid_date_retained(self):
        response = self.unchanged(); nodes = _GeneralHistoryMarkup(response.content.decode()).nodes
        self.assertEqual(next(values["value"] for _, values in nodes if values.get("name") == "fecha_nacimiento"), "1992-05-10")
        response = self.unchanged(method="post", data=self.data(fecha_nacimiento="fecha-sintetica-invalida")); self.assert_mobile(response, bound=True)
        self.assertEqual(response.context["form"].data["fecha_nacimiento"], "fecha-sintetica-invalida"); self.assertIn("fecha_nacimiento", response.context["form"].errors)

    def test_no_private_token_ip_author_json_photos_or_public_transport_in_markup(self):
        response = self.unchanged(); content = response.content.decode()
        for sentinel in self.PRIVATE: self.assertNotContains(response, sentinel)
        self.assertNotIn("localStorage", content); self.assertNotIn("/preconsulta/publica/", content)
        self.assertFalse(any(values.get("type") == "file" for _, values in _GeneralHistoryMarkup(content).nodes))
        self.assertFalse(any(values.get("name") in {"token", "token_hash", "token_preview", "ip_completada", "foto_perfil"} for _, values in _GeneralHistoryMarkup(content).nodes))
        for key in ("preconsulta", "paciente"):
            self.assertFalse(hasattr(response.context[key], "_meta")); self.assertFalse(hasattr(response.context[key], "token_hash"))

    def test_selected_general_imported_wrong_company_denied_before_form_or_save(self):
        PreconsultaClinica.objects.filter(pk=self.preconsulta.pk).update(empresa=self.empresas["medical_spa"])
        with patch.object(views.PreconsultaClinicaPublicaForm, "__init__", side_effect=AssertionError("Must reject before Form")):
            self.denied(early=False); self.denied(method="post", data=self.data(), early=False)
        PreconsultaClinica.objects.filter(pk=self.preconsulta.pk).update(empresa=self.empresa)
        imported = PreconsultaClinica.objects.create(empresa=self.empresas["medical_spa"], paciente=self.paciente, tipo="general", token_hash="GENERAL-IMPORTED-OLDER", token_preview="OLD", fecha_expiracion=self.NOW, datos_generales={})
        PreconsultaClinica.objects.filter(pk=imported.pk).update(fecha_creacion=self.NOW-timedelta(days=2))
        self.denied(early=False); self.denied(method="post", data=self.data(), early=False)

    def test_creator_null_or_existing_foreign_inactive_is_original_and_preserved(self):
        author = Usuario.objects.create_user(username="GENERAL-PRIVATE-AUTHOR", empresa=self.externa, is_active=False)
        for author_id in (None, author.pk):
            PreconsultaClinica.objects.filter(pk=self.preconsulta.pk).update(creada_por_id=author_id)
            before = self.snapshot(); response = self.client.post(self.url(), self.data())
            self.assertEqual(response.status_code, 302); self.assertTrue(self.changed(before) <= self.BUSINESS_ALLOWED)
            self.preconsulta.refresh_from_db(); self.assertEqual(self.preconsulta.creada_por_id, author_id or self.usuario.pk)

    def test_orphan_creator_used_by_saver_denied_without_mutation(self):
        PreconsultaClinica.objects.filter(pk=self.preconsulta.pk).update(creada_por_id=999999999)
        self.addCleanup(lambda: PreconsultaClinica.objects.filter(pk=self.preconsulta.pk).update(creada_por_id=None))
        self.assert_mobile(self.unchanged())  # Unused author is not a GET rule.
        self.denied(method="post", data=self.data(), early=False)

    def test_foreign_client_original_fallback_allowed_but_orphan_denied_only_for_post(self):
        client = Cliente.objects.create(empresa=self.externa, nombre="Cliente externo general sintético", rtn="0801199609999")
        original = list(Cliente.objects.filter(pk=client.pk).values())
        Paciente.objects.filter(pk=self.paciente.pk).update(cliente_id=client.pk)
        self.assert_mobile(self.unchanged()); response = self.client.post(self.url(), self.data())
        self.assertEqual(response.status_code, 302); self.paciente.refresh_from_db()
        self.assertEqual(self.paciente.cliente.empresa_id, self.empresa.pk)
        self.assertEqual(list(Cliente.objects.filter(pk=client.pk).values()), original)
        own_client_id = self.paciente.cliente_id
        Paciente.objects.filter(pk=self.paciente.pk).update(cliente_id=999999998)
        self.addCleanup(lambda: Paciente.objects.filter(pk=self.paciente.pk).update(cliente_id=own_client_id))
        self.assert_mobile(self.unchanged()); self.denied(method="post", data=self.data(), early=False)

    def test_used_incompatible_json_fails_closed_before_constructor_but_top_nondict_ignored(self):
        for data in ({"formulario_general": ["truthy"]}, {"formulario_general": {"motivo_categoria": [{"nested": True}]}}, {"formulario_general": {"alergias_detalle": {"nested": True}}},
                     {"formulario_general": {"procedimientos_interes": 1}}, {"formulario_general": {"procedimientos_interes": True}}):
            PreconsultaClinica.objects.filter(pk=self.preconsulta.pk).update(datos_generales=data)
            with patch.object(views.PreconsultaClinicaPublicaForm, "__init__", side_effect=AssertionError("Guard before Form")):
                self.denied(early=False); self.denied(method="post", data=self.data(), early=False)
        for data in (["ignored-original"], None, {"formulario_general": False}):
            PreconsultaClinica.objects.filter(pk=self.preconsulta.pk).update(datos_generales=data if data is not None else {})
            self.assert_mobile(self.unchanged())

    def test_unused_nested_json_is_preserved_exactly_by_original_valid_save(self):
        before = deepcopy(self.preconsulta.datos_generales); response = self.client.post(self.url(), self.data())
        self.assertEqual(response.status_code, 302); self.preconsulta.refresh_from_db()
        self.assertEqual(self.preconsulta.datos_generales["unknown_top"], before["unknown_top"])
        self.assertEqual(self.preconsulta.datos_generales["formulario_general"]["unknown_general"], before["formulario_general"]["unknown_general"])

    def test_original_form_save_and_patient_updater_execute_once_without_parallel_model_path(self):
        original_save = PreconsultaClinicaPublicaForm.save
        for role in (self.rol, self.legado):
            self.use_role(role)
            for company in self.empresas.values():
                patient = self.pacientes[company.slug]
                with patch.object(PreconsultaClinicaPublicaForm, "save", autospec=True, side_effect=original_save) as save, patch.object(views, "_actualizar_paciente_desde_preconsulta", wraps=views._actualizar_paciente_desde_preconsulta) as update:
                    before = self.snapshot(); response = self.client.post(self.url(company), self.data(patient))
                self.assertEqual(response.status_code, 302); save.assert_called_once(); self.assertEqual(save.call_args.kwargs, {"commit": False}); update.assert_called_once()
                self.assertIs(update.call_args.args[1], save.call_args.args[0]); self.assertTrue(self.changed(before) <= self.BUSINESS_ALLOWED)
                record = PreconsultaClinica.objects.get(pk=self.preconsultas[company.slug].pk)
                self.assertEqual(record.estado, "completada"); self.assertEqual(record.empresa_id, company.pk); self.assertEqual(record.paciente_id, patient.pk)

    def test_full_original_form_expected_model_and_json_match_valid_app(self):
        payload = self.data(diagnostico_medico="si", diagnostico_medico_detalle="Asma controlada", alergias_respuesta="si", alergias_detalle="Alergia sintética", medicamentos_respuesta="si", medicamentos_detalle="Medicación sintética", funcion_sed="alterada")
        expected = PreconsultaClinicaPublicaForm(payload, instance=PreconsultaClinica.objects.get(pk=self.preconsulta.pk), paciente=self.paciente, empresa=self.empresa)
        self.assertTrue(expected.is_valid(), expected.errors); expected_record = expected.save(commit=False); expected_data = expected.datos_generales_limpios(); expected_data.pop("formulario_general_pendiente_doctor", None)
        response = self.client.post(self.url(), payload); self.assertEqual(response.status_code, 302); self.preconsulta.refresh_from_db()
        self.assertEqual(self.preconsulta.datos_generales, expected_data)
        for field in PreconsultaClinica._meta.concrete_fields:
            if field.name in {"id", "datos_generales", "estado", "fecha_completada", "ip_completada", "creada_por", "fecha_actualizacion"}: continue
            self.assertEqual(field.value_from_object(self.preconsulta), field.value_from_object(expected_record), field.name)

    def test_historical_arrays_unknown_keys_and_model_backups_remain_original(self):
        response = self.client.post(self.url(), self.data()); self.assertEqual(response.status_code, 302); self.preconsulta.refresh_from_db()
        self.assertEqual(self.preconsulta.antecedentes_personales, ["asma"]); self.assertEqual(self.preconsulta.medicamentos_habituales, ["historico"])
        general = self.preconsulta.datos_generales["formulario_general"]
        self.assertEqual(general["medicamentos_actuales_seleccion"], ["anticonceptivos"]); self.assertEqual(general["expectativas_realistas"], ["si"])
        self.assertEqual(general["medicamentos_detalle_historico"], "Uso diario histórico"); self.assertEqual(general["alergias_detalle_historico"], "Detalle histórico")
        self.assertEqual(general["dieta_historico"], "Vegetariana histórica"); self.assertEqual(general["consumo_riesgo_historico"], ["historico"])
        self.assertEqual(general["evaluacion_psicologica_historica"], ["codigo_historico"])

    def test_completion_original_date_ip_and_audit_actor_are_not_body_authority(self):
        response = self.client.post(self.url(), self.data(), HTTP_X_FORWARDED_FOR="198.51.100.25, 198.51.100.26")
        self.assertEqual(response.status_code, 302); self.preconsulta.refresh_from_db()
        self.assertEqual(self.preconsulta.fecha_completada, self.NOW); self.assertEqual(self.preconsulta.ip_completada, "198.51.100.25"); self.assertEqual(self.preconsulta.creada_por_id, self.usuario.pk)
        audit = RegistroAuditoria.objects.filter(app_label="clinica", modelo="preconsultaclinica", objeto_id=str(self.preconsulta.pk), accion="modificar").latest("id")
        self.assertEqual(audit.usuario_id, self.usuario.pk); self.assertEqual(audit.empresa_id, self.empresa.pk)

    def test_latest_general_only_is_updated_older_and_other_types_remain_exact(self):
        older = PreconsultaClinica.objects.create(empresa=self.empresa, paciente=self.paciente, tipo="general", token_hash="GENERAL-OLDER", token_preview="OLD", fecha_expiracion=self.NOW, datos_generales={"unchanged": True})
        other = PreconsultaClinica.objects.create(empresa=self.empresa, paciente=self.paciente, tipo="capilar", token_hash="GENERAL-OTHER-TYPE", token_preview="OTHER", fecha_expiracion=self.NOW, datos_generales={"unchanged": True})
        PreconsultaClinica.objects.filter(pk=older.pk).update(fecha_creacion=self.NOW-timedelta(days=2))
        original = list(PreconsultaClinica.objects.filter(pk__in=[older.pk, other.pk]).order_by("id").values())
        response = self.client.post(self.url(), self.data()); self.assertEqual(response.status_code, 302)
        self.assertEqual(list(PreconsultaClinica.objects.filter(pk__in=[older.pk, other.pk]).order_by("id").values()), original)
        self.preconsulta.refresh_from_db(); self.assertEqual(self.preconsulta.estado, "completada")

    def test_internal_expired_revoked_and_completed_states_keep_original_editability(self):
        for state in ("pendiente", "revocada", "completada"):
            PreconsultaClinica.objects.filter(pk=self.preconsulta.pk).update(estado=state, fecha_expiracion=self.NOW-timedelta(days=10))
            self.assert_mobile(self.unchanged()); response = self.client.post(self.url(), self.data()); self.assertEqual(response.status_code, 302)
            self.preconsulta.refresh_from_db(); self.assertEqual(self.preconsulta.estado, "completada")

    def test_shared_customer_local_copies_and_accounts_are_original_valid_post_effects(self):
        before = self.snapshot(); response = self.client.post(self.url(), self.data()); self.assertEqual(response.status_code, 302)
        self.assertTrue(self.changed(before) <= self.BUSINESS_ALLOWED); self.paciente.refresh_from_db()
        client = Cliente.objects.get(pk=self.paciente.cliente_id); self.assertEqual(client.empresa_id, self.empresa.pk); self.assertIsNotNone(client.cuenta_contable_id)
        copies = Cliente.objects.filter(perfil_compartido_id=client.perfil_compartido_id)
        self.assertEqual(set(copies.values_list("empresa__slug", flat=True)), set(self.SLUGS))
        for copy in copies:
            self.assertEqual(copy.rtn, self.paciente.identidad); self.assertEqual(copy.nombre, self.paciente.nombre)
            self.assertTrue(Paciente.objects.filter(empresa=copy.empresa, cliente=copy).exists())

    def test_original_sync_failure_keeps_narrow_existing_partial_state_not_new_atomicity(self):
        before = self.snapshot()
        with patch.object(views, "_sincronizar_cliente_facturacion_paciente", side_effect=RuntimeError("Synthetic original sync failure")):
            with self.assertRaisesRegex(RuntimeError, "Synthetic original sync failure"): self.client.post(self.url(), self.data())
        self.preconsulta.refresh_from_db(); self.paciente.refresh_from_db(); self.assertEqual(self.preconsulta.estado, "completada"); self.assertTrue(self.paciente.nombre.startswith("Ana Actualizada"))
        self.assertEqual(self.changed(before), {"clinica.preconsultaclinica", "clinica.paciente", "core.registroauditoria"})

    def test_writer_only_success_returns_to_authorized_app_without_patient_grant(self):
        self.assertFalse(self.usuario.tiene_permiso_erp("puede_ver_datos_generales_paciente", self.empresa))
        response = self.client.post(self.url(), self.data()); self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("agenda_mobile_modules_screen", args=[self.empresa.slug]))
        self.assertEqual(self.client.get(response["Location"]).status_code, 200)

    def test_pure_capability_and_safe_navigation_do_not_read_history_or_grant_other_actions(self):
        from .mobile_general_history import historia_general_disponible_app, url_historia_general_app
        request = self.request(); before = self.snapshot(); reset_queries()
        with CaptureQueriesContext(connection) as captured:
            self.assertTrue(historia_general_disponible_app(request, self.empresa))
            self.assertEqual(url_historia_general_app(self.empresa, self.paciente), self.url())
        self.assert_no_writes(captured); self.assertEqual(self.snapshot(), before)
        self.assertFalse(any(table in row["sql"].lower() for table in self.PRIVATE_TABLES for row in captured))
        response = self.unchanged(); self.assertFalse(response.context["can_view_patient"]); self.assertFalse(response.context["can_view_history"])
        self.assertFalse(response.context["app_home_disponible"])
        self.assertEqual(response.context["app_complete_history_back_url"], reverse("agenda_mobile_modules_screen", args=[self.empresa.slug]))

    def test_additive_links_on_patient_detail_and_histories_use_original_read_and_edit_permissions(self):
        reader = self.use_role(puede_ver_datos_generales_paciente=True, puede_ver_historia_clinica=True)
        editor = RolSistema.objects.create(nombre="Leer y completar general QA", codigo="general-link-edit", activo=True, es_rol_clinico=True, usa_permisos_clinicos_granulares=True, puede_ver_datos_generales_paciente=True, puede_ver_historia_clinica=True, puede_editar_historia_clinica=True)
        legacy_exp = RolSistema.objects.create(nombre="Enlaces sólo expediente QA", codigo="general-link-exp", activo=True, puede_expediente_clinico=True)
        legacy_both = RolSistema.objects.create(nombre="Enlaces pacientes expediente QA", codigo="general-link-both", activo=True, puede_pacientes=True, puede_expediente_clinico=True)
        cases = ((reader, False, ("clinica_paciente_detalle", "clinica_historias_especialidad")),
                 (editor, True, ("clinica_paciente_detalle", "clinica_historias_especialidad")),
                 (self.legado, True, ("clinica_paciente_detalle",)),
                 (legacy_exp, False, ("clinica_historias_especialidad",)),
                 (legacy_both, True, ("clinica_paciente_detalle", "clinica_historias_especialidad")))
        summary_url = reverse("clinica_historial_clinico_consolidado", args=[self.empresa.slug, self.paciente.pk])+"?app=1"
        for role, allowed, origins in cases:
            self.use_role(role)
            for origin in origins:
                target = reverse(origin, args=[self.empresa.slug, self.paciente.pk])+"?app=1"
                before = self.snapshot(); reset_queries()
                with CaptureQueriesContext(connection) as captured: response = self.client.get(target)
                self.assert_no_writes(captured); self.assertEqual(self.snapshot(), before); self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["can_complete_general_history"], allowed)
                if allowed: self.assertContains(response, f'href="{self.url()}"')
                else: self.assertNotContains(response, f'href="{self.url()}"')
                if origin == "clinica_historias_especialidad" or response.context["can_view_history"]:
                    self.assertContains(response, f'href="{summary_url}"')  # Existing summary is not replaced.
        self.token_mock.assert_not_called()

    def test_original_unflagged_and_outside_four_templates_and_forms_are_unchanged(self):
        for company, target in ((self.empresa, self.url(mobile=False)), (self.externa, self.url(self.externa))):
            self.use_role(self.legado); response = self.unchanged(target); self.assertEqual(response.status_code, 200)
            self.assertTemplateUsed(response, "clinica/preconsulta_publica.html"); self.assertIsInstance(response.context["form"], PreconsultaClinicaPublicaForm)
            self.assertNotContains(response, "mobile_general_history"); self.assertContains(response, "Paso 9 de 9")
            self.assertIn("foto_perfil", response.context["form"].fields)

    def test_csrf_original_session_notice_is_explicit_and_valid_csrf_envelope_denial_writes_nothing(self):
        from core.views import SESSION_EXPIRED_MESSAGE_KEY
        strict = Client(enforce_csrf_checks=True); strict.force_login(self.usuario); before = self.snapshot()
        session_before = {row.pk: row.get_decoded() for row in Session.objects.all()}
        response = strict.post(self.url(), self.data())
        self.assertEqual(response.status_code, 302); self.assertEqual(response["Location"], reverse("empresa_login", kwargs={"slug": self.empresa.slug}))
        self.assertEqual(self.changed(before), {"sessions.session"}); self.assertEqual(set(session_before), set(Session.objects.values_list("pk", flat=True)))
        for session in Session.objects.all():
            expected = dict(session_before[session.pk])
            if session.pk == strict.session.session_key: expected[SESSION_EXPIRED_MESSAGE_KEY] = "Vuelve a iniciar sesion para continuar."
            self.assertEqual(session.get_decoded(), expected)
        strict.cookies["csrftoken"] = "a"*32; before = self.snapshot(); reset_queries()
        with CaptureQueriesContext(connection) as captured: response = strict.post(self.url(), {**self.data(), "csrfmiddlewaretoken": "a"*32, "estado": "completada"})
        self.assertEqual(response.status_code, 403); self.assert_no_writes(captured); self.assertEqual(self.snapshot(), before)
