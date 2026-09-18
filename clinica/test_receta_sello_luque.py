from datetime import date
from pathlib import Path
from types import SimpleNamespace

from django.test import SimpleTestCase

from .views import (
    SELLO_FIRMA_RECETA_LUQUE,
    _contexto_receta_impresion,
)


class RecetaSelloLuqueTests(SimpleTestCase):
    def setUp(self):
        self.paciente = SimpleNamespace(
            id=1,
            nombre="Paciente de prueba",
            identidad="0801199900000",
            expediente_codigo="LUQ-001",
            edad=35,
        )
        self.receta = SimpleNamespace(
            id=1,
            fecha=date(2026, 9, 18),
            diagnostico="",
            indicaciones="",
            observaciones="",
            profesional=None,
        )

    def test_sello_se_activa_solo_para_luque_aestetic(self):
        luque = SimpleNamespace(slug="luque_aestetic", logo=None)
        otra_empresa = SimpleNamespace(slug="hospital_mia", logo=None)

        contexto_web = _contexto_receta_impresion(luque, self.paciente, self.receta)
        contexto_pdf = _contexto_receta_impresion(
            luque,
            self.paciente,
            self.receta,
            para_pdf=True,
        )
        contexto_otra = _contexto_receta_impresion(
            otra_empresa,
            self.paciente,
            self.receta,
            para_pdf=True,
        )

        self.assertEqual(
            contexto_web["sello_firma_src"],
            f"/static/{SELLO_FIRMA_RECETA_LUQUE}",
        )
        self.assertTrue(contexto_pdf["sello_firma_src"].startswith("file:///"))
        self.assertEqual(contexto_otra["sello_firma_src"], "")
        self.assertEqual(contexto_otra["logo_src"], "")
        self.assertTrue(
            Path("clinica", "static", SELLO_FIRMA_RECETA_LUQUE).is_file(),
            "La imagen limpia del sello y firma debe formar parte del proyecto.",
        )

    def test_pdf_usa_ruta_local_para_el_logo_de_la_empresa(self):
        ruta_logo = Path("clinica", "static", SELLO_FIRMA_RECETA_LUQUE).resolve()
        logo = SimpleNamespace(path=str(ruta_logo), url="/media/logos/empresa.png")
        empresa = SimpleNamespace(slug="hospital_mia", logo=logo)

        contexto_web = _contexto_receta_impresion(empresa, self.paciente, self.receta)
        contexto_pdf = _contexto_receta_impresion(
            empresa,
            self.paciente,
            self.receta,
            para_pdf=True,
        )

        self.assertEqual(contexto_web["logo_src"], "/media/logos/empresa.png")
        self.assertEqual(contexto_pdf["logo_src"], ruta_logo.as_uri())
