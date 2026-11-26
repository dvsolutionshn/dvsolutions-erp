from copy import deepcopy

from django.test import SimpleTestCase

from core.models import Empresa

from .models import Paciente, PreconsultaClinica
from .preconsulta_lectura_negativas import (
    negativas_guardadas,
    texto_negativa_guardada,
)
from .views import _resumen_preconsulta, _secciones_preconsulta


class LecturaNegativasExplicitasTests(SimpleTestCase):
    def preconsulta(self, general=None, declaraciones=None, **modelo):
        empresa = Empresa(id=1, nombre="Clínica QA", slug="hospital_mia")
        paciente = Paciente(id=1, empresa=empresa, nombre="Paciente QA", sexo="femenino")
        datos = {"formulario_general": general or {}}
        if declaraciones is not None:
            datos["negativas_explicitas"] = {"version": 1, "respuestas": declaraciones}
        return PreconsultaClinica(empresa=empresa, paciente=paciente, datos_generales=datos, **modelo)

    def test_las_declaraciones_nuevas_se_leen_con_su_texto_y_sin_cambiar_datos(self):
        declaraciones = {
            "antecedentes_familiares": {"valor": "no_aplica", "texto": "Niego antecedentes familiares conocidos"},
            "riesgo_tromboembolico": {"valor": "ninguno", "texto": "Niego antecedentes de riesgo tromboembólico"},
            "evaluacion_psicologica": {"valor": "ninguna", "texto": "Niego ansiedad o depresión"},
        }
        registro = self.preconsulta(
            general={"riesgo_tromboembolico": ["ninguno"], "evaluacion_psicologica": ["ninguna"]},
            declaraciones=declaraciones, antecedentes_familiares=["no_aplica"],
        )
        original = deepcopy(registro.datos_generales)
        resumen = _resumen_preconsulta(registro)
        secciones = _secciones_preconsulta(registro)
        campos = [campo for seccion in secciones for campo in seccion["campos"]]
        for nombre, etiqueta in (
            ("antecedentes_familiares", "Antecedentes familiares"),
            ("riesgo_tromboembolico", "Riesgo tromboembólico"),
            ("evaluacion_psicologica", "Evaluación psicológica"),
        ):
            esperado = [declaraciones[nombre]["texto"]]
            self.assertEqual(resumen[nombre], esperado)
            self.assertTrue(any(campo["label"] == etiqueta and campo["value"] == esperado for campo in campos))
        self.assertEqual(registro.datos_generales, original)
        self.assertEqual(registro.antecedentes_familiares, ["no_aplica"])

    def test_no_aplica_historico_no_se_convierte_en_una_negativa(self):
        registro = self.preconsulta(
            general={"riesgo_tromboembolico": ["no_aplica"], "evaluacion_psicologica": ["ninguna"]},
            antecedentes_familiares=["no_aplica"], antecedentes_infecciosos="No aplica",
        )
        original = deepcopy(registro.datos_generales)
        resumen = _resumen_preconsulta(registro)
        self.assertEqual(resumen["antecedentes_familiares"], ["No aplica / no conozco antecedentes familiares"])
        self.assertEqual(resumen["riesgo_tromboembolico"], ["No aplica"])
        self.assertEqual(resumen["evaluacion_psicologica"], ["Ninguno"])
        campos = [campo for seccion in _secciones_preconsulta(registro) for campo in seccion["campos"]]
        self.assertTrue(any(campo["value"] == "No aplica" for campo in campos))
        self.assertEqual(registro.datos_generales, original)

    def test_metadatos_invalidos_o_no_versionados_no_reinterpretan_respuestas(self):
        declaracion = {"riesgo_tromboembolico": {"valor": "no_aplica", "texto": "Niego riesgo tromboembólico"}}
        for version in (None, "1", True, 2):
            with self.subTest(version=version):
                registro = self.preconsulta(general={"riesgo_tromboembolico": ["no_aplica"]})
                registro.datos_generales["negativas_explicitas"] = {"version": version, "respuestas": declaracion}
                self.assertEqual(_resumen_preconsulta(registro)["riesgo_tromboembolico"], ["No aplica"])
        for respuestas in (None, [], "negativas"):
            registro = self.preconsulta()
            registro.datos_generales["negativas_explicitas"] = {"version": 1, "respuestas": respuestas}
            self.assertEqual(negativas_guardadas(registro), {})

    def test_declaracion_obsoleta_o_contradictoria_no_oculta_valores_positivos(self):
        declaraciones = {
            "riesgo_tromboembolico": {"valor": "ninguno", "texto": "Niego factores de riesgo"},
        }
        for seleccion in (["trombofilia"], ["ninguno", "trombofilia"], []):
            with self.subTest(seleccion=seleccion):
                registro = self.preconsulta(general={"riesgo_tromboembolico": seleccion}, declaraciones=declaraciones)
                resumen = _resumen_preconsulta(registro)
                self.assertNotIn("Niego factores de riesgo", resumen["riesgo_tromboembolico"])
        registro = self.preconsulta(
            general={"riesgo_tromboembolico": ["ninguno"]},
            declaraciones={"antecedentes_familiares": {"valor": "ninguno", "texto": "Texto de otro campo"}},
        )
        self.assertEqual(_resumen_preconsulta(registro)["riesgo_tromboembolico"], ["Ninguno"])

    def test_booleano_y_texto_no_usan_la_declaracion_exacta_sin_duplicados(self):
        registro = self.preconsulta(
            general={"dieta": "No", "ejercicio": "No"},
            declaraciones={
                "dieta": {"valor": "no", "texto": "No sigo una dieta"},
                "ejercicio": {"valor": "no", "texto": "Niego realizar actividad física"},
                "antecedentes_hospitalarios": {"valor": "no", "texto": "Niego hospitalizaciones o traumatismos previos"},
            },
            dieta="No", ejercicio="No", antecedentes_hospitalarios=False, consentimiento_datos=True,
        )
        campos = [campo for seccion in _secciones_preconsulta(registro) for campo in seccion["campos"]]
        for etiqueta, esperado in (
            ("Dieta", "No sigo una dieta"),
            ("Ejercicio", "Niego realizar actividad física"),
            ("Hospitalizaciones, accidentes, fracturas o cirugías previas", "Niego hospitalizaciones o traumatismos previos"),
        ):
            presentes = [campo for campo in campos if campo["label"] == etiqueta]
            self.assertEqual(len(presentes), 1)
            valor = presentes[0]["value"]
            self.assertEqual(valor[0] if isinstance(valor, list) else valor, esperado)

    def test_lectura_respeta_snapshot_y_limita_la_negativa_a_su_campo(self):
        texto = "Niego este antecedente, según la redacción vigente al contestar"
        negativas = {"antecedentes_infecciosos": {"valor": "niega", "texto": texto}}
        self.assertEqual(texto_negativa_guardada(negativas, "antecedentes_infecciosos", texto), texto)
        self.assertIsNone(texto_negativa_guardada(negativas, "antecedentes_infecciosos", "No aplica"))
        self.assertIsNone(texto_negativa_guardada(negativas, "alergias_respuesta", texto))
        self.assertIsNone(texto_negativa_guardada({"dieta": {"valor": "no", "texto": ""}}, "dieta", "No"))

    def test_no_se_exponen_datos_generales_en_lectura_limitada_por_negativas(self):
        registro = self.preconsulta(
            general={"evaluacion_psicologica": ["ninguna"]},
            declaraciones={"evaluacion_psicologica": {"valor": "ninguna", "texto": "Niego ansiedad o depresión"}},
        )
        registro.datos_generales["identidad"] = "0801199500001"
        secciones = _secciones_preconsulta(registro, incluir_datos_generales=False)
        self.assertNotIn("datos_generales", [seccion["clave"] for seccion in secciones])
        self.assertNotIn("0801199500001", str(secciones))
