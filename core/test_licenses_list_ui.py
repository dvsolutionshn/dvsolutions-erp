"""License presentation preserves resolved states, payment limits and links."""

from datetime import timedelta
from html.parser import HTMLParser

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from .models import Empresa, PagoLicenciaEmpresa, PlanComercial, Usuario


class _LicensesListMarkup(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.companies = {}
        self.company = None
        self.payments = []
        self.forms = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "tr" and "data-license-company" in attrs:
            self.company = {"attrs": attrs, "links": []}
            self.companies[attrs["data-license-company"]] = self.company
        elif tag == "tr" and "data-license-payment" in attrs:
            self.payments.append(attrs["data-license-payment"])
        elif tag == "a" and self.company is not None:
            self.company["links"].append(attrs.get("href"))
        elif tag == "form":
            self.forms.append(attrs)

    def handle_endtag(self, tag):
        if tag == "tr":
            self.company = None


class LicensesListPresentationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = Usuario.objects.create_superuser(username="licenses-list-admin")
        cls.member = Usuario.objects.create_user(username="licenses-list-member")
        cls.plan = PlanComercial.objects.create(nombre="Plan licencias UI", codigo="licenses-list-plan")
        cls.companies = []
        today = timezone.localdate()
        for index, state, active, days in (
            (1, "activa", True, 30),
            (2, "activa", False, 30),
            (3, "suspendida", True, 30),
            (4, "activa", True, -1),
            (5, "prueba", True, 5),
        ):
            cls.companies.append(Empresa.objects.create(
                nombre=f"Empresa licencias UI {index}", slug=f"licenses-list-{index}",
                rtn=f"LICENSES-LIST-{index}", plan_comercial=cls.plan,
                estado_licencia=state, activa=active,
                fecha_vencimiento_plan=today + timedelta(days=days),
            ))
        cls.payments = PagoLicenciaEmpresa.objects.bulk_create([
            PagoLicenciaEmpresa(
                empresa=cls.companies[0], plan_comercial=cls.plan,
                fecha_pago=today - timedelta(days=index), cantidad_meses=1,
                monto="123.45", referencia=f"LICENSES-UI-{index}",
            )
            for index in range(31)
        ])

    def setUp(self):
        self.client.force_login(self.admin)
        self.url = reverse("superadmin_licencias")

    def test_listado_conserva_estados_resueltos_30_pagos_y_acciones_sin_escribir(self):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual([
            query["sql"] for query in queries
            if query["sql"].lstrip().split(maxsplit=1)[0].upper()
            in {"INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "ALTER", "DROP"}
        ], [])
        self.assertEqual(response.context["resumen"], {
            "operativas": 2, "prueba": 1, "suspendidas": 1, "vencidas": 1,
        })
        markup = _LicensesListMarkup(response.content.decode())
        self.assertEqual(markup.forms, [])
        self.assertEqual(list(markup.companies), [str(company.pk) for company in self.companies])
        for company, state in zip(self.companies, ("activa", "activa", "suspendida", "vencida", "prueba")):
            with self.subTest(company=company.slug):
                row = markup.companies[str(company.pk)]
                self.assertEqual(row["attrs"]["data-list-state"], state)
                self.assertEqual(set(row["links"]), {
                    reverse("superadmin_empresa_detail", args=[company.pk]),
                    reverse("superadmin_empresa_registrar_pago_licencia", args=[company.pk]),
                })
        self.assertEqual(markup.payments, [str(payment.pk) for payment in self.payments[:30]])
        self.assertEqual([payment.pk for payment in response.context["pagos"]], [payment.pk for payment in self.payments[:30]])
        self.assertContains(response, "Empresa inactiva")
        self.assertContains(response, "Licencias Comerciales")

    def test_listado_sigue_restringido_a_superadministradores(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertRedirects(response, f"{reverse('superadmin_login')}?next={self.url}")
        self.client.force_login(self.member)
        response = self.client.get(self.url)
        self.assertRedirects(response, reverse("superadmin_login"))
        self.assertNotContains(response, self.companies[0].nombre, status_code=302)
