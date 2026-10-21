"""Presentation checks: a new mobile screen must not request an unauthorized manifest."""

from types import SimpleNamespace

from django.template.loader import render_to_string
from django.test import SimpleTestCase
from django.urls import reverse


class MobileAppMetadataTests(SimpleTestCase):
    def render_base(self, **context):
        return render_to_string("facturacion/mobile_invoice_base.html", {
            "empresa": SimpleNamespace(slug="hospital_mia", nombre="Clínica de prueba", logo=None),
            **context,
        })

    def test_new_screen_without_calendar_does_not_request_protected_manifest(self):
        html = self.render_base(app_home_disponible=False)
        self.assertNotIn('rel="manifest"', html)
        self.assertNotIn(reverse("agenda_mobile_manifest", args=["hospital_mia"]), html)
        self.assertIn('name="apple-mobile-web-app-capable" content="yes"', html)

    def test_authorized_screen_keeps_the_existing_manifest(self):
        html = self.render_base(app_home_disponible=True)
        self.assertIn('rel="manifest"', html)
        self.assertIn(reverse("agenda_mobile_manifest", args=["hospital_mia"]), html)

    def test_existing_invoice_context_without_new_flag_keeps_manifest_unchanged(self):
        html = self.render_base()
        self.assertIn('rel="manifest"', html)
        self.assertIn(reverse("agenda_mobile_manifest", args=["hospital_mia"]), html)
