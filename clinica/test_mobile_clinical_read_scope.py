"""Synthetic imported-relation checks for existing clinical APP readers."""

from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlencode

from django.apps import apps
from django.core.exceptions import PermissionDenied
from django.db import connection, reset_queries
from django.test import RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from core.models import Empresa, EmpresaModulo, Modulo, RolSistema, Usuario, UsuarioEmpresaPermiso
from .mobile_clinical_read_scope import validar_historias_lectura_app
from .models import HistoriaClinicaEspecialidad, Paciente, ProfesionalSalud


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class MobileClinicalReadScopeTests(TestCase):
    SLUGS = ("hospital_mia", "medical_spa", "luque_aestetic", "serviciosmedicos")
    ROUTES = ("clinica_historias_especialidad", "clinica_historial_clinico_consolidado")

    @classmethod
    def setUpTestData(cls):
        cls.empresas, cls.pacientes, cls.profesionales, cls.historias = {}, {}, {}, {}
        module, _ = Modulo.objects.get_or_create(codigo="clinica_medica", defaults={"nombre": "Clínica scope sintético"})
        for index, slug in enumerate(cls.SLUGS, 1):
            company = Empresa.objects.create(nombre=f"Scope clínico {slug}", slug=slug, rtn=f"CLINICAL-SCOPE-{index}", tipo_solucion="clinica")
            EmpresaModulo.objects.create(empresa=company, modulo=module, activo=True)
            patient = Paciente.objects.create(empresa=company, nombre=f"Paciente scope {slug}", expediente_codigo=f"SCOPE-{index}")
            professional = ProfesionalSalud.objects.create(empresa=company, nombre=f"Profesional scope {slug}")
            history = HistoriaClinicaEspecialidad.objects.create(empresa=company, paciente=patient, profesional=professional, tipo="terapias", observaciones=f"Nota propia {slug}")
            cls.empresas[slug], cls.pacientes[slug] = company, patient
            cls.profesionales[slug], cls.historias[slug] = professional, history
        cls.empresa = cls.empresas[cls.SLUGS[0]]
        cls.paciente = cls.pacientes[cls.SLUGS[0]]
        cls.historia = cls.historias[cls.SLUGS[0]]
        cls.rol = RolSistema.objects.create(nombre="Lectura scope", codigo="clinical-read-scope", es_rol_clinico=True, usa_permisos_clinicos_granulares=True, puede_ver_historia_clinica=True, puede_editar_historia_clinica=True)
        cls.usuario = Usuario.objects.create_user(username="clinical-read-scope", password="synthetic-only", empresa=cls.empresa, rol_sistema=cls.rol)
        cls.usuario.empresas_acceso.add(*cls.empresas.values())
        cls.autor_compartido = Usuario.objects.create_user(username="clinical-shared-author", password="synthetic-only", empresa=cls.empresas["medical_spa"], is_active=False)

    def setUp(self):
        self.client.force_login(self.usuario)
        self.url()
        for target in ("socket.create_connection", "socket.socket.connect", "socket.socket.connect_ex", "clinica.views.EmailMultiAlternatives.send", "clinica.views.subir_documento_whatsapp", "clinica.views.enviar_documento_whatsapp"):
            blocker = patch(target, side_effect=AssertionError("No real transport in clinical scope tests"))
            mock = blocker.start()
            self.addCleanup(blocker.stop)
            self.addCleanup(mock.assert_not_called)
        # Initialize only the normal authenticated session/CSRF outside the
        # measured read; all subsequent ALL-model snapshots include it.
        self.assertEqual(self.client.get(self.url()).status_code, 200)

    def url(self, route=None, company=None, patient=None, mobile=True, **query):
        company = company or self.empresa
        patient = patient or self.pacientes[company.slug]
        query = {"app": "1", **query} if mobile else query
        return reverse(route or self.ROUTES[0], args=[company.slug, patient.pk]) + ("?" + urlencode(query) if query else "")

    def snapshot(self):
        return {model._meta.label_lower: list(model._base_manager.order_by("pk").values())
                for model in apps.get_models(include_auto_created=True) if model._meta.managed and not model._meta.proxy}

    def read(self, url=None, method="get", data=None):
        before = self.snapshot()
        reset_queries()
        with CaptureQueriesContext(connection) as queries:
            response = getattr(self.client, method)(url or self.url(), data or {})
        self.assertEqual(self.snapshot(), before)
        for row in queries:
            self.assertFalse(row["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "REPLACE")), row["sql"])
        return response, queries

    def denied(self, url=None):
        response, queries = self.read(url)
        self.assertEqual(response.status_code, 403)
        self.assertNotContains(response, "Nota propia", status_code=403)
        for row in queries:
            if HistoriaClinicaEspecialidad._meta.db_table in row["sql"]:
                self.assertNotIn("JOIN", row["sql"].upper())
                self.assertNotIn('"observaciones"', row["sql"])
        return response

    def request(self, mobile=True):
        request = RequestFactory().get(self.url(mobile=mobile))
        request.user = Usuario.objects.get(pk=self.usuario.pk)
        return request

    def test_four_companies_and_both_role_types_read_existing_notes_without_mutation(self):
        for company in self.empresas.values():
            for granular in (True, False):
                flags = {"puede_ver_historia_clinica": True, "puede_editar_historia_clinica": True} if granular else {"puede_expediente_clinico": True}
                role = RolSistema.objects.create(nombre="Scope por empresa", codigo=f"scope-role-{RolSistema.objects.count()}", es_rol_clinico=granular, usa_permisos_clinicos_granulares=granular, **flags)
                UsuarioEmpresaPermiso.objects.update_or_create(usuario=self.usuario, empresa=company, defaults={"rol_sistema": role, "activo": True})
                for route in self.ROUTES:
                    for method in ("get", "head"):
                        with self.subTest(company=company.slug, granular=granular, route=route, method=method):
                            response, _ = self.read(self.url(route, company=company), method)
                            self.assertEqual(response.status_code, 200)

    def test_wrong_company_note_for_current_patient_is_denied_before_both_readers_load_it(self):
        HistoriaClinicaEspecialidad.objects.filter(pk=self.historia.pk).update(empresa=self.empresas["medical_spa"])
        for route in self.ROUTES:
            self.denied(self.url(route))

    def test_foreign_professional_outside_requested_area_is_denied_before_filter_or_join(self):
        HistoriaClinicaEspecialidad.objects.filter(pk=self.historia.pk).update(tipo="capilar", profesional=self.profesionales["medical_spa"])
        for route in self.ROUTES:
            self.denied(self.url(route, seccion="especialidad", tipo="terapias") if route == self.ROUTES[1] else self.url(route))

    def test_orphan_professional_is_denied_without_inner_join_hiding_note(self):
        with connection.constraint_checks_disabled():
            HistoriaClinicaEspecialidad.objects.filter(pk=self.historia.pk).update(profesional_id=9223372036854775000)
            try:
                for route in self.ROUTES:
                    self.denied(self.url(route))
            finally:
                HistoriaClinicaEspecialidad.objects.filter(pk=self.historia.pk).update(profesional_id=None)

    def test_null_or_inactive_own_professional_and_multicompany_authors_are_valid_history(self):
        for professional in (None, self.profesionales[self.empresa.slug]):
            ProfesionalSalud.objects.filter(pk=self.profesionales[self.empresa.slug].pk).update(activo=False)
            HistoriaClinicaEspecialidad.objects.filter(pk=self.historia.pk).update(profesional=professional, creado_por=self.autor_compartido, actualizado_por=self.autor_compartido)
            for route in self.ROUTES:
                response, _ = self.read(self.url(route))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "Nota propia")

    def test_other_patient_corruption_is_not_in_this_reader_scope(self):
        HistoriaClinicaEspecialidad.objects.filter(pk=self.historias["medical_spa"].pk).update(empresa=self.empresa)
        for route in self.ROUTES:
            response, _ = self.read(self.url(route))
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, "Nota propia medical_spa")

    def test_foreign_patient_url_is_still_denied_by_original_own_patient_lookup(self):
        for route in self.ROUTES:
            response, _ = self.read(self.url(route, patient=self.pacientes["medical_spa"]))
            self.assertEqual(response.status_code, 404)

    def test_guard_patient_scope_checks_imported_orphan_fk_and_rejects_foreign_patient(self):
        with CaptureQueriesContext(connection) as queries:
            with self.assertRaises(PermissionDenied):
                validar_historias_lectura_app(self.request(), self.empresa, self.pacientes["medical_spa"])
        self.assertFalse(any(HistoriaClinicaEspecialidad._meta.db_table in row["sql"] for row in queries))
        with connection.constraint_checks_disabled():
            orphan_id = 9223372036854775000
            HistoriaClinicaEspecialidad.objects.filter(pk=self.historia.pk).update(paciente_id=orphan_id)
            try:
                with self.assertRaises(PermissionDenied):
                    validar_historias_lectura_app(self.request(), self.empresa, SimpleNamespace(pk=orphan_id, empresa_id=self.empresa.pk))
            finally:
                HistoriaClinicaEspecialidad.objects.filter(pk=self.historia.pk).update(paciente_id=self.paciente.pk)

    def test_original_web_reader_keeps_its_original_scope_and_template(self):
        HistoriaClinicaEspecialidad.objects.filter(pk=self.historia.pk).update(empresa=self.empresas["medical_spa"], profesional=self.profesionales["medical_spa"])
        for route, template in zip(self.ROUTES, ("clinica/historias_especialidad.html", "clinica/historial_clinico_consolidado.html")):
            response, _ = self.read(self.url(route, mobile=False))
            self.assertEqual(response.status_code, 200)
            self.assertTemplateUsed(response, template)
        request = self.request(mobile=False)
        with CaptureQueriesContext(connection) as queries:
            validar_historias_lectura_app(request, self.empresa, self.paciente)
        self.assertEqual(len(queries), 0)

    def test_special_composer_get_and_invalid_post_never_read_prior_notes_even_if_corrupt(self):
        HistoriaClinicaEspecialidad.objects.filter(pk=self.historia.pk).update(empresa=self.empresas["medical_spa"])
        url = self.url(self.ROUTES[1], formulario="tricopigmentacion")
        for method, data in (("get", None), ("post", {"app": "1", "formulario": "tricopigmentacion", "accion": "guardar_tricopigmentacion", "tricopigmentacion-fecha_atencion": "invalid"})):
            response, queries = self.read(url, method, data)
            self.assertEqual(response.status_code, 200)
            self.assertTemplateUsed(response, "clinica/mobile_special_history_form.html")
            self.assertFalse(any(HistoriaClinicaEspecialidad._meta.db_table in row["sql"] for row in queries))

    def test_create_and_edit_previous_note_reads_validate_all_areas_before_loading(self):
        other = HistoriaClinicaEspecialidad.objects.create(empresa=self.empresa, paciente=self.paciente, tipo="capilar", profesional=self.profesionales[self.empresa.slug])
        HistoriaClinicaEspecialidad.objects.filter(pk=other.pk).update(profesional=self.profesionales["medical_spa"])
        create_url = reverse("clinica_crear_historia_especialidad", args=[self.empresa.slug, self.paciente.pk, "terapias"]) + "?app=1"
        edit_url = reverse("clinica_editar_historia_especialidad", args=[self.empresa.slug, self.paciente.pk, self.historia.pk]) + "?app=1"
        # Add only the existing create permission; no new read or role grant.
        RolSistema.objects.filter(pk=self.rol.pk).update(puede_escribir_terapias=True)
        self.denied(create_url)
        # Editing necessarily loads its authorized current record before the
        # prior-history guard, so only the 403 and no-mutation are asserted.
        response, _ = self.read(edit_url)
        self.assertEqual(response.status_code, 403)

    def test_writer_only_composers_keep_current_form_without_prior_history_queries(self):
        role = RolSistema.objects.create(nombre="Scope writer only", codigo="scope-writer-only", es_rol_clinico=True, usa_permisos_clinicos_granulares=True, puede_escribir_terapias=True, puede_editar_historia_clinica=True)
        UsuarioEmpresaPermiso.objects.update_or_create(usuario=self.usuario, empresa=self.empresa, defaults={"rol_sistema": role, "activo": True})
        other = HistoriaClinicaEspecialidad.objects.create(empresa=self.empresa, paciente=self.paciente, tipo="capilar")
        HistoriaClinicaEspecialidad.objects.filter(pk=other.pk).update(empresa=self.empresas["medical_spa"])
        url = reverse("clinica_crear_historia_especialidad", args=[self.empresa.slug, self.paciente.pk, "terapias"]) + "?app=1"
        response, queries = self.read(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["historias_previas"], [])
        self.assertFalse(any(HistoriaClinicaEspecialidad._meta.db_table in row["sql"] for row in queries))
        url = reverse("clinica_editar_historia_especialidad", args=[self.empresa.slug, self.paciente.pk, self.historia.pk]) + "?app=1"
        response, queries = self.read(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["historias_previas"], [])
        self.assertFalse(any("SELECT 1 AS" in row["sql"] and HistoriaClinicaEspecialidad._meta.db_table in row["sql"] for row in queries))

    def test_app_previous_therapy_notes_keep_observations_and_exclude_current_edit_only_in_app(self):
        RolSistema.objects.filter(pk=self.rol.pk).update(puede_escribir_terapias=True)
        other = HistoriaClinicaEspecialidad.objects.create(empresa=self.empresa, paciente=self.paciente, tipo="terapias", observaciones="Otra nota previa sin plan", plan_tratamiento="")
        create = reverse("clinica_crear_historia_especialidad", args=[self.empresa.slug, self.paciente.pk, "terapias"])
        response, _ = self.read(create + "?app=1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual({record.pk for record in response.context["historias_previas"]}, {self.historia.pk, other.pk})
        web, _ = self.read(create)
        self.assertEqual(list(web.context["historias_previas"]), [])
        edit = reverse("clinica_editar_historia_especialidad", args=[self.empresa.slug, self.paciente.pk, self.historia.pk])
        response, _ = self.read(edit + "?app=1")
        self.assertEqual([record.pk for record in response.context["historias_previas"]], [other.pk])
        # The old web query still includes its own record when it has a plan.
        HistoriaClinicaEspecialidad.objects.filter(pk=self.historia.pk).update(plan_tratamiento="Plan web original")
        web, _ = self.read(edit)
        self.assertEqual([record.pk for record in web.context["historias_previas"]], [self.historia.pk])
