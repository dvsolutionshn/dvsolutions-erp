"""Synthetic virtual-appointment coverage; no external WhatsApp transport."""

from datetime import date, datetime, timedelta, timezone as datetime_timezone
from html.parser import HTMLParser
import json
from unittest.mock import patch

from django.db import IntegrityError, connection, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from clinica.forms import CitaClinicaForm
from clinica.models import CitaClinica, Paciente, ProfesionalSalud, ServicioClinico
from clinica.views import _sincronizar_agenda_desde_cita_clinica
from core.models import Empresa, EmpresaModulo, Modulo, RolSistema, Usuario, UsuarioEmpresaPermiso

from .forms import CitaClienteForm
from .models import CitaCliente
from .views import _sincronizar_cita_clinica


class VirtualAppointmentSchemaIntegrationTests(TestCase):
    def test_reviewed_schema_helper_accepts_real_migrated_tables_idempotently(self):
        from deploy.virtual_appointments_schema import inspect_schema, apply_schema

        before = (CitaCliente.objects.count(), CitaClinica.objects.count(), Usuario.objects.count())
        _, plan = inspect_schema()
        self.assertEqual(plan, [])
        self.assertEqual(apply_schema(), [])
        self.assertEqual(before, (CitaCliente.objects.count(), CitaClinica.objects.count(), Usuario.objects.count()))


class _VirtualMarkup(HTMLParser):
    def __init__(self, content):
        super().__init__(convert_charrefs=True)
        self.nodes = []
        self.feed(content.decode() if isinstance(content, bytes) else content)

    def handle_starttag(self, tag, attrs):
        self.nodes.append((tag, dict(attrs)))


class _VirtualFixtures(TestCase):
    SLUGS = ("hospital_mia", "medical_spa", "luque_aestetic", "serviciosmedicos")
    NOW = datetime(2026, 11, 25, 18, tzinfo=datetime_timezone.utc)

    @classmethod
    def setUpTestData(cls):
        cls.companies, cls.patients, cls.professionals, cls.services = {}, {}, {}, {}
        modules = [Modulo.objects.get_or_create(codigo=code, defaults={"nombre": code})[0]
                   for code in ("agenda_citas", "clinica_medica", "crm_marketing")]
        for index, slug in enumerate((*cls.SLUGS, "virtual_outside"), 1):
            company = Empresa.objects.create(nombre=f"Virtual QA {slug}", slug=slug,
                rtn=f"VIRTUAL-QA-{index}", tipo_solucion="clinica", estado_licencia="activa")
            for module in modules:
                EmpresaModulo.objects.create(empresa=company, modulo=module, activo=True)
            cls.companies[slug] = company
            cls.patients[slug] = Paciente.objects.create(empresa=company, nombre=f"Paciente Virtual QA {slug}",
                expediente_codigo=f"VIRTUAL-QA-{index}", telefono="PRIVATE-CONTACT-QA")
            cls.professionals[slug] = ProfesionalSalud.objects.create(empresa=company, nombre=f"Dr. Luis Gonzales QA {slug}")
            cls.services[slug] = ServicioClinico.objects.create(empresa=company, nombre="Consulta Virtual QA",
                duracion_minutos=30, color_calendario="#167f91")
        cls.hospital = cls.companies["hospital_mia"]
        cls.readonly = RolSistema.objects.create(codigo="virtual-readonly-qa", nombre="Consulta Virtual QA",
            es_rol_clinico=True, usa_permisos_clinicos_granulares=True,
            usa_permisos_clinicos_v2=True, usa_permisos_acciones_v3=True,
            puede_ver_calendario=True, puede_ver_pacientes=True)
        cls.reception = RolSistema.objects.create(codigo="virtual-reception-qa", nombre="Recepción Virtual QA",
            es_rol_clinico=True, usa_permisos_clinicos_granulares=True,
            usa_permisos_clinicos_v2=True, usa_permisos_acciones_v3=True,
            puede_ver_calendario=True, puede_ver_pacientes=True, solo_tarjetas_pacientes=True,
            puede_crear_citas=True, puede_editar_citas=True, puede_reagendar_citas=True)
        cls.admin = Usuario.objects.create_user(username="virtual-admin-qa", empresa=cls.hospital, es_administrador_empresa=True)
        cls.reader = Usuario.objects.create_user(username="virtual-reader-qa", empresa=cls.hospital, rol_sistema=cls.readonly)
        cls.receptionist = Usuario.objects.create_user(username="virtual-reception-qa", empresa=cls.hospital, rol_sistema=cls.reception)
        for user in (cls.admin, cls.reader, cls.receptionist):
            user.empresas_acceso.add(*(cls.companies[slug] for slug in cls.SLUGS))

    def setUp(self):
        for name, kwargs in (
            ("django.utils.timezone.now", {"return_value": self.NOW}),
            ("crm.views._programar_whatsapp_cita", {}),
            ("crm.views.programar_notificaciones_cita", {"return_value": []}),
            ("crm.views._enviar_aviso_cita_whatsapp", {}),
            ("crm.appointment_notifications.programar_notificaciones_cita", {"return_value": []}),
            ("crm.appointment_notifications.procesar_notificacion", {}),
        ):
            mocked = patch(name, **kwargs)
            mocked.start()
            self.addCleanup(mocked.stop)
        self.client.force_login(self.admin)

    def data(self, slug="hospital_mia", *, day="2030-01-04", **changes):
        result = {"paciente": self.patients[slug].pk, "profesional_salud": self.professionals[slug].pk,
            "servicio_clinico": self.services[slug].pk, "fecha_cita": day,
            "hora_cita": "10:00", "periodo_cita": "AM", "estado": "pendiente"}
        result.update(changes)
        return result

    def appointment(self, slug="hospital_mia", *, virtual=True, hour=10, **changes):
        values = {"empresa": self.companies[slug], "paciente": self.patients[slug],
            "profesional_salud": self.professionals[slug], "servicio_clinico": self.services[slug],
            "titulo": self.services[slug].nombre, "fecha_hora": timezone.make_aware(datetime(2030, 1, 4, hour)),
            "virtual": virtual, "observacion": "PRIVATE-CLINICAL-OBSERVATION-QA"}
        values.update(changes)
        return CitaCliente.objects.create(**values)

    @staticmethod
    def access_snapshot():
        return tuple(list(model.objects.order_by("pk").values())
                     for model in (Usuario, RolSistema, UsuarioEmpresaPermiso))


class CitasVirtualesFormsAndStorageTests(_VirtualFixtures):
    def test_defaults_are_presencial_in_both_existing_models(self):
        for model in (CitaCliente, CitaClinica):
            with self.subTest(model=model.__name__):
                self.assertIs(model._meta.get_field("virtual").default, False)
                self.assertIs(model._meta.get_field("virtual").db_default, False)
                self.assertEqual(model._meta.get_field("virtual").verbose_name, "Cita virtual")
        values = {"empresa": self.hospital, "paciente": self.patients["hospital_mia"], "fecha_hora": self.NOW}
        agenda = CitaCliente.objects.create(**values, titulo="Presencial QA")
        clinical = CitaClinica.objects.create(**values, motivo="Presencial QA")
        agenda.refresh_from_db(); clinical.refresh_from_db()
        self.assertFalse(agenda.virtual)
        self.assertFalse(clinical.virtual)

    def test_legacy_sql_inserts_without_virtual_column_still_default_to_presencial(self):
        values = {"empresa": self.hospital, "paciente": self.patients["hospital_mia"], "fecha_hora": self.NOW,
                  "virtual": True}
        for model, extra in ((CitaCliente, {"titulo": "Legacy agenda QA"}), (CitaClinica, {"motivo": "Legacy clínica QA"})):
            with self.subTest(model=model.__name__):
                original = model.objects.create(**values, **extra)
                columns = [field.column for field in model._meta.local_concrete_fields
                           if not field.primary_key and field.name != "virtual"]
                quote = connection.ops.quote_name
                sql_columns = ", ".join(quote(column) for column in columns)
                with connection.cursor() as cursor:
                    cursor.execute(f"INSERT INTO {quote(model._meta.db_table)} ({sql_columns}) "
                        f"SELECT {sql_columns} FROM {quote(model._meta.db_table)} WHERE {quote(model._meta.pk.column)} = %s", [original.pk])
                legacy = model.objects.latest("pk")
                self.assertNotEqual(legacy.pk, original.pk)
                self.assertFalse(legacy.virtual)
                original.refresh_from_db()
                self.assertTrue(original.virtual)

    def test_checkbox_is_exposed_only_in_the_four_authorized_company_forms(self):
        for slug, company in self.companies.items():
            for form_class in (CitaClienteForm, CitaClinicaForm):
                with self.subTest(company=slug, form=form_class.__name__):
                    form = form_class(empresa=company, usuario=self.admin)
                    self.assertEqual("virtual" in form.fields, slug in self.SLUGS)
                    if slug in self.SLUGS:
                        self.assertEqual(form.fields["virtual"].label, "Cita virtual")
                        self.assertFalse(form.fields["virtual"].required)
                        self.assertEqual(form.fields["virtual"].widget.input_type, "checkbox")

    def test_virtual_is_independent_of_paid_and_courtesy_and_keeps_existing_exclusion(self):
        for slug in self.SLUGS:
            for flags in ({}, {"pagada": "on"}, {"cortesia": "on"}):
                form = CitaClienteForm(self.data(slug, virtual="on", **flags), empresa=self.companies[slug], usuario=self.admin)
                self.assertTrue(form.is_valid(), form.errors.as_json())
                self.assertTrue(form.cleaned_data["virtual"])
                clinical = CitaClinicaForm({"paciente": self.patients[slug].pk, "motivo": "Virtual QA",
                    "fecha_cita": "2030-01-04", "hora_cita": "10:00", "periodo_cita": "AM",
                    "estado": "solicitada", "canal": "recepcion", "virtual": "on", **flags},
                    empresa=self.companies[slug], usuario=self.admin)
                self.assertTrue(clinical.is_valid(), clinical.errors.as_json())
                self.assertTrue(clinical.cleaned_data["virtual"])
            invalid = CitaClienteForm(self.data(slug, virtual="on", pagada="on", cortesia="on"), empresa=self.companies[slug], usuario=self.admin)
            self.assertFalse(invalid.is_valid())
            self.assertIn("cortesia", invalid.errors)
        values = {"empresa": self.hospital, "paciente": self.patients["hospital_mia"], "fecha_hora": self.NOW,
                  "virtual": True, "pagada": True, "cortesia": True}
        for model, extra in ((CitaCliente, {"titulo": "Inválida QA"}), (CitaClinica, {"motivo": "Inválida QA"})):
            with self.subTest(model=model.__name__), self.assertRaises(IntegrityError), transaction.atomic():
                model.objects.create(**values, **extra)

    def test_legacy_outside_company_form_cannot_change_virtual_by_forged_field(self):
        slug = "virtual_outside"
        cita = self.appointment(slug)
        data = self.data(slug, hora_cita="11:00", virtual="")
        form = CitaClienteForm(data, empresa=self.companies[slug], usuario=self.admin, instance=cita)
        self.assertNotIn("virtual", form.fields)
        self.assertTrue(form.is_valid(), form.errors.as_json())
        saved = form.save()
        self.assertTrue(saved.virtual)

    def test_virtual_does_not_override_service_or_professional_colors(self):
        cita = self.appointment(virtual=False)
        previous = (cita.agenda_color, cita.agenda_color_personalizado, cita.agenda_profesional_color)
        cita.virtual = True
        cita.save(update_fields=["virtual"])
        cita.refresh_from_db()
        self.assertEqual(previous, (cita.agenda_color, cita.agenda_color_personalizado, cita.agenda_profesional_color))


class CitasVirtualesWriteTests(_VirtualFixtures):
    def test_existing_clinical_create_route_mirrors_virtual_for_four_companies(self):
        for slug in self.SLUGS:
            response = self.client.post(reverse("clinica_crear_cita", args=[slug]), {
                "paciente": self.patients[slug].pk, "profesional": self.professionals[slug].pk,
                "servicio": self.services[slug].pk, "motivo": "Consulta virtual desde clínica QA",
                "fecha_cita": "2030-01-04", "hora_cita": "10:00", "periodo_cita": "AM",
                "estado": "solicitada", "canal": "recepcion", "virtual": "on",
            })
            self.assertEqual(response.status_code, 302, response.context["form"].errors.as_json() if response.context else None)
            clinical = CitaClinica.objects.get(empresa=self.companies[slug])
            self.assertTrue(clinical.virtual)
            self.assertTrue(CitaCliente.objects.get(cita_clinica=clinical).virtual)

    def test_create_edit_reopen_and_uncheck_in_web_and_app_for_four_companies(self):
        before = self.access_snapshot()
        for number, slug in enumerate(self.SLUGS):
            for offset, view in enumerate(("agenda_citas", "agenda_mobile")):
                with self.subTest(company=slug, view=view):
                    origin = "hospital_mia" if view == "agenda_mobile" else slug
                    day = (date(2030, 1, 4) + timedelta(days=number * 12 + offset * 3)).isoformat()
                    url = reverse(view, args=[slug])
                    data = self.data(origin, day=day, virtual="on")
                    response = self.client.post(url, data)
                    self.assertEqual(response.status_code, 302, response.context["form"].errors.as_json() if response.context else None)
                    cita = CitaCliente.objects.get(empresa=self.companies[origin], paciente=self.patients[origin], fecha_hora__date=day)
                    self.assertTrue(cita.virtual)
                    self.assertTrue(cita.cita_clinica.virtual)
                    reopened = self.client.get(url, {"editar": cita.pk, "fecha": day})
                    self.assertEqual(reopened.status_code, 200)
                    self.assertTrue(reopened.context["form"]["virtual"].value())
                    update = {**data, "cita_id": cita.pk, "hora_cita": "11:00"}
                    update.pop("virtual")
                    response = self.client.post(url, update)
                    self.assertEqual(response.status_code, 302, response.context["form"].errors.as_json() if response.context else None)
                    cita.refresh_from_db()
                    self.assertFalse(cita.virtual)
                    self.assertFalse(cita.cita_clinica.virtual)
                    self.assertEqual(timezone.localtime(cita.fecha_hora).hour, 11)
        self.assertEqual(before, self.access_snapshot())

    def test_series_clones_keep_virtual_and_skip_sunday(self):
        for number, slug in enumerate(self.SLUGS):
            day = date(2030, 1, 4) + timedelta(days=7 * number)
            response = self.client.post(reverse("agenda_mobile", args=[slug]), self.data(
                day=day.isoformat(), virtual="on", crear_serie="on", cantidad_serie="3"))
            self.assertEqual(response.status_code, 302, response.context["form"].errors.as_json() if response.context else None)
            citas = list(CitaCliente.objects.filter(grupo_atencion__isnull=False, fecha_hora__date__gte=day,
                fecha_hora__date__lte=day + timedelta(days=3)).select_related("cita_clinica").order_by("fecha_hora"))
            self.assertEqual(len(citas), 3)
            self.assertTrue(all(cita.virtual and cita.cita_clinica.virtual for cita in citas))
            self.assertEqual([timezone.localtime(cita.fecha_hora).weekday() for cita in citas], [4, 5, 0])
            self.assertEqual(len({cita.grupo_atencion for cita in citas}), 1)

    def test_multisession_creation_copies_virtual_to_every_appointment(self):
        CitaClienteForm(empresa=self.hospital)
        service = ServicioClinico.objects.get(empresa=self.hospital, nombre="Terapias")
        details = [{"tipo": "terapias", "clave": f"terapia-1-{session}", "fase": 1, "sesion": session,
                    "hora": f"0{session + 7}:00", "periodo": "AM"} for session in (1, 2)]
        response = self.client.post(reverse("agenda_mobile", args=[self.hospital.slug]), self.data(
            servicio_clinico=service.pk, hora_cita="08:00", virtual="on", detalles_agenda=json.dumps(details)))
        self.assertEqual(response.status_code, 302, response.context["form"].errors.as_json() if response.context else None)
        citas = list(CitaCliente.objects.filter(servicio_clinico=service).select_related("cita_clinica"))
        self.assertEqual(len(citas), 2)
        self.assertTrue(all(cita.virtual and cita.cita_clinica.virtual for cita in citas))
        self.assertEqual({cita.sesion_servicio for cita in citas}, {1, 2})

    def test_rescheduling_preserves_virtual_without_accepting_forged_other_changes(self):
        cita = self.appointment(pagada=True)
        _sincronizar_cita_clinica(cita)
        response = self.client.post(reverse("agenda_cita_reagendar_whatsapp", args=[self.hospital.slug, cita.pk]), {
            "nueva_fecha_hora": "2030-01-09T14:00", "return_to": "mobile", "virtual": "", "pagada": "",
        })
        self.assertEqual(response.status_code, 302)
        cita.refresh_from_db()
        self.assertTrue(cita.virtual)
        self.assertTrue(cita.pagada)
        self.assertTrue(cita.cita_clinica.virtual)
        self.assertEqual(timezone.localtime(cita.fecha_hora).strftime("%Y-%m-%dT%H:%M"), "2030-01-09T14:00")

    def test_bidirectional_clinical_mirror_copies_true_and_false_without_duplicates(self):
        for slug in self.SLUGS:
            cita = self.appointment(slug)
            _sincronizar_cita_clinica(cita)
            clinical = cita.cita_clinica
            self.assertTrue(clinical.virtual)
            for value in (False, True, False):
                clinical.virtual = value
                clinical.save(update_fields=["virtual"])
                mirrored = _sincronizar_agenda_desde_cita_clinica(clinical)
                cita.refresh_from_db()
                self.assertEqual(mirrored.pk, cita.pk)
                self.assertEqual(cita.virtual, value)
                self.assertEqual(CitaCliente.objects.filter(cita_clinica=clinical).count(), 1)
                _sincronizar_cita_clinica(cita)
                clinical.refresh_from_db()
                self.assertEqual(clinical.virtual, value)

    def test_reception_can_mark_virtual_only_in_its_existing_local_agenda(self):
        self.client.force_login(self.receptionist)
        for slug in self.SLUGS:
            response = self.client.post(reverse("agenda_mobile", args=[slug]), self.data(slug, virtual="on"))
            self.assertEqual(response.status_code, 302, response.context["form"].errors.as_json() if response.context else None)
            cita = CitaCliente.objects.get(empresa=self.companies[slug], paciente=self.patients[slug])
            self.assertTrue(cita.virtual)
            self.assertTrue(cita.cita_clinica.virtual)

    def test_readonly_forged_posts_cannot_change_virtual_or_create_new_appointments(self):
        cita = self.appointment(virtual=False)
        _sincronizar_cita_clinica(cita)
        self.client.force_login(self.reader)
        before = (list(CitaCliente.objects.values()), list(CitaClinica.objects.values()), self.access_snapshot())
        for slug in self.SLUGS:
            for view in ("agenda_mobile", "agenda_citas"):
                for data in (self.data(virtual="on"), self.data(virtual="on", cita_id=cita.pk)):
                    with self.subTest(company=slug, view=view, editing="cita_id" in data):
                        response = self.client.post(reverse(view, args=[slug]), data)
                        self.assertIn(response.status_code, (302, 403))
        self.assertEqual(before, (list(CitaCliente.objects.values()), list(CitaClinica.objects.values()), self.access_snapshot()))

    def test_foreign_appointment_and_nonmember_company_remain_blocked(self):
        foreign = self.appointment("virtual_outside", virtual=False)
        before = (list(CitaCliente.objects.values()), list(CitaClinica.objects.values()), self.access_snapshot())
        for slug in self.SLUGS:
            response = self.client.post(reverse("agenda_mobile", args=[slug]), self.data(virtual="on", cita_id=foreign.pk))
            self.assertEqual(response.status_code, 404)
        response = self.client.post(reverse("agenda_mobile", args=["virtual_outside"]), self.data("virtual_outside", virtual="on"))
        self.assertIn(response.status_code, (302, 403))
        self.assertEqual(before, (list(CitaCliente.objects.values()), list(CitaClinica.objects.values()), self.access_snapshot()))


class CitasVirtualesPresentationTests(_VirtualFixtures):
    def badge_nodes(self, response):
        self.assertEqual(response.status_code, 200)
        return [(tag, attrs) for tag, attrs in _VirtualMarkup(response.content).nodes
                if "appointment-virtual-badge" in attrs.get("class", "").split()]

    def test_virtual_badge_is_rendered_in_four_companies_web_and_app_calendar_views(self):
        for slug in self.SLUGS:
            self.appointment(slug)
            self.appointment(slug, virtual=False, hour=12)
        for slug in self.SLUGS:
            for view in ("agenda_citas", "agenda_mobile"):
                for calendar in ("dia", "semana", "mes", "anio"):
                    with self.subTest(company=slug, view=view, calendar=calendar):
                        response = self.client.get(reverse(view, args=[slug]), {"vista": calendar, "fecha": "2030-01-04"})
                        badges = self.badge_nodes(response)
                        self.assertTrue(badges)
                        self.assertTrue(all("data-appointment-virtual" in attrs for _, attrs in badges))
                        self.assertContains(response, "Virtual")
                        self.assertContains(response, "color-servicio-")
                        self.assertContains(response, "professional-doctor-luis")

    def test_presencial_calendar_has_no_virtual_badge_even_with_toggle_available(self):
        self.appointment(virtual=False)
        for view in ("agenda_citas", "agenda_mobile"):
            response = self.client.get(reverse(view, args=[self.hospital.slug]), {"vista": "dia", "fecha": "2030-01-04"})
            self.assertFalse(self.badge_nodes(response))
            self.assertContains(response, 'name="virtual"')

    def test_search_dto_keeps_virtual_type_without_leaking_notes_to_readonly(self):
        virtual = self.appointment()
        presencial = self.appointment(virtual=False, hour=12)
        self.client.force_login(self.reader)
        for slug in self.SLUGS:
            with self.subTest(company=slug):
                response = self.client.get(reverse("agenda_mobile", args=[slug]), {
                    "citas_q": "Paciente Virtual QA hospital_mia", "citas_periodo": "todas", "fecha": "1999-01-01",
                })
                self.assertEqual(response.status_code, 200)
                result = {item["id"]: item for item in response.context["citas_busqueda_page"].object_list}
                self.assertEqual(set(result), {virtual.pk, presencial.pk})
                self.assertTrue(result[virtual.pk]["virtual"])
                self.assertFalse(result[presencial.pk]["virtual"])
                self.assertTrue(self.badge_nodes(response))
                self.assertNotContains(response, "PRIVATE-CLINICAL-OBSERVATION-QA")
                self.assertNotContains(response, "PRIVATE-CONTACT-QA")
                self.assertNotContains(response, 'name="virtual"')

    def test_luis_mirror_filters_other_professional_virtuals_and_foreign_rows(self):
        allowed = self.appointment()
        other_doctor = ProfesionalSalud.objects.create(empresa=self.hospital, nombre="Dra. Candy QA")
        excluded = self.appointment(hour=12, profesional_salud=other_doctor, responsable="Dr Luis Gonzales QA")
        foreign = self.appointment("virtual_outside")
        response = self.client.get(reverse("agenda_mobile", args=["serviciosmedicos"]), {"citas_q": "Paciente Virtual QA"})
        self.assertEqual(response.status_code, 200)
        result = {item["id"] for item in response.context["citas_busqueda_page"].object_list}
        self.assertEqual(result, {allowed.pk})
        self.assertNotIn(excluded.pk, result)
        self.assertNotIn(foreign.pk, result)
