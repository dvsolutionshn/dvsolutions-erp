from datetime import date
from html.parser import HTMLParser
import json
import re
import shutil
import subprocess
from unittest import skipUnless
from unittest.mock import patch

from django.template.loader import render_to_string
from django.test import SimpleTestCase
from django.utils import timezone

from core.models import Empresa

from .forms import PreconsultaClinicaPublicaForm
from .models import Paciente, PreconsultaClinica


class FormularioHistoriaPacienteExistenteTests(SimpleTestCase):
    def setUp(self):
        self.empresa = Empresa(id=1, nombre="Clínica QA", slug="hospital_mia", tipo_solucion="clinica")
        self.paciente = Paciente(
            id=1, empresa=self.empresa, nombre="Paciente QA", identidad="0801199500001",
            fecha_nacimiento=date(1995, 1, 1), sexo="femenino",
        )
        self.preconsulta = PreconsultaClinica(
            empresa=self.empresa, paciente=self.paciente,
            fecha_expiracion=timezone.now() + timezone.timedelta(days=7),
        )

    def formulario(self, datos=None, **kwargs):
        return PreconsultaClinicaPublicaForm(
            datos, instance=self.preconsulta, paciente=self.paciente, empresa=self.empresa,
            formulario_paciente_existente=True, **kwargs,
        )

    def respuestas(self):
        return {
            "motivo_consulta": "Dolor de espalda antes de mi cita",
            "funcion_apetito": "normal", "funcion_sueno": "alterada", "funcion_sed": "normal",
            "funcion_miccion": "normal", "funcion_evacuaciones": "normal",
            "funciones_detalle": "Me despierto tres veces por la noche",
            "diagnostico_medico": "si", "diagnostico_medico_detalle": "Asma desde la adolescencia",
            "alergias_respuesta": "si", "alergias_detalle": "Alergia a penicilina",
            "medicamentos_respuesta": "si", "medicamentos_detalle": "Inhalador de rescate",
            "antecedentes_hospitalarios": ["si"], "antecedentes_hospitalarios_detalle": "Hospitalización respiratoria en 2020",
            "quirurgicos_operado": ["si"], "quirurgicos_detalle": "Apendicectomía en 2018",
            "antecedentes_familiares": ["diabetes"], "antecedentes_familiares_detalle": "Madre con diabetes",
            "consumo_riesgo": ["si"], "consumo_riesgo_detalle": "Tabaco ocasional",
            "dieta": ["si"], "ejercicio": ["si"], "antecedentes_infecciosos": "COVID-19 en 2021",
            "gine_menarca": "12", "gine_gestas": "2", "gine_partos": "1", "gine_cesareas": "1",
            "gine_abortos": "0", "gine_ultima_menstruacion": "2026-11-01", "gine_embarazada": ["no"],
            "gine_lactancia": ["no"], "gine_mamografia": ["si"], "gine_mamografia_fecha": "2026-10-01",
            "decision_cirugia": ["usted"], "decision_cirugia_otros": "Conversado con mi médica",
            "evaluacion_psicologica": ["otros"], "evaluacion_psicologica_detalle": "Apoyo emocional en curso",
            "consentimiento_datos": "on",
        }

    def test_historia_existente_empieza_en_motivo_y_conserva_todos_los_pasos(self):
        form = self.formulario(sin_datos_previos=True)
        html = render_to_string("clinica/preconsulta_publica.html", {
            "form": form, "preconsulta": self.preconsulta, "omitir_pasos_iniciales": True,
            "paso_numero_inicial": 4,
        })
        self.assertLess(html.index("<h2>Motivo de Consulta</h2>"), html.index("<h2>Funciones orgánicas generales</h2>"))
        self.assertEqual(html.count('name="motivo_consulta"'), 1)
        self.assertIn('data-history-reason-step', html)
        self.assertIn('Paso 1 de 7', html)
        self.assertNotIn('<h2>Datos generales</h2>', html)
        self.assertIn('const stepNumberOffset=existingPatientHistory?0:', html)
        self.assertIn('body.set("evento",event)', html)
        self.assertNotIn('Math.min(3,Number(step)', html)
        self.assertTrue(form.fields["identidad"].disabled)
        self.assertEqual(form["sexo"].value(), "femenino")
        self.assertNotRegex(html, r'<(?:select|input)\b[^>]*\bname="sexo"')
        self.assertIn('data-optional-history-section', html)
        self.assertIn('Puede continuar sin responder este paso', html)

    def test_conserva_respuestas_clinicas_y_ginecologicas_de_enlace_delegado(self):
        form = self.formulario(self.respuestas(), sin_datos_previos=True)
        self.assertTrue(form.is_valid(), form.errors.as_json())
        preconsulta = form.save(commit=False)
        datos = form.datos_generales_limpios()["formulario_general"]
        self.assertEqual(preconsulta.motivo_consulta, self.respuestas()["motivo_consulta"])
        self.assertEqual(preconsulta.funciones_detalle, self.respuestas()["funciones_detalle"])
        self.assertEqual(preconsulta.alergias, "Alergia a penicilina")
        self.assertEqual(preconsulta.medicamentos_habituales_detalle, "Inhalador de rescate")
        self.assertEqual(preconsulta.antecedentes_familiares, ["diabetes"])
        for campo in (
            "diagnostico_medico_detalle", "alergias_detalle", "medicamentos_detalle", "quirurgicos_detalle",
            "consumo_riesgo_detalle", "gine_menarca", "gine_gestas", "gine_partos", "gine_cesareas",
            "gine_abortos", "gine_ultima_menstruacion", "gine_embarazada", "gine_lactancia",
            "gine_mamografia", "decision_cirugia", "decision_cirugia_otros", "evaluacion_psicologica_detalle",
        ):
            with self.subTest(campo=campo):
                self.assertEqual(datos[campo], self.respuestas()[campo])
        self.assertEqual(datos["gine_mamografia_fecha"], "2026-10-01")

    def test_error_de_validacion_mantiene_el_motivo_y_las_respuestas(self):
        datos = self.respuestas()
        datos["alergias_detalle"] = ""
        form = self.formulario(datos, sin_datos_previos=True)
        self.assertFalse(form.is_valid())
        self.assertIn("alergias_detalle", form.errors)
        html = render_to_string("clinica/preconsulta_publica.html", {
            "form": form, "preconsulta": self.preconsulta, "omitir_pasos_iniciales": True,
        })
        self.assertIn(datos["motivo_consulta"], html)
        self.assertIn(datos["funciones_detalle"], html)
        self.assertIn(datos["medicamentos_detalle"], html)
        self.assertIn("No pudimos enviar todavía.", html)

    def test_respuestas_de_pagina_anterior_se_validan_y_conservan_sin_cambiar_el_paciente(self):
        anteriores = {
            **self.respuestas(), "nombres": "Nombre actualizado", "apellidos": "Apellido actualizado",
            "identidad": self.paciente.identidad, "fecha_nacimiento": "1995-01-01",
            "sexo": "masculino", "telefono": "99991111", "telefono_codigo_area": "504",
            "informante": "yo_mismo", "referido_por": "no_aplica", "estado_civil": "soltero",
        }
        invalido = self.formulario({**anteriores, "identidad": "0801199900099"}, sin_datos_previos=True)
        self.assertFalse(invalido.is_valid())
        self.assertIn("identidad", invalido.errors)

        form = self.formulario(anteriores, sin_datos_previos=True)
        self.assertTrue(form.is_valid(), form.errors.as_json())
        datos = form.datos_generales_limpios()
        self.assertEqual(datos["nombres"], "Nombre actualizado")
        self.assertEqual(datos["apellidos"], "Apellido actualizado")
        self.assertEqual(datos["identidad"], self.paciente.identidad)
        self.assertNotIn("sexo", datos)
        self.assertTrue(form.fields["sexo"].disabled)
        self.assertTrue(form.fields["correo"].disabled)
        self.assertEqual(self.paciente.nombre, "Paciente QA")

    def test_error_de_pagina_anterior_muestra_el_dato_recibido_y_permite_reintento(self):
        datos = {
            **self.respuestas(), "identidad": "0801199900099", "nombres": "Nombre digitado",
            "motivo_categoria": ["cirugia_facial"], "procedimientos_interes": ["rinoplastia"],
        }
        form = self.formulario(datos, sin_datos_previos=True)
        self.assertFalse(form.is_valid())
        correccion = form.campos_administrativos_reintento
        self.assertEqual([campo.name for campo in correccion["errores"]], ["identidad"])
        html = render_to_string("clinica/preconsulta_publica.html", {
            "form": form, "preconsulta": self.preconsulta, "omitir_pasos_iniciales": True,
        })
        self.assertIn('data-legacy-correction', html)
        self.assertIn('name="identidad" value="0801199900099"', html)
        self.assertNotIn(self.paciente.identidad, html)
        self.assertIn('data-preserved-legacy', html)
        self.assertIn('type="hidden" name="nombres" value="Nombre digitado"', html)
        self.assertNotRegex(html, r'<(?:select|input)\b[^>]*\bname="sexo"')

        enviados_nuevamente = dict(self.respuestas())
        enviados_nuevamente.update({campo.name: campo.value() for campo in correccion["conservar"]})
        enviados_nuevamente["identidad"] = self.paciente.identidad
        reintento = self.formulario(enviados_nuevamente, sin_datos_previos=True)
        self.assertTrue(reintento.is_valid(), reintento.errors.as_json())
        self.assertEqual(reintento.datos_generales_limpios()["nombres"], "Nombre digitado")
        self.assertEqual(reintento.cleaned_data["motivo_consulta"], datos["motivo_consulta"])

    def test_error_de_componente_oculto_del_nombre_tiene_campo_visible(self):
        datos = {**self.respuestas(), "primer_nombre": "A" * 81}
        form = self.formulario(datos, sin_datos_previos=True)
        self.assertFalse(form.is_valid())
        self.assertIn("primer_nombre", form.errors)
        campos = form.campos_administrativos_reintento["errores"]
        self.assertEqual([campo.name for campo in campos], ["primer_nombre"])
        self.assertFalse(campos[0].is_hidden)
        html = render_to_string("clinica/preconsulta_publica.html", {
            "form": form, "preconsulta": self.preconsulta, "omitir_pasos_iniciales": True,
        })
        self.assertIn('type="text" name="primer_nombre"', html)
        self.assertNotIn('type="hidden" name="primer_nombre"', html)

    def test_sexo_sin_registrar_no_produce_error_de_opcion_oculta(self):
        self.paciente.sexo = "no_indicado"
        form = self.formulario(self.respuestas(), sin_datos_previos=True)
        self.assertTrue(form.is_valid(), form.errors.as_json())
        self.assertEqual(form.cleaned_data["sexo"], "")
        self.assertEqual(form.datos_generales_limpios()["formulario_general"]["gine_menarca"], "12")

    def test_formulario_no_revela_sexo_ni_datos_anteriores_del_paciente(self):
        paginas = []
        for sexo in ("femenino", "masculino", "no_indicado"):
            with self.subTest(sexo=sexo):
                self.paciente.sexo = sexo
                form = self.formulario(sin_datos_previos=True)
                html = render_to_string("clinica/preconsulta_publica.html", {
                    "form": form, "preconsulta": self.preconsulta, "omitir_pasos_iniciales": True,
                })
                self.assertNotRegex(html, r'<(?:select|input)\b[^>]*\bname="sexo"')
                self.assertNotRegex(html, r'<(?:select|input)\b[^>]*\bname="identidad"')
                self.assertNotIn(self.paciente.identidad, html)
                paginas.append(html)
        self.assertEqual(paginas[0], paginas[1])
        self.assertEqual(paginas[1], paginas[2])

    def test_respuestas_del_enlace_no_inyectan_datos_maestros_no_enviados(self):
        self.preconsulta.datos_generales = {"fecha_nacimiento": "1995-01-01", "ocupacion": "Dato histórico"}
        form = self.formulario(self.respuestas())
        with patch("clinica.forms.Paciente.objects.filter") as pacientes:
            pacientes.return_value.exclude.return_value.exists.return_value = False
            self.assertTrue(form.is_valid(), form.errors.as_json())
        datos = form.datos_generales_limpios()
        self.assertEqual(datos["fecha_nacimiento"], "1995-01-01")
        self.assertEqual(datos["ocupacion"], "Dato histórico")
        self.assertNotIn("sexo", datos)
        self.assertNotIn("nombres", datos)
        self.assertNotIn("identidad", datos)

    def test_paso_ginecologico_puede_omitirse_o_contestar_sin_inferir_el_sexo_registrado(self):
        for sexo in ("femenino", "masculino", "no_indicado"):
            with self.subTest(sexo=sexo):
                self.paciente.sexo = sexo
                respuestas = self.respuestas()
                form = self.formulario(respuestas, sin_datos_previos=True)
                self.assertTrue(form.is_valid(), form.errors.as_json())
                datos = form.datos_generales_limpios()["formulario_general"]
                self.assertEqual(datos["gine_menarca"], "12")
                self.assertEqual(datos["gine_mamografia_fecha"], "2026-10-01")
                respuestas_sin_paso = {campo: valor for campo, valor in respuestas.items() if not campo.startswith("gine_")}
                omitido = self.formulario(respuestas_sin_paso, sin_datos_previos=True)
                self.assertTrue(omitido.is_valid(), omitido.errors.as_json())
                self.assertNotIn("gine_menarca", omitido.datos_generales_limpios()["formulario_general"])
                no_aplica = self.formulario({**respuestas, "gine_mamografia": ["no"]}, sin_datos_previos=True)
                self.assertTrue(no_aplica.is_valid(), no_aplica.errors.as_json())
                self.assertNotIn("gine_mamografia_fecha", no_aplica.datos_generales_limpios()["formulario_general"])

    @skipUnless(shutil.which("node"), "Node permite ejecutar la navegación real del formulario")
    def test_decision_es_visible_en_enlace_existente_y_conserva_condicional_del_registro(self):
        html = render_to_string("clinica/preconsulta_publica.html", {
            "form": self.formulario(sin_datos_previos=True), "preconsulta": self.preconsulta,
            "omitir_pasos_iniciales": True,
        })

        class PanelesDecision(HTMLParser):
            def __init__(self):
                super().__init__()
                self.paneles = []
                self.panel = None
                self.profundidad = 0
                self.textarea = None

            def handle_starttag(self, tag, atributos):
                atributos = dict(atributos)
                if tag == "div":
                    if self.panel is not None:
                        self.profundidad += 1
                    elif "decision-surgery-field" in atributos.get("class", "").split():
                        self.panel = []
                        self.paneles.append(self.panel)
                        self.profundidad = 1
                if self.panel is not None and tag in ("input", "textarea", "select"):
                    campo = {
                        "name": atributos.get("name"), "type": atributos.get("type", tag),
                        "value": atributos.get("value", ""), "checked": "checked" in atributos,
                        "disabled": "disabled" in atributos,
                    }
                    self.panel.append(campo)
                    if tag == "textarea":
                        self.textarea = campo

            def handle_data(self, datos):
                if self.textarea is not None:
                    self.textarea["value"] += datos

            def handle_endtag(self, tag):
                if tag == "textarea":
                    self.textarea = None
                if tag == "div" and self.panel is not None:
                    self.profundidad -= 1
                    if self.profundidad == 0:
                        self.panel = None

        controles = PanelesDecision()
        controles.feed(html)
        self.assertEqual(len(controles.paneles), 2)
        self.assertEqual(
            {campo["name"] for panel in controles.paneles for campo in panel},
            {"decision_cirugia", "decision_cirugia_otros"},
        )
        funciones = "\n".join(
            re.search(rf"function {nombre}\(\)\{{[^\n]+", html).group(0)
            for nombre in ("selectedMotivos", "toggleDecisionCirugia")
        )
        programa = """
const original = PANEL;
const resultados = [];
for (const caso of [
    {existente:true,categoria:'no_aplica',contestado:false},
    {existente:true,categoria:'no_aplica',contestado:true},
    {existente:false,categoria:'no_aplica',contestado:false},
    {existente:false,categoria:'cirugia_facial',contestado:false},
]) {
    const existingPatientHistory=caso.existente;
    const paneles=structuredClone(original).map(campos=>({hidden:false,campos,querySelectorAll(){return this.campos;}}));
    const campos=paneles.flatMap(panel=>panel.campos);
    if(caso.contestado){campos.find(c=>c.name==='decision_cirugia'&&c.value==='usted').checked=true;campos.find(c=>c.name==='decision_cirugia_otros').value='Conversado con mi médica';}
    const form={querySelectorAll(selector){
        if(selector.startsWith('[data-preserved-legacy]'))return [];
        if(selector==='[name="motivo_categoria"]:checked')return [{value:caso.categoria}];
        return campos.filter(c=>c.name==='decision_cirugia'&&c.checked);
    },querySelector(selector){return this.querySelectorAll(selector)[0]||null;}};
    const document={querySelectorAll(){return paneles;}};
    function refreshChecks(){}
    FUNCIONES
    toggleDecisionCirugia();
    resultados.push({ocultos:paneles.map(p=>p.hidden),deshabilitados:campos.map(c=>c.disabled),seleccion:campos.filter(c=>c.checked).map(c=>c.value),detalle:campos.find(c=>c.name==='decision_cirugia_otros').value});
}
process.stdout.write(JSON.stringify(resultados));
""".replace("PANEL", json.dumps(controles.paneles)).replace("FUNCIONES", funciones)
        ejecutado = subprocess.run(
            [shutil.which("node"), "-e", programa], capture_output=True, text=True, encoding="utf-8", check=True,
        )
        nuevo, contestado, registro_general, registro_cirugia = json.loads(ejecutado.stdout)
        self.assertEqual(nuevo["ocultos"], [False, False])
        self.assertTrue(all(not valor for valor in nuevo["deshabilitados"]))
        self.assertEqual(contestado["seleccion"], ["usted"])
        self.assertEqual(contestado["detalle"], "Conversado con mi médica")
        self.assertEqual(registro_general["ocultos"], [True, True])
        self.assertEqual(registro_cirugia["ocultos"], [False, False])

    def test_decision_del_paciente_existente_puede_contestarse_u_omitirse(self):
        for contestar in (True, False):
            with self.subTest(contestar=contestar):
                datos = self.respuestas()
                if not contestar:
                    datos.pop("decision_cirugia")
                    datos.pop("decision_cirugia_otros")
                form = self.formulario(datos, sin_datos_previos=True)
                self.assertTrue(form.is_valid(), form.errors.as_json())
                recibido = form.datos_generales_limpios()["formulario_general"]
                if contestar:
                    self.assertEqual(recibido["decision_cirugia"], ["usted"])
                    self.assertEqual(recibido["decision_cirugia_otros"], datos["decision_cirugia_otros"])
                else:
                    self.assertNotIn("decision_cirugia", recibido)
                    self.assertNotIn("decision_cirugia_otros", recibido)

    def test_registro_nuevo_conserva_sus_tres_pasos(self):
        form = PreconsultaClinicaPublicaForm(
            instance=self.preconsulta, empresa=self.empresa, modo_basico_paciente_nuevo=True,
        )
        html = render_to_string("clinica/preconsulta_publica.html", {
            "form": form, "preconsulta": self.preconsulta, "flujo_paciente_nuevo_corto": True,
            "registro_nuevo": True,
        })
        self.assertIn('<h2>Datos generales</h2>', html)
        self.assertIn('Paso 1 de 3', html)
        self.assertNotIn('data-history-reason-step', html)
        self.assertNotIn('name="motivo_consulta"', html)
