"""The module catalog keeps direct assignments distinct from plan access."""

from html.parser import HTMLParser

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from .models import Empresa, EmpresaModulo, Modulo, PlanComercial, PlanModulo, Usuario


class _ModulesDirectoryMarkup(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.cards = {}
        self.forms = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "article" and "data-module-card" in attrs:
            self.cards[attrs["data-module-id"]] = attrs
        elif tag == "form":
            self.forms.append(attrs)


class ModulesDirectoryPresentationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # Isolate this catalog from modules supplied by data migrations. The
        # TestCase transaction restores those flags after the test class.
        Modulo.objects.update(es_comercial=False)
        cls.admin = Usuario.objects.create_superuser(username="modules-directory-admin")
        cls.member = Usuario.objects.create_user(username="modules-directory-member")
        cls.unassigned = Modulo.objects.create(
            nombre="A Módulo sin asignaciones UI", codigo="modules-dir-unassigned", es_comercial=True,
        )
        cls.plan_only = Modulo.objects.create(
            nombre="B Módulo incluido por plan UI", codigo="modules-dir-plan-only", es_comercial=True,
        )
        cls.both = Modulo.objects.create(
            nombre="C Módulo con planes y activaciones UI", codigo="modules-dir-both", es_comercial=True,
        )
        cls.internal = Modulo.objects.create(
            nombre="Módulo interno fuera del catálogo UI", codigo="modules-dir-internal", es_comercial=False,
        )
        cls.active_plan = PlanComercial.objects.create(nombre="Plan activo módulos UI", codigo="modules-dir-active")
        cls.inactive_plan = PlanComercial.objects.create(
            nombre="Plan inactivo módulos UI", codigo="modules-dir-inactive", activo=False,
        )
        cls.other_plan = PlanComercial.objects.create(nombre="Plan no vinculado UI", codigo="modules-dir-other")
        cls.companies = []
        for index, active in ((1, True), (2, False), (3, True)):
            cls.companies.append(Empresa.objects.create(
                nombre=f"Empresa módulos UI {index}", slug=f"modules-dir-company-{index}",
                rtn=f"MODULES-DIR-{index}", activa=active, plan_comercial=cls.active_plan,
            ))
        for plan in (cls.active_plan, cls.inactive_plan):
            PlanModulo.objects.create(plan=plan, modulo=cls.both, activo=True)
        PlanModulo.objects.create(plan=cls.other_plan, modulo=cls.both, activo=False)
        PlanModulo.objects.create(plan=cls.active_plan, modulo=cls.plan_only, activo=True)
        PlanModulo.objects.create(plan=cls.other_plan, modulo=cls.unassigned, activo=False)
        PlanModulo.objects.create(plan=cls.active_plan, modulo=cls.internal, activo=True)
        for company in cls.companies[:2]:
            EmpresaModulo.objects.create(empresa=company, modulo=cls.both, activo=True)
        EmpresaModulo.objects.create(empresa=cls.companies[2], modulo=cls.both, activo=False)
        EmpresaModulo.objects.create(empresa=cls.companies[0], modulo=cls.plan_only, activo=False)
        EmpresaModulo.objects.create(empresa=cls.companies[0], modulo=cls.unassigned, activo=False)
        EmpresaModulo.objects.create(empresa=cls.companies[0], modulo=cls.internal, activo=True)

    def setUp(self):
        self.client.force_login(self.admin)
        self.url = reverse("superadmin_modulos")

    def test_catalogo_conserva_metricas_directas_orden_y_filtro_comercial_sin_escribir(self):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual([
            query["sql"] for query in queries
            if query["sql"].lstrip().split(maxsplit=1)[0].upper()
            in {"INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "ALTER", "DROP"}
        ], [])
        items = response.context["modulos"]
        self.assertEqual([item["modulo"].pk for item in items], [
            self.unassigned.pk, self.plan_only.pk, self.both.pk,
        ])
        counts = {item["modulo"].pk: (item["planes_count"], item["empresas_count"]) for item in items}
        # Two plans joined with two direct companies must still produce 2/2.
        # Inactive plans and companies count when their relation is active.
        self.assertEqual(counts[self.both.pk], (2, 2))
        self.assertEqual(counts[self.plan_only.pk], (1, 0))
        self.assertEqual(counts[self.unassigned.pk], (0, 0))
        # All three companies inherit this module from their plan, but that
        # access must not be counted as a direct EmpresaModulo activation.
        self.assertTrue(all(company.tiene_modulo_activo(self.plan_only.codigo) for company in self.companies))
        self.assertEqual(response.context["resumen_modulos"], {
            "total": 3, "con_planes": 2, "con_ajustes": 1, "sin_asignaciones": 1,
        })
        self.assertNotContains(response, self.internal.nombre)
        self.assertNotContains(response, self.internal.codigo)
        markup = _ModulesDirectoryMarkup(response.content.decode())
        self.assertEqual(list(markup.cards), [str(self.unassigned.pk), str(self.plan_only.pk), str(self.both.pk)])
        self.assertEqual(markup.forms, [])
        for item in items:
            with self.subTest(module=item["modulo"].codigo):
                attrs = markup.cards[str(item["modulo"].pk)]
                self.assertEqual(attrs["data-module-plans"], str(item["planes_count"]))
                self.assertEqual(attrs["data-module-companies"], str(item["empresas_count"]))
                self.assertContains(response, item["modulo"].nombre)
                self.assertContains(response, item["modulo"].codigo)

    def test_catalogo_sigue_restringido_a_superadministradores(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertRedirects(response, f"{reverse('superadmin_login')}?next={self.url}")
        self.client.force_login(self.member)
        response = self.client.get(self.url)
        self.assertRedirects(response, reverse("superadmin_login"))
        self.assertNotContains(response, self.both.nombre, status_code=302)
