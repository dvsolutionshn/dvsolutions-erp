"""Additional Therapy links over the existing patient/history handlers.

All identities and clinical notes are synthetic. These tests do not introduce
therapy permissions, forms, savers, or a new clinical endpoint.
"""

from html.parser import HTMLParser
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

from django.apps import apps
from django.contrib.auth.models import AnonymousUser
from django.db import connection
from django.test import RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.models import (
    Empresa, EmpresaModulo, Modulo, RegistroAuditoria, RolSistema,
    Usuario, UsuarioEmpresaPermiso,
)
from clinica.forms import HistoriaClinicaEspecialidadForm
from clinica.models import HistoriaClinicaEspecialidad, Paciente, ProfesionalSalud
from .mobile_modules import construir_contexto_modulos_app


class _TherapyMarkup(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.links = []
        self.forms = {}
        self.current_form = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and "href" in attrs:
            self.links.append(attrs["href"])
        if tag == "form":
            self.current_form = attrs.get("id", "")
            self.forms[self.current_form] = {"attrs": attrs, "names": []}
        if tag in {"input", "select", "textarea", "button"} and self.current_form is not None:
            if attrs.get("name"):
                self.forms[self.current_form]["names"].append(attrs["name"])

    def handle_endtag(self, tag):
        if tag == "form":
            self.current_form = None


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class MobileTherapyNavigationTests(TestCase):
    SLUGS = ("hospital_mia", "medical_spa", "luque_aestetic", "serviciosmedicos")
    FIELDS = ("profesional", "fecha_atencion", "observaciones", "estado")

    @classmethod
    def setUpTestData(cls):
        cls.empresas, cls.pacientes, cls.profesionales = {}, {}, {}
        cls.modulo, _ = Modulo.objects.get_or_create(
            codigo="clinica_medica", defaults={"nombre": "Clínica de prueba de Terapias"},
        )
        for index, slug in enumerate(cls.SLUGS, 1):
            company = Empresa.objects.create(
                nombre=f"Terapias sintéticas {slug}", slug=slug,
                rtn=f"THERAPY-NAV-{index}", tipo_solucion="clinica",
            )
            EmpresaModulo.objects.get_or_create(
                empresa=company, modulo=cls.modulo, defaults={"activo": True},
            )
            cls.empresas[slug] = company
            cls.pacientes[slug] = Paciente.objects.create(
                empresa=company, nombre=f"Paciente Terapias {slug}",
                expediente_codigo=f"THERAPY-{index}", sexo="no_indicado",
            )
            cls.profesionales[slug] = ProfesionalSalud.objects.create(
                empresa=company, nombre=f"Profesional Terapias {slug}",
            )
            HistoriaClinicaEspecialidad.objects.create(
                empresa=company, paciente=cls.pacientes[slug],
                profesional=cls.profesionales[slug], tipo="terapias",
                observaciones=f"Nota sintética previa {slug}", estado="borrador",
            )
        cls.empresa = cls.empresas[cls.SLUGS[0]]
        cls.paciente = cls.pacientes[cls.empresa.slug]
        cls.role = RolSistema.objects.create(
            nombre="Terapias navegación completo", codigo="therapy-navigation-all",
            es_rol_clinico=True, usa_permisos_clinicos_granulares=True,
            puede_ver_terapias=True, puede_escribir_terapias=True,
            puede_ver_pacientes=True, puede_ver_historia_clinica=True,
        )
        cls.actor = Usuario.objects.create_user(
            username="therapy-navigation-actor", password="synthetic-only",
            empresa=cls.empresa, rol_sistema=cls.role,
        )
        cls.actor.empresas_acceso.add(*cls.empresas.values())

    def setUp(self):
        self.client.force_login(self.actor)
        # Resolve URL metadata before replacing any callable; no view is run.
        self.picker_url()
        for target in (
            "socket.socket.connect", "clinica.views.EmailMultiAlternatives.send",
            "clinica.views.subir_documento_whatsapp", "clinica.views.enviar_documento_whatsapp",
        ):
            blocker = patch(target, side_effect=AssertionError("Real transport is forbidden"))
            blocked = blocker.start()
            self.addCleanup(blocker.stop)
            self.addCleanup(blocked.assert_not_called)

    def role_for(self, company=None, granular=True, **flags):
        role = RolSistema.objects.create(
            nombre="Terapias navegación restringido", codigo=f"therapy-nav-{RolSistema.objects.count()}",
            es_rol_clinico=granular, usa_permisos_clinicos_granulares=granular, **flags,
        )
        UsuarioEmpresaPermiso.objects.update_or_create(
            usuario=self.actor, empresa=company or self.empresa,
            defaults={"rol_sistema": role, "activo": True},
        )
        return role

    def fresh_user(self):
        return Usuario.objects.get(pk=self.actor.pk)

    def modules(self, company=None, user=None):
        company = company or self.empresa
        request = RequestFactory().get(reverse("agenda_mobile_modules_screen", args=[company.slug]))
        request.user = user or self.fresh_user()
        context = construir_contexto_modulos_app(request, company)
        return {item["key"]: item for item in context["modulos_app"]}

    def picker_url(self, company=None, creating=False, **filters):
        company = company or self.empresa
        params = {"app": "1", "destino": "crear_historia" if creating else "historias"}
        params["tipo"] = "terapias"
        params.update(filters)
        return reverse("clinica_pacientes", args=[company.slug]) + "?" + urlencode(params)

    def form_url(self, company=None, patient=None, mobile=True, tipo="terapias"):
        company = company or self.empresa
        patient = patient or self.pacientes[company.slug]
        return reverse("clinica_crear_historia_especialidad", args=[company.slug, patient.pk, tipo]) + ("?app=1" if mobile else "")

    def histories_url(self, company=None, patient=None, focused=False):
        company = company or self.empresa
        patient = patient or self.pacientes[company.slug]
        return reverse("clinica_historias_especialidad", args=[company.slug, patient.pk]) + "?app=1" + ("&tipo=terapias" if focused else "")

    def data(self, company=None, **changes):
        company = company or self.empresa
        result = {
            "app": "1", "profesional": str(self.profesionales[company.slug].pk),
            "fecha_atencion": "2026-10-13T10:15", "observaciones": "Nota sintética de terapia\nSegunda línea",
            "estado": "borrador",
        }
        result.update(changes)
        return result

    def snapshot(self):
        return {
            model._meta.label: list(model.objects.order_by("pk").values())
            for model in apps.get_models(include_auto_created=True)
            if not model._meta.proxy and model._meta.managed
        }

    def assert_readonly(self, callback):
        before = self.snapshot()
        # Long company/role matrices must not overflow Django's 9000-query
        # debug buffer and silently leave later SQL assertions empty.
        connection.queries_log.clear()
        with CaptureQueriesContext(connection) as queries:
            response = callback()
        self.assertEqual(self.snapshot(), before)
        for entry in queries:
            self.assertFalse(entry["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "REPLACE")), entry["sql"])
        return response, queries

    def assert_tile(self, item, creating=False, company=None, state="disponible"):
        self.assertEqual(item["estado"], state)
        self.assertEqual(item["panel"], "")
        self.assertEqual(item["url"], self.picker_url(company, creating) if state == "disponible" else "")
        if item["url"]:
            parsed = urlsplit(item["url"])
            self.assertFalse(parsed.scheme or parsed.netloc or parsed.fragment)
            self.assertEqual(parse_qs(parsed.query), {
                "app": ["1"], "destino": ["crear_historia" if creating else "historias"],
                "tipo": ["terapias"],
            })

    def test_four_companies_both_original_role_types_have_available_exact_mobile_tiles(self):
        for company in self.empresas.values():
            for granular in (True, False):
                with self.subTest(company=company.slug, granular=granular):
                    flags = {"puede_ver_terapias": True, "puede_escribir_terapias": True,
                             "puede_ver_pacientes": True, "puede_ver_historia_clinica": True} if granular else {
                                 "puede_pacientes": True, "puede_expediente_clinico": True,
                             }
                    self.role_for(company, granular, **flags)
                    items, _ = self.assert_readonly(lambda: self.modules(company))
                    self.assert_tile(items["terapias"], company=company)
                    self.assert_tile(items["registrar_terapia"], creating=True, company=company)

    def test_read_tile_needs_therapy_read_not_write_or_generic_history_alone(self):
        for therapy_read in (False, True):
            self.role_for(puede_ver_terapias=therapy_read, puede_ver_pacientes=True, puede_ver_historia_clinica=True)
            items = self.modules()
            self.assertEqual("terapias" in items, therapy_read)
            self.assertNotIn("registrar_terapia", items)

    def test_read_tile_pending_if_either_directory_or_general_history_read_is_missing(self):
        for patients, histories in ((False, False), (True, False), (False, True)):
            with self.subTest(patients=patients, histories=histories):
                self.role_for(puede_ver_terapias=True, puede_ver_pacientes=patients, puede_ver_historia_clinica=histories)
                self.assert_tile(self.modules()["terapias"], state="pendiente")

    def test_write_tile_uses_only_write_permission_and_directory_not_history_read(self):
        self.role_for(puede_escribir_terapias=True, puede_ver_pacientes=True)
        items = self.modules()
        self.assertNotIn("terapias", items)
        self.assert_tile(items["registrar_terapia"], creating=True)
        self.assertFalse(self.fresh_user().tiene_permiso_erp("puede_ver_historia_clinica", self.empresa))

    def test_write_tile_pending_without_directory_but_original_direct_form_remains_authorized(self):
        self.role_for(puede_escribir_terapias=True)
        self.assert_tile(self.modules()["registrar_terapia"], creating=True, state="pendiente")
        response, _ = self.assert_readonly(lambda: self.client.get(self.form_url()))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "clinica/mobile_history_form.html")
        self.assertFalse(response.context["can_view_history"])
        self.assertEqual(response.context["historias_previas"], [])

    def test_legacy_expediente_permission_remains_combined_and_directory_remains_separate(self):
        self.role_for(granular=False, puede_expediente_clinico=True)
        items = self.modules()
        self.assert_tile(items["terapias"], state="pendiente")
        self.assert_tile(items["registrar_terapia"], creating=True, state="pendiente")
        self.assertTrue(self.fresh_user().tiene_permiso_erp("puede_expediente_clinico", self.empresa))
        self.role_for(granular=False, puede_pacientes=True)
        self.assertNotIn("terapias", self.modules())
        self.assertNotIn("registrar_terapia", self.modules())

    def test_clinical_module_disabled_removes_both_tiles_without_altering_roles(self):
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.modulo).update(activo=False)
        items, _ = self.assert_readonly(self.modules)
        self.assertNotIn("terapias", items)
        self.assertNotIn("registrar_terapia", items)
        response = self.client.get(self.picker_url())
        self.assertRedirects(response, reverse("dashboard", args=[self.empresa.slug]), fetch_redirect_response=False)

    def test_effective_company_role_override_and_explicit_no_role_do_not_fall_back(self):
        self.role_for(self.empresas["medical_spa"], puede_escribir_terapias=True, puede_ver_pacientes=True)
        self.assertNotIn("terapias", self.modules(self.empresas["medical_spa"]))
        self.assert_tile(self.modules(self.empresas["medical_spa"])["registrar_terapia"], creating=True, company=self.empresas["medical_spa"])
        self.assertIn("terapias", self.modules(self.empresa))
        UsuarioEmpresaPermiso.objects.update_or_create(usuario=self.actor, empresa=self.empresa, defaults={"rol_sistema": None})
        self.assertNotIn("terapias", self.modules())
        self.assertNotIn("registrar_terapia", self.modules())

    def test_inactive_role_or_no_flags_cannot_gain_therapy_navigation(self):
        for flags in ({}, {"puede_ver_terapias": True, "puede_escribir_terapias": True, "activo": False}):
            self.role_for(**flags)
            self.assertNotIn("terapias", self.modules())
            self.assertNotIn("registrar_terapia", self.modules())

    def test_exact_four_clinical_profiles_only_and_foreign_membership_denied(self):
        outside = Empresa.objects.create(nombre="Otra clínica sintética", slug="therapy_external", rtn="THERAPY-OUT", tipo_solucion="clinica")
        EmpresaModulo.objects.create(empresa=outside, modulo=self.modulo)
        self.actor.empresas_acceso.add(outside)
        self.assertEqual(self.modules(outside), {})
        Empresa.objects.filter(pk=self.empresa.pk).update(tipo_solucion="erp")
        self.empresa.refresh_from_db()
        self.assertEqual(self.modules(), {})
        company = self.empresas["medical_spa"]
        self.actor.empresas_acceso.remove(company)
        self.assertEqual(self.modules(company), {})
        response = self.client.get(self.picker_url(company))
        self.assertRedirects(response, reverse("empresa_login", args=[company.slug]), fetch_redirect_response=False)

    def test_anonymous_and_inactive_company_have_no_authorized_picker(self):
        self.assertEqual(self.modules(user=AnonymousUser()), {})
        Empresa.objects.filter(pk=self.empresa.pk).update(activa=False)
        before = self.snapshot()
        self.assertEqual(self.client.get(self.picker_url()).status_code, 404)
        self.assertEqual(self.snapshot(), before)

    def test_suspended_or_expired_license_denies_operations_and_hides_operational_tiles(self):
        for license_state in ("suspendida", "vencida"):
            Empresa.objects.filter(pk=self.empresa.pk).update(estado_licencia=license_state)
            self.empresa.refresh_from_db()
            self.assertNotIn("terapias", self.modules())
            self.assertNotIn("registrar_terapia", self.modules())
            for url in (self.picker_url(), self.form_url()):
                response = self.client.get(url)
                self.assertRedirects(response, reverse("empresa_login", args=[self.empresa.slug]), fetch_redirect_response=False)

    def test_original_company_admin_and_superuser_permissions_not_new_therapy_grants(self):
        self.role_for()
        for admin, superuser in ((True, False), (False, True)):
            Usuario.objects.filter(pk=self.actor.pk).update(es_administrador_empresa=admin, is_superuser=superuser)
            if superuser:
                Empresa.objects.filter(pk=self.empresa.pk).update(estado_licencia="suspendida")
                self.empresa.refresh_from_db()
            items = self.modules()
            self.assert_tile(items["terapias"])
            self.assert_tile(items["registrar_terapia"], creating=True)
            self.assertEqual(self.client.get(self.form_url()).status_code, 200)

    def test_hub_html_and_json_same_links_without_patient_history_queries_or_get_writes(self):
        for company in self.empresas.values():
            for granular in (True, False):
                flags = {"puede_ver_terapias": True, "puede_escribir_terapias": True, "puede_ver_pacientes": True,
                         "puede_ver_historia_clinica": True} if granular else {"puede_pacientes": True, "puede_expediente_clinico": True}
                self.role_for(company, granular, **flags)
                for route, json_response in (("agenda_mobile_modules_screen", False), ("agenda_mobile_modules", True)):
                    with self.subTest(company=company.slug, granular=granular, route=route):
                        response, queries = self.assert_readonly(lambda: self.client.get(reverse(route, args=[company.slug])))
                        self.assertEqual(response.status_code, 200)
                        self.assertIn("no-store", response["Cache-Control"])
                        items = response.json()["modulos"] if json_response else response.context["modulos_app"]
                        by_key = {item["key"]: item for item in items}
                        self.assert_tile(by_key["terapias"], company=company)
                        self.assert_tile(by_key["registrar_terapia"], company=company, creating=True)
                        for entry in queries:
                            self.assertNotIn('"clinica_paciente"', entry["sql"].lower())
                            self.assertNotIn('"clinica_historiaclinicaespecialidad"', entry["sql"].lower())
                        if not json_response:
                            links = _TherapyMarkup(response.content.decode()).links
                            self.assertIn(self.picker_url(company), links)
                            self.assertIn(self.picker_url(company, True), links)

    def test_consultation_picker_and_patient_histories_use_existing_mobile_handlers(self):
        for company in self.empresas.values():
            with self.subTest(company=company.slug):
                tile = self.modules(company)["terapias"]
                picker, _ = self.assert_readonly(lambda: self.client.get(tile["url"]))
                self.assertEqual(picker.status_code, 200)
                self.assertTemplateUsed(picker, "clinica/mobile_patient_list.html")
                self.assertEqual(picker.context["mobile_patient_history_mode"], "historias")
                links = _TherapyMarkup(picker.content.decode()).links
                self.assertIn(self.histories_url(company, focused=True), links)
                for other in self.empresas.values():
                    if other.pk != company.pk:
                        self.assertNotIn(self.histories_url(other, focused=True), links)
                history, _ = self.assert_readonly(lambda: self.client.get(self.histories_url(company, focused=True)))
                self.assertEqual(history.status_code, 200)
                self.assertTemplateUsed(history, "clinica/mobile_patient_histories.html")
                self.assertContains(history, f"Nota sintética previa {company.slug}")

    def test_registration_picker_to_original_four_fields_writer_only_no_history_select(self):
        for company in self.empresas.values():
            self.role_for(company, puede_escribir_terapias=True, puede_ver_pacientes=True)
            picker, _ = self.assert_readonly(lambda: self.client.get(self.modules(company)["registrar_terapia"]["url"]))
            self.assertEqual(picker.status_code, 200)
            self.assertEqual(picker.context["mobile_patient_history_create_type"]["codigo"], "terapias")
            self.assertIn(self.form_url(company), _TherapyMarkup(picker.content.decode()).links)
            response, queries = self.assert_readonly(lambda: self.client.get(self.form_url(company)))
            self.assertEqual(response.status_code, 200)
            form = response.context["form"]
            self.assertIsInstance(form, HistoriaClinicaEspecialidadForm)
            self.assertEqual(tuple(form.fields), self.FIELDS)
            original = HistoriaClinicaEspecialidadForm(empresa=company, paciente=self.pacientes[company.slug], tipo="terapias")
            self.assertEqual(list(form.fields["estado"].choices), list(original.fields["estado"].choices))
            self.assertEqual(list(form.fields["profesional"].queryset.values_list("pk", flat=True)), [self.profesionales[company.slug].pk])
            self.assertFalse(response.context["can_view_history"])
            self.assertEqual(response.context["historias_previas"], [])
            names = _TherapyMarkup(response.content.decode()).forms["mobileHistoryForm"]["names"]
            for name in (*self.FIELDS, "app", "csrfmiddlewaretoken"):
                self.assertEqual(names.count(name), 1)
            self.assertNotIn("plan_tratamiento", names)
            for entry in queries:
                self.assertNotIn('"clinica_historiaclinicaespecialidad"', entry["sql"].lower())
                self.assertNotIn('"clinica_preconsultaclinica"', entry["sql"].lower())

    def test_therapy_read_does_not_authorize_general_history_or_therapy_write(self):
        self.role_for(puede_ver_terapias=True, puede_ver_pacientes=True)
        self.assert_tile(self.modules()["terapias"], state="pendiente")
        self.assertNotIn("registrar_terapia", self.modules())
        for url in (self.histories_url(), self.form_url(), self.form_url(tipo="enfermeria")):
            response = self.client.get(url)
            self.assertRedirects(response, reverse("dashboard", args=[self.empresa.slug]), fetch_redirect_response=False)
        self.assertFalse(self.fresh_user().tiene_permiso_erp("puede_ver_historia_clinica", self.empresa))
        self.assertFalse(self.fresh_user().tiene_permiso_erp("puede_escribir_terapias", self.empresa))

    def test_existing_saver_once_four_companies_both_roles_preserves_actor_and_four_values(self):
        original_save = HistoriaClinicaEspecialidadForm.save
        for company in self.empresas.values():
            for granular in (True, False):
                with self.subTest(company=company.slug, granular=granular):
                    flags = {"puede_escribir_terapias": True, "puede_ver_pacientes": True} if granular else {
                        "puede_pacientes": True, "puede_expediente_clinico": True,
                    }
                    self.role_for(company, granular, **flags)
                    data = self.data(company)
                    before = self.snapshot()
                    previous = set(HistoriaClinicaEspecialidad.objects.values_list("pk", flat=True))
                    with patch.object(HistoriaClinicaEspecialidadForm, "save", autospec=True, side_effect=original_save) as saver:
                        response = self.client.post(self.form_url(company), data)
                    self.assertEqual(response.status_code, 302)
                    saver.assert_called_once()
                    self.assertEqual(saver.call_args.kwargs, {"commit": False})
                    created = HistoriaClinicaEspecialidad.objects.exclude(pk__in=previous).get()
                    self.assertEqual((created.empresa_id, created.paciente_id, created.profesional_id),
                                     (company.pk, self.pacientes[company.slug].pk, self.profesionales[company.slug].pk))
                    self.assertEqual((created.tipo, created.estado, created.observaciones), ("terapias", data["estado"], data["observaciones"]))
                    self.assertEqual((created.creado_por_id, created.actualizado_por_id), (self.actor.pk, self.actor.pk))
                    self.assertEqual(timezone.localtime(created.fecha_atencion).strftime("%Y-%m-%dT%H:%M"), data["fecha_atencion"])
                    self.assertEqual(created.plan_tratamiento, "")
                    self.assertEqual(response["Location"], self.histories_url(company) if not granular else self.form_url(company))
                    after = self.snapshot()
                    changed = {label for label in before if before[label] != after[label]}
                    self.assertLessEqual(changed, {"clinica.HistoriaClinicaEspecialidad", "core.RegistroAuditoria", "sessions.Session"})
                    audit = RegistroAuditoria.objects.filter(app_label="clinica", modelo="historiaclinicaespecialidad", objeto_id=str(created.pk), accion="crear").get()
                    self.assertEqual((audit.usuario_id, audit.empresa_id), (self.actor.pk, company.pk))

    def test_invalid_original_form_keeps_bound_fields_without_business_mutation(self):
        self.role_for(puede_escribir_terapias=True, puede_ver_pacientes=True)
        original_save = HistoriaClinicaEspecialidadForm.save
        data = self.data(fecha_atencion="not-a-date", observaciones="Borrador sintético <script>keep</script>")
        with patch.object(HistoriaClinicaEspecialidadForm, "save", autospec=True, side_effect=original_save) as saver:
            response, _ = self.assert_readonly(lambda: self.client.post(self.form_url(), data))
        self.assertEqual(response.status_code, 200)
        saver.assert_not_called()
        form = response.context["form"]
        self.assertTrue(form.is_bound)
        self.assertIn("fecha_atencion", form.errors)
        for field in self.FIELDS:
            self.assertEqual(form[field].value(), data[field])
        self.assertContains(response, "&lt;script&gt;keep&lt;/script&gt;")
        self.assertNotContains(response, "<script>keep</script>")

    def test_foreign_patient_and_professional_use_original_scope_validation_no_save(self):
        self.role_for(puede_escribir_terapias=True, puede_ver_pacientes=True)
        foreign = self.pacientes["medical_spa"]
        before = self.snapshot()
        self.assertEqual(self.client.get(self.form_url(patient=foreign)).status_code, 404)
        self.assertEqual(self.snapshot(), before)
        data = self.data(profesional=str(self.profesionales["medical_spa"].pk))
        response, _ = self.assert_readonly(lambda: self.client.post(self.form_url(), data))
        self.assertEqual(response.status_code, 200)
        self.assertIn("profesional", response.context["form"].errors)
        self.assertEqual(response.context["form"]["profesional"].value(), data["profesional"])

    def test_selector_search_pagination_keep_exact_create_destination_and_escape_patient_name(self):
        self.role_for(puede_escribir_terapias=True, puede_ver_pacientes=True)
        for index in range(27):
            Paciente.objects.create(empresa=self.empresa, nombre=f"Navegación Terapia {index:02d}", expediente_codigo=f"THERAPY-PAGE-{index}")
        Paciente.objects.filter(pk=self.paciente.pk).update(nombre="<script>synthetic-patient</script>")
        page, _ = self.assert_readonly(lambda: self.client.get(self.picker_url(creating=True, page=2)))
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.context["pacientes"].number, 2)
        pagination = []
        for link in _TherapyMarkup(page.content.decode()).links:
            parsed = urlsplit(urljoin(self.picker_url(creating=True, page=2), link))
            params = parse_qs(parsed.query)
            # The trained bottom-navigation "Pacientes" link intentionally
            # opens the regular directory, not this specialised selector.
            if parsed.path == reverse("clinica_pacientes", args=[self.empresa.slug]) and "page" in params:
                pagination.append(params)
        self.assertTrue(pagination)
        for params in pagination:
            self.assertEqual(params["app"], ["1"])
            self.assertEqual(params.get("destino"), ["crear_historia"])
            self.assertEqual(params.get("tipo"), ["terapias"])
        query, _ = self.assert_readonly(lambda: self.client.get(self.picker_url(creating=True, q="synthetic-patient")))
        self.assertEqual(query.context["pacientes"].paginator.count, 1)
        self.assertContains(query, 'name="destino" value="crear_historia"')
        self.assertContains(query, 'name="tipo" value="terapias"')
        self.assertContains(query, "&lt;script&gt;synthetic-patient&lt;/script&gt;")
        self.assertNotContains(query, "<script>synthetic-patient</script>")

    def test_new_tiles_escape_role_label_and_never_offer_desktop_or_other_area_urls(self):
        role = self.role_for(puede_escribir_terapias=True, puede_ver_pacientes=True)
        RolSistema.objects.filter(pk=role.pk).update(nombre="<script>synthetic-role</script>")
        response, _ = self.assert_readonly(lambda: self.client.get(reverse("agenda_mobile_modules_screen", args=[self.empresa.slug])))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "&lt;script&gt;synthetic-role&lt;/script&gt;")
        self.assertNotContains(response, "<script>synthetic-role</script>")
        items = {item["key"]: item for item in response.context["modulos_app"]}
        self.assert_tile(items["registrar_terapia"], creating=True)
        self.assertFalse(response.context["app_home_disponible"])
        self.assertNotContains(response, 'rel="manifest"')
        self.assertFalse(self.fresh_user().tiene_permiso_erp("puede_ver_calendario", self.empresa))
        self.assertFalse(self.fresh_user().tiene_permiso_erp("puede_crear_historia_clinica", self.empresa))
        self.assertNotIn("camara", items)
        self.assertNotIn("postquirurgicas", items)

    def test_unflagged_web_form_and_outside_four_app_marker_keep_original_template_and_fields(self):
        self.role_for(granular=False, puede_pacientes=True, puede_expediente_clinico=True)
        web, _ = self.assert_readonly(lambda: self.client.get(self.form_url(mobile=False)))
        self.assertEqual(web.status_code, 200)
        self.assertTemplateUsed(web, "clinica/historia_especialidad_form.html")
        self.assertTemplateNotUsed(web, "clinica/mobile_history_form.html")
        self.assertEqual(tuple(web.context["form"].fields), self.FIELDS)
        outside = Empresa.objects.create(nombre="Terapia web externa", slug="therapy_web_external", rtn="THERAPY-WEB", tipo_solucion="clinica")
        self.role_for(company=outside, granular=False, puede_pacientes=True, puede_expediente_clinico=True)
        EmpresaModulo.objects.create(empresa=outside, modulo=self.modulo)
        self.actor.empresas_acceso.add(outside)
        patient = Paciente.objects.create(empresa=outside, nombre="Paciente externo sintético", expediente_codigo="THERAPY-WEB")
        response, _ = self.assert_readonly(lambda: self.client.get(self.form_url(outside, patient)))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "clinica/historia_especialidad_form.html")
        self.assertTemplateNotUsed(response, "clinica/mobile_history_form.html")
        self.assertEqual(self.modules(outside), {})
