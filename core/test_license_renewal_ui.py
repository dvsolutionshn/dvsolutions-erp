"""The renewal layout must preserve payment fields and private access."""

from datetime import timedelta
from decimal import Decimal
from html.parser import HTMLParser

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from .models import Empresa, PagoLicenciaEmpresa, PlanComercial, Usuario


class _LicenseRenewalMarkup(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.forms = []
        self.form = None
        self.select = None
        self.textarea = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form" and "data-license-renewal" in attrs:
            self.form = {"attributes": attrs, "fields": {}, "selected": {}}
            self.forms.append(self.form)
        elif self.form is not None and tag in {"input", "select", "textarea"} and attrs.get("name"):
            self.form["fields"].setdefault(attrs["name"], []).append(attrs)
            if tag == "select":
                self.select = attrs["name"]
                self.form["selected"][self.select] = []
            elif tag == "textarea":
                self.textarea = attrs
                attrs["text"] = ""
        elif self.form is not None and tag == "option" and self.select and "selected" in attrs:
            self.form["selected"][self.select].append(attrs.get("value"))

    def handle_data(self, data):
        if self.textarea is not None:
            self.textarea["text"] += data

    def handle_endtag(self, tag):
        if tag == "form":
            self.form = None
        elif tag == "select":
            self.select = None
        elif tag == "textarea":
            self.textarea = None


class LicenseRenewalPresentationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = Usuario.objects.create_superuser(username="renewal-ui-admin")
        cls.member = Usuario.objects.create_user(username="renewal-ui-member")
        cls.plan = PlanComercial.objects.create(
            nombre="Plan renovación UI", codigo="renewal-ui-plan", precio_mensual="450.00",
        )
        cls.alternative = PlanComercial.objects.create(
            nombre="Plan alternativo UI", codigo="renewal-ui-alternative", precio_mensual="800.00",
        )
        cls.company = Empresa.objects.create(
            nombre="Empresa renovación UI", slug="renewal-ui-company", rtn="RENEWAL-UI",
            plan_comercial=cls.plan, activa=False, estado_licencia="suspendida",
            fecha_inicio_plan=timezone.localdate() - timedelta(days=30),
            fecha_vencimiento_plan=timezone.localdate() + timedelta(days=10),
        )

    def setUp(self):
        self.client.force_login(self.admin)
        self.url = reverse("superadmin_empresa_registrar_pago_licencia", args=[self.company.pk])

    def _form(self, response):
        self.assertEqual(response.status_code, 200)
        markup = _LicenseRenewalMarkup(response.content.decode())
        self.assertEqual(len(markup.forms), 1)
        form = markup.forms[0]
        self.assertEqual(set(form["fields"]), {
            "csrfmiddlewaretoken", "plan_comercial", "fecha_pago", "initial-fecha_pago", "cantidad_meses",
            "monto", "metodo", "referencia", "observacion",
        })
        for name, fields in form["fields"].items():
            with self.subTest(field=name):
                self.assertEqual(len(fields), 1)
                self.assertNotIn("disabled", fields[0])
                if name == "initial-fecha_pago":
                    self.assertEqual(fields[0].get("type"), "hidden")
                    self.assertEqual(fields[0].get("id"), "initial-id_fecha_pago")
                elif name != "csrfmiddlewaretoken":
                    self.assertEqual(fields[0].get("id"), f"id_{name}")
        self.assertEqual(form["attributes"].get("method", "get").lower(), "post")
        self.assertTrue(form["fields"]["csrfmiddlewaretoken"][0].get("value"))
        return form

    def test_get_conserva_campos_y_defaults_sin_modificar_la_empresa(self):
        before = Empresa.objects.filter(pk=self.company.pk).values().get()
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.url)

        form = self._form(response)
        self.assertEqual([
            query["sql"] for query in queries
            if query["sql"].lstrip().split(maxsplit=1)[0].upper()
            in {"INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "ALTER", "DROP"}
        ], [])
        self.assertEqual(Empresa.objects.filter(pk=self.company.pk).values().get(), before)
        self.assertEqual(PagoLicenciaEmpresa.objects.count(), 0)
        self.assertEqual(form["selected"]["plan_comercial"], [str(self.plan.pk)])
        self.assertEqual(form["selected"]["metodo"], ["transferencia"])
        self.assertEqual(form["fields"]["fecha_pago"][0].get("type"), "date")
        self.assertEqual(form["fields"]["fecha_pago"][0].get("value"), timezone.localdate().isoformat())
        self.assertEqual(form["fields"]["cantidad_meses"][0].get("value"), "1")
        self.assertEqual(Decimal(form["fields"]["monto"][0]["value"]), Decimal("0"))
        self.assertContains(response, self.company.nombre)

    def test_post_invalido_conserva_el_pago_sin_registrarlo_ni_cambiar_la_empresa(self):
        before = Empresa.objects.filter(pk=self.company.pk).values().get()
        payment_date = (timezone.localdate() + timedelta(days=2)).isoformat()
        data = {
            "plan_comercial": str(self.alternative.pk), "fecha_pago": payment_date,
            "cantidad_meses": "0", "monto": "231.27", "metodo": "tarjeta",
            "referencia": "RENEWAL-UI-INVALID", "observacion": "Conservar esta nota del pago.",
        }

        response = self.client.post(self.url, data)

        form = self._form(response)
        self.assertEqual(set(response.context["form"].errors), {"cantidad_meses"})
        self.assertEqual(form["selected"]["plan_comercial"], [str(self.alternative.pk)])
        self.assertEqual(form["selected"]["metodo"], ["tarjeta"])
        for name in ("fecha_pago", "cantidad_meses", "monto", "referencia"):
            self.assertEqual(form["fields"][name][0].get("value"), data[name])
        self.assertEqual(form["fields"]["observacion"][0]["text"].strip(), data["observacion"])
        self.assertEqual(PagoLicenciaEmpresa.objects.count(), 0)
        self.assertEqual(Empresa.objects.filter(pk=self.company.pk).values().get(), before)
        self.assertContains(response, "La cantidad de meses debe ser mayor que cero.")

    def test_renovacion_sigue_restringida_a_superadministradores(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertRedirects(response, f"{reverse('superadmin_login')}?next={self.url}")
        self.client.force_login(self.member)
        response = self.client.get(self.url)
        self.assertRedirects(response, reverse("superadmin_login"))
        response = self.client.post(self.url, {"cantidad_meses": "12", "monto": "1200"})
        self.assertRedirects(response, reverse("superadmin_login"))
        self.assertEqual(PagoLicenciaEmpresa.objects.count(), 0)
