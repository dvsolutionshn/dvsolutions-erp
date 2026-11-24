"""Las respuestas explícitas «No aplica» también deben viajar al expediente."""

import json
import shutil
import subprocess
from html.parser import HTMLParser
from unittest import skipUnless

from django.http import QueryDict
from django.template.loader import render_to_string
from django.test import SimpleTestCase

from .forms import PreconsultaClinicaPublicaForm
from . import test_preconsulta_paciente_existente as datos_publicos
from .views import _secciones_preconsulta


class NoAplicaRespuestasFormularioTests(SimpleTestCase):
    def setUp(self):
        self.caso = datos_publicos.FormularioHistoriaPacienteExistenteTests()
        self.caso.setUp()

    def ejecutar_control(self, nombre="gine_menarca", *, administrativo=False):
        caso = self.caso
        form = (
            PreconsultaClinicaPublicaForm(instance=caso.preconsulta, empresa=caso.empresa, modo_basico_paciente_nuevo=True)
            if administrativo else caso.formulario(sin_datos_previos=True)
        )
        html = render_to_string("clinica/preconsulta_publica.html", {
            "form": form, "preconsulta": caso.preconsulta,
            "omitir_pasos_iniciales": not administrativo, "flujo_paciente_nuevo_corto": administrativo,
        })
        funciones = "\n".join(
            html[html.index("    function " + inicio):html.index("    function " + fin)]
            for inicio, fin in (
                ("serializableFields", "saveDraft"),
                ("saveDraft", "restoreDraft"),
                ("restoreDraft", "refreshChecks"),
                ("installNoAplicaControls", "isNoApplyChecked"),
                ("updateNoApply", "show("),
            )
        )
        class Controles(HTMLParser):
            atributos = None

            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if tag == "input" and attrs.get("name") == nombre:
                    self.atributos = attrs

        controles = Controles()
        controles.feed(html)
        programa = r"""
const css={contains(){return false},add(){},toggle(){}};
const attrs=ATRIBUTOS;
const target={type:attrs.type||'text',name:attrs.name,value:'12',required:false,disabled:false,getAttribute(name){return attrs[name]},dispatchEvent(){},closest(){return field}};
const form={elements:[target],querySelector(){return null}};
const draftKey='qa_no_aplica',draftMaxAge=1000*60*60*48,storage=new Map();
const localStorage={setItem(k,v){storage.set(k,v)},getItem(k){return storage.get(k)},removeItem(k){storage.delete(k)}};
let checkbox,labelNoApply;
const field={classList:css,querySelector(selector){return selector==='[data-no-apply-for]'?checkbox:target},appendChild(label){
    labelNoApply=label;
    const name=label.innerHTML.match(/\bname="([^"]*)"/);
    const value=label.innerHTML.match(/\bvalue="([^"]*)"/);
    checkbox={type:'checkbox',name:name?name[1]:'',value:value?value[1]:'on',checked:false,disabled:false,dataset:{noApplyFor:target.name},closest(selector){return selector==='.field'?field:label}};
    form.elements.push(checkbox);
}};
const document={querySelectorAll(){return [field]},createElement(){return {classList:css,innerHTML:'',querySelector(){return checkbox}}}};
class Event{constructor(type,options){this.type=type}}
FUNCIONES
installNoAplicaControls();
checkbox.checked=true;
updateNoApply(checkbox);
const payload=()=>serializableFields().filter(i=>!i.disabled&&(i.type!=='checkbox'||i.checked)).map(i=>[i.name,i.value]);
const marcada=payload();
saveDraft();
const borrador=JSON.parse(localStorage.getItem(draftKey));
checkbox.checked=false;
target.value='';
restoreDraft();
const restaurada={marcada:checkbox.checked,payload:payload()};
target.value='14';
clearNoAplicaOnInput(target);
process.stdout.write(JSON.stringify({marcada,borrador,restaurada,reemplazada:payload(),seleccionFinal:checkbox.checked,etiqueta:labelNoApply.innerHTML}));
""".replace("FUNCIONES", funciones).replace("ATRIBUTOS", json.dumps(controles.atributos))
        ejecutado = subprocess.run(
            [shutil.which("node"), "-e", programa], capture_output=True, text=True, encoding="utf-8", check=True,
        )
        return json.loads(ejecutado.stdout)

    def enviar(self, valores):
        caso = self.caso
        respuestas = QueryDict(mutable=True)
        for nombre, valor in caso.respuestas().items():
            respuestas.setlist(nombre, valor if isinstance(valor, list) else [valor])
        for nombre, valor in valores:
            respuestas.setlist(nombre, [valor])
        form = caso.formulario(respuestas, sin_datos_previos=True)
        self.assertTrue(form.is_valid(), form.errors.as_json())
        preconsulta = form.save(commit=False)
        preconsulta.datos_generales = form.datos_generales_limpios()
        return preconsulta

    @skipUnless(shutil.which("node"), "Node permite ejecutar el control real de No aplica")
    def test_no_aplica_ginecologico_viaja_desde_el_control_hasta_el_historial(self):
        resultado = self.ejecutar_control()
        self.assertIn("<span>No aplica</span>", resultado["etiqueta"])
        preconsulta = self.enviar(resultado["marcada"])
        campos = [campo for seccion in _secciones_preconsulta(preconsulta) for campo in seccion["campos"]]
        self.assertTrue(any(campo["label"] == "Menarca" and campo["value"] == "No aplica" for campo in campos),
                        "El paciente marcó No aplica, pero esa respuesta desapareció antes del historial")

    @skipUnless(shutil.which("node"), "Node permite ejecutar el control administrativo")
    def test_control_administrativo_dice_dejar_en_blanco_sin_inventar_respuesta(self):
        resultado = self.ejecutar_control("correo", administrativo=True)
        self.assertIn("<span>Dejar en blanco</span>", resultado["etiqueta"])
        self.assertNotIn("<span>No aplica</span>", resultado["etiqueta"])
        self.assertEqual(resultado["marcada"], [["correo", ""]])
        self.assertNotIn("no_aplica_correo", resultado["borrador"]["fields"])

    @skipUnless(shutil.which("node"), "Node permite ejecutar el control real de No aplica")
    def test_borrador_conserva_la_marca_y_escribir_una_respuesta_la_desmarca(self):
        resultado = self.ejecutar_control()
        self.assertEqual(resultado["borrador"]["fields"]["no_aplica_gine_menarca"], ["1"])
        self.assertTrue(resultado["restaurada"]["marcada"])
        self.assertEqual(resultado["restaurada"]["payload"], resultado["marcada"])
        self.assertFalse(resultado["seleccionFinal"])
        self.assertNotIn("no_aplica_gine_menarca", dict(resultado["reemplazada"]))
        preconsulta = self.enviar(resultado["reemplazada"])
        self.assertEqual(preconsulta.datos_generales["formulario_general"]["gine_menarca"], "14")

    def test_ausencia_de_marca_no_se_convierte_en_una_respuesta(self):
        preconsulta = self.enviar([("gine_menarca", "")])
        self.assertNotIn("gine_menarca", preconsulta.datos_generales["formulario_general"])

    def test_marcadores_no_pueden_cambiar_campos_administrativos_ni_fecha(self):
        preconsulta = self.enviar([
            ("no_aplica_correo", "1"), ("correo", "qa@example.test"),
            ("no_aplica_sexo", "1"), ("no_aplica_identidad", "1"),
            ("no_aplica_gine_mamografia_fecha", "1"),
        ])
        self.assertEqual(preconsulta.datos_generales["correo"], "qa@example.test")
        self.assertEqual(preconsulta.datos_generales["formulario_general"]["gine_mamografia_fecha"], "2026-10-01")
        self.assertEqual(self.caso.paciente.identidad, "0801199500001")
        self.assertNotIn("No aplica", str(preconsulta.datos_generales.get("identidad", "")))

    def test_marca_no_booleana_o_duplicada_no_sustituye_el_texto(self):
        for valor in ("true", "0", "si", ["1", "1"]):
            with self.subTest(valor=valor):
                respuestas = dict(self.caso.respuestas(), no_aplica_gine_menarca=valor)
                form = self.caso.formulario(respuestas, sin_datos_previos=True)
                self.assertTrue(form.is_valid(), form.errors.as_json())
                self.assertEqual(form.datos_generales_limpios()["formulario_general"]["gine_menarca"], "12")
        duplicada = QueryDict(mutable=True)
        for nombre, valor in self.caso.respuestas().items():
            duplicada.setlist(nombre, valor if isinstance(valor, list) else [valor])
        duplicada.setlist("no_aplica_gine_menarca", ["1", "1"])
        form = self.caso.formulario(duplicada, sin_datos_previos=True)
        self.assertTrue(form.is_valid(), form.errors.as_json())
        self.assertEqual(form.datos_generales_limpios()["formulario_general"]["gine_menarca"], "12")

    def test_solo_campos_clinicos_compatibles_reciben_marcadores(self):
        form = self.caso.formulario(sin_datos_previos=True)
        for nombre in ("correo", "telefono", "identidad", "fecha_nacimiento", "gine_mamografia_fecha", "sexo"):
            with self.subTest(nombre=nombre):
                self.assertNotIn("data-no-aplica-name", form.fields[nombre].widget.attrs)
        self.assertEqual(form.fields["gine_menarca"].widget.attrs["data-no-aplica-name"], "no_aplica_gine_menarca")

    def test_reintento_con_error_conserva_marca_explicita_y_prefijo(self):
        respuestas = {"revision-" + nombre: valor for nombre, valor in self.caso.respuestas().items()}
        respuestas.update({"revision-no_aplica_gine_menarca": "1", "revision-gine_menarca": "", "revision-alergias_detalle": ""})
        form = self.caso.formulario(respuestas, sin_datos_previos=True, prefix="revision")
        self.assertFalse(form.is_valid())
        self.assertIn("alergias_detalle", form.errors)
        self.assertEqual(form.fields["gine_menarca"].widget.attrs["data-no-aplica-name"], "revision-no_aplica_gine_menarca")
        self.assertEqual(form.fields["gine_menarca"].widget.attrs["data-no-aplica-selected"], "true")
        respuestas["revision-alergias_detalle"] = "Alergia a penicilina"
        reintento = self.caso.formulario(respuestas, sin_datos_previos=True, prefix="revision")
        self.assertTrue(reintento.is_valid(), reintento.errors.as_json())
        self.assertEqual(reintento.datos_generales_limpios()["formulario_general"]["gine_menarca"], "No aplica")
