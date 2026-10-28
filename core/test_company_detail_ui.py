"""Presentation changes must preserve the company's private action contracts."""

from datetime import timedelta
from html.parser import HTMLParser

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from .models import (
    Empresa, EmpresaModulo, Modulo, PagoLicenciaEmpresa, PlanComercial,
    RespaldoEmpresa, RolSistema, TokenRespaldoEmpresa, Usuario, UsuarioEmpresaPermiso,
)


class _CompanyDetailMarkup(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.forms = []
        self.links = []
        self.users = {}
        self.form = None
        self.select = None
        self.user = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "tr" and "data-detail-user" in attrs:
            self.user = {"links": [], "text": []}
            self.users[attrs["data-detail-user"]] = self.user
        if tag == "a":
            self.links.append(attrs.get("href"))
            if self.user is not None:
                self.user["links"].append(attrs.get("href"))
        if tag == "form":
            self.form = {"attributes": attrs, "fields": {}, "options": {}}
            self.forms.append(self.form)
        elif self.form is not None and tag in {"input", "select"} and attrs.get("name"):
            self.form["fields"].setdefault(attrs["name"], []).append(attrs)
            if tag == "select":
                self.select = attrs["name"]
                self.form["options"][self.select] = []
        elif self.form is not None and tag == "option" and self.select:
            self.form["options"][self.select].append(attrs.get("value"))

    def handle_data(self, data):
        if self.user is not None:
            self.user["text"].append(data)

    def handle_endtag(self, tag):
        if tag == "tr":
            self.user = None
        elif tag == "select":
            self.select = None
        elif tag == "form":
            self.form = None


class CompanyDetailPresentationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = Empresa.objects.create(
            nombre="Empresa ficha UI", slug="company-detail-ui", rtn="DETAIL-UI",
            activa=True, estado_licencia="activa",
            fecha_vencimiento_plan=timezone.localdate() + timedelta(days=30),
        )
        cls.foreign = Empresa.objects.create(
            nombre="Empresa ajena privada UI", slug="company-detail-foreign", rtn="DETAIL-FOREIGN",
        )
        cls.admin = Usuario.objects.create_superuser(username="company-detail-admin", empresa=cls.company)
        cls.role = RolSistema.objects.create(nombre="Rol global ficha UI", codigo="company-detail-global")
        cls.local_role = RolSistema.objects.create(nombre="Rol local ficha UI", codigo="company-detail-local")
        cls.member = Usuario.objects.create_user(
            username="company-detail-member", empresa=cls.company, rol_sistema=cls.role,
        )
        UsuarioEmpresaPermiso.objects.create(usuario=cls.member, empresa=cls.company, rol_sistema=cls.local_role)
        cls.additional = Usuario.objects.create_user(username="company-detail-additional", empresa=cls.foreign)
        cls.additional.empresas_acceso.add(cls.company)
        cls.retired = Usuario.objects.create_user(
            username="company-detail-retired", empresa=cls.company, is_active=False, retirado_control=True,
        )
        cls.outsider = Usuario.objects.create_user(username="company-detail-outsider", empresa=cls.foreign)
        cls.module = Modulo.objects.create(nombre="Módulo ficha UI", codigo="company-detail-ui", es_comercial=True)
        EmpresaModulo.objects.create(empresa=cls.company, modulo=cls.module, activo=True)
        cls.foreign_module = Modulo.objects.create(
            nombre="Módulo privado ajeno UI", codigo="company-detail-private", es_comercial=False,
        )
        EmpresaModulo.objects.create(empresa=cls.foreign, modulo=cls.foreign_module, activo=True)
        cls.plan = PlanComercial.objects.create(nombre="Plan ficha UI", codigo="company-detail-plan")
        for empresa, reference in ((cls.company, "DETAIL-PAYMENT-OWN"), (cls.foreign, "DETAIL-PAYMENT-FOREIGN")):
            PagoLicenciaEmpresa.objects.create(empresa=empresa, plan_comercial=cls.plan, referencia=reference)
            RespaldoEmpresa.objects.create(empresa=empresa, generado_por=cls.admin, registros_incluidos=17)
            TokenRespaldoEmpresa.objects.create(
                empresa=empresa, creado_por=cls.admin, token_hash=("a" if empresa == cls.company else "b") * 64,
                token_preview="DETAIL-OWN-PREVIEW" if empresa == cls.company else "DETAIL-FOREIGN-PREVIEW",
                referencia_pago=reference, fecha_expiracion=timezone.now() + timedelta(hours=24),
            )

    def setUp(self):
        self.client.force_login(self.admin)
        self.url = reverse("superadmin_empresa_detail", args=[self.company.pk])

    def test_get_conserva_aislamiento_y_no_modifica_datos(self):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual([
            query["sql"] for query in queries
            if query["sql"].lstrip().split(maxsplit=1)[0].upper()
            in {"INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "ALTER", "DROP"}
        ], [])
        self.assertEqual({user.pk for user in response.context["usuarios_empresa"]}, {
            self.admin.pk, self.member.pk, self.additional.pk, self.retired.pk,
        })
        self.assertEqual([item.pk for item in response.context["modulos_empresa"]], [self.module.pk])
        self.assertTrue(all(payment.empresa_id == self.company.pk for payment in response.context["pagos_licencia"]))
        self.assertTrue(all(backup.empresa_id == self.company.pk for backup in response.context["respaldos_empresa"]))
        self.assertTrue(all(token.empresa_id == self.company.pk for token in response.context["tokens_respaldo"]))
        self.assertContains(response, "DETAIL-OWN-PREVIEW")
        self.assertContains(response, "DETAIL-PAYMENT-OWN")
        for private in (self.foreign.nombre, self.outsider.username, self.foreign_module.nombre,
                        "DETAIL-FOREIGN-PREVIEW", "DETAIL-PAYMENT-FOREIGN", "a" * 64, "b" * 64):
            self.assertNotContains(response, private)

    def test_acciones_post_csrf_token_y_restriccion_de_la_cuenta_actual(self):
        for license_state, action in (
            ("activa", "superadmin_empresa_suspender_licencia"),
            ("suspendida", "superadmin_empresa_activar_licencia"),
        ):
            with self.subTest(license=license_state):
                Empresa.objects.filter(pk=self.company.pk).update(estado_licencia=license_state)
                response = self.client.get(self.url)
                self.assertEqual(response.status_code, 200)
                markup = _CompanyDetailMarkup(response.content.decode())
                expected = {
                    reverse(action, args=[self.company.pk]),
                    reverse("superadmin_empresa_generar_respaldo", args=[self.company.pk]),
                    reverse("superadmin_empresa_generar_token_respaldo", args=[self.company.pk]),
                }
                self.assertEqual({form["attributes"].get("action") for form in markup.forms}, expected)
                for form in markup.forms:
                    self.assertEqual(form["attributes"].get("method", "get").lower(), "post")
                    csrf = form["fields"].get("csrfmiddlewaretoken", [])
                    self.assertEqual(len(csrf), 1)
                    self.assertEqual(csrf[0].get("type"), "hidden")
                    self.assertTrue(csrf[0].get("value"))
                token_form = next(form for form in markup.forms if form["attributes"]["action"] == reverse(
                    "superadmin_empresa_generar_token_respaldo", args=[self.company.pk],
                ))
                self.assertEqual(set(token_form["fields"]), {"csrfmiddlewaretoken", "horas_vigencia", "referencia_pago"})
                self.assertEqual(token_form["fields"]["horas_vigencia"][0].get("id"), "horas_vigencia")
                self.assertEqual(token_form["options"]["horas_vigencia"], ["24", "48", "72", "168"])
                self.assertEqual(token_form["fields"]["referencia_pago"][0].get("maxlength"), "160")
                self.assertIn(reverse("superadmin_empresa_edit", args=[self.company.pk]), markup.links)
                self.assertIn(reverse("superadmin_empresa_registrar_pago_licencia", args=[self.company.pk]), markup.links)
                self.assertNotIn(reverse("superadmin_usuario_delete", args=[self.admin.pk]), markup.users[str(self.admin.pk)]["links"])
                self.assertIn(reverse("superadmin_usuario_delete", args=[self.member.pk]), markup.users[str(self.member.pk)]["links"])
                self.assertContains(response, "Rol global")
                self.assertIn(self.role.nombre, " ".join(markup.users[str(self.member.pk)]["text"]))

    def test_ficha_sigue_restringida_a_superadministradores(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertRedirects(response, f"{reverse('superadmin_login')}?next={self.url}")
        self.client.force_login(self.member)
        response = self.client.get(self.url)
        self.assertRedirects(response, reverse("superadmin_login"))
        self.assertNotContains(response, "DETAIL-OWN-PREVIEW", status_code=302)
