"""The commercial catalog must keep its counts, links and private access."""

from html.parser import HTMLParser

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from .models import Empresa, Modulo, PlanComercial, PlanModulo, Usuario


class _PlansDirectoryMarkup(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.cards = {}
        self.card = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "article" and "data-plan-card" in attrs:
            self.card = {"attributes": attrs, "links": []}
            self.cards[attrs["data-plan-id"]] = self.card
        elif tag == "a" and self.card is not None:
            self.card["links"].append(attrs.get("href"))

    def handle_endtag(self, tag):
        if tag == "article":
            self.card = None


class PlansDirectoryPresentationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = Usuario.objects.create_superuser(username="plans-directory-admin")
        cls.member = Usuario.objects.create_user(username="plans-directory-member")
        cls.active_plan = PlanComercial.objects.create(
            nombre="Z Plan catálogo UI", codigo="plans-directory-active", precio_mensual="345.67",
            descripcion="Plan con servicios configurados para la prueba del directorio.", activo=True,
        )
        cls.inactive_plan = PlanComercial.objects.create(
            nombre="A Plan catálogo UI", codigo="plans-directory-inactive", precio_mensual="0", activo=False,
        )
        cls.commercial = Modulo.objects.create(
            nombre="A Módulo comercial del directorio", codigo="plans-dir-commercial", es_comercial=True,
        )
        cls.internal = Modulo.objects.create(
            nombre="B Módulo interno del directorio", codigo="plans-dir-internal", es_comercial=False,
        )
        cls.inactive_module = Modulo.objects.create(
            nombre="Módulo inactivo de prueba UI", codigo="plans-dir-disabled", es_comercial=True,
        )
        PlanModulo.objects.create(plan=cls.active_plan, modulo=cls.commercial, activo=True)
        PlanModulo.objects.create(plan=cls.active_plan, modulo=cls.internal, activo=True)
        PlanModulo.objects.create(plan=cls.active_plan, modulo=cls.inactive_module, activo=False)
        PlanModulo.objects.create(plan=cls.inactive_plan, modulo=cls.commercial, activo=True)
        for index, plan, active in (
            (1, cls.active_plan, True), (2, cls.active_plan, False), (3, cls.inactive_plan, True),
        ):
            Empresa.objects.create(
                nombre=f"Empresa catálogo UI {index}", slug=f"plans-directory-company-{index}",
                rtn=f"PLAN-DIR-{index}", plan_comercial=plan, activa=active,
            )

    def setUp(self):
        self.client.force_login(self.admin)
        self.url = reverse("superadmin_planes")

    def test_listado_conserva_metricas_orden_modulos_y_enlaces_sin_modificar_datos(self):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual([
            query["sql"] for query in queries
            if query["sql"].lstrip().split(maxsplit=1)[0].upper()
            in {"INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "ALTER", "DROP"}
        ], [])
        plans = list(response.context["planes"])
        self.assertEqual([plan.pk for plan in plans], [self.inactive_plan.pk, self.active_plan.pk])
        inactive, active = plans
        # Two companies and two modules must not multiply either annotation.
        self.assertEqual((active.modulos_count, active.empresas_count), (2, 2))
        self.assertEqual((inactive.modulos_count, inactive.empresas_count), (1, 1))
        self.assertEqual(response.context["resumen_planes"], {
            "total": 2, "activos": 1, "inactivos": 1, "empresas": 3,
        })
        self.assertEqual([relation.modulo_id for relation in active.modulos_catalogo], [
            self.commercial.pk, self.internal.pk,
        ])
        self.assertTrue(all(relation.activo for relation in active.modulos_catalogo))
        self.assertContains(response, self.internal.nombre)
        self.assertNotContains(response, self.inactive_module.nombre)
        self.assertContains(response, reverse("superadmin_plan_create"))

        cards = _PlansDirectoryMarkup(response.content.decode()).cards
        self.assertEqual(list(cards), [str(self.inactive_plan.pk), str(self.active_plan.pk)])
        for plan, state in ((self.inactive_plan, "inactivo"), (self.active_plan, "activo")):
            with self.subTest(plan=plan.codigo):
                card = cards[str(plan.pk)]
                self.assertEqual(card["attributes"]["data-plan-state"], state)
                self.assertEqual(set(card["links"]), {reverse("superadmin_plan_edit", args=[plan.pk])})
                self.assertContains(response, plan.nombre)
                self.assertContains(response, plan.codigo)

    def test_listado_sigue_restringido_a_superadministradores(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertRedirects(response, f"{reverse('superadmin_login')}?next={self.url}")
        self.client.force_login(self.member)
        response = self.client.get(self.url)
        self.assertRedirects(response, reverse("superadmin_login"))
        self.assertNotContains(response, self.active_plan.nombre, status_code=302)
