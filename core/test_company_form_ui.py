"""The company editor must keep its submitted fields while its layout changes."""

from datetime import date
from html.parser import HTMLParser

from django.test import TestCase
from django.urls import reverse

from .forms import EmpresaControlForm
from .models import ConfiguracionAvanzadaEmpresa, Empresa, EmpresaModulo, Modulo, PlanComercial, Usuario


class _CompanyFormMarkup(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.forms = []
        self.form = None
        self.details = {}
        self.links = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "details" and attrs.get("id"):
            self.details[attrs["id"]] = attrs
        if tag == "a":
            self.links.append(attrs.get("href"))
        if tag == "form" and "data-company-form" in attrs:
            self.form = {"attributes": attrs, "fields": {}}
            self.forms.append(self.form)
        elif self.form is not None and tag in {"input", "select", "textarea"} and attrs.get("name"):
            self.form["fields"].setdefault(attrs["name"], []).append(attrs)

    def handle_endtag(self, tag):
        if tag == "form":
            self.form = None


class CompanyFormPresentationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = Usuario.objects.create_superuser(username="company-form-ui-admin")
        cls.plan = PlanComercial.objects.create(nombre="Plan de prueba de interfaz", codigo="company-form-ui-plan")
        cls.facturacion = Modulo.objects.create(nombre="Facturación UI", codigo="facturacion", es_comercial=True)
        cls.contabilidad = Modulo.objects.create(nombre="Contabilidad UI", codigo="company-ui-contabilidad", es_comercial=True)
        cls.rrhh = Modulo.objects.create(nombre="Personal UI", codigo="company-ui-rrhh", es_comercial=True)
        cls.empresa = Empresa.objects.create(
            nombre="Empresa de prueba de interfaz", slug="company-form-ui", rtn="COMPANY-FORM-UI",
            tipo_solucion="clinica", pais="Honduras", activa=True, plan_comercial=cls.plan,
            estado_licencia="activa", fecha_inicio_plan=date(2026, 1, 3),
            fecha_vencimiento_plan=date(2030, 12, 17), logo="logos/company-form-ui.png",
        )
        EmpresaModulo.objects.create(empresa=cls.empresa, modulo=cls.contabilidad, activo=True)
        cls.configuracion = ConfiguracionAvanzadaEmpresa.objects.create(
            empresa=cls.empresa, permite_cai_historico=True,
            permite_plantilla_factura_independiente=True, manuales_pdf_habilitados=True,
        )
        cls.configuracion.modulos_adicionales_visibles_clinica.add(cls.rrhh)

    def setUp(self):
        self.client.force_login(self.admin)

    def _markup(self, response):
        self.assertEqual(response.status_code, 200)
        markup = _CompanyFormMarkup(response.content.decode())
        self.assertEqual(len(markup.forms), 1)
        return markup, markup.forms[0]

    def _assert_fields(self, form, markup_form):
        fields = markup_form["fields"]
        extra = {"csrfmiddlewaretoken", "logo-clear"}
        self.assertEqual(set(fields) - extra, set(form.fields))
        for name, field in form.fields.items():
            with self.subTest(field=name):
                if name in {"tipo_solucion", "modulos_activos", "modulos_adicionales_visibles_clinica"}:
                    choices = [str(value) for value, _ in field.choices]
                    self.assertEqual([item.get("value") for item in fields[name]], choices)
                else:
                    self.assertEqual(len(fields[name]), 1)
                self.assertTrue(all("disabled" not in item for item in fields[name]))
        self.assertEqual(markup_form["attributes"].get("method", "get").lower(), "post")
        self.assertEqual(markup_form["attributes"].get("enctype"), "multipart/form-data")
        self.assertEqual(len(fields["csrfmiddlewaretoken"]), 1)
        self.assertTrue(fields["csrfmiddlewaretoken"][0].get("value"))

    def test_crear_y_editar_conservan_campos_radios_fechas_y_logo(self):
        for editing, url, expected_profile in (
            (False, reverse("superadmin_empresa_create"), "erp"),
            (True, reverse("superadmin_empresa_edit", args=[self.empresa.pk]), "clinica"),
        ):
            with self.subTest(editing=editing):
                response = self.client.get(url)
                _, markup_form = self._markup(response)
                self._assert_fields(response.context["form"], markup_form)
                fields = markup_form["fields"]
                self.assertEqual(
                    [item["value"] for item in fields["tipo_solucion"] if "checked" in item],
                    [expected_profile],
                )
                self.assertEqual(fields["logo"][0].get("type"), "file")
                self.assertIn("checked", fields["activa"][0])
                if editing:
                    self.assertEqual(fields["fecha_inicio_plan"][0].get("value"), "2026-01-03")
                    self.assertEqual(fields["fecha_vencimiento_plan"][0].get("value"), "2030-12-17")
                    self.assertEqual(fields["logo-clear"][0].get("type"), "checkbox")
                    self.assertEqual(
                        [item["value"] for item in fields["modulos_activos"] if "checked" in item],
                        [str(self.contabilidad.pk)],
                    )
                    self.assertEqual(
                        [item["value"] for item in fields["modulos_adicionales_visibles_clinica"] if "checked" in item],
                        [str(self.rrhh.pk)],
                    )

    def test_post_invalido_conserva_selecciones_fechas_y_abre_errores_sin_guardar(self):
        antes = Empresa.objects.filter(pk=self.empresa.pk).values().get()
        config_antes = ConfiguracionAvanzadaEmpresa.objects.filter(pk=self.configuracion.pk).values().get()
        modulos_antes = list(EmpresaModulo.objects.filter(empresa=self.empresa).values())
        visibles_antes = list(self.configuracion.modulos_adicionales_visibles_clinica.values_list("pk", flat=True))
        data = {
            "nombre": "", "slug": self.empresa.slug, "rtn": self.empresa.rtn, "pais": "Honduras",
            "tipo_solucion": "tecnicentro", "plan_comercial": str(self.plan.pk), "estado_licencia": "suspendida",
            "fecha_inicio_plan": "2031-04-20", "fecha_vencimiento_plan": "2031-04-01",
            "activa": "on", "permite_cai_historico": "on", "permite_plantilla_factura_independiente": "on",
            "manuales_pdf_habilitados": "on", "modulos_activos": [str(self.rrhh.pk)],
            "modulos_adicionales_visibles_clinica": [str(self.rrhh.pk), "2147483647"],
        }

        response = self.client.post(reverse("superadmin_empresa_edit", args=[self.empresa.pk]), data)

        markup, markup_form = self._markup(response)
        self._assert_fields(response.context["form"], markup_form)
        fields = markup_form["fields"]
        self.assertEqual(set(response.context["form"].errors), {"nombre", "fecha_vencimiento_plan", "modulos_adicionales_visibles_clinica"})
        self.assertEqual([item["value"] for item in fields["tipo_solucion"] if "checked" in item], ["tecnicentro"])
        self.assertEqual(fields["fecha_inicio_plan"][0].get("value"), data["fecha_inicio_plan"])
        self.assertEqual(fields["fecha_vencimiento_plan"][0].get("value"), data["fecha_vencimiento_plan"])
        self.assertEqual([item["value"] for item in fields["modulos_activos"] if "checked" in item], [str(self.rrhh.pk)])
        self.assertEqual([item["value"] for item in fields["modulos_adicionales_visibles_clinica"] if "checked" in item], [str(self.rrhh.pk)])
        for name in ("activa", "permite_cai_historico", "permite_plantilla_factura_independiente", "manuales_pdf_habilitados"):
            self.assertIn("checked", fields[name][0])
        self.assertIn("open", markup.details["cf-clinical"])
        self.assertIn("#cf-clinical", markup.links)
        self.assertIn("#id_fecha_vencimiento_plan", markup.links)
        self.assertEqual(Empresa.objects.filter(pk=self.empresa.pk).values().get(), antes)
        self.assertEqual(ConfiguracionAvanzadaEmpresa.objects.filter(pk=self.configuracion.pk).values().get(), config_antes)
        self.assertEqual(list(EmpresaModulo.objects.filter(empresa=self.empresa).values()), modulos_antes)
        self.assertEqual(list(self.configuracion.modulos_adicionales_visibles_clinica.values_list("pk", flat=True)), visibles_antes)
