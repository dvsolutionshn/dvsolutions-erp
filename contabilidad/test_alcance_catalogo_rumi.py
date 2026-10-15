from io import BytesIO

from django.core.exceptions import ValidationError
from django.test import TestCase

from core.models import Empresa
from .alcance_catalogo import es_empresa_rumi
from .forms import CuentaContableForm
from .importadores import analizar_catalogo_cuentas_desde_excel, aplicar_catalogo_cuentas, importar_catalogo_cuentas_desde_excel
from . import importadores_rumi
from .models import CuentaContable


class AlcanceCatalogoRumiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.rumi = Empresa.objects.create(nombre="RUMI", slug="rumi", rtn="08011999177001")
        cls.otra = Empresa.objects.create(nombre="RUMI sucursal", slug="rumi-otra", rtn="08011999177002")

    def test_identifica_empresa_exacta_y_verifica_identidad_guardada(self):
        self.assertTrue(es_empresa_rumi(self.rumi))
        self.assertFalse(es_empresa_rumi(self.otra))
        self.assertFalse(es_empresa_rumi(None))
        falsa = Empresa.objects.get(pk=self.otra.pk)
        falsa.slug = "rumi"
        self.assertFalse(es_empresa_rumi(falsa))

    def test_apis_avanzadas_rechazan_empresa_distinta_antes_de_leer_archivo(self):
        for analizar in (analizar_catalogo_cuentas_desde_excel, importadores_rumi.analizar_catalogo_cuentas_desde_excel):
            with self.subTest(api=analizar.__module__), self.assertRaisesMessage(ValidationError, "RUMI"):
                analizar(self.otra, BytesIO(b"archivo no necesario"))
        for aplicar in (aplicar_catalogo_cuentas, importadores_rumi.aplicar_catalogo_cuentas):
            with self.subTest(api=aplicar.__module__), self.assertRaisesMessage(ValidationError, "RUMI"):
                aplicar(self.otra, [])
        with self.assertRaisesMessage(ValidationError, "RUMI"):
            importadores_rumi.importar_catalogo_cuentas_desde_excel(self.otra, BytesIO())
        with self.assertRaisesMessage(ValidationError, "RUMI"):
            importar_catalogo_cuentas_desde_excel(self.otra, BytesIO(), prefijo_codigos="RUMI-")
        self.assertFalse(CuentaContable.objects.exists())

    def test_tipo_de_control_se_ofrece_solo_en_formulario_de_rumi(self):
        form_rumi = CuentaContableForm(empresa=self.rumi)
        form_otra = CuentaContableForm(empresa=self.otra)
        self.assertIn("orden", dict(form_rumi.fields["tipo"].choices))
        self.assertNotIn("orden", dict(form_otra.fields["tipo"].choices))
        self.assertEqual(form_otra.fields["tipo"].help_text, "")

    def test_modelo_rechaza_nuevos_controles_en_otras_empresas(self):
        with self.assertRaisesMessage(ValidationError, "únicamente para la empresa RUMI"):
            CuentaContable.objects.create(
                empresa=self.otra, codigo="7", nombre="Control no autorizado", tipo="orden",
                activa=False, acepta_movimientos=False,
            )
        self.assertFalse(CuentaContable.objects.exists())

    def test_restriccion_conserva_eventuales_registros_importados_anteriormente(self):
        cuenta = CuentaContable.objects.create(
            empresa=self.otra, codigo="7", nombre="Registro anterior", tipo="activo",
            activa=False, acepta_movimientos=False,
        )
        # Representa datos guardados antes de restringir el importador por empresa.
        CuentaContable.objects.filter(pk=cuenta.pk).update(tipo="orden")
        cuenta.refresh_from_db()
        cuenta.nombre = "Registro anterior conservado"
        cuenta.save(update_fields=["nombre"])
        self.assertEqual(CuentaContable.objects.get(pk=cuenta.pk).tipo, "orden")
        form = CuentaContableForm(empresa=self.otra, instance=cuenta)
        self.assertTrue(form.fields["tipo"].disabled)
