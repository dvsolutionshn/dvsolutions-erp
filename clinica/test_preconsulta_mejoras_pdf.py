"""Reglas nuevas del enlace y conservación de respuestas anteriores."""

from copy import deepcopy
import json
import shutil
import subprocess
from unittest import skipUnless
from unittest.mock import patch

from django.template.loader import render_to_string
from django.test import SimpleTestCase

from . import test_preconsulta_negativas_explicitas as datos_negativas


class MejorasPdfFormularioTests(SimpleTestCase):
    def setUp(self):
        self.base = datos_negativas.NegativasExplicitasFormularioTests()
        self.base.setUp()

    def formulario(self, **cambios):
        return self.base.formulario(self.base.respuestas(**cambios))

    def test_condicion_listada_no_requiere_otras_y_admite_varias(self):
        form = self.base.formulario()
        opciones = dict(form.fields["condiciones_diagnosticadas"].choices)
        seleccion = [codigo for codigo, texto in opciones.items() if texto in {"Asma", "Hipertensión arterial"}]
        self.assertEqual(len(seleccion), 2)
        form = self.formulario(condiciones_diagnosticadas=seleccion, diagnostico_medico_detalle="")
        self.assertTrue(form.is_valid(), form.errors.as_json())
        self.assertEqual(form.datos_generales_limpios()["formulario_general"]["condiciones_diagnosticadas"], seleccion)

    def test_si_con_ninguna_anterior_exige_detalle_y_conserva_afirmacion(self):
        form = self.formulario(condiciones_diagnosticadas=["ninguna_anteriores"], diagnostico_medico_detalle="")
        self.assertFalse(form.is_valid())
        self.assertIn("diagnostico_medico_detalle", form.errors)
        form = self.formulario(condiciones_diagnosticadas=["ninguna_anteriores"], diagnostico_medico_detalle="Condición no enumerada")
        self.assertTrue(form.is_valid(), form.errors.as_json())
        datos = form.datos_generales_limpios()["formulario_general"]
        self.assertEqual(datos["diagnostico_medico"], "si")
        self.assertEqual(datos["diagnostico_medico_detalle"], "Condición no enumerada")

    def test_ninguna_anterior_no_se_combina_con_una_condicion(self):
        form = self.formulario(condiciones_diagnosticadas=["ninguna_anteriores", "otras"])
        self.assertFalse(form.is_valid())
        self.assertIn("condiciones_diagnosticadas", form.errors)

    def test_diagnostico_afirmativo_exige_seleccion_y_negativo_rechaza_condiciones(self):
        form = self.formulario(condiciones_diagnosticadas=[])
        self.assertFalse(form.is_valid())
        self.assertIn("condiciones_diagnosticadas", form.errors)
        form = self.formulario(diagnostico_medico="no", diagnostico_medico_detalle="", condiciones_diagnosticadas=["otras"])
        self.assertFalse(form.is_valid())
        self.assertIn("condiciones_diagnosticadas", form.errors)

    def test_no_otra_alteracion_permite_funcion_alterada_sin_detalle(self):
        form = self.formulario(funcion_sueno="alterada", funciones_otras_alteraciones="no", funciones_detalle="")
        self.assertTrue(form.is_valid(), form.errors.as_json())
        datos = form.datos_generales_limpios()["formulario_general"]
        self.assertEqual(datos["funcion_sueno"], "alterada")
        self.assertEqual(datos["funciones_otras_alteraciones"], "no")

    def test_otra_alteracion_si_exige_detalle_independientemente_funciones_normales(self):
        cambios = {nombre: "normal" for nombre, _ in self.base.formulario().FUNCIONES_ORGANICAS_CAMPOS}
        form = self.formulario(**cambios, funciones_otras_alteraciones="si", funciones_detalle="")
        self.assertFalse(form.is_valid())
        self.assertIn("funciones_detalle", form.errors)
        form = self.formulario(**cambios, funciones_otras_alteraciones="si", funciones_detalle="Otra alteración descrita")
        self.assertTrue(form.is_valid(), form.errors.as_json())

    def test_alergias_separadas_exigen_su_propio_detalle(self):
        for tipo in ("farmacos", "alimentos"):
            with self.subTest(tipo=tipo):
                form = self.formulario(**{f"alergias_{tipo}_respuesta": "si", f"alergias_{tipo}_detalle": ""})
                self.assertFalse(form.is_valid())
                self.assertIn(f"alergias_{tipo}_detalle", form.errors)
        form = self.formulario(alergias_alimentos_respuesta="si", alergias_alimentos_detalle="Maní")
        self.assertTrue(form.is_valid(), form.errors.as_json())
        datos = form.datos_generales_limpios()["formulario_general"]
        self.assertEqual(datos["alergias_farmacos_detalle"], "Alergia a penicilina")
        self.assertEqual(datos["alergias_alimentos_detalle"], "Maní")

    def test_infecciosos_no_no_requiere_texto_pero_si_exige_detalle(self):
        form = self.formulario(antecedentes_infecciosos_respuesta="no", antecedentes_infecciosos="")
        self.assertTrue(form.is_valid(), form.errors.as_json())
        form = self.formulario(antecedentes_infecciosos_respuesta="si", antecedentes_infecciosos="")
        self.assertFalse(form.is_valid())
        self.assertIn("antecedentes_infecciosos", form.errors)

    def test_familia_y_riesgos_solo_piden_detalle_con_otros(self):
        form = self.formulario(antecedentes_familiares=["diabetes"], antecedentes_familiares_detalle="")
        self.assertTrue(form.is_valid(), form.errors.as_json())
        for seleccion, detalle in (("antecedentes_familiares", "antecedentes_familiares_detalle"), ("riesgo_tromboembolico", "riesgo_tromboembolico_otros")):
            with self.subTest(pregunta=seleccion):
                codigo = "otra" if seleccion == "antecedentes_familiares" else "otros"
                form = self.formulario(**{seleccion: [codigo], detalle: ""})
                self.assertFalse(form.is_valid())
                self.assertIn(detalle, form.errors)
                form = self.formulario(**{seleccion: [codigo], detalle: "Detalle específico"})
                self.assertTrue(form.is_valid(), form.errors.as_json())

    def test_riesgo_ninguno_excluyente_y_no_aplica_ya_no_disponible(self):
        form = self.base.formulario()
        opciones = dict(form.fields["riesgo_tromboembolico"].choices)
        self.assertEqual(opciones["ninguno"], "Ninguno")
        self.assertNotIn("no_aplica", opciones)
        for seleccion in (["no_aplica"], ["ninguno", "otros"]):
            with self.subTest(seleccion=seleccion):
                form = self.formulario(riesgo_tromboembolico=seleccion, riesgo_tromboembolico_otros="Detalle")
                self.assertFalse(form.is_valid())
                self.assertIn("riesgo_tromboembolico", form.errors)

    def test_gestas_no_omite_recuentos_sin_exigirlos(self):
        form = self.formulario(gine_gestas_respuesta="no", gine_partos="", gine_cesareas="", gine_abortos="")
        self.assertTrue(form.is_valid(), form.errors.as_json())
        for campo in ("gine_partos", "gine_cesareas", "gine_abortos"):
            with self.subTest(campo=campo):
                form = self.formulario(**{campo: ""})
                self.assertFalse(form.is_valid())
                self.assertIn(campo, form.errors)

    def test_campos_retirados_no_aparecen_en_enlace_pero_siguen_en_formulario_interno(self):
        form = self.base.formulario()
        html = render_to_string("clinica/preconsulta_publica.html", {"form": form, "preconsulta": self.base.caso.preconsulta})
        for nombre in ("evaluacion_psicologica", "evaluacion_psicologica_detalle", "decision_cirugia_otros", "gine_gestas", "alergias_respuesta", "alergias_detalle"):
            self.assertNotRegex(html, rf'<(?:input|textarea|select)\b[^>]*\bname="{nombre}"')
        interno = self.base.caso.formulario()
        for nombre in ("evaluacion_psicologica", "decision_cirugia_otros", "gine_gestas", "alergias_respuesta"):
            self.assertIn(nombre, interno.fields)

    def test_nuevas_preguntas_no_reinterpretan_ni_borran_historia(self):
        anterior = {
            "alergias_respuesta": "si", "alergias_detalle": "Alergia genérica a látex",
            "alergias_medicamentos_detalle": "Medicamentos y alergias antes combinados",
            "gine_gestas": "3", "evaluacion_psicologica": ["otros"],
            "evaluacion_psicologica_detalle": "Respuesta emocional anterior", "decision_cirugia_otros": "Consulta familiar anterior",
        }
        self.base.caso.preconsulta.alergias = "Alergia genérica a látex"
        self.base.caso.preconsulta.datos_generales = {"formulario_general": deepcopy(anterior)}
        form = self.base.formulario(self.base.respuestas(), sin_datos_previos=False)
        with patch("clinica.forms.Paciente.objects.filter") as pacientes:
            pacientes.return_value.exclude.return_value.exists.return_value = False
            self.assertTrue(form.is_valid(), form.errors.as_json())
        preconsulta = form.save(commit=False)
        datos = form.datos_generales_limpios()["formulario_general"]
        for nombre, valor in anterior.items():
            with self.subTest(campo=nombre):
                self.assertEqual(datos[nombre], valor)
        self.assertEqual(preconsulta.alergias, "Alergia genérica a látex")
        self.assertEqual(form.datos_generales_limpios()["version_formulario_publico"], 2)

    def test_gestas_no_conserva_recuentos_anteriores_como_historia(self):
        anterior = {"gine_gestas": "3", "gine_partos": "2", "gine_cesareas": "1", "gine_abortos": 0}
        self.base.caso.preconsulta.datos_generales = {"formulario_general": deepcopy(anterior)}
        form = self.formulario(gine_gestas_respuesta="no", gine_partos="", gine_cesareas="", gine_abortos="")
        self.assertTrue(form.is_valid(), form.errors.as_json())
        guardado = form.datos_generales_limpios()["formulario_general"]
        self.assertEqual(guardado["gine_gestas"], "3")
        self.assertEqual(guardado["gine_gestas_respuesta"], "no")
        for nombre in ("gine_partos", "gine_cesareas", "gine_abortos"):
            self.assertEqual(guardado[f"{nombre}_historico"], anterior[nombre])

    def test_detalle_familiar_solo_modelo_se_conserva_antes_de_no_solicitar_otros(self):
        self.base.caso.preconsulta.antecedentes_familiares_detalle = "Madre con diabetes, registro anterior"
        form = self.formulario(antecedentes_familiares=["diabetes"], antecedentes_familiares_detalle="")
        self.assertTrue(form.is_valid(), form.errors.as_json())
        guardado = form.datos_generales_limpios()["formulario_general"]
        self.assertEqual(guardado["antecedentes_familiares_detalle_historico"], "Madre con diabetes, registro anterior")

    def test_declaraciones_explicitas_de_preguntas_retiradas_siguen_intactas(self):
        declaracion = {"valor": "ninguna", "texto": "Niego ansiedad, depresión u otra condición psicológica o emocional"}
        self.base.caso.preconsulta.datos_generales = {
            "formulario_general": {"evaluacion_psicologica": ["ninguna"]},
            "negativas_explicitas": {"version": 1, "respuestas": {"evaluacion_psicologica": deepcopy(declaracion)}},
        }
        form = self.formulario()
        self.assertTrue(form.is_valid(), form.errors.as_json())
        datos = form.datos_generales_limpios()
        self.assertEqual(datos["formulario_general"]["evaluacion_psicologica"], ["ninguna"])
        self.assertEqual(datos["negativas_explicitas"]["respuestas"]["evaluacion_psicologica"], declaracion)

    def test_metadatos_heredados_con_version_invalida_no_se_elevan_a_declaraciones(self):
        for version in (True, "1", None):
            with self.subTest(version=version):
                self.base.caso.preconsulta.datos_generales = {
                    "formulario_general": {"evaluacion_psicologica": ["ninguna"]},
                    "negativas_explicitas": {"version": version, "respuestas": {
                        "evaluacion_psicologica": {"valor": "ninguna", "texto": "Texto de metadatos no válido"},
                    }},
                }
                form = self.formulario()
                self.assertTrue(form.is_valid(), form.errors.as_json())
                respuestas = form.datos_generales_limpios()["negativas_explicitas"]["respuestas"]
                self.assertNotIn("evaluacion_psicologica", respuestas)

    @skipUnless(shutil.which("node"), "Node permite verificar los condicionales de la plantilla real")
    def test_condicionales_cliente_y_reintento_no_pierden_el_detalle_antes_de_corregir(self):
        html = render_to_string("clinica/preconsulta_publica.html", {"form": self.base.formulario(), "preconsulta": self.base.caso.preconsulta})
        funciones = html[html.index("    function clearConditionalRetry"):html.index("    function installNoAplicaControls")]
        programa = r"""
const explicitClinicalAnswers=true,selected=new Map(),panels=new Map();
let hasSummary=false;
const selectedValue=(name,value)=>(selected.get(name)||[]).includes(value);
const getPanel=selector=>{
    if(!panels.has(selector)){
        const input={type:'textarea',value:'Detalle original',disabled:false};
        panels.set(selector,{hidden:false,dataset:{},hasError:false,input,
            querySelector(){return this.hasError?{}:null},querySelectorAll(){return [input]}});
    }
    return panels.get(selector);
};
const document={querySelectorAll(selector){return selector.split(',').map(getPanel)}};
const form={querySelector(selector){return selector==='.error-summary'?(hasSummary?{}:null):null}};
FUNCIONES
selected.set('funciones_otras_alteraciones',['no']);
toggleConditionalDetails();
const organicNo={hidden:getPanel('.funciones-detail').hidden,disabled:getPanel('.funciones-detail').input.disabled};
selected.set('diagnostico_medico',['si']);selected.set('condiciones_diagnosticadas',['ninguna_anteriores']);
selected.set('gine_gestas_respuesta',['si']);
toggleConditionalDetails();
const diagnosisOther={listHidden:getPanel('.condiciones-diagnosticadas').hidden,detailHidden:getPanel('.diagnostico-medico-detail').hidden};
const gestasSi=getPanel('.gestas-detail').hidden;
selected.set('gine_gestas_respuesta',['no']);toggleConditionalDetails();
const gestasNo=getPanel('.gestas-detail').hidden;
hasSummary=true;
const retry=getPanel('.alergias-farmacos-detail');retry.hasError=true;retry.input.value='Respuesta que debe corregirse';
toggleDetail('.alergias-farmacos-detail',false);
const beforeCorrection={hidden:retry.hidden,disabled:retry.input.disabled,value:retry.input.value};
clearConditionalRetry('alergias_farmacos_respuesta');toggleDetail('.alergias-farmacos-detail',false);
const afterCorrection={hidden:retry.hidden,disabled:retry.input.disabled,value:retry.input.value};
process.stdout.write(JSON.stringify({organicNo,diagnosisOther,gestasSi,gestasNo,beforeCorrection,afterCorrection}));
""".replace("FUNCIONES", funciones)
        resultado = subprocess.run([shutil.which("node"), "-e", programa], check=True, capture_output=True, text=True, encoding="utf-8")
        datos = json.loads(resultado.stdout)
        self.assertEqual(datos["organicNo"], {"hidden": True, "disabled": True})
        self.assertEqual(datos["diagnosisOther"], {"listHidden": False, "detailHidden": False})
        self.assertFalse(datos["gestasSi"])
        self.assertTrue(datos["gestasNo"])
        self.assertEqual(datos["beforeCorrection"], {"hidden": False, "disabled": False, "value": "Respuesta que debe corregirse"})
        self.assertEqual(datos["afterCorrection"], {"hidden": True, "disabled": True, "value": ""})
