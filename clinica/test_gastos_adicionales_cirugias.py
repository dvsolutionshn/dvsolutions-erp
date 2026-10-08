from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Empresa, EmpresaModulo, Modulo, Usuario
from facturacion.models import Factura, Producto, TipoImpuesto

from .catalogo_cirugias_gastos_adicionales import (
    cirugias_gastos_adicionales_choices,
    nombre_cirugia_gasto_adicional,
    profesional_luis_gasto_adicional,
    profesionales_luis_gasto_adicional,
)
from .forms_gastos_adicionales import GastoAdicionalForm
from .models import GastoAdicional, LineaGastoAdicional, Paciente, ProfesionalSalud
from .services_gastos_adicionales import convertir_gasto_adicional


class GastoAdicionalCirugiasTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(nombre="Hospital MIA", slug="hospital_mia", rtn="GAS-1")
        cls.otra = Empresa.objects.create(nombre="Medical Spa", slug="medical_spa", rtn="GAS-2")
        for codigo in ("gastos_adicionales", "facturacion"):
            modulo, _ = Modulo.objects.get_or_create(codigo=codigo, defaults={"nombre": codigo})
            EmpresaModulo.objects.get_or_create(empresa=cls.empresa, modulo=modulo)
        cls.usuario = Usuario.objects.create_superuser(username="ga_cirugias", password=None, empresa=cls.empresa)
        cls.paciente = Paciente.objects.create(
            empresa=cls.empresa, expediente_codigo="CIR-1", nombre="Paciente cirugía", identidad="0801199940001",
        )
        cls.luis = ProfesionalSalud.objects.create(empresa=cls.empresa, nombre="Dr. Luis González")
        cls.candy = ProfesionalSalud.objects.create(empresa=cls.empresa, nombre="Dra. Candy Luque", usuario=cls.usuario)
        cls.luis_otro = ProfesionalSalud.objects.create(empresa=cls.otra, nombre="Dr. Luis González")
        cls.impuesto = TipoImpuesto.objects.create(nombre="Exento cirugía", porcentaje=0)
        cls.producto = Producto.objects.create(
            empresa=cls.empresa, nombre="Material quirúrgico", precio=100, impuesto_predeterminado=cls.impuesto,
        )

    def _datos_form(self, **overrides):
        data = {"paciente": self.paciente.pk, "fecha": timezone.localdate().isoformat(), "tipo_cirugia": "rinoplastia", "profesional": "", "observacion": ""}
        data.update(overrides)
        return data

    def _gasto(self, **overrides):
        defaults = {
            "empresa": self.empresa, "paciente": self.paciente,
            "tipo_cirugia": "rinoplastia", "tipo_cirugia_nombre": "Rinoplastia", "creado_por": self.usuario,
        }
        defaults.update(overrides)
        gasto = GastoAdicional.objects.create(**defaults)
        LineaGastoAdicional.objects.create(gasto=gasto, producto=self.producto, cantidad=1, precio_unitario=100)
        gasto.calcular_totales()
        gasto.save()
        return gasto

    def test_choices_reutilizan_solo_cirugias_del_catalogo_existente(self):
        from .forms import PROCEDIMIENTOS_GENERALES_GRUPOS
        esperadas = {codigo for _, opciones in PROCEDIMIENTOS_GENERALES_GRUPOS[:4] for codigo, _ in opciones}
        choices = cirugias_gastos_adicionales_choices()
        self.assertEqual(len(choices), 4)
        self.assertEqual({codigo for _, opciones in choices for codigo, _ in opciones}, esperadas)
        self.assertEqual(len(esperadas), 31)
        self.assertTrue(all("(" not in etiqueta for _, opciones in choices for _, etiqueta in opciones))
        self.assertEqual(nombre_cirugia_gasto_adicional("rinoplastia"), "Rinoplastia")
        self.assertEqual(nombre_cirugia_gasto_adicional("evaluacion_alopecia"), "")
        self.assertEqual(nombre_cirugia_gasto_adicional("fumador"), "")

    def test_cirugia_requerida_y_validada_en_formulario(self):
        for valor in ("", "otra_cirugia_inventada", "evaluacion_alopecia", "fumador"):
            with self.subTest(valor=valor):
                form = GastoAdicionalForm(self._datos_form(tipo_cirugia=valor), empresa=self.empresa)
                self.assertFalse(form.is_valid())
                self.assertIn("tipo_cirugia", form.errors)
        self.assertEqual(GastoAdicionalForm(empresa=self.empresa).initial["tipo_cirugia"], "")
        self.assertTrue(GastoAdicionalForm(self._datos_form(), empresa=self.empresa).is_valid())

    def test_profesional_es_fijo_local_e_ignora_ids_manipulados_y_usuario_candy(self):
        for valor in (self.candy.pk, self.luis_otro.pk, "no-es-un-id", self.usuario.pk, ""):
            with self.subTest(valor=valor):
                form = GastoAdicionalForm(
                    self._datos_form(profesional=valor), empresa=self.empresa,
                    initial={"profesional": self.candy.pk},
                )
                self.assertTrue(form.is_valid(), form.errors)
                self.assertTrue(form.fields["profesional"].disabled)
                self.assertEqual(form.cleaned_data["profesional"], self.luis)
                self.assertEqual(list(form.fields["profesional"].queryset), [self.luis])

    def test_profesional_requiere_token_luis_local_activo_y_prioriza_gonzalez(self):
        ProfesionalSalud.objects.create(empresa=self.empresa, nombre="Luisana Rodríguez")
        ProfesionalSalud.objects.create(empresa=self.empresa, nombre="Dr Luis López", activo=False)
        alterno = ProfesionalSalud.objects.create(empresa=self.empresa, nombre="Dr Luís Ramirez")
        self.assertEqual(set(profesionales_luis_gasto_adicional(self.empresa)), {self.luis, alterno})
        self.assertEqual(profesional_luis_gasto_adicional(self.empresa), self.luis)
        segundo = ProfesionalSalud.objects.create(empresa=self.empresa, nombre="Dr Luis Gonzales")
        self.assertIsNone(profesional_luis_gasto_adicional(self.empresa))
        form = GastoAdicionalForm(self._datos_form(profesional=self.luis.pk), empresa=self.empresa)
        self.assertTrue(form.is_valid())
        self.assertIsNone(form.cleaned_data["profesional"])
        ProfesionalSalud.objects.filter(pk__in=[self.luis.pk, segundo.pk]).update(activo=False)
        self.assertIsNone(profesional_luis_gasto_adicional(self.empresa))
        form = GastoAdicionalForm(self._datos_form(profesional=alterno.pk), empresa=self.empresa)
        self.assertTrue(form.is_valid())
        self.assertIsNone(form.cleaned_data["profesional"])

    def test_sin_doctor_local_no_crea_profesionales_y_referencia_ajena_se_ignora(self):
        self.luis.activo = False
        self.luis.save(update_fields=["activo"])
        cantidad = ProfesionalSalud.objects.count()
        form = GastoAdicionalForm(self._datos_form(profesional=self.luis_otro.pk), empresa=self.empresa)
        self.assertTrue(form.is_valid())
        self.assertIsNone(form.cleaned_data["profesional"])
        self.assertEqual(ProfesionalSalud.objects.count(), cantidad)

    def test_snapshot_persistido_y_label_no_depende_de_catalogo_vivo(self):
        gasto = self._gasto(profesional=self.luis)
        gasto.refresh_from_db()
        self.assertEqual(gasto.tipo_cirugia, "rinoplastia")
        self.assertEqual(gasto.tipo_cirugia_label, "Rinoplastia")
        self.assertEqual(gasto.profesional_nombre, "Dr. Luis González")
        with patch("clinica.catalogo_cirugias_gastos_adicionales.nombre_cirugia_gasto_adicional", return_value="Nombre futuro"):
            self.assertEqual(gasto.tipo_cirugia_label, "Rinoplastia")
        gasto.tipo_cirugia_nombre = ""
        self.assertEqual(gasto.tipo_cirugia_label, "Rinoplastia")

    def test_modelo_rechaza_codigo_desconocido_pero_conserva_documentos_legacy(self):
        with self.assertRaises(ValidationError):
            self._gasto(tipo_cirugia="inventada")
        legacy = self._gasto(tipo_cirugia="", tipo_cirugia_nombre="")
        self.assertEqual(legacy.tipo_cirugia_label, "")
        legacy.observacion = "Documento previo a la selección de cirugía."
        legacy.save()
        factura, creada = convertir_gasto_adicional(legacy, self.usuario)
        self.assertTrue(creada)
        self.assertEqual(factura.estado, "borrador")

    def test_cirugia_y_profesional_snapshot_inmutables_despues_de_conversion(self):
        gasto = self._gasto(profesional=self.luis)
        convertir_gasto_adicional(gasto, self.usuario)
        for campo, valor in (
            ("tipo_cirugia", "lipoescultura"),
            ("tipo_cirugia_nombre", "Etiqueta modificada"),
            ("profesional_nombre", "Profesional cambiado"),
        ):
            with self.subTest(campo=campo):
                gasto.refresh_from_db()
                setattr(gasto, campo, valor)
                with self.assertRaises(ValidationError):
                    gasto.save()

    def test_vista_persiste_cirugia_y_profesional_fijo_y_exige_tipo(self):
        import json
        self.client.force_login(self.usuario)
        url = reverse("clinica_gasto_adicional_crear", kwargs={"empresa_slug": self.empresa.slug})
        lineas = json.dumps([{"producto_id": self.producto.pk, "cantidad": "1", "precio_unitario": "100"}])
        response = self.client.post(url, {**self._datos_form(tipo_cirugia=""), "lineas": lineas})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(GastoAdicional.objects.exists())
        response = self.client.post(url, {**self._datos_form(profesional=self.candy.pk), "lineas": lineas})
        self.assertEqual(response.status_code, 302)
        gasto = GastoAdicional.objects.get()
        self.assertEqual(gasto.tipo_cirugia, "rinoplastia")
        self.assertEqual(gasto.tipo_cirugia_nombre, "Rinoplastia")
        self.assertEqual(gasto.profesional_id, self.luis.pk)
        self.assertEqual(gasto.profesional_nombre, "Dr. Luis González")
