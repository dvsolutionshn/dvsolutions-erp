"""El enlace clínico exige respuestas expresas sin inventar negativas."""

from copy import deepcopy
from html.parser import HTMLParser
import json
import shutil
import subprocess
from unittest import skipUnless
from unittest.mock import patch

from django.http import QueryDict
from django.template.loader import render_to_string
from django.test import SimpleTestCase

from .forms import PreconsultaClinicaPublicaForm
from .mobile_preconsultas import _secciones_seguras
from . import test_preconsulta_paciente_existente as datos_publicos
from .views import _secciones_preconsulta


class NegativasExplicitasFormularioTests(SimpleTestCase):
    def setUp(self):
        self.caso = datos_publicos.FormularioHistoriaPacienteExistenteTests()
        self.caso.setUp()

    def respuestas(self, **cambios):
        valores = self.caso.respuestas()
        valores["riesgo_tromboembolico"] = ["ninguno"]
        valores["version_declaracion"] = "1"
        valores.update(cambios)
        return valores

    def formulario(self, valores=None, **opciones):
        opciones.setdefault("sin_datos_previos", True)
        return self.caso.formulario(valores, negativas_explicitas=True, **opciones)

    def test_no_se_inventan_respuestas_negativas_en_preguntas_sin_contestar(self):
        for nombre in (
            "diagnostico_medico", "alergias_respuesta", "medicamentos_respuesta",
            "antecedentes_hospitalarios", "quirurgicos_operado", "consumo_riesgo",
            "dieta", "ejercicio", "antecedentes_familiares", "riesgo_tromboembolico",
            "evaluacion_psicologica",
        ):
            with self.subTest(pregunta=nombre):
                valores = self.respuestas()
                valores.pop(nombre)
                form = self.formulario(valores)
                self.assertFalse(form.is_valid())
                self.assertIn(nombre, form.errors)

    def test_texto_infeccioso_vacio_requiere_respuesta_o_negativa_explicita(self):
        form = self.formulario(self.respuestas(antecedentes_infecciosos=""))
        self.assertFalse(form.is_valid())
        self.assertIn("antecedentes_infecciosos", form.errors)

    def test_ginecologia_oculta_no_valida_ni_borra_respuestas_historicas(self):
        self.caso.paciente.sexo = "masculino"
        for estudio, fecha in (("si", ""), ("no", "2026-10-01"), ("si", "No aplica")):
            with self.subTest(estudio=estudio, fecha=fecha):
                historico = {"gine_mamografia": [estudio], "gine_mamografia_fecha": fecha}
                self.caso.preconsulta.datos_generales = {"formulario_general": deepcopy(historico)}
                form = self.formulario(self.respuestas(), sin_datos_previos=False)
                with patch("clinica.forms.Paciente.objects.filter") as pacientes:
                    pacientes.return_value.exclude.return_value.exists.return_value = False
                    self.assertTrue(form.is_valid(), form.errors.as_json())
                recibido = form.datos_generales_limpios()["formulario_general"]
                for nombre, valor in historico.items():
                    self.assertEqual(recibido[nombre], valor)
                self.assertEqual(self.caso.preconsulta.datos_generales["formulario_general"], historico)

    def test_reintento_con_negativa_contradictoria_conserva_el_texto_visible(self):
        form = self.formulario(self.respuestas(no_aplica_antecedentes_infecciosos="1"))
        self.assertFalse(form.is_valid())
        self.assertNotEqual(form.fields["antecedentes_infecciosos"].widget.attrs.get("data-no-aplica-selected"), "true")
        self.assertEqual(form["antecedentes_infecciosos"].value(), "COVID-19 en 2021")

    def test_funcion_organica_no_admite_dos_respuestas_en_la_misma_peticion(self):
        datos = QueryDict(mutable=True)
        for nombre, valor in self.respuestas().items():
            datos.setlist(nombre, valor if isinstance(valor, list) else [valor])
        datos.setlist("funcion_sueno", ["normal", "alterada"])
        form = self.formulario(datos)
        self.assertFalse(form.is_valid())
        self.assertIn("funcion_sueno", form.errors)

    def test_negativa_infecciosa_se_adapta_y_se_guarda_en_el_expediente(self):
        valores = self.respuestas(antecedentes_infecciosos="", no_aplica_antecedentes_infecciosos="1")
        form = self.formulario(valores)
        self.assertTrue(form.is_valid(), form.errors.as_json())
        etiqueta = form.fields["antecedentes_infecciosos"].widget.attrs["data-no-aplica-label"]
        self.assertIn("Niego", etiqueta)
        self.assertIn("infeccios", etiqueta.lower())
        self.assertIn("COVID", etiqueta)
        preconsulta = form.save(commit=False)
        self.assertEqual(preconsulta.antecedentes_infecciosos, etiqueta)
        self.assertEqual(form.cleaned_data["antecedentes_infecciosos"], etiqueta)
        declaracion = form.datos_generales_limpios()["negativas_explicitas"]
        self.assertEqual(declaracion["version"], 1)
        self.assertEqual(declaracion["respuestas"]["antecedentes_infecciosos"]["texto"], etiqueta)
        self.assertEqual(declaracion["respuestas"]["antecedentes_infecciosos"]["valor"], "niega")

    def test_paginas_anteriores_sin_version_no_se_convierten_en_declaraciones(self):
        for version in (None, "", "0", "2", ["1", "1"]):
            with self.subTest(version=version):
                datos = QueryDict(mutable=True)
                for nombre, valor in self.respuestas().items():
                    if nombre != "version_declaracion":
                        datos.setlist(nombre, valor if isinstance(valor, list) else [valor])
                if version is not None:
                    datos.setlist("version_declaracion", version if isinstance(version, list) else [version])
                form = self.formulario(datos)
                self.assertFalse(form.is_valid())
                self.assertIn("version_declaracion", form.errors)

    def test_texto_antiguo_no_aplica_no_es_una_negativa_nueva(self):
        form = self.formulario(self.respuestas(antecedentes_infecciosos="No aplica"))
        self.assertFalse(form.is_valid())
        self.assertIn("antecedentes_infecciosos", form.errors)

    def test_historial_muestra_la_declaracion_nueva_sin_renombrar_el_codigo_historico(self):
        form = self.formulario(self.respuestas(
            antecedentes_familiares=["no_aplica"], antecedentes_familiares_detalle="",
        ))
        self.assertTrue(form.is_valid(), form.errors.as_json())
        preconsulta = form.save(commit=False)
        preconsulta.datos_generales = form.datos_generales_limpios()
        texto = dict(form.fields["antecedentes_familiares"].choices)["no_aplica"]
        campos = [campo for seccion in _secciones_preconsulta(preconsulta) for campo in seccion["campos"]]
        self.assertTrue(any(campo["label"] == "Antecedentes familiares" and texto in campo["value"] for campo in campos))
        # Un registro anterior con el mismo código carece de una declaración expresa.
        preconsulta.datos_generales.pop("negativas_explicitas")
        campos = [campo for seccion in _secciones_preconsulta(preconsulta) for campo in seccion["campos"]]
        self.assertFalse(any(texto in str(campo["value"]) for campo in campos))
        self.assertTrue(any(campo["label"] == "Antecedentes familiares" and "No aplica" in str(campo["value"]) for campo in campos))

    def test_lector_movil_conserva_el_significado_de_las_declaraciones_nuevas(self):
        form = self.formulario(self.respuestas(
            antecedentes_familiares=["no_aplica"], antecedentes_familiares_detalle="",
        ))
        self.assertTrue(form.is_valid(), form.errors.as_json())
        preconsulta = form.save(commit=False)
        preconsulta.datos_generales = form.datos_generales_limpios()
        secciones = _secciones_seguras(preconsulta, _secciones_preconsulta)
        campos = [campo for seccion in secciones for campo in seccion["campos"]]
        texto = dict(form.fields["antecedentes_familiares"].choices)["no_aplica"]
        self.assertTrue(any(campo["label"] == "Antecedentes familiares" and texto in campo["value"] for campo in campos))

    def test_un_si_no_puede_omitir_su_detalle_marcando_una_negativa(self):
        for detalle in (
            "diagnostico_medico_detalle", "alergias_detalle", "medicamentos_detalle",
            "antecedentes_hospitalarios_detalle", "quirurgicos_detalle", "consumo_riesgo_detalle",
            "evaluacion_psicologica_detalle",
        ):
            with self.subTest(detalle=detalle):
                valores = self.respuestas(**{detalle: "", f"no_aplica_{detalle}": "1"})
                form = self.formulario(valores)
                self.assertFalse(form.is_valid())
                self.assertIn(detalle, form.errors)

    def test_no_se_guardan_respuestas_positivas_y_negativas_contradictorias(self):
        casos = (
            ({"alergias_respuesta": "no", "alergias_detalle": "Alergia a penicilina"}, "alergias_detalle"),
            ({"antecedentes_familiares": ["no_aplica", "diabetes"]}, "antecedentes_familiares"),
            ({"consumo_riesgo": ["no", "si"]}, "consumo_riesgo"),
            ({"antecedentes_infecciosos": "COVID-19 en 2021", "no_aplica_antecedentes_infecciosos": "1"}, "antecedentes_infecciosos"),
        )
        for cambios, campo in casos:
            with self.subTest(pregunta=campo):
                form = self.formulario(self.respuestas(**cambios))
                self.assertFalse(form.is_valid())
                self.assertIn(campo, form.errors)

    def test_no_aplica_escrito_no_satisface_el_detalle_de_un_si(self):
        form = self.formulario(self.respuestas(alergias_detalle="No aplica"))
        self.assertFalse(form.is_valid())
        self.assertIn("alergias_detalle", form.errors)

    def test_marcador_falso_o_repetido_no_declara_una_negativa(self):
        for valor in ("true", "0", "si", ["1", "1"]):
            with self.subTest(marcador=valor):
                datos = QueryDict(mutable=True)
                for nombre, respuesta in self.respuestas(antecedentes_infecciosos="").items():
                    datos.setlist(nombre, respuesta if isinstance(respuesta, list) else [respuesta])
                datos.setlist("no_aplica_antecedentes_infecciosos", valor if isinstance(valor, list) else [valor])
                form = self.formulario(datos)
                self.assertFalse(form.is_valid())
                self.assertIn("antecedentes_infecciosos", form.errors)

    def test_marcador_invalido_tampoco_se_ignora_si_llega_un_texto_positivo(self):
        form = self.formulario(self.respuestas(
            antecedentes_infecciosos="COVID-19 en 2021", no_aplica_antecedentes_infecciosos="true",
        ))
        self.assertFalse(form.is_valid())
        self.assertIn("antecedentes_infecciosos", form.errors)

    def test_historico_no_aplica_no_se_reinterpreta_como_negativa_del_paciente(self):
        original = {"formulario_general": {"antecedentes_infecciosos": "No aplica"}, "otra_clave": "Conservar"}
        self.caso.preconsulta.datos_generales = deepcopy(original)
        self.caso.preconsulta.antecedentes_infecciosos = "No aplica"
        form = self.formulario(sin_datos_previos=False)
        self.assertNotEqual(
            form.fields["antecedentes_infecciosos"].widget.attrs.get("data-no-aplica-selected"), "true",
        )
        self.assertEqual(self.caso.preconsulta.datos_generales, original)
        self.assertEqual(self.caso.preconsulta.antecedentes_infecciosos, "No aplica")

    def test_negativas_antiguas_no_aparecen_preseleccionadas_como_declaracion_nueva(self):
        self.caso.preconsulta.datos_generales = {
            "formulario_general": {
                "diagnostico_medico": "no", "alergias_respuesta": "no", "medicamentos_respuesta": "no",
                "consumo_riesgo": ["no"], "dieta": ["no"], "ejercicio": ["no"],
                "antecedentes_familiares": ["no_aplica"], "riesgo_tromboembolico": ["ninguno"],
                "evaluacion_psicologica": ["ninguna"],
            },
        }
        form = self.formulario(sin_datos_previos=False)
        for campo in (
            "diagnostico_medico", "alergias_respuesta", "medicamentos_respuesta", "consumo_riesgo",
            "dieta", "ejercicio", "antecedentes_familiares", "riesgo_tromboembolico", "evaluacion_psicologica",
        ):
            with self.subTest(pregunta=campo):
                self.assertIn(form[campo].value(), (None, "", []))

    def test_alternativas_negativas_tienen_textos_segun_la_pregunta(self):
        form = self.formulario()
        for campo, fragmento in (
            ("diagnostico_medico", "diagnostic"), ("alergias_respuesta", "alerg"),
            ("medicamentos_respuesta", "medicamento"), ("antecedentes_hospitalarios", "hospital"),
            ("quirurgicos_operado", "cirug"), ("consumo_riesgo", "consum"),
            ("antecedentes_familiares", "familiar"),
        ):
            with self.subTest(pregunta=campo):
                opciones = dict(form.fields[campo].choices)
                valor_negativo = "no_aplica" if campo == "antecedentes_familiares" else "no"
                etiqueta = opciones[valor_negativo]
                self.assertIn(fragmento, etiqueta.lower())
                self.assertNotIn("me niego", etiqueta.lower())
                self.assertNotIn("no aplica", etiqueta.lower())

    def test_la_historia_masculina_no_exige_respuestas_ginecologicas(self):
        self.caso.paciente.sexo = "masculino"
        datos = {nombre: valor for nombre, valor in self.respuestas().items() if not nombre.startswith("gine_")}
        form = self.formulario(datos)
        self.assertTrue(form.is_valid(), form.errors.as_json())
        declaradas = form.datos_generales_limpios()["negativas_explicitas"]["respuestas"]
        self.assertFalse(any(nombre.startswith("gine_") for nombre in declaradas))

    def test_la_historia_femenina_debe_responder_las_preguntas_ginecologicas_visibles(self):
        for campo in ("gine_menarca", "gine_gestas", "gine_partos", "gine_cesareas", "gine_abortos", "gine_ultima_menstruacion"):
            with self.subTest(pregunta=campo):
                datos = self.respuestas(**{campo: ""})
                form = self.formulario(datos)
                self.assertFalse(form.is_valid())
                self.assertIn(campo, form.errors)

    def test_formulario_interno_conserva_el_contrato_anterior(self):
        form = self.caso.formulario(
            self.respuestas(antecedentes_infecciosos="", no_aplica_antecedentes_infecciosos="1"),
            sin_datos_previos=True,
        )
        self.assertTrue(form.is_valid(), form.errors.as_json())
        self.assertEqual(form.cleaned_data["antecedentes_infecciosos"], "No aplica")

    def test_el_flujo_corto_de_tres_pasos_no_exige_campos_clinicos(self):
        valores = {
            "nombres": "Ana", "apellidos": "Pérez", "identidad": self.caso.paciente.identidad,
            "fecha_nacimiento": "1995-01-01", "sexo": "femenino", "estado_civil": "soltero",
            "telefono_codigo_area": "504", "telefono": "99999999", "informante": "yo_mismo",
            "referido_por": "facebook", "motivo_categoria": ["no_aplica"],
        }
        form = PreconsultaClinicaPublicaForm(
            valores, instance=self.caso.preconsulta, paciente=self.caso.paciente, empresa=self.caso.empresa,
            modo_basico_paciente_nuevo=True, negativas_explicitas=True,
        )
        with patch("clinica.forms.Paciente.objects.filter") as pacientes:
            pacientes.return_value.exclude.return_value.exists.return_value = False
            self.assertTrue(form.is_valid(), form.errors.as_json())
        self.assertNotIn("antecedentes_infecciosos", form.fields)
        html = render_to_string("clinica/preconsulta_publica.html", {
            "form": form, "preconsulta": self.caso.preconsulta, "flujo_paciente_nuevo_corto": True,
        })
        self.assertNotIn('name="antecedentes_infecciosos"', html)

    def ejecutar_controles_js(self):
        form = self.formulario()
        html = render_to_string("clinica/preconsulta_publica.html", {
            "form": form, "preconsulta": self.caso.preconsulta, "omitir_pasos_iniciales": True,
        })

        class Controles(HTMLParser):
            atributos = None

            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if tag in {"input", "textarea"} and attrs.get("name") == "antecedentes_infecciosos":
                    self.atributos = attrs

        controles = Controles()
        controles.feed(html)
        funciones = "\n".join(
            html[html.index("    function " + inicio):html.index("    function " + fin)]
            for inicio, fin in (
                ("serializableFields", "saveDraft"),
                ("saveDraft", "restoreDraft"),
                ("restoreDraft", "refreshChecks"),
                ("isNoAplicaOption", "toggleProcedureGroups"),
                ("installNoAplicaControls", "show("),
            )
        )
        programa = r"""
const explicitClinicalAnswers=true;
const attrs=ATRIBUTOS;
const classList={contains(){return false},add(){},toggle(){}};
const target={type:'textarea',name:attrs.name,value:'Detalle escrito antes de la negativa',hidden:false,disabled:false,
    required:false,getAttribute(name){return attrs[name]??null},dispatchEvent(){},
    closest(selector){return selector==='.field'?field:null}};
let checkbox,labelNoApply;
const form={elements:[target],querySelector(){return null}};
const draftKey='qa_negativa_explicita',draftMaxAge=1000*60*60*48,storage=new Map();
const localStorage={setItem(k,v){storage.set(k,v)},getItem(k){return storage.get(k)},removeItem(k){storage.delete(k)}};
const field={classList,closest(){return null},querySelector(selector){
    if(selector==='[data-no-apply-for]')return checkbox;
    if(selector==='[data-no-apply-for]:checked')return checkbox?.checked?checkbox:null;
    return target;
},appendChild(label){
    labelNoApply=label;
    const name=label.innerHTML.match(/\bname="([^"]*)"/),value=label.innerHTML.match(/\bvalue="([^"]*)"/);
    checkbox={type:'checkbox',name:name?name[1]:'',value:value?value[1]:'on',checked:false,disabled:false,
        dataset:{noApplyFor:target.name},getAttribute(){return null},
        closest(selector){return selector==='.field'?field:label}};
    form.elements.push(checkbox);
}};
const document={querySelectorAll(){return [field]},createElement(){return {classList,innerHTML:'',querySelector(){return checkbox}}}};
const CSS={escape(value){return value}};
class Event{constructor(type,options){this.type=type}}
FUNCIONES
installNoAplicaControls();
const initial={checked:checkbox.checked,label:labelNoApply.innerHTML};
checkbox.checked=true;
updateNoApply(checkbox);
const marked={hidden:target.hidden,disabled:target.disabled,text:target.value};
const payload=()=>serializableFields().filter(i=>!i.disabled&&(i.type!=='checkbox'||i.checked)).map(i=>[i.name,i.value]);
const markedPayload=payload();
saveDraft();
const draft=JSON.parse(localStorage.getItem(draftKey));
checkbox.checked=false;target.value='';target.hidden=false;target.disabled=false;
restoreDraft();updateNoApply(checkbox);
const restored={checked:checkbox.checked,hidden:target.hidden,disabled:target.disabled,payload:payload()};
checkbox.checked=false;updateNoApply(checkbox);
const cleared={hidden:target.hidden,disabled:target.disabled};
storage.set(draftKey,JSON.stringify({savedAt:Date.now(),fields:{[checkbox.name]:['1'],[target.name]:'No aplica'}}));
restoreDraft();updateNoApply(checkbox);
const oldDraft={checked:checkbox.checked,hidden:target.hidden};
const labelFor=text=>({hidden:false,textContent:text,classList,toggleAttribute(name,value){if(name==='hidden')this.hidden=value}});
const positiveLabel=labelFor('Diabetes'),negativeLabel=labelFor('Niego antecedentes familiares');
let group;
const inputFor=(value,label)=>({type:'checkbox',name:'antecedentes_familiares',value,checked:false,disabled:false,
    getAttribute(name){return name==='data-negativa-value'?'no_aplica':null},
    closest(selector){return selector==='label'?label:selector==='.check-grid,.radio-row'?group:null}});
const positive=inputFor('diabetes',positiveLabel),negative=inputFor('no_aplica',negativeLabel),inputs=[positive,negative];
group={hidden:false,closest(){return null},matches(){return true},querySelectorAll(selector){
    return selector==='.check-grid,.radio-row,.procedure-group'?[]:inputs;
}};
positive.checked=true;negative.checked=true;normalizeNoAplicaGroup(negative);
const lockedGroup={hidden:positiveLabel.hidden,disabled:positive.disabled,positive:positive.checked,negative:negative.checked};
negative.checked=false;applyNoAplicaLocks(group);
const unlockedGroup={hidden:positiveLabel.hidden,disabled:positive.disabled};
process.stdout.write(JSON.stringify({initial,marked,markedPayload,draft,restored,cleared,oldDraft,lockedGroup,unlockedGroup}));
""".replace("FUNCIONES", funciones).replace("ATRIBUTOS", json.dumps(controles.atributos))
        resultado = subprocess.run(
            [shutil.which("node"), "-e", programa], capture_output=True, text=True, encoding="utf-8", check=True,
        )
        return json.loads(resultado.stdout)

    @skipUnless(shutil.which("node"), "Node permite ejecutar los controles del formulario real")
    def test_los_controles_ocultan_y_restauran_sin_perder_el_marcador_del_borrador(self):
        resultado = self.ejecutar_controles_js()
        self.assertFalse(resultado["initial"]["checked"])
        self.assertIn("Niego", resultado["initial"]["label"])
        self.assertEqual(resultado["marked"], {"hidden": True, "disabled": True, "text": ""})
        self.assertEqual(resultado["markedPayload"], [["no_aplica_antecedentes_infecciosos", "1"]])
        self.assertEqual(resultado["draft"]["declarationVersion"], "1")
        self.assertEqual(resultado["restored"], {
            "checked": True, "hidden": True, "disabled": True, "payload": resultado["markedPayload"],
        })
        self.assertEqual(resultado["cleared"], {"hidden": False, "disabled": False})
        self.assertEqual(resultado["oldDraft"], {"checked": False, "hidden": False})
        self.assertEqual(resultado["lockedGroup"], {"hidden": True, "disabled": True, "positive": False, "negative": True})
        self.assertEqual(resultado["unlockedGroup"], {"hidden": False, "disabled": False})
