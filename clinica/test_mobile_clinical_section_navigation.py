"""Clinical section navigation, without a second clinical backend or saver.

Independent synthetic fixtures exercise the existing patient picker and Forms.
Imported-relation integrity is covered by the separate read-scope test module.
"""

from html.parser import HTMLParser
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

from django.apps import apps
from django.db import connection
from django.test import RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import resolve, reverse
from django.utils import timezone

from core.models import Empresa, EmpresaModulo, Modulo, RolSistema, Usuario, UsuarioEmpresaPermiso
from core.clinical_permissions import permiso_clinica_granular
from crm.mobile_modules import construir_contexto_modulos_app
from crm.models import ProgramaCamaraHiperbarica, ProgramaTerapiaPostQuirurgica
from . import views
from .forms import HistoriaClinicaEspecialidadForm, EvaluacionClinicaIntegralForm, TricopigmentacionForm, TratamientoPacienteForm
from .models import HistoriaClinicaEspecialidad, Paciente, PreconsultaClinica, ProfesionalSalud, TratamientoPaciente


class _ClinicalNavigationMarkup(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.forms = []
        self.tags = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.tags.append((tag, attrs))
        if tag == "a" and "href" in attrs:
            self.links.append(attrs["href"])
        if tag == "form":
            self.forms.append(attrs)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class MobileClinicalSectionNavigationTests(TestCase):
    SLUGS = ("hospital_mia", "medical_spa", "luque_aestetic", "serviciosmedicos")
    TILES = ("expediente", "registrar_historia", "evaluaciones", "tratamientos", "terapias", "registrar_terapia", "camara", "postquirurgicas")
    AREAS = ("capilar", "cirugia_plastica", "medicina_estetica", "enfermeria", "terapias")
    SPECIAL = ("evaluacion_integral", "funciones_organicas", "examen_fisico", "tricopigmentacion")
    FULL_GRANULAR = {
        "puede_ver_pacientes": True, "puede_ver_historia_clinica": True,
        "puede_crear_historia_clinica": True, "puede_editar_historia_clinica": True,
        "puede_escribir_enfermeria": True, "puede_ver_terapias": True, "puede_escribir_terapias": True,
        "puede_ver_planes_tratamiento": True, "puede_editar_planes_tratamiento": True,
        "puede_ver_camara_hiperbarica": True, "puede_escribir_camara_hiperbarica": True,
        "puede_ver_postquirurgicas": True, "puede_escribir_postquirurgicas": True,
    }
    FULL_LEGACY = {"puede_pacientes": True, "puede_expediente_clinico": True,
                   "puede_tratamientos_clinicos": True, "puede_clinica": True}

    @classmethod
    def setUpTestData(cls):
        cls.empresas, cls.pacientes, cls.profesionales, cls.historias, cls.preconsultas = {}, {}, {}, {}, {}
        cls.modulo, _ = Modulo.objects.get_or_create(codigo="clinica_medica", defaults={"nombre": "Clínica navegación sintética"})
        for index, slug in enumerate(cls.SLUGS, 1):
            company = Empresa.objects.create(nombre=f"Clínica navegación {slug}", slug=slug,
                                             rtn=f"CLINICAL-NAV-{index}", tipo_solucion="clinica")
            EmpresaModulo.objects.get_or_create(empresa=company, modulo=cls.modulo, defaults={"activo": True})
            cls.empresas[slug] = company
            patient = Paciente.objects.create(empresa=company, nombre=f"Paciente navegación {slug}",
                                              expediente_codigo=f"CNAV-{index}", sexo="no_indicado")
            cls.pacientes[slug] = patient
            cls.profesionales[slug] = ProfesionalSalud.objects.create(empresa=company, nombre=f"Profesional navegación {slug}")
            for area in cls.AREAS:
                cls.historias[(slug, area)] = HistoriaClinicaEspecialidad.objects.create(
                    empresa=company, paciente=patient, profesional=cls.profesionales[slug], tipo=area,
                    estado="borrador", observaciones=f"Nota sintética {slug} {area}",
                )
            for area in ("capilar", "enfermeria", "terapias"):
                cls.preconsultas[(slug, area)] = PreconsultaClinica.objects.create(
                    empresa=company, paciente=patient, tipo=area, estado="completada",
                    token_hash=f"CNAV-PRIVATE-{slug}-{area}", token_preview="PRIVATE-CNAV",
                    fecha_expiracion=timezone.now() + timezone.timedelta(days=10), fecha_completada=timezone.now(),
                    motivo_consulta=f"Motivo sintético {slug} {area}",
                )
            for model in (ProgramaCamaraHiperbarica, ProgramaTerapiaPostQuirurgica):
                model.objects.create(empresa=company, paciente=patient, cirugia="Procedimiento sintético", fecha_cirugia="2026-10-01")
            TratamientoPaciente.objects.create(empresa=company, paciente=patient, nombre=f"Tratamiento sintético {slug}", fecha_inicio="2026-10-01")
        cls.empresa = cls.empresas[cls.SLUGS[0]]
        cls.paciente = cls.pacientes[cls.empresa.slug]
        cls.default_role = RolSistema.objects.create(nombre="Clínica navegación completa", codigo="clinical-section-full",
                                                      es_rol_clinico=True, usa_permisos_clinicos_granulares=True, **cls.FULL_GRANULAR)
        cls.actor = Usuario.objects.create_user(username="clinical-section-user", password="synthetic-only",
                                               empresa=cls.empresa, rol_sistema=cls.default_role)
        cls.actor.empresas_acceso.add(*cls.empresas.values())

    def setUp(self):
        self.client.force_login(self.actor)
        self.picker()
        for target in ("socket.socket.connect", "clinica.views.EmailMultiAlternatives.send",
                       "clinica.views.subir_documento_whatsapp", "clinica.views.enviar_documento_whatsapp"):
            blocker = patch(target, side_effect=AssertionError("Real transport is forbidden"))
            mock = blocker.start()
            self.addCleanup(blocker.stop)
            self.addCleanup(mock.assert_not_called)

    def role_for(self, company=None, granular=True, **flags):
        role = RolSistema.objects.create(nombre="Clínica navegación restringida", codigo=f"clinical-nav-{RolSistema.objects.count()}",
                                         es_rol_clinico=granular, usa_permisos_clinicos_granulares=granular, **flags)
        UsuarioEmpresaPermiso.objects.update_or_create(usuario=self.actor, empresa=company or self.empresa,
                                                       defaults={"rol_sistema": role, "activo": True})
        return role

    def registry(self, company=None):
        company = company or self.empresa
        request = RequestFactory().get(reverse("agenda_mobile_modules_screen", args=[company.slug]))
        request.user = Usuario.objects.get(pk=self.actor.pk)
        return {item["key"]: item for item in construir_contexto_modulos_app(request, company)["modulos_app"]}

    def picker(self, company=None, **params):
        return reverse("clinica_pacientes", args=[(company or self.empresa).slug]) + "?" + urlencode({"app": "1", **params})

    def history(self, company=None, patient=None, **params):
        company = company or self.empresa
        patient = patient or self.pacientes[company.slug]
        return reverse("clinica_historias_especialidad", args=[company.slug, patient.pk]) + "?" + urlencode({"app": "1", **params})

    def note_form(self, area="terapias", company=None):
        company = company or self.empresa
        return reverse("clinica_crear_historia_especialidad", args=[company.slug, self.pacientes[company.slug].pk, area]) + "?app=1"

    def special_form(self, code="evaluacion_integral", company=None):
        company = company or self.empresa
        return reverse("clinica_historial_clinico_consolidado", args=[company.slug, self.pacientes[company.slug].pk]) + "?" + urlencode({"app": "1", "formulario": code})

    def snapshot(self):
        return {model._meta.label: list(model.objects.order_by("pk").values())
                for model in apps.get_models(include_auto_created=True)
                if model._meta.managed and not model._meta.proxy}

    def readonly(self, callback):
        before = self.snapshot()
        connection.queries_log.clear()
        with CaptureQueriesContext(connection) as queries:
            result = callback()
        self.assertEqual(self.snapshot(), before)
        for entry in queries:
            self.assertFalse(entry["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "REPLACE")), entry["sql"])
        return result, queries

    def links(self, response):
        return _ClinicalNavigationMarkup(response.content.decode()).links

    def assert_tile(self, item, route, company=None, **params):
        company = company or self.empresa
        self.assertEqual(item["estado"], "disponible")
        self.assertEqual(item["panel"], "")
        parsed = urlsplit(item["url"])
        self.assertEqual(parsed.path, reverse(route, args=[company.slug]))
        self.assertEqual(parse_qs(parsed.query), {"app": ["1"], **{key: [value] for key, value in params.items()}})
        self.assertFalse(parsed.scheme or parsed.netloc or parsed.fragment)

    def test_eight_clinical_tiles_available_with_original_prerequisites_four_companies_both_roles(self):
        for company in self.empresas.values():
            for granular in (True, False):
                self.role_for(company, granular, **(self.FULL_GRANULAR if granular else self.FULL_LEGACY))
                with self.subTest(company=company.slug, granular=granular):
                    items, _ = self.readonly(lambda: self.registry(company))
                    for key in self.TILES:
                        self.assertIn(key, items)
                        self.assertEqual(items[key]["estado"], "disponible")
                    self.assert_tile(items["expediente"], "clinica_pacientes", company, destino="historias")
                    self.assert_tile(items["registrar_historia"], "clinica_pacientes", company, destino="crear_historia", seleccionar="area")
                    self.assert_tile(items["evaluaciones"], "clinica_pacientes", company, destino="evaluacion_clinica", seleccionar="formulario")
                    self.assert_tile(items["terapias"], "clinica_pacientes", company, destino="historias", tipo="terapias")
                    self.assert_tile(items["registrar_terapia"], "clinica_pacientes", company, destino="crear_historia", tipo="terapias")

    def test_register_note_explicit_area_choice_precedes_patient_links_four_companies_both_roles(self):
        for company in self.empresas.values():
            for granular in (True, False):
                self.role_for(company, granular, **(self.FULL_GRANULAR if granular else self.FULL_LEGACY))
                with self.subTest(company=company.slug, granular=granular):
                    response, _ = self.readonly(lambda: self.client.get(self.registry(company)["registrar_historia"]["url"]))
                    self.assertEqual(response.status_code, 200)
                    self.assertTrue(response.context["mobile_patient_clinical_choice_mode"])
                    markup = _ClinicalNavigationMarkup(response.content.decode())
                    self.assertFalse(any("mp-patient-list" in attrs.get("class", "").split() for _, attrs in markup.tags))
                    self.assertFalse(any("mp-search" in attrs.get("class", "").split() for attrs in markup.forms))
                    choices = response.context["mobile_patient_clinical_choices"]
                    self.assertEqual([item["codigo"] for item in choices], list(self.AREAS))
                    for item in choices:
                        self.assertIn(item["url"], self.links(response))
                        query = parse_qs(urlsplit(item["url"]).query)
                        self.assertEqual(query["app"], ["1"])
                        self.assertEqual(query["destino"], ["crear_historia"])
                        self.assertEqual(query["tipo"], [item["codigo"]])
                    for area in self.AREAS:
                        self.assertNotIn(self.note_form(area, company), self.links(response))

    def test_evaluation_explicit_form_choice_precedes_patient_links_four_companies_both_roles(self):
        for company in self.empresas.values():
            for granular in (True, False):
                self.role_for(company, granular, **(self.FULL_GRANULAR if granular else self.FULL_LEGACY))
                with self.subTest(company=company.slug, granular=granular):
                    response, _ = self.readonly(lambda: self.client.get(self.registry(company)["evaluaciones"]["url"]))
                    self.assertEqual(response.status_code, 200)
                    self.assertTrue(response.context["mobile_patient_clinical_choice_mode"])
                    choices = response.context["mobile_patient_clinical_choices"]
                    self.assertEqual([item["codigo"] for item in choices], list(self.SPECIAL))
                    for item in choices:
                        self.assertIn(item["url"], self.links(response))
                        query = parse_qs(urlsplit(item["url"]).query)
                        self.assertEqual(query["destino"], ["evaluacion_clinica"])
                        self.assertEqual(query["formulario"], [item["codigo"]])
                        self.assertNotIn(self.special_form(item["codigo"], company), self.links(response))

    def test_explicit_area_choice_uses_existing_five_original_forms_without_saves(self):
        for company in self.empresas.values():
            for area in self.AREAS:
                with self.subTest(company=company.slug, area=area):
                    picker, _ = self.readonly(lambda: self.client.get(self.picker(company, destino="crear_historia", tipo=area)))
                    self.assertEqual(picker.context["mobile_patient_history_create_type"]["codigo"], area)
                    self.assertIn(self.note_form(area, company), self.links(picker))
                    form_response, _ = self.readonly(lambda: self.client.get(self.note_form(area, company)))
                    self.assertEqual(form_response.status_code, 200)
                    self.assertTemplateUsed(form_response, "clinica/mobile_history_form.html")
                    self.assertIsInstance(form_response.context["form"], HistoriaClinicaEspecialidadForm)
                    original = HistoriaClinicaEspecialidadForm(empresa=company, paciente=self.pacientes[company.slug], tipo=area)
                    self.assertEqual(list(form_response.context["form"].fields), list(original.fields))

    def test_explicit_special_choice_uses_existing_four_original_prefixed_forms(self):
        for company in self.empresas.values():
            for code in self.SPECIAL:
                with self.subTest(company=company.slug, form=code):
                    picker, _ = self.readonly(lambda: self.client.get(self.picker(company, destino="evaluacion_clinica", formulario=code)))
                    self.assertIn(self.special_form(code, company), self.links(picker))
                    response, _ = self.readonly(lambda: self.client.get(self.special_form(code, company)))
                    self.assertEqual(response.status_code, 200)
                    self.assertTemplateUsed(response, "clinica/mobile_special_history_form.html")
                    self.assertIsInstance(response.context["form"], TricopigmentacionForm if code == "tricopigmentacion" else EvaluacionClinicaIntegralForm)
                    self.assertEqual(response.context["mobile_special_history_form"]["codigo"], code)

    def test_existing_selector_without_new_marker_keeps_original_first_defaults(self):
        for destination, field, first in (("crear_historia", "mobile_patient_history_create_type", "capilar"),
                                           ("evaluacion_clinica", "mobile_patient_special_history_form", "evaluacion_integral")):
            response, _ = self.readonly(lambda: self.client.get(self.picker(destino=destination)))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context[field]["codigo"], first)
            self.assertFalse(response.context.get("mobile_patient_clinical_choices"))
            self.assertIn(self.note_form("capilar") if destination == "crear_historia" else self.special_form(), self.links(response))

    def test_area_choices_derive_from_specific_writing_permissions_not_generic_read(self):
        for flags, expected in (({"puede_escribir_terapias": True}, ["terapias"]),
                                 ({"puede_escribir_enfermeria": True}, ["enfermeria"]),
                                 ({"puede_crear_historia_clinica": True}, list(self.AREAS[:3]))):
            self.role_for(puede_ver_pacientes=True, **flags)
            response, _ = self.readonly(lambda: self.client.get(self.picker(destino="crear_historia", seleccionar="area")))
            self.assertEqual([item["codigo"] for item in response.context["mobile_patient_clinical_choices"]], expected)
            self.assertFalse(response.context["can_view_history"])
            self.assertFalse(Usuario.objects.get(pk=self.actor.pk).tiene_permiso_erp("puede_ver_historia_clinica", self.empresa))

    def test_special_choices_require_both_original_read_and_edit_not_creation_or_configuration(self):
        for flags in ({"puede_ver_historia_clinica": True}, {"puede_editar_historia_clinica": True},
                      {"puede_crear_historia_clinica": True}, {"puede_configuracion_clinica": True}):
            self.role_for(puede_ver_pacientes=True, **flags)
            response, _ = self.readonly(lambda: self.client.get(self.picker(destino="evaluacion_clinica", seleccionar="formulario")))
            self.assertFalse(response.context.get("mobile_patient_clinical_choices"))
            self.assertNotIn("evaluaciones", self.registry())
            for code in self.SPECIAL:
                self.assertNotIn(self.special_form(code), self.links(response))

    def test_therapy_picker_forwards_readable_typed_list_not_edit_only_consolidated_route(self):
        for company in self.empresas.values():
            for granular in (True, False):
                flags = {"puede_ver_pacientes": True, "puede_ver_historia_clinica": True, "puede_ver_terapias": True} if granular else self.FULL_LEGACY
                self.role_for(company, granular, **flags)
                with self.subTest(company=company.slug, granular=granular):
                    picker, _ = self.readonly(lambda: self.client.get(self.registry(company)["terapias"]["url"]))
                    typed_url = self.history(company, tipo="terapias")
                    self.assertIn(typed_url, self.links(picker))
                    self.assertNotIn(self.history(company), self.links(picker))
                    response, _ = self.readonly(lambda: self.client.get(typed_url))
                    self.assertEqual(response.status_code, 200)
                    self.assertTemplateUsed(response, "clinica/mobile_patient_histories.html")
                    self.assertTrue(all(item["tipo"] == "terapias" for item in response.context["mobile_history_notes"]))
                    self.assertEqual([item["id"] for item in response.context["mobile_history_notes"]], [self.historias[(company.slug, "terapias")].pk])

    def test_typed_list_filters_preconsultas_notes_and_creation_shortcuts_to_same_area(self):
        response, _ = self.readonly(lambda: self.client.get(self.history(tipo="terapias")))
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["codigo"] for item in response.context["mobile_history_create_types"]], ["terapias"])
        self.assertEqual([item["id"] for item in response.context["mobile_preconsulta_items"]], [self.preconsultas[(self.empresa.slug, "terapias")].pk])
        self.assertContains(response, f"Nota sintética {self.empresa.slug} terapias")
        self.assertContains(response, f"Motivo sintético {self.empresa.slug} terapias")
        for area in ("capilar", "enfermeria"):
            self.assertNotContains(response, f"Nota sintética {self.empresa.slug} {area}")
            self.assertNotContains(response, f"Motivo sintético {self.empresa.slug} {area}")
            self.assertNotIn(self.note_form(area), self.links(response))

    def test_reader_without_edit_keeps_original_consolidated_read_and_typed_area_links(self):
        self.role_for(puede_ver_historia_clinica=True, puede_ver_pacientes=True)
        response, _ = self.readonly(lambda: self.client.get(self.history()))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["can_view_consolidated_history"])
        self.assertFalse(response.context["can_edit_history"])
        consolidated = reverse("clinica_historial_clinico_consolidado", args=[self.empresa.slug, self.paciente.pk])
        self.assertIn(consolidated + "?app=1", self.links(response))
        summary, _ = self.readonly(lambda: self.client.get(consolidated + "?app=1"))
        self.assertEqual(summary.status_code, 200)
        self.assertFalse(summary.context["can_edit_history"])
        self.assertFalse(summary.context["mobile_special_history_forms"])
        self.assertFalse(any(form.get("method", "get").lower() == "post"
                             for form in _ClinicalNavigationMarkup(summary.content.decode()).forms))
        for area in self.AREAS:
            self.assertIn(self.history(tipo=area), self.links(response))
            self.assertEqual(self.client.get(self.history(tipo=area)).status_code, 200)
        self.assertFalse(Usuario.objects.get(pk=self.actor.pk).tiene_permiso_erp("puede_editar_historia_clinica", self.empresa))

    def test_real_resolved_consolidated_route_reads_but_complete_history_route_requires_edit(self):
        for route, expected in (("clinica_historial_clinico_consolidado", "puede_ver_historia_clinica"),
                                 ("clinica_completar_historia_clinica_paciente", "puede_editar_historia_clinica")):
            path = reverse(route, args=[self.empresa.slug, self.paciente.pk])
            match = resolve(path)
            self.assertEqual(match.url_name, route)
            self.assertEqual(match.kwargs["paciente_id"], self.paciente.pk)
            # Exactly the suffix extracted by the permission middleware, not
            # an unwired alias or a guessed name for the original endpoint.
            suffix = "/".join(path.strip("/").split("/")[3:])
            self.assertEqual(permiso_clinica_granular(suffix, "GET"), expected)

    def test_superuser_owner_mobile_summary_does_not_offer_or_accept_unadapted_delete(self):
        Usuario.objects.filter(pk=self.actor.pk).update(is_superuser=True, first_name="Daniel", last_name="Varela")
        self.assertTrue(views._es_dueno_erp(Usuario.objects.get(pk=self.actor.pk)))
        url = reverse("clinica_historial_clinico_consolidado", args=[self.empresa.slug, self.paciente.pk]) + "?app=1"
        response, _ = self.readonly(lambda: self.client.get(url))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["puede_eliminar_notas_clinicas"])
        self.assertNotContains(response, 'value="eliminar_historia"')
        self.assertFalse(any(form.get("method", "get").lower() == "post"
                             for form in _ClinicalNavigationMarkup(response.content.decode()).forms))
        rejected, _ = self.readonly(lambda: self.client.post(url, {
            "app": "1", "accion": "eliminar_historia", "historia_id": str(self.historias[(self.empresa.slug, "terapias")].pk),
        }))
        self.assertEqual(rejected.status_code, 403)

    def test_original_read_edit_and_legacy_read_allow_consolidated_link_without_new_permission(self):
        for granular, flags in ((True, {"puede_ver_historia_clinica": True, "puede_editar_historia_clinica": True}),
                                 (False, {"puede_expediente_clinico": True})):
            self.role_for(granular=granular, **flags)
            response, _ = self.readonly(lambda: self.client.get(self.history()))
            self.assertTrue(response.context["can_view_consolidated_history"])
            consolidated = reverse("clinica_historial_clinico_consolidado", args=[self.empresa.slug, self.paciente.pk]) + "?app=1"
            self.assertIn(consolidated, self.links(response))
            self.assertEqual(self.client.get(consolidated).status_code, 200)

    def test_ver_terapias_alone_never_grants_general_history_typed_or_untyped(self):
        self.role_for(puede_ver_terapias=True, puede_ver_pacientes=True)
        tile = self.registry()["terapias"]
        self.assertEqual((tile["estado"], tile["url"]), ("pendiente", ""))
        for url in (self.history(), self.history(tipo="terapias")):
            response = self.client.get(url)
            self.assertRedirects(response, reverse("dashboard", args=[self.empresa.slug]), fetch_redirect_response=False)
        self.assertFalse(Usuario.objects.get(pk=self.actor.pk).tiene_permiso_erp("puede_ver_historia_clinica", self.empresa))

    def test_specific_therapy_writer_direct_form_has_no_prior_read_and_only_original_four_fields(self):
        self.role_for(puede_ver_pacientes=True, puede_escribir_terapias=True)
        tile = self.registry()["registrar_terapia"]
        self.assertEqual(tile["estado"], "disponible")
        picker, _ = self.readonly(lambda: self.client.get(tile["url"]))
        self.assertIn(self.note_form(), self.links(picker))
        response, queries = self.readonly(lambda: self.client.get(self.note_form()))
        self.assertEqual(tuple(response.context["form"].fields), ("profesional", "fecha_atencion", "observaciones", "estado"))
        self.assertFalse(response.context["can_view_history"])
        for query in queries:
            self.assertNotIn('"clinica_historiaclinicaespecialidad"', query["sql"].lower())
            self.assertNotIn('"clinica_preconsultaclinica"', query["sql"].lower())

    def test_missing_directory_keeps_tiles_pending_but_does_not_remove_existing_direct_form_permission(self):
        self.role_for(puede_escribir_terapias=True, puede_ver_historia_clinica=True)
        items = self.registry()
        for key in ("expediente", "registrar_historia", "registrar_terapia"):
            self.assertEqual((items[key]["estado"], items[key]["url"]), ("pendiente", ""))
        self.assertEqual(self.client.get(self.note_form()).status_code, 200)
        self.assertFalse(Usuario.objects.get(pk=self.actor.pk).tiene_permiso_erp("puede_ver_pacientes", self.empresa))

    def test_control_tiles_follow_original_specific_writers_not_general_history_or_type_readers(self):
        for flags in ({"puede_ver_camara_hiperbarica": True}, {"puede_ver_postquirurgicas": True}):
            self.role_for(puede_ver_pacientes=True, **flags)
            items = self.registry()
            for key in ("camara", "postquirurgicas"):
                if key in items:
                    self.assertEqual((items[key]["estado"], items[key]["url"]), ("pendiente", ""))
        self.role_for(puede_ver_pacientes=True, puede_ver_historia_clinica=True, puede_editar_historia_clinica=True)
        self.assertNotIn("camara", self.registry())
        self.assertNotIn("postquirurgicas", self.registry())

    def test_direct_control_picker_to_existing_boards_four_companies_both_legacy_and_granular(self):
        for company in self.empresas.values():
            for granular in (True, False):
                flags = {"puede_ver_pacientes": True, "puede_escribir_camara_hiperbarica": True,
                         "puede_escribir_postquirurgicas": True} if granular else self.FULL_LEGACY
                self.role_for(company, granular, **flags)
                for key, area, program_model in (("camara", "camara_hiperbarica", ProgramaCamaraHiperbarica),
                                                 ("postquirurgicas", "terapias_postquirurgicas", ProgramaTerapiaPostQuirurgica)):
                    with self.subTest(company=company.slug, granular=granular, area=area):
                        picker, _ = self.readonly(lambda: self.client.get(self.registry(company)[key]["url"]))
                        board = self.note_form(area, company)
                        self.assertIn(board, self.links(picker))
                        response, _ = self.readonly(lambda: self.client.get(board))
                        self.assertEqual(response.status_code, 200)
                        self.assertTemplateUsed(response, "clinica/mobile_control_board.html")
                        self.assertEqual(response.context["mobile_control_type"]["codigo"], area)
                        self.assertTrue(response.context["can_view_control_board"])
                        # The board uses the same existing program, not a new patient history.
                        program = response.context["mobile_control_program"]
                        self.assertIsInstance(program, program_model)
                        self.assertEqual((program.empresa_id, program.paciente_id), (company.pk, self.pacientes[company.slug].pk))

    def test_treatments_reader_and_writer_destinations_remain_existing_list_and_ten_field_form(self):
        for flags, route, template in (({"puede_ver_planes_tratamiento": True}, "clinica_tratamientos", "clinica/mobile_treatment_list.html"),
                                       ({"puede_editar_planes_tratamiento": True}, "clinica_crear_tratamiento", "clinica/mobile_treatment_form.html")):
            self.role_for(**flags)
            item = self.registry()["tratamientos"]
            self.assert_tile(item, route)
            response, _ = self.readonly(lambda: self.client.get(item["url"]))
            self.assertEqual(response.status_code, 200)
            self.assertTemplateUsed(response, template)
            if route == "clinica_crear_tratamiento":
                self.assertIsInstance(response.context["form"], TratamientoPacienteForm)
                self.assertEqual(len(response.context["form"].fields), 10)
            self.assertFalse(Usuario.objects.get(pk=self.actor.pk).tiene_permiso_erp("puede_ver_historia_clinica", self.empresa))

    def test_typed_list_unknown_duplicate_or_extra_query_is_denied_before_private_reads(self):
        paths = [self.history(tipo="unknown"), self.history(tipo="terapias").replace("app=1", "app=0"),
                 self.history(tipo="terapias") + "&tipo=capilar",
                 self.history(tipo="terapias") + "&app=1", self.history(tipo="terapias", q="not-allowed"),
                 self.history(tipo="terapias", formulario="evaluacion_integral")]
        for url in paths:
            with self.subTest(url=url):
                response, queries = self.readonly(lambda: self.client.get(url))
                self.assertEqual(response.status_code, 403)
                for query in queries:
                    self.assertNotIn('"clinica_historiaclinicaespecialidad"', query["sql"].lower())
                    self.assertNotIn('"clinica_preconsultaclinica"', query["sql"].lower())

    def test_typed_list_rejects_raw_get_head_bodies_and_post_without_mutation(self):
        for method in ("GET", "HEAD", "POST"):
            response, _ = self.readonly(lambda: self.client.generic(method, self.history(tipo="terapias"),
                                                                   data='{"app":"1"}', content_type="application/json"))
            self.assertEqual(response.status_code, 403)

    def test_preconsulta_detail_remains_app_only_and_does_not_accept_history_type_filter(self):
        preconsulta = self.preconsultas[(self.empresa.slug, "terapias")]
        url = reverse("clinica_preconsulta_detalle", args=[self.empresa.slug, self.paciente.pk, preconsulta.pk]) + "?app=1&tipo=terapias"
        response, _ = self.readonly(lambda: self.client.get(url))
        self.assertEqual(response.status_code, 403)

    def test_company_membership_module_license_and_outside_scope_keep_original_denials(self):
        company = self.empresas["medical_spa"]
        self.actor.empresas_acceso.remove(company)
        self.assertNotIn("expediente", self.registry(company))
        self.assertRedirects(self.client.get(self.history(company, tipo="terapias")), reverse("empresa_login", args=[company.slug]), fetch_redirect_response=False)
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.modulo).update(activo=False)
        self.assertFalse(set(self.TILES).intersection(self.registry()))
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.modulo).update(activo=True)
        Empresa.objects.filter(pk=self.empresa.pk).update(estado_licencia="vencida")
        self.empresa.refresh_from_db()
        self.assertFalse(set(self.TILES).intersection(self.registry()))
        outside = Empresa.objects.create(nombre="Clínica fuera de sección APP", slug="clinical_navigation_external", rtn="CNAV-OUT", tipo_solucion="clinica")
        self.actor.empresas_acceso.add(outside)
        self.assertFalse(set(self.TILES).intersection(self.registry(outside)))

    def test_escape_typed_notes_and_patient_names_without_public_secret_or_web_actions(self):
        Paciente.objects.filter(pk=self.paciente.pk).update(nombre="<script>synthetic-patient</script>")
        HistoriaClinicaEspecialidad.objects.filter(pk=self.historias[(self.empresa.slug, "terapias")].pk).update(observaciones="<script>synthetic-note</script>")
        response, _ = self.readonly(lambda: self.client.get(self.history(tipo="terapias")))
        self.assertContains(response, "&lt;script&gt;synthetic-patient&lt;/script&gt;")
        self.assertContains(response, "&lt;script&gt;synthetic-note&lt;/script&gt;")
        self.assertNotContains(response, "<script>synthetic-note</script>")
        self.assertNotContains(response, "CNAV-PRIVATE-")
        self.assertNotContains(response, "PRIVATE-CNAV")
        for link in self.links(response):
            if "historias" in urlsplit(link).path or "preconsulta" in urlsplit(link).path:
                self.assertEqual(parse_qs(urlsplit(link).query).get("app"), ["1"])

    def test_original_web_history_and_default_picker_remain_unchanged_without_opt_in(self):
        self.role_for(granular=False, **self.FULL_LEGACY)
        url = reverse("clinica_historias_especialidad", args=[self.empresa.slug, self.paciente.pk])
        web, _ = self.readonly(lambda: self.client.get(url))
        self.assertEqual(web.status_code, 200)
        self.assertTemplateUsed(web, "clinica/historias_especialidad.html")
        self.assertTemplateNotUsed(web, "clinica/mobile_patient_histories.html")
        self.assertEqual({item.tipo for item in web.context["historias"]}, set(self.AREAS))
        original_picker, _ = self.readonly(lambda: self.client.get(self.picker(destino="crear_historia")))
        self.assertEqual(original_picker.context["mobile_patient_history_create_type"]["codigo"], "capilar")
