from datetime import timedelta
from html.parser import HTMLParser

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from .models import Empresa, Usuario


class _CompanyDirectoryMarkup(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.cards = {}
        self.card = None
        self.form = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "article" and "data-company-card" in attrs:
            self.card = {"attributes": attrs, "links": [], "forms": []}
            self.cards[attrs["data-company-id"]] = self.card
        elif self.card is not None:
            if tag == "a":
                self.card["links"].append(attrs.get("href"))
            elif tag == "form":
                self.form = {"attributes": attrs, "inputs": []}
                self.card["forms"].append(self.form)
            elif tag == "input" and self.form is not None:
                self.form["inputs"].append(attrs)

    def handle_endtag(self, tag):
        if tag == "form":
            self.form = None
        elif tag == "article":
            self.card = None


class CompanyDirectoryPresentationTests(TestCase):
    def test_listado_no_muta_licencias_y_conserva_acciones_post_con_csrf(self):
        admin = Usuario.objects.create_superuser(username="empresas-directorio-admin")
        suspendida = Empresa.objects.create(
            nombre="A Empresa suspendida", slug="directorio-suspendida", rtn="DIRECTORIO-SUSPENDIDA",
            activa=True, estado_licencia="suspendida", tipo_solucion="erp",
            fecha_vencimiento_plan=timezone.localdate() + timedelta(days=15),
        )
        vencida = Empresa.objects.create(
            nombre="B Empresa inactiva y vencida", slug="directorio-vencida", rtn="DIRECTORIO-VENCIDA",
            activa=False, estado_licencia="activa", tipo_solucion="clinica",
            fecha_vencimiento_plan=timezone.localdate() - timedelta(days=1),
        )
        self.client.force_login(admin)
        antes = list(Empresa.objects.order_by("pk").values())

        with CaptureQueriesContext(connection) as consultas:
            response = self.client.get(reverse("superadmin_empresas"))

        self.assertEqual(response.status_code, 200)
        escrituras = [
            consulta["sql"] for consulta in consultas
            if consulta["sql"].lstrip().split(maxsplit=1)[0].upper()
            in {"INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "ALTER", "DROP"}
        ]
        self.assertEqual(escrituras, [])
        self.assertEqual(list(Empresa.objects.order_by("pk").values()), antes)
        self.assertEqual(response.context["resumen"]["activas"], 1)
        self.assertEqual(response.context["resumen"]["inactivas"], 1)
        self.assertEqual(response.context["resumen"]["vencidas"], 1)

        cards = _CompanyDirectoryMarkup(response.content.decode()).cards
        self.assertEqual(list(cards), [str(suspendida.pk), str(vencida.pk)])
        for empresa, estado, licencia, accion in (
            (suspendida, "activa", "suspendida", "superadmin_empresa_activar_licencia"),
            (vencida, "inactiva", "vencida", "superadmin_empresa_suspender_licencia"),
        ):
            with self.subTest(empresa=empresa.slug):
                card = cards[str(empresa.pk)]
                self.assertEqual(card["attributes"]["data-company-state"], estado)
                self.assertEqual(card["attributes"]["data-company-license"], licencia)
                self.assertEqual(card["attributes"]["data-company-type"], empresa.tipo_solucion)
                self.assertEqual(set(card["links"]), {
                    reverse("superadmin_empresa_detail", args=[empresa.pk]),
                    reverse("superadmin_empresa_edit", args=[empresa.pk]),
                    reverse("superadmin_empresa_registrar_pago_licencia", args=[empresa.pk]),
                })
                self.assertEqual(len(card["forms"]), 1)
                form = card["forms"][0]
                self.assertEqual(form["attributes"].get("action"), reverse(accion, args=[empresa.pk]))
                self.assertEqual(form["attributes"].get("method", "get").lower(), "post")
                csrf = [field for field in form["inputs"] if field.get("name") == "csrfmiddlewaretoken"]
                self.assertEqual(len(csrf), 1)
                self.assertEqual(csrf[0].get("type"), "hidden")
                self.assertTrue(csrf[0].get("value"))
