"""The global audit layout preserves server filters and protected event details."""

from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from .models import Empresa, RegistroAuditoria, Usuario


class _AuditListMarkup(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.forms = []
        self.links = []
        self.form = None
        self.select = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.form = {"attributes": attrs, "fields": {}, "selected": {}}
            self.forms.append(self.form)
        elif tag == "a":
            self.links.append(attrs.get("href", ""))
        elif self.form is not None and tag in {"input", "select"} and attrs.get("name"):
            self.form["fields"].setdefault(attrs["name"], []).append(attrs)
            if tag == "select":
                self.select = attrs["name"]
                self.form["selected"][self.select] = []
        elif self.form is not None and tag == "option" and self.select and "selected" in attrs:
            self.form["selected"][self.select].append(attrs.get("value"))

    def handle_endtag(self, tag):
        if tag == "form":
            self.form = None
        elif tag == "select":
            self.select = None


class AuditListPresentationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = Empresa.objects.create(nombre="Empresa bitácora UI", slug="audit-list-ui", rtn="AUDIT-LIST-UI")
        cls.foreign = Empresa.objects.create(nombre="Otra empresa bitácora UI", slug="audit-list-other", rtn="AUDIT-LIST-OTHER")
        cls.admin = Usuario.objects.create_superuser(username="audit-list-admin")
        cls.member = Usuario.objects.create_user(username="audit-list-member", empresa=cls.company)
        cls.query = "UI audit & +"
        cls.module = "Facturación & Cajas"
        cls.events = RegistroAuditoria.objects.bulk_create([
            RegistroAuditoria(
                empresa=cls.company, usuario=cls.admin, accion="modificar", modulo=cls.module,
                app_label="facturacion", modelo="factura", objeto_id=str(index),
                objeto_representacion=f"{cls.query} {index}", motivo="Revisión solicitada UI",
                cambios={"nombre": {"anterior": "<script>old-ui</script>", "nuevo": "nuevo & revisado"}},
                direccion_ip="192.0.2.25", ruta="/ruta-de-demostracion/", metodo_http="POST",
            )
            for index in range(76)
        ])
        cls.foreign_event = RegistroAuditoria.objects.create(
            empresa=cls.foreign, usuario=cls.admin, accion="modificar", modulo=cls.module,
            app_label="facturacion", modelo="factura", objeto_id="foreign",
            objeto_representacion=f"{cls.query} registro ajeno excluido",
        )
        cls.other_user_event = RegistroAuditoria.objects.create(
            empresa=cls.company, usuario=cls.member, accion="modificar", modulo=cls.module,
            app_label="facturacion", modelo="factura", objeto_id="other-user",
            objeto_representacion=f"{cls.query} otro usuario excluido",
        )

    def setUp(self):
        self.client.force_login(self.admin)
        self.url = reverse("superadmin_auditoria")

    def test_filtros_get_paginacion_y_detalle_conservan_su_contrato_sin_escribir(self):
        today = timezone.localdate().isoformat()
        filters = {
            "q": self.query, "empresa": str(self.company.pk), "accion": "modificar",
            "modulo": self.module, "usuario": str(self.admin.pk), "desde": today, "hasta": today,
        }
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.url, filters)

        self.assertEqual(response.status_code, 200)
        self.assertEqual([
            query["sql"] for query in queries
            if query["sql"].lstrip().split(maxsplit=1)[0].upper()
            in {"INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "ALTER", "DROP"}
        ], [])
        self.assertEqual(response.context["total_registros"], 76)
        self.assertEqual(len(response.context["registros"]), 75)
        self.assertEqual(response.context["registros"].paginator.num_pages, 2)
        markup = _AuditListMarkup(response.content.decode())
        self.assertEqual(len(markup.forms), 1)
        form = markup.forms[0]
        self.assertEqual(form["attributes"].get("method", "get").lower(), "get")
        self.assertEqual(set(form["fields"]), set(filters))
        ids = {"q": "audit-q", "empresa": "audit-company", "accion": "audit-action", "modulo": "audit-module", "usuario": "audit-user", "desde": "audit-from", "hasta": "audit-to"}
        for name, fields in form["fields"].items():
            with self.subTest(field=name):
                self.assertEqual(len(fields), 1)
                self.assertEqual(fields[0].get("id"), ids[name])
                self.assertNotIn("disabled", fields[0])
                if name in form["selected"]:
                    self.assertEqual(form["selected"][name], [filters[name]])
                else:
                    self.assertEqual(fields[0].get("value"), filters[name])
        next_link = next(link for link in markup.links if parse_qs(urlsplit(link).query).get("page") == ["2"])
        query = parse_qs(urlsplit(next_link).query)
        self.assertEqual({name: query[name][0] for name in filters}, filters)
        page_one_ids = {event.pk for event in response.context["registros"]}
        second = self.client.get(self.url + next_link)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(len(second.context["registros"]), 1)
        self.assertEqual(page_one_ids | {event.pk for event in second.context["registros"]}, {event.pk for event in self.events})
        self.assertContains(response, "&lt;script&gt;old-ui&lt;/script&gt;")
        self.assertNotContains(response, "<script>old-ui</script>")
        self.assertContains(response, "nuevo &amp; revisado")
        self.assertContains(response, 'class="audit-details"')
        self.assertNotContains(response, self.foreign_event.objeto_representacion)
        self.assertNotContains(response, self.other_user_event.objeto_representacion)
        first = response.context["registros"][0]
        self.assertContains(response, reverse("auditoria_objeto", args=[self.company.slug, first.app_label, first.modelo, first.objeto_id]))

    def test_bitacora_global_sigue_restringida_a_superadministradores(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertRedirects(response, f"{reverse('superadmin_login')}?next={self.url}")
        self.client.force_login(self.member)
        response = self.client.get(self.url)
        self.assertRedirects(response, reverse("superadmin_login"))
        self.assertNotContains(response, self.query, status_code=302)
