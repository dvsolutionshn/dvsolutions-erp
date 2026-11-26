"""Consulta sintética de citas por nombre sin guardados ni transportes."""

from datetime import datetime, timedelta, timezone as datetime_timezone
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.db import connection
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from clinica.models import Paciente, ProfesionalSalud, ServicioClinico
from core.models import Empresa, EmpresaModulo, Modulo, RolSistema, Usuario, UsuarioEmpresaPermiso
from facturacion.models import Cliente

from .mobile_appointment_search import construir_contexto_busqueda_citas_app
from .models import CitaCliente


class MobileAppointmentSearchTests(TestCase):
    NOW = datetime(2026, 11, 23, 18, tzinfo=datetime_timezone.utc)
    SLUGS = ("hospital_mia", "medical_spa", "luque_aestetic", "serviciosmedicos")

    @classmethod
    def setUpTestData(cls):
        cls.empresas = {}
        agenda, _ = Modulo.objects.get_or_create(codigo="agenda_citas", defaults={"nombre": "Agenda búsqueda QA"})
        clinica, _ = Modulo.objects.get_or_create(codigo="clinica_medica", defaults={"nombre": "Clínica búsqueda QA"})
        cls.pacientes = {}
        for index, slug in enumerate(cls.SLUGS, 1):
            company = Empresa.objects.create(nombre=f"Búsqueda QA {slug}", slug=slug, rtn=f"SEARCH-QA-{index}", tipo_solucion="clinica", estado_licencia="activa")
            cls.empresas[slug] = company
            for module in (agenda, clinica):
                EmpresaModulo.objects.create(empresa=company, modulo=module, activo=True)
            cls.pacientes[slug] = Paciente.objects.create(
                empresa=company, nombre="Ana María Luna", expediente_codigo=f"QA-{index}",
                identidad=f"IDENTIDAD-PRIVADA-{index}", telefono="PRIVADO-TELEFONO",
            )
        cls.hospital = cls.empresas["hospital_mia"]
        cls.rol = RolSistema.objects.create(nombre="Agenda búsqueda ordinaria", codigo="mobile-search-ordinary", activo=True,
            es_rol_clinico=True, usa_permisos_clinicos_granulares=True, usa_permisos_clinicos_v2=True,
            usa_permisos_acciones_v3=True, puede_ver_calendario=True, puede_ver_pacientes=True)
        cls.recepcion = RolSistema.objects.create(nombre="Agenda búsqueda Recepción", codigo="mobile-search-reception", activo=True,
            es_rol_clinico=True, usa_permisos_clinicos_granulares=True, usa_permisos_clinicos_v2=True,
            usa_permisos_acciones_v3=True, puede_ver_calendario=True, puede_ver_pacientes=True,
            puede_crear_citas=True, solo_tarjetas_pacientes=True)
        cls.usuario = Usuario.objects.create_user(username="mobile-search-qa", empresa=cls.hospital, rol_sistema=cls.rol)
        cls.usuario.empresas_acceso.add(*cls.empresas.values())
        cls.luis = ProfesionalSalud.objects.create(empresa=cls.hospital, nombre="Dr. Luis Gónzalez QA")
        cls.candy = ProfesionalSalud.objects.create(empresa=cls.hospital, nombre="Dra. Candy QA")
        cls.otro_luis = ProfesionalSalud.objects.create(empresa=cls.hospital, nombre="Dr. Luis Pérez QA")
        cls.servicio = ServicioClinico.objects.create(empresa=cls.hospital, nombre="Consulta reservada QA")
        cls.citas = []
        for delta in (-70, 70):
            cls.citas.append(CitaCliente.objects.create(empresa=cls.hospital, paciente=cls.pacientes["hospital_mia"],
                profesional_salud=cls.luis, servicio_clinico=cls.servicio, titulo="Agenda QA",
                fecha_hora=cls.NOW + timedelta(days=delta), observacion="NOTA PRIVADA QA", cirugia_detalle="CIRUGÍA PRIVADA QA"))
        cls.ajena = Empresa.objects.create(nombre="Empresa ajena QA", slug="search_outside", rtn="SEARCH-EXT", tipo_solucion="clinica", estado_licencia="activa")
        paciente_ajeno = Paciente.objects.create(empresa=cls.ajena, nombre="Ana María Luna", expediente_codigo="QA-EXT")
        cls.cita_ajena = CitaCliente.objects.create(empresa=cls.ajena, paciente=paciente_ajeno, titulo="NO DEBE SALIR", fecha_hora=cls.NOW)

    def setUp(self):
        clock = patch("django.utils.timezone.now", return_value=self.NOW)
        clock.start()
        self.addCleanup(clock.stop)

    def snapshot(self):
        return tuple(list(model.objects.order_by("pk").values()) for model in (
            CitaCliente, Paciente, Usuario, UsuarioEmpresaPermiso, RolSistema, Empresa, EmpresaModulo,
        ))

    def search(self, slug="hospital_mia", *, origin=None, user=None, method="get", details=True, readonly=False, **params):
        company = self.empresas[slug]
        actor = user if user is not None else Usuario.objects.get(pk=self.usuario.pk)
        if origin is None:
            from core.clinical_permissions import pacientes_solo_tarjetas
            origin = company if pacientes_solo_tarjetas(actor, company) else self.hospital
        request = getattr(RequestFactory(), method)(f"/{slug}/dashboard/citas/app/", params)
        request.user = actor
        before = self.snapshot()
        with CaptureQueriesContext(connection) as queries:
            context = construir_contexto_busqueda_citas_app(request, company, origin, es_clinica=True,
                agenda_solo_consulta=readonly, agenda_ver_detalles_clinicos=details)
        self.last_search_queries = [row["sql"] for row in queries]
        self.assertFalse(any(row["sql"].lstrip().split(" ", 1)[0].upper() in {"INSERT", "UPDATE", "DELETE", "REPLACE"} for row in queries))
        self.assertEqual(before, self.snapshot())
        return context

    def ids(self, context):
        return [item["id"] for item in context["citas_busqueda_page"].object_list]

    def test_four_companies_search_all_dates_and_multiple_name_terms_only(self):
        for slug in self.SLUGS:
            with self.subTest(company=slug):
                context = self.search(slug, citas_q="  ANA   Luna  ", fecha="1999-01-01", vista="dia")
                self.assertTrue(context["citas_busqueda_disponible"])
                self.assertEqual(context["citas_busqueda_q"], "ANA Luna")
                self.assertEqual(set(self.ids(context)), {cita.pk for cita in self.citas})
                self.assertNotIn(self.cita_ajena.pk, self.ids(context))
                self.assertEqual(self.search(slug, citas_q="IDENTIDAD-PRIVADA")["citas_busqueda_total"], 0)

    def test_future_and_past_use_current_instant_not_selected_day_or_midnight(self):
        before = CitaCliente.objects.create(empresa=self.hospital, paciente=self.pacientes["hospital_mia"],
            titulo="Una hora antes", fecha_hora=self.NOW - timedelta(hours=1))
        exact = CitaCliente.objects.create(empresa=self.hospital, paciente=self.pacientes["hospital_mia"],
            titulo="Instante actual", fecha_hora=self.NOW)
        past = self.search(citas_q="Ana", citas_periodo="anteriores", fecha="2030-01-01")
        future = self.search(citas_q="Ana", citas_periodo="futuras", fecha="1990-01-01")
        self.assertEqual(self.ids(past), [before.pk, self.citas[0].pk])
        self.assertEqual(self.ids(future), [exact.pk, self.citas[1].pk])
        self.assertTrue(all(item["es_futura"] for item in future["citas_busqueda_page"]))
        self.assertTrue(all(not item["es_futura"] for item in past["citas_busqueda_page"]))

    def test_mirror_keeps_original_display_professional_precedence_before_pagination(self):
        fallback = CitaCliente.objects.create(empresa=self.hospital, paciente=self.pacientes["hospital_mia"],
            responsable="Luis Gonzales QA", titulo="Responsable sin profesional", fecha_hora=self.NOW)
        excluded = []
        for number in range(35):
            excluded.append(CitaCliente.objects.create(empresa=self.hospital, paciente=self.pacientes["hospital_mia"],
                profesional_salud=self.candy if number % 2 else self.otro_luis, responsable="Luis Gonzales QA",
                titulo="Profesional no autorizado en espejo", fecha_hora=self.NOW + timedelta(days=number+1)))
        context = self.search("serviciosmedicos", citas_q="Ana")
        self.assertEqual(context["citas_busqueda_total"], 3)
        self.assertEqual(set(self.ids(context)), {fallback.pk, *(cita.pk for cita in self.citas)})
        self.assertFalse(set(self.ids(context)) & {cita.pk for cita in excluded})

    def test_reception_searches_own_company_not_hospital_mirror(self):
        Usuario.objects.filter(pk=self.usuario.pk).update(rol_sistema=self.recepcion)
        for slug, company in self.empresas.items():
            own = CitaCliente.objects.create(empresa=company, paciente=self.pacientes[slug], titulo="Agenda local QA", fecha_hora=self.NOW)
            context = self.search(slug, citas_q="Ana", details=False)
            self.assertIn(own.pk, self.ids(context))
            if slug != "hospital_mia":
                self.assertEqual(self.ids(context), [own.pk])
                self.assertFalse(self.search(slug, origin=self.hospital, citas_q="Ana")["citas_busqueda_disponible"])

    def test_luis_mirror_pages_the_filtered_queryset_in_sql(self):
        for _ in range(35):
            CitaCliente.objects.create(empresa=self.hospital, paciente=self.pacientes["hospital_mia"],
                profesional_salud=self.luis, titulo="Página Luis QA", fecha_hora=self.NOW)
        context = self.search("serviciosmedicos", citas_q="Ana")
        self.assertEqual(context["citas_busqueda_total"], 37)
        self.assertEqual(len(context["citas_busqueda_page"]), 30)
        row_queries = [sql for sql in self.last_search_queries if 'SELECT "crm_citacliente"."id"' in sql]
        self.assertEqual(len(row_queries), 1)
        self.assertIn("LIMIT 30", row_queries[0])

    def test_readonly_and_reception_dto_exposes_no_private_fields(self):
        for readonly, details in ((True, True), (False, False)):
            context = self.search(citas_q="Ana", readonly=readonly, details=details)
            for row in context["citas_busqueda_page"]:
                self.assertEqual(set(row), {"id", "nombre", "fecha", "estado", "estado_label", "servicio", "profesional", "es_futura", "agenda_url", "virtual"})
                self.assertIsInstance(row["virtual"], bool)  # Modality is operational, never a private clinical field.
                self.assertEqual(row["servicio"], "")
                self.assertEqual(row["profesional"], "")
                self.assertNotIn("PRIVAD", str(row))
                query = parse_qs(urlsplit(row["agenda_url"]).query)
                self.assertEqual(query["vista"], ["dia"])
                self.assertEqual(query["app"], ["agenda"])
                self.assertEqual(urlsplit(row["agenda_url"]).fragment, "agenda-app")

    def test_display_patient_name_wins_over_invisible_client_and_legacy_client_fallback(self):
        invisible = Cliente.objects.create(empresa=self.hospital, nombre="Nombre Oculto QA", rtn="0801199900011")
        linked = CitaCliente.objects.create(empresa=self.hospital, paciente=self.pacientes["hospital_mia"], cliente=invisible,
            titulo="Ambos enlaces", fecha_hora=self.NOW)
        fallback = CitaCliente.objects.create(empresa=self.hospital, cliente=invisible, titulo="Cliente histórico", fecha_hora=self.NOW)
        context = self.search(citas_q="Nombre Oculto")
        self.assertEqual(self.ids(context), [fallback.pk])
        self.assertNotIn(linked.pk, self.ids(context))

    def test_stable_pagination_bounds_and_safe_presentation_links(self):
        ids = [CitaCliente.objects.create(empresa=self.hospital, paciente=self.pacientes["hospital_mia"],
            titulo="Misma hora QA", fecha_hora=self.NOW).pk for _ in range(35)]
        first = self.search(citas_q="Ana", citas_pagina="1", vista="semana", fecha="2026-11-23", editar="INJECT", citas_periodo="todas")
        second = self.search(citas_q="Ana", citas_pagina="2")
        self.assertEqual(len(first["citas_busqueda_page"]), 30)
        self.assertEqual(first["citas_busqueda_total"], 37)
        self.assertEqual(self.ids(first)[1:], list(reversed(ids))[:29])
        self.assertFalse(set(self.ids(first)) & set(self.ids(second)))
        self.assertEqual(set(self.ids(first)) | set(self.ids(second)), {*ids, *(c.pk for c in self.citas)})
        self.assertNotIn("editar", first["citas_busqueda_next_url"])
        self.assertEqual(parse_qs(urlsplit(first["citas_busqueda_next_url"]).query)["fecha"], ["2026-11-23"])
        self.assertEqual(self.search(citas_q="Ana", citas_pagina="99999")["citas_busqueda_page"].number, 2)
        self.assertEqual(self.search(citas_q="Ana", citas_pagina="bad")["citas_busqueda_page"].number, 1)

    def test_empty_invalid_period_and_query_length_are_bounded(self):
        empty = self.search(citas_q="  \n  ", citas_periodo="invalid")
        self.assertFalse(empty["citas_busqueda_activa"])
        self.assertIsNone(empty["citas_busqueda_page"])
        self.assertEqual(empty["citas_busqueda_periodo"], "todas")
        self.assertEqual(len(self.search(citas_q="a" * 250)["citas_busqueda_q"]), 100)

    def test_inconsistent_cross_company_relations_do_not_leak_other_company_names(self):
        foreign_patient = self.cita_ajena.paciente
        corrupt_patient = CitaCliente.objects.create(empresa=self.hospital, paciente=self.pacientes["hospital_mia"], titulo="Relación sintética inconsistente", fecha_hora=self.NOW)
        CitaCliente.objects.filter(pk=corrupt_patient.pk).update(paciente=foreign_patient)
        foreign_doctor = ProfesionalSalud.objects.create(empresa=self.ajena, nombre="Luis Gonzales de empresa ajena")
        corrupt_doctor = CitaCliente.objects.create(empresa=self.hospital, paciente=self.pacientes["hospital_mia"], titulo="Profesional sintético ajeno", fecha_hora=self.NOW)
        CitaCliente.objects.filter(pk=corrupt_doctor.pk).update(profesional_salud=foreign_doctor)
        for slug in self.SLUGS:
            with self.subTest(company=slug):
                self.assertEqual(set(self.ids(self.search(slug, citas_q="Ana"))), {cita.pk for cita in self.citas})

    def test_defensive_gates_for_nonmember_anonymous_license_and_wrong_origin(self):
        self.assertFalse(self.search(user=AnonymousUser(), citas_q="Ana")["citas_busqueda_disponible"])
        self.assertFalse(self.search(origin=self.ajena, citas_q="Ana")["citas_busqueda_disponible"])
        self.assertFalse(self.search(method="post", citas_q="Ana")["citas_busqueda_disponible"])
        self.usuario.empresas_acceso.remove(self.empresas["medical_spa"])
        self.assertFalse(self.search("medical_spa", citas_q="Ana")["citas_busqueda_disponible"])
        RolSistema.objects.filter(pk=self.rol.pk).update(puede_ver_calendario=False)
        self.assertFalse(self.search(citas_q="Ana")["citas_busqueda_disponible"])
        RolSistema.objects.filter(pk=self.rol.pk).update(puede_ver_calendario=True)
        self.hospital.estado_licencia = "suspendida"
        self.hospital.save(update_fields=["estado_licencia"])
        self.assertFalse(self.search(citas_q="Ana")["citas_busqueda_disponible"])

    def test_integrated_readonly_and_reception_gets_keep_results_basic_without_mutating_records(self):
        for role in (self.rol, self.recepcion):
            Usuario.objects.filter(pk=self.usuario.pk).update(rol_sistema=role)
            self.usuario.refresh_from_db()
            self.client.force_login(self.usuario)
            for slug in self.SLUGS:
                with self.subTest(role=role.codigo, company=slug):
                    before = self.snapshot()
                    response = self.client.get(reverse("agenda_mobile", args=[slug]), {"citas_q": "Ana", "app": "agenda"})
                    self.assertEqual(response.status_code, 200)
                    self.assertTrue(response.context["citas_busqueda_disponible"])
                    self.assertContains(response, 'aria-label="Buscar citas por nombre"')
                    html = response.content.decode()
                    start = html.index('<section class="mobile-appointment-results"')
                    results = html[start:html.index("</section>", start)]
                    for private in ("NOTA PRIVADA QA", "CIRUGÍA PRIVADA QA", "IDENTIDAD-PRIVADA", "PRIVADO-TELEFONO", self.servicio.nombre, self.luis.nombre):
                        self.assertNotIn(private, results)
                    self.assertEqual(before, self.snapshot())

    def test_desktop_calendar_ignores_mobile_search_parameters(self):
        self.client.force_login(self.usuario)
        url = reverse("agenda_citas", args=[self.hospital.slug])
        original = self.client.get(url, {"vista": "dia", "fecha": "2026-11-23"})
        with_search = self.client.get(url, {"vista": "dia", "fecha": "2026-11-23", "citas_q": "Ana", "citas_periodo": "anteriores"})
        self.assertEqual(original.status_code, 200)
        self.assertEqual(with_search.status_code, 200)
        self.assertEqual([c.pk for c in original.context["citas"]], [c.pk for c in with_search.context["citas"]])
        self.assertNotIn("citas_busqueda_disponible", with_search.context)
        self.assertNotContains(with_search, 'name="citas_q"')

    def test_search_preserves_calendar_filters_for_return_without_limiting_history(self):
        self.client.force_login(self.usuario)
        filtros = {
            "vista": "mes", "fecha": "1990-01-01", "servicio": str(self.servicio.pk),
            "profesional": str(self.luis.pk), "estado": "cancelada",
            "paciente_historial": str(self.pacientes["hospital_mia"].pk),
        }
        response = self.client.get(reverse("agenda_mobile", args=[self.hospital.slug]),
            {**filtros, "app": "agenda", "citas_q": "Ana", "citas_periodo": "todas"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["citas_busqueda_total"], 2)
        regreso = parse_qs(urlsplit(response.context["citas_busqueda_calendario_url"]).query)
        for campo, valor in filtros.items():
            self.assertEqual(regreso[campo], [valor])
        self.assertEqual(regreso["app"], ["agenda"])
        self.assertNotIn("citas_q", regreso)
