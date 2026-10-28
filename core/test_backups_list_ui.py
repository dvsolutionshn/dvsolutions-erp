"""Backup presentation preserves private actions, bounded history and previews."""

from datetime import timedelta
from html.parser import HTMLParser

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from .models import Empresa, RespaldoEmpresa, TokenRespaldoEmpresa, Usuario


class _BackupsListMarkup(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.forms = []
        self.links = []
        self.form = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.form = {"attributes": attrs, "inputs": []}
            self.forms.append(self.form)
        elif tag == "input" and self.form is not None:
            self.form["inputs"].append(attrs)
        elif tag == "a":
            self.links.append(attrs.get("href"))

    def handle_endtag(self, tag):
        if tag == "form":
            self.form = None


class BackupsListPresentationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = Usuario.objects.create_superuser(username="backups-list-ui-admin")
        cls.member = Usuario.objects.create_user(username="backups-list-ui-member")
        cls.company = Empresa.objects.create(nombre="A Respaldos UI", slug="backups-list-a", rtn="BACKUPS-LIST-A")
        cls.inactive = Empresa.objects.create(
            nombre="B Respaldos UI", slug="backups-list-b", rtn="BACKUPS-LIST-B", activa=False,
        )
        cls.older_backups = RespaldoEmpresa.objects.bulk_create([
            RespaldoEmpresa(
                empresa=cls.company, generado_por=cls.admin, estado="exitoso",
                registros_incluidos=index, archivos_incluidos=2, tamano_bytes=1024, sha256="a" * 64,
            )
            for index in range(101)
        ])
        cls.latest_backup = RespaldoEmpresa.objects.create(
            empresa=cls.inactive, generado_por=cls.admin, estado="fallido", sha256="b" * 64,
        )
        cls.older_tokens = TokenRespaldoEmpresa.objects.bulk_create([
            TokenRespaldoEmpresa(
                empresa=cls.company, creado_por=cls.admin, token_hash=f"{index + 1:064x}",
                token_preview=f"CLIENT-{index:03}", fecha_expiracion=timezone.now() + timedelta(hours=24),
            )
            for index in range(101)
        ])
        cls.latest_token = TokenRespaldoEmpresa.objects.create(
            empresa=cls.inactive, creado_por=cls.admin, token_hash="f" * 64,
            token_preview="CLIENT-PREVIEW-UI", referencia_pago="BACKUPS-UI-AUTH",
            fecha_expiracion=timezone.now() + timedelta(hours=24),
        )

    def setUp(self):
        self.client.force_login(self.admin)
        self.url = reverse("superadmin_respaldos")

    def test_get_conserva_descargas_post_csrf_historial_acotado_y_previews_sin_escribir(self):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual([
            query["sql"] for query in queries
            if query["sql"].lstrip().split(maxsplit=1)[0].upper()
            in {"INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "ALTER", "DROP"}
        ], [])
        self.assertEqual(response.context["resumen"], {
            "total": 102, "exitosos": 101, "fallidos": 1, "empresas_respaldadas": 1,
        })
        self.assertEqual([company.pk for company in response.context["empresas"]], [self.company.pk, self.inactive.pk])
        backups = list(response.context["respaldos"])
        tokens = list(response.context["tokens_respaldo"])
        self.assertEqual(len(backups), 100)
        self.assertEqual(len(tokens), 100)
        self.assertEqual(backups[0].pk, self.latest_backup.pk)
        self.assertEqual(tokens[0].pk, self.latest_token.pk)
        self.assertNotIn(self.older_backups[0].pk, {backup.pk for backup in backups})
        self.assertNotIn(self.older_tokens[0].pk, {token.pk for token in tokens})

        markup = _BackupsListMarkup(response.content.decode())
        self.assertEqual({form["attributes"].get("action") for form in markup.forms}, {
            reverse("superadmin_empresa_generar_respaldo", args=[self.company.pk]),
            reverse("superadmin_empresa_generar_respaldo", args=[self.inactive.pk]),
        })
        self.assertEqual(len(markup.forms), 2)
        for form in markup.forms:
            self.assertEqual(form["attributes"].get("method", "get").lower(), "post")
            csrf = [item for item in form["inputs"] if item.get("name") == "csrfmiddlewaretoken"]
            self.assertEqual(len(csrf), 1)
            self.assertEqual(csrf[0].get("type"), "hidden")
            self.assertTrue(csrf[0].get("value"))
        for company in (self.company, self.inactive):
            self.assertIn(reverse("superadmin_empresa_detail", args=[company.pk]), markup.links)
        self.assertContains(response, f'title="{self.latest_backup.sha256}"')
        self.assertContains(response, self.latest_backup.sha256[:12])
        self.assertContains(response, self.latest_token.token_preview)
        self.assertContains(response, self.latest_token.referencia_pago)
        self.assertNotContains(response, self.latest_token.token_hash)

    def test_listado_sigue_restringido_a_superadministradores(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertRedirects(response, f"{reverse('superadmin_login')}?next={self.url}")
        self.client.force_login(self.member)
        response = self.client.get(self.url)
        self.assertRedirects(response, reverse("superadmin_login"))
        self.assertNotContains(response, self.latest_token.token_preview, status_code=302)
