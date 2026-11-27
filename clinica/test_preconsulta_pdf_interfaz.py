"""Los controles condicionales no descartan respuestas durante un reintento."""

import json
from pathlib import Path
import shutil
import subprocess
from unittest import skipUnless

from django.test import SimpleTestCase


@skipUnless(shutil.which("node"), "Node es necesario para ejecutar el JavaScript del formulario")
class FormularioPdfInterfazTests(SimpleTestCase):
    def ejecutar(self, escenario):
        plantilla = (Path(__file__).parent / "templates/clinica/preconsulta_publica.html").read_text(encoding="utf-8")
        funciones = plantilla[
            plantilla.index("    function clearConditionalRetry("):
            plantilla.index("    function installNoAplicaControls(")
        ]
        script = r"""
const assert=require('node:assert/strict');
let explicitClinicalAnswers=true,serverErrors=true;
const answers={};
const panels={};
function panel(selector,{error=false,value='',checkbox=false}={}){
    const input={type:checkbox?'checkbox':'textarea',value,checked:checkbox,disabled:false};
    const item={dataset:{},hidden:false,input,error,
        querySelector(selector){return selector==='.field-error'&&this.error?{}:null;},
        querySelectorAll(){return [input];}};
    panels[selector]=item;
    return item;
}
const document={querySelectorAll(selector){return selector.split(',').flatMap(part=>panels[part]?[panels[part]]:[]);}};
const form={querySelector(selector){
    if(selector==='.error-summary')return serverErrors?{}:null;
    if(selector==='.condiciones-diagnosticadas .field-error')return panels['.condiciones-diagnosticadas']?.error?{}:null;
    if(selector==='[data-conditional-gine]')return {hidden:false};
    return null;
}};
function selectedValue(name,value){return (answers[name]||[]).includes(value);}
""" + funciones + "\n" + escenario
        resultado = subprocess.run(
            [shutil.which("node"), "-e", script], capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(resultado.returncode, 0, resultado.stdout + resultado.stderr)
        return json.loads(resultado.stdout)

    def test_no_con_texto_conserva_el_reintento_y_limpia_al_cambiar_respuesta(self):
        resultado = self.ejecutar(r"""
const detail=panel('.infecciosos-detail',{error:true,value:'COVID-19 en 2021'});
answers.antecedentes_infecciosos_respuesta=['no'];
toggleConditionalDetails();
assert.equal(detail.hidden,false);
assert.equal(detail.input.disabled,false);
assert.equal(detail.input.value,'COVID-19 en 2021');
assert.deepEqual(answers.antecedentes_infecciosos_respuesta,['no']);
clearConditionalRetry('antecedentes_infecciosos_respuesta');
answers.antecedentes_infecciosos_respuesta=['si'];
toggleConditionalDetails();
assert.equal(detail.hidden,false);
assert.equal(detail.input.value,'COVID-19 en 2021');
clearConditionalRetry('antecedentes_infecciosos_respuesta');
answers.antecedentes_infecciosos_respuesta=['no'];
toggleConditionalDetails();
toggleConditionalDetails();
console.log(JSON.stringify({hidden:detail.hidden,disabled:detail.input.disabled,value:detail.input.value}));
""")
        self.assertEqual(resultado, {"hidden": True, "disabled": True, "value": ""})

    def test_diagnostico_no_con_lista_y_texto_permite_corregir_sin_perderlos(self):
        resultado = self.ejecutar(r"""
const conditions=panel('.condiciones-diagnosticadas',{error:true,value:'otras',checkbox:true});
const detail=panel('.diagnostico-medico-detail',{value:'Otra condición no listada'});
answers.diagnostico_medico=['no'];
answers.condiciones_diagnosticadas=['otras'];
toggleConditionalDetails();
assert.equal(conditions.hidden,false);
assert.equal(conditions.input.checked,true);
assert.equal(detail.hidden,false);
assert.equal(detail.input.value,'Otra condición no listada');
assert.deepEqual(answers.diagnostico_medico,['no']);
clearConditionalRetry('diagnostico_medico');
toggleConditionalDetails();
toggleConditionalDetails();
console.log(JSON.stringify({hidden:conditions.hidden,checked:conditions.input.checked,detailHidden:detail.hidden,value:detail.input.value}));
""")
        self.assertEqual(resultado, {"hidden": True, "checked": False, "detailHidden": True, "value": ""})

    def test_legacy_no_activa_preservacion_nueva_en_reintento(self):
        resultado = self.ejecutar(r"""
explicitClinicalAnswers=false;
const detail=panel('.alergias-detail',{error:true,value:'Dato de prueba'});
answers.alergias_respuesta=['no'];
toggleConditionalDetails();
console.log(JSON.stringify({hidden:detail.hidden,disabled:detail.input.disabled,value:detail.input.value}));
""")
        self.assertEqual(resultado, {"hidden": True, "disabled": True, "value": ""})
