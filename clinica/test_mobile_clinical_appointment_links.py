"""Read-only links from direct boards to the existing Hospital appointment UI."""

from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.apps import apps
from django.db import connection, reset_queries
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.models import EmpresaModulo
from crm import test_mobile_appointment_controls as appointment_fixtures
from crm import mobile_appointment_controls
from crm.models import CitaCliente


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class MobileClinicalAppointmentLinksTests(TestCase):
    # Reuse synthetic fixture setup without inheriting/discovering old tests.
    TIPOS = appointment_fixtures.MobileAppointmentControlsIntegrationTests.TIPOS
    CONFIG = appointment_fixtures.MobileAppointmentControlsIntegrationTests.CONFIG
    asignar_rol = appointment_fixtures.MobileAppointmentControlsIntegrationTests.asignar_rol

    @classmethod
    def setUpTestData(cls):
        appointment_fixtures.MobileAppointmentControlsIntegrationTests.setUpTestData.__func__(cls)
        for key, session in cls.sesiones.items():
            if key[2] == "draft":
                cls.CONFIG[key[1]][7].objects.filter(pk=session.pk).update(numero_sesion_adicional=2)

    def setUp(self):
        appointment_fixtures.MobileAppointmentControlsIntegrationTests.setUp(self)
        self.url()
        for target in ("socket.create_connection", "socket.socket.connect", "socket.socket.connect_ex"):
            blocker = patch(target, side_effect=AssertionError("No real transport in appointment-link tests"))
            mock = blocker.start()
            self.addCleanup(blocker.stop)
            self.addCleanup(mock.assert_not_called)
        self.assertEqual(self.client.get(self.url()).status_code, 200)

    def key(self, tipo=None, company=None, case="draft"):
        return ((company or self.empresa).slug, tipo or self.TIPOS[0], case)

    def url(self, tipo=None, company=None, case="draft", mobile=True):
        company = company or self.empresa
        cita = self.citas[self.key(tipo, company, case)]
        return reverse("clinica_crear_historia_especialidad", args=[company.slug, cita.paciente_id, tipo or self.TIPOS[0]]) + ("?app=1" if mobile else "")

    def snapshot(self):
        return {model._meta.label_lower: list(model._base_manager.order_by("pk").values())
                for model in apps.get_models(include_auto_created=True) if model._meta.managed and not model._meta.proxy}

    def read(self, url=None):
        before = self.snapshot()
        reset_queries()
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(url or self.url())
        self.assertEqual(self.snapshot(), before)
        for row in queries:
            self.assertFalse(row["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "REPLACE")), row["sql"])
        return response, queries

    def linked_record(self, response):
        records = [item for item in response.context["mobile_control_records"] if item["ligada_cita"]]
        self.assertEqual(len(records), 1)
        return records[0]

    def expected_url(self, tipo=None, company=None, case="draft"):
        tipo = tipo or self.TIPOS[0]
        company = company or self.empresa
        cita = self.citas[self.key(tipo, company, case)]
        return mobile_appointment_controls.url_control_cita_app(company, tipo, fecha=timezone.localtime(cita.fecha_hora).date(), cita=cita)

    def test_both_original_hospital_types_link_readonly_attention_not_direct_session(self):
        for tipo in self.TIPOS:
            response, _ = self.read(self.url(tipo))
            self.assertEqual(response.status_code, 200)
            record = self.linked_record(response)
            self.assertEqual(record["appointment_url"], self.expected_url(tipo))
            self.assertEqual(record["url"], "")
            self.assertTrue(record["campos"])
            self.assertContains(response, "Ver atención con cita")
            self.assertContains(response, record["appointment_url"].replace("&", "&amp;"))
            parsed = urlsplit(record["appointment_url"])
            self.assertEqual(parsed.fragment, "")
            self.assertEqual(parse_qs(parsed.query), {"app": ["1"], "fecha": [timezone.localtime(self.citas[self.key(tipo)].fecha_hora).date().isoformat()], self.CONFIG[tipo][2]: [str(self.citas[self.key(tipo)].pk)]})
            for card in response.context["mobile_control_board_items"]:
                if card["ligada_cita"]:
                    self.assertEqual(card["url"], "")
                    self.assertEqual(card["appointment_url"], record["appointment_url"])

    def test_original_appointment_reader_can_follow_each_link_without_writes(self):
        for tipo in self.TIPOS:
            board, _ = self.read(self.url(tipo))
            record = self.linked_record(board)
            attention, _ = self.read(record["appointment_url"])
            self.assertEqual(attention.status_code, 200)
            self.assertTemplateUsed(attention, "crm/mobile_appointment_control.html")
            self.assertEqual(attention.context["mobile_appointment_control_cita"].pk, self.citas[self.key(tipo)].pk)

    def test_session_covering_two_numbers_reuses_one_original_integrity_check(self):
        original = mobile_appointment_controls._validar_cita
        with patch("crm.mobile_appointment_controls._validar_cita", wraps=original) as validator:
            response, _ = self.read()
        validator.assert_called_once()
        cards = [item for item in response.context["mobile_control_board_items"] if item["ligada_cita"]]
        self.assertEqual([item["numero"] for item in cards], [1, 2])
        self.assertEqual(cards[0]["appointment_url"], cards[1]["appointment_url"])

    def test_writer_without_appointment_read_gets_only_metadata_no_link_or_fields(self):
        self.asignar_rol(puede_escribir_camara_hiperbarica=True, puede_escribir_postquirurgicas=True)
        for tipo in self.TIPOS:
            response, _ = self.read(self.url(tipo))
            self.assertEqual(response.status_code, 200)
            record = self.linked_record(response)
            self.assertEqual((record["appointment_url"], record["url"], record["campos"]), ("", "", []))
            self.assertNotContains(response, "Ver atención con cita")
            self.assertNotContains(response, self.sesiones[self.key(tipo)].nota_enfermeria)

    def test_other_three_clinics_never_retarget_linked_records_to_hospital(self):
        for company in self.empresas.values():
            if company.slug == "hospital_mia":
                continue
            for tipo in self.TIPOS:
                response, _ = self.read(self.url(tipo, company))
                self.assertEqual(response.status_code, 200)
                record = self.linked_record(response)
                self.assertEqual((record["appointment_url"], record["url"], record["campos"]), ("", "", []))
                self.assertNotContains(response, self.expected_url(tipo))

    def test_legacy_citas_is_required_for_link_and_never_grants_general_history(self):
        for can_read in (False, True):
            self.asignar_rol(granular=False, puede_expediente_clinico=True, puede_citas=can_read)
            for tipo in self.TIPOS:
                response, _ = self.read(self.url(tipo))
                self.assertEqual(response.status_code, 200)
                record = self.linked_record(response)
                self.assertEqual(bool(record["appointment_url"]), can_read)
                self.assertEqual(bool(record["campos"]), can_read)

    def test_disabled_agenda_keeps_direct_board_but_no_appointment_link_or_fields(self):
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.modulo_agenda).update(activo=False)
        for tipo in self.TIPOS:
            response, _ = self.read(self.url(tipo))
            self.assertEqual(response.status_code, 200)
            record = self.linked_record(response)
            self.assertEqual((record["appointment_url"], record["campos"]), ("", []))

    def test_foreign_company_or_patient_link_is_denied_before_any_href(self):
        cita = self.citas[self.key()]
        own_patient = cita.paciente_id
        for changes in ({"empresa": self.empresas["medical_spa"]}, {"paciente_id": self.citas[self.key(company=self.empresas["medical_spa"])].paciente_id}):
            CitaCliente.objects.filter(pk=cita.pk).update(**changes)
            response, _ = self.read()
            self.assertEqual(response.status_code, 403)
            self.assertNotContains(response, "Ver atención con cita", status_code=403)
            CitaCliente.objects.filter(pk=cita.pk).update(empresa=self.empresa, paciente_id=own_patient)

    def test_original_cita_relation_guard_denies_foreign_service_and_professional(self):
        cita = self.citas[self.key()]
        originals = {"servicio_clinico_id": cita.servicio_clinico_id, "profesional_salud_id": cita.profesional_salud_id}
        for changes in ({"servicio_clinico": self.servicios[("medical_spa", self.TIPOS[0])]}, {"profesional_salud": self.profesionales["medical_spa"]}):
            CitaCliente.objects.filter(pk=cita.pk).update(**changes)
            response, _ = self.read()
            self.assertEqual(response.status_code, 403)
            CitaCliente.objects.filter(pk=cita.pk).update(**originals)

    def test_changed_service_type_does_not_offer_a_wrong_attention_or_retarget(self):
        for tipo in self.TIPOS:
            other_tipo = next(code for code in self.TIPOS if code != tipo)
            cita = self.citas[self.key(tipo)]
            CitaCliente.objects.filter(pk=cita.pk).update(servicio_clinico=self.servicios[(self.empresa.slug, other_tipo)])
            response, _ = self.read(self.url(tipo))
            self.assertEqual(response.status_code, 200)
            record = self.linked_record(response)
            self.assertEqual((record["appointment_url"], record["campos"], record["url"]), ("", [], ""))
            self.assertNotContains(response, "Ver atención con cita")

    def test_orphan_cita_or_related_professional_becomes_safe_permission_denial(self):
        tipo = self.TIPOS[0]
        session = self.sesiones[self.key(tipo)]
        cita = self.citas[self.key(tipo)]
        with connection.constraint_checks_disabled():
            self.CONFIG[tipo][7].objects.filter(pk=session.pk).update(cita_id=9223372036854775000)
            try:
                response, _ = self.read()
                self.assertEqual(response.status_code, 403)
            finally:
                self.CONFIG[tipo][7].objects.filter(pk=session.pk).update(cita_id=cita.pk)
            CitaCliente.objects.filter(pk=cita.pk).update(profesional_salud_id=9223372036854775000)
            try:
                response, _ = self.read()
                self.assertEqual(response.status_code, 403)
            finally:
                CitaCliente.objects.filter(pk=cita.pk).update(profesional_salud_id=cita.profesional_salud_id)

    def test_all_program_appointments_checked_before_phase_or_board_prefetch(self):
        tipo = self.TIPOS[1]
        program = self.programas[self.key(tipo)]
        session = self.CONFIG[tipo][7].objects.create(programa=program, empresa=self.empresa, paciente_id=program.paciente_id, fase=2, numero_sesion=4)
        with connection.constraint_checks_disabled():
            self.CONFIG[tipo][7].objects.filter(pk=session.pk).update(cita_id=9223372036854775000)
            try:
                response, queries = self.read(self.url(tipo) + "&fase=1")
                self.assertEqual(response.status_code, 403)
                # No prefetch of full appointment records after the EXISTS
                # guard, even though the bad row is outside the shown phase.
                self.assertFalse(any('"crm_citacliente"."fecha_hora"' in row["sql"] for row in queries))
            finally:
                self.CONFIG[tipo][7].objects.filter(pk=session.pk).update(cita_id=None)

    def test_finalized_link_is_consultation_and_never_unlinks_or_unlocks_session(self):
        for tipo in self.TIPOS:
            response, _ = self.read(self.url(tipo, case="locked"))
            self.assertEqual(response.status_code, 200)
            record = self.linked_record(response)
            self.assertEqual(record["appointment_url"], self.expected_url(tipo, case="locked"))
            self.assertTrue(record["bloqueada"])
            self.assertEqual(record["url"], "")

    def test_nonlinked_direct_session_links_and_web_template_remain_original(self):
        tipo = self.TIPOS[0]
        session = self.sesiones[self.key(tipo)]
        self.CONFIG[tipo][7].objects.filter(pk=session.pk).update(cita=None)
        response, _ = self.read()
        record = next(item for item in response.context["mobile_control_records"] if item["registro"].pk == session.pk)
        self.assertFalse(record["ligada_cita"])
        self.assertEqual(record["appointment_url"], "")
        self.assertTrue(record["url"])
        self.assertTrue(record["campos"])
        web, _ = self.read(self.url(mobile=False))
        self.assertEqual(web.status_code, 200)
        self.assertTemplateUsed(web, "clinica/historia_camara_hiperbarica.html")
        self.assertTemplateNotUsed(web, "clinica/mobile_control_board.html")
