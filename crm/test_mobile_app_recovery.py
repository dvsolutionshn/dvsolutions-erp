"""APP startup regression checks with synthetic accounts and no live transport.

The original Home may create its original configurations. Worker requests are
measured strictly read-only; Home checks preserve business/account rows, not a
claim that the pre-existing workflow performs zero initialization writes.
"""

from datetime import datetime, timezone as datetime_timezone
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from unittest.mock import patch

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from clinica.models import Paciente
from core.models import Empresa, EmpresaModulo, Modulo, RolSistema, Usuario, UsuarioEmpresaPermiso
from facturacion.models import CompraInventario, Factura, InventarioProducto, PagoFactura

from .models import CitaCliente


class _RecoveryMarkup(HTMLParser):
    def __init__(self, content):
        super().__init__(convert_charrefs=True)
        self.nodes = []
        self.scripts = []
        self._script = None
        self.feed(content.decode() if isinstance(content, bytes) else content)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.nodes.append((tag, attrs))
        if tag == "script":
            self._script = {"attrs": attrs, "code": ""}
            self.scripts.append(self._script)

    def handle_data(self, value):
        if self._script is not None:
            self._script["code"] += value

    def handle_endtag(self, tag):
        if tag == "script":
            self._script = None

    def by_id(self, value):
        return [(tag, attrs) for tag, attrs in self.nodes if attrs.get("id") == value]


class _RecoveryFixtures(TestCase):
    SLUGS = ("hospital_mia", "medical_spa", "luque_aestetic", "serviciosmedicos")
    NOW = datetime(2026, 11, 25, 18, tzinfo=datetime_timezone.utc)

    @classmethod
    def setUpTestData(cls):
        cls.empresas = {}
        modules = [Modulo.objects.get_or_create(codigo=code, defaults={"nombre": code})[0]
                   for code in ("agenda_citas", "clinica_medica", "facturacion")]
        for index, slug in enumerate((*cls.SLUGS, "recovery_outside"), 1):
            company = Empresa.objects.create(nombre=f"Recuperación QA {index}", slug=slug,
                rtn=f"RECOVERY-QA-{index}", tipo_solucion="clinica", estado_licencia="activa")
            cls.empresas[slug] = company
            for module in modules:
                EmpresaModulo.objects.create(empresa=company, modulo=module, activo=True)
        cls.hospital = cls.empresas["hospital_mia"]
        cls.rol = RolSistema.objects.create(nombre="Consulta QA recuperación", codigo="recovery-read-qa", activo=True,
            es_rol_clinico=True, usa_permisos_clinicos_granulares=True,
            usa_permisos_clinicos_v2=True, usa_permisos_acciones_v3=True,
            puede_ver_calendario=True, puede_ver_pacientes=True)
        cls.usuario = Usuario.objects.create_user(username="PRIVATE_RECOVERY_USER_QA", empresa=cls.hospital,
            es_administrador_empresa=True, rol_sistema=cls.rol)
        cls.usuario.empresas_acceso.add(*cls.empresas.values())
        cls.paciente = Paciente.objects.create(empresa=cls.hospital, nombre="PATIENT_RECOVERY_PRIVATE_QA",
            expediente_codigo="RECOVERY-PRIVATE-001", telefono="PRIVATE-TELEPHONE-QA")
        cls.cita = CitaCliente.objects.create(empresa=cls.hospital, paciente=cls.paciente,
            titulo="PRIVATE-APPOINTMENT-QA", observacion="PRIVATE-CLINICAL-NOTE-QA", fecha_hora=cls.NOW)

    def setUp(self):
        clock = patch("django.utils.timezone.now", return_value=self.NOW)
        clock.start()
        self.addCleanup(clock.stop)
        self.client.force_login(self.usuario)

    @staticmethod
    def business_snapshot():
        return tuple(list(model.objects.order_by("pk").values()) for model in (
            Usuario, RolSistema, UsuarioEmpresaPermiso, Paciente, CitaCliente,
            Factura, PagoFactura, CompraInventario, InventarioProducto,
        ))

    def worker(self, slug, **params):
        before = self.business_snapshot()
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse("agenda_mobile_service_worker", args=[slug]), params)
        self.assertFalse(any(re.match(r"\s*(INSERT|UPDATE|DELETE|REPLACE|CREATE|ALTER|DROP)\b", row["sql"], re.I) for row in queries))
        self.assertEqual(before, self.business_snapshot())
        return response


class MobileAppWorkerRecoveryTests(_RecoveryFixtures):
    def test_four_clinic_workers_keep_own_scope_no_cache_and_no_private_payload(self):
        for slug in self.SLUGS:
            with self.subTest(company=slug):
                response = self.worker(slug, citas_q="PRIVATE-PATIENT", token="SECRET-QUERY", fecha="2030-01-01")
                self.assertEqual(response.status_code, 200)
                self.assertIn("application/javascript", response["Content-Type"])
                self.assertEqual(response["Service-Worker-Allowed"], reverse("dashboard", args=[slug]))
                self.assertIn("no-store", response["Cache-Control"])
                code = response.content.decode()
                self.assertRegex(code, r'addEventListener\(["\']fetch["\']')
                self.assertIn(reverse("agenda_mobile", args=[slug]), code)
                for value in ("PATIENT_RECOVERY_PRIVATE_QA", "PRIVATE_RECOVERY_USER_QA", "PRIVATE-TELEPHONE-QA", "PRIVATE-CLINICAL-NOTE-QA", "SECRET-QUERY", "PRIVATE-PATIENT"):
                    self.assertNotIn(value, code)
                self.assertNotRegex(code, r"\bcaches\s*[.\[]")
                self.assertNotIn("localStorage", code)
                self.assertNotIn("sessionStorage", code)
                self.assertIn('"notificationclick"', code)
                self.assertIn('"SHOW_APPOINTMENT"', code)

    def test_company_outside_the_four_keeps_original_notification_only_worker(self):
        response = self.worker("recovery_outside")
        self.assertEqual(response.status_code, 200)
        self.assertNotRegex(response.content.decode(), r'addEventListener\(["\']fetch["\']')
        self.assertContains(response, "notificationclick")
        self.assertContains(response, "SHOW_APPOINTMENT")

    def test_existing_anonymous_membership_and_license_denials_are_not_bypassed(self):
        services = self.empresas["serviciosmedicos"]
        self.client.logout()
        anonymous = self.worker(services.slug)
        self.assertEqual(anonymous.status_code, 302)
        self.assertIn(reverse("empresa_login", args=[services.slug]), anonymous["Location"])
        outsider = Usuario.objects.create_user(username="recovery-outsider-qa", empresa=self.empresas["recovery_outside"], es_administrador_empresa=True)
        self.client.force_login(outsider)
        foreign = self.worker(services.slug)
        self.assertEqual(foreign.status_code, 302)
        self.assertEqual(foreign["Location"], reverse("empresa_login", args=[services.slug]))
        self.client.force_login(self.usuario)
        Empresa.objects.filter(pk=services.pk).update(estado_licencia="suspendida")
        suspended = self.worker(services.slug)
        self.assertEqual(suspended.status_code, 302)
        self.assertEqual(suspended["Location"], reverse("empresa_login", args=[services.slug]))

    def test_generated_workers_network_faults_in_closed_node_vm(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node is required for the closed service-worker harness")
        workers = [{"code": self.worker(slug).content.decode(), "home": reverse("agenda_mobile", args=[slug])}
                   for slug in self.SLUGS]
        environment = {key: os.environ[key] for key in ("PATH", "SystemRoot", "WINDIR", "TEMP", "TMP") if key in os.environ}
        result = subprocess.run([node, str(Path(__file__).with_suffix(".js"))], input=json.dumps({"workers": workers}),
            text=True, capture_output=True, timeout=25, env=environment)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(summary["workers"], 4)
        self.assertGreaterEqual(summary["checks"], 120)
        self.assertEqual((summary["realFetches"], summary["cacheWrites"], summary["posts"]), (0, 0, 0))


class MobileAppRecoveryMarkupTests(_RecoveryFixtures):
    def test_rendered_bootstrap_and_existing_preferences_survive_closed_vm_faults(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node is required for the closed startup harness")
        pages = []
        for slug in self.SLUGS:
            home = reverse("agenda_mobile", args=[slug])
            before = self.business_snapshot()
            response = self.client.get(home)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(before, self.business_snapshot())
            markup = _RecoveryMarkup(response.content)
            bootstrap = [script["code"] for script in markup.scripts
                         if script["attrs"].get("id") == "mobileAppRecoveryBootstrap"]
            main = [script["code"] for script in markup.scripts
                    if "const quickRecentIds=" in script["code"] and "const patientFavoriteKey=" in script["code"]]
            self.assertEqual(len(bootstrap), 1)
            self.assertEqual(len(main), 1)
            pages.append({"slug": slug, "home": home, "bootstrap": bootstrap[0], "main": main[0]})
        environment = {key: os.environ[key] for key in ("PATH", "SystemRoot", "WINDIR", "TEMP", "TMP") if key in os.environ}
        harness = Path(__file__).with_name("test_mobile_app_bootstrap.js")
        result = subprocess.run([node, str(harness)], input=json.dumps({"pages": pages}),
            text=True, capture_output=True, timeout=25, env=environment)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(summary["pages"], 4)
        self.assertGreaterEqual(summary["checks"], 60)
        self.assertEqual((summary["realTransports"], summary["draftMutations"], summary["automaticNavigations"]), (0, 0, 0))

    def test_four_clinics_keep_visible_original_home_and_hidden_manual_recovery(self):
        for slug in self.SLUGS:
            with self.subTest(company=slug):
                before = self.business_snapshot()
                response = self.client.get(reverse("agenda_mobile", args=[slug]))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(before, self.business_snapshot())
                markup = _RecoveryMarkup(response.content)
                self.assertEqual(len(markup.by_id("mobileAppRecovery")), 1)
                self.assertIn("hidden", markup.by_id("mobileAppRecovery")[0][1])
                home = markup.by_id("inicio")[0][1]
                self.assertIn("active", home["class"].split())
                self.assertNotIn("hidden", home)
                self.assertTrue(any(tag == "header" and "hero" in attrs.get("class", "").split() for tag, attrs in markup.nodes))
                self.assertTrue(any(tag == "nav" and "bottom" in attrs.get("class", "").split() for tag, attrs in markup.nodes))
                self.assertEqual(markup.by_id("mobileAppRecoveryHome")[0][1]["href"], reverse("agenda_mobile", args=[slug]))
                self.assertEqual(markup.by_id("mobileAppRecoveryDismiss")[0][1]["type"], "button")
                html = response.content.decode()
                self.assertLess(html.index('id="mobileAppRecoveryBootstrap"'), html.index('id="mobileAgendaNavigation"'))
                self.assertIn("datos sin guardar", html)

    def test_outside_company_and_desktop_have_no_recovery_controls(self):
        outside = self.client.get(reverse("agenda_mobile", args=["recovery_outside"]))
        self.assertEqual(outside.status_code, 200)
        self.assertNotContains(outside, 'id="mobileAppRecovery"')
        self.assertNotContains(outside, 'id="mobileAppRecoveryBootstrap"')
        desktop = self.client.get(reverse("dashboard", args=[self.hospital.slug]))
        self.assertEqual(desktop.status_code, 200)
        self.assertNotContains(desktop, 'id="mobileAppRecovery"')

    def test_recovery_does_not_grant_calendar_writes_or_private_sections_to_readonly_role(self):
        Usuario.objects.filter(pk=self.usuario.pk).update(es_administrador_empresa=False)
        self.usuario.refresh_from_db()
        self.client.force_login(self.usuario)
        response = self.client.get(reverse("agenda_mobile", args=[self.hospital.slug]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["agenda_solo_consulta"])
        self.assertTrue(response.context["datos_paciente_limitados"])
        self.assertTrue(all(not value for value in response.context["agenda_permisos_accion"].values()))
        self.assertContains(response, 'id="mobileAppRecovery"')
        markup = _RecoveryMarkup(response.content)
        self.assertFalse(any("data-open-editor" in attrs for _, attrs in markup.nodes))
        for value in (self.cita.observacion, self.paciente.telefono):
            self.assertNotContains(response, value)
