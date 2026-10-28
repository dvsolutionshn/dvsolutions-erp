"""The commercial plan editor keeps its submitted fields and module contracts."""

from decimal import Decimal
from html.parser import HTMLParser

from django.test import TestCase
from django.urls import reverse

from .models import Empresa, Modulo, PlanComercial, PlanModulo, Usuario


class _PlanFormMarkup(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.forms = []
        self.form = None
        self.textarea = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form" and "data-commercial-plan-form" in attrs:
            self.form = {"attributes": attrs, "fields": {}}
            self.forms.append(self.form)
        elif self.form is not None and tag in {"input", "textarea", "select"} and attrs.get("name"):
            self.form["fields"].setdefault(attrs["name"], []).append(attrs)
            if tag == "textarea":
                self.textarea = attrs
                attrs["text"] = ""

    def handle_data(self, data):
        if self.textarea is not None:
            self.textarea["text"] += data

    def handle_endtag(self, tag):
        if tag == "form":
            self.form = None
        elif tag == "textarea":
            self.textarea = None


class CommercialPlanFormPresentationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = Usuario.objects.create_superuser(username="plan-form-ui-admin")
        cls.commercial_a = Modulo.objects.create(nombre="A Módulo comercial UI", codigo="plan-form-a", es_comercial=True)
        cls.commercial_b = Modulo.objects.create(nombre="B Módulo comercial UI", codigo="plan-form-b", es_comercial=True)
        cls.commercial_c = Modulo.objects.create(nombre="C Módulo comercial UI", codigo="plan-form-c", es_comercial=True)
        cls.internal = Modulo.objects.create(nombre="Módulo interno no editable UI", codigo="plan-form-internal", es_comercial=False)
        cls.plan = PlanComercial.objects.create(
            nombre="Plan de prueba del editor", codigo="plan-form-ui", descripcion="Descripción existente UI.",
            precio_mensual="345.67", activo=False,
        )
        cls.other = PlanComercial.objects.create(nombre="Otro plan UI", codigo="plan-form-other")
        for module, active in (
            (cls.commercial_a, True), (cls.commercial_b, True),
            (cls.commercial_c, False), (cls.internal, True),
        ):
            PlanModulo.objects.create(plan=cls.plan, modulo=module, activo=active)
        cls.company = Empresa.objects.create(
            nombre="Empresa vinculada al editor UI", slug="plan-form-company", rtn="PLAN-FORM-COMPANY",
            plan_comercial=cls.plan, activa=False, estado_licencia="suspendida",
        )

    def setUp(self):
        self.client.force_login(self.admin)
        self.edit_url = reverse("superadmin_plan_edit", args=[self.plan.pk])

    def _form(self, response):
        self.assertEqual(response.status_code, 200)
        markup = _PlanFormMarkup(response.content.decode())
        self.assertEqual(len(markup.forms), 1)
        form = markup.forms[0]
        fields = form["fields"]
        self.assertEqual(set(fields), {"csrfmiddlewaretoken", "nombre", "codigo", "descripcion", "precio_mensual", "activo", "modulos"})
        self.assertEqual(form["attributes"].get("method", "get").lower(), "post")
        commercial_ids = list(Modulo.objects.filter(es_comercial=True).order_by("nombre").values_list("pk", flat=True))
        for name, inputs in fields.items():
            with self.subTest(field=name):
                self.assertTrue(all("disabled" not in item for item in inputs))
                if name == "modulos":
                    self.assertEqual([item.get("value") for item in inputs], [str(module_id) for module_id in commercial_ids])
                    self.assertEqual([item.get("id") for item in inputs], [f"id_modulos_{index}" for index in range(len(commercial_ids))])
                else:
                    self.assertEqual(len(inputs), 1)
                    if name != "csrfmiddlewaretoken":
                        self.assertEqual(inputs[0].get("id"), f"id_{name}")
        self.assertTrue(fields["csrfmiddlewaretoken"][0].get("value"))
        self.assertNotContains(response, self.internal.nombre)
        return fields

    def test_get_crear_y_editar_conservan_campos_y_selecciones_comerciales(self):
        for editing, url in ((False, reverse("superadmin_plan_create")), (True, self.edit_url)):
            with self.subTest(editing=editing):
                fields = self._form(self.client.get(url))
                selected = [item["value"] for item in fields["modulos"] if "checked" in item]
                if editing:
                    self.assertEqual(fields["nombre"][0].get("value"), self.plan.nombre)
                    self.assertEqual(fields["codigo"][0].get("value"), self.plan.codigo)
                    self.assertEqual(Decimal(fields["precio_mensual"][0]["value"]), Decimal(str(self.plan.precio_mensual)))
                    self.assertNotIn("checked", fields["activo"][0])
                    self.assertEqual(selected, [str(self.commercial_a.pk), str(self.commercial_b.pk)])
                else:
                    self.assertEqual(Decimal(fields["precio_mensual"][0]["value"]), Decimal("0"))
                    self.assertIn("checked", fields["activo"][0])
                    self.assertEqual(selected, [])

    def test_post_invalido_conserva_datos_y_modulos_sin_guardar_cambios(self):
        before = PlanComercial.objects.filter(pk=self.plan.pk).values().get()
        modules_before = list(PlanModulo.objects.filter(plan=self.plan).order_by("pk").values())
        company_before = Empresa.objects.filter(pk=self.company.pk).values().get()
        data = {
            "nombre": "", "codigo": self.other.codigo, "descripcion": "Conservar descripción del plan.",
            "precio_mensual": "231.27", "activo": "on", "modulos": [str(self.commercial_c.pk), "2147483647"],
        }

        response = self.client.post(self.edit_url, data)

        fields = self._form(response)
        self.assertEqual(set(response.context["form"].errors), {"nombre", "codigo", "modulos"})
        self.assertEqual(fields["codigo"][0].get("value"), data["codigo"])
        self.assertEqual(fields["descripcion"][0]["text"].strip(), data["descripcion"])
        self.assertEqual(fields["precio_mensual"][0].get("value"), data["precio_mensual"])
        self.assertIn("checked", fields["activo"][0])
        self.assertEqual([item["value"] for item in fields["modulos"] if "checked" in item], [str(self.commercial_c.pk)])
        self.assertEqual(PlanComercial.objects.filter(pk=self.plan.pk).values().get(), before)
        self.assertEqual(list(PlanModulo.objects.filter(plan=self.plan).order_by("pk").values()), modules_before)
        self.assertEqual(Empresa.objects.filter(pk=self.company.pk).values().get(), company_before)

    def test_edicion_valida_actualiza_comerciales_y_conserva_modulo_interno_y_empresa(self):
        company_before = Empresa.objects.filter(pk=self.company.pk).values().get()
        response = self.client.post(self.edit_url, {
            "nombre": "Plan actualizado UI", "codigo": self.plan.codigo,
            "descripcion": "Nueva descripción UI.", "precio_mensual": "456.78", "activo": "on",
            "modulos": [str(self.commercial_c.pk)],
        })

        self.assertRedirects(response, reverse("superadmin_planes"))
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.nombre, "Plan actualizado UI")
        self.assertEqual(self.plan.precio_mensual, Decimal("456.78"))
        self.assertTrue(self.plan.activo)
        self.assertEqual(dict(PlanModulo.objects.filter(plan=self.plan).values_list("modulo_id", "activo")), {
            self.commercial_a.pk: False, self.commercial_b.pk: False,
            self.commercial_c.pk: True, self.internal.pk: True,
        })
        self.assertEqual(Empresa.objects.filter(pk=self.company.pk).values().get(), company_before)
