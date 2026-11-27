from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.exceptions import PermissionDenied
from django.template.loader import render_to_string
from django.test import SimpleTestCase

from core.models import Empresa

from .mobile_preconsultas import _secciones_seguras
from .models import Paciente, PreconsultaClinica
from .preconsulta_alertas import alerta_condiciones_paciente
from .preconsulta_mejoras_pdf import CONDICIONES_DIAGNOSTICADAS_CHOICES
from .views import _secciones_preconsulta, _secciones_preconsulta_usuario


class LecturaMejorasPdfTests(SimpleTestCase):
    def setUp(self):
        self.empresa = Empresa(id=1, nombre="Clínica QA", slug="hospital_mia")
        self.paciente = Paciente(id=1, empresa=self.empresa, nombre="Paciente QA", sexo="femenino")

    def registro(self, general, **modelo):
        return PreconsultaClinica(
            id=1, empresa=self.empresa, paciente=self.paciente,
            datos_generales={"version_formulario_publico": 2, "formulario_general": general},
            **modelo,
        )

    def test_datos_nuevos_se_leen_igual_en_web_y_app_y_conservan_el_historico(self):
        codigo, condicion = CONDICIONES_DIAGNOSTICADAS_CHOICES[0]
        registro = self.registro({
            "condiciones_diagnosticadas": [codigo],
            "diagnostico_medico": "si",
            "diagnostico_medico_detalle_historico": "Antecedente previo conservado",
            "alergias_respuesta": "si", "alergias_detalle": "Alergia genérica previa",
            "alergias_medicamentos_detalle": "Respuesta combinada anterior",
            "alergias_farmacos_respuesta": "si", "alergias_farmacos_detalle": "Penicilina",
            "alergias_alimentos_respuesta": "si", "alergias_alimentos_detalle": "Maní",
            "funciones_otras_alteraciones": "no", "gine_gestas_respuesta": "no", "gine_gestas": "2",
            "gine_partos_historico": "1", "gine_cesareas_historico": "1", "gine_abortos_historico": "0",
            "antecedentes_infecciosos_respuesta": "no",
            "antecedentes_infecciosos_historico": "Antecedente infeccioso anterior",
            "riesgo_tromboembolico": ["otros"], "riesgo_tromboembolico_otros": "Otro riesgo declarado",
            "riesgo_tromboembolico_otros_historico": "Riesgo histórico",
            "antecedentes_familiares_detalle_historico": "Detalle familiar anterior",
            "evaluacion_psicologica": ["ansiedad"], "decision_cirugia_otros": "Decisión anterior",
        }, alergias="Alergia genérica previa")
        original = deepcopy(registro.datos_generales)
        web = _secciones_preconsulta(registro)
        app = _secciones_seguras(registro, _secciones_preconsulta)
        self.assertEqual(web, app)
        campos = {campo["label"]: campo["value"] for seccion in web for campo in seccion["campos"]}
        self.assertEqual(campos["Condiciones médicas seleccionadas"], [condicion])
        self.assertEqual(campos["Alergias a medicamentos"], ["Si"])
        self.assertEqual(campos["Detalle de alergias a medicamentos"], "Penicilina")
        self.assertEqual(campos["Detalle de alergias a alimentos"], "Maní")
        self.assertEqual(campos["Detalle combinado histórico"], "Respuesta combinada anterior")
        self.assertEqual(campos["Detalle de alergias"], "Alergia genérica previa")
        self.assertEqual(campos["Gestas (respuesta histórica)"], "2")
        self.assertEqual(campos["Ha tenido embarazos"], ["No"])
        self.assertEqual(campos["Partos (respuesta histórica)"], "1")
        self.assertEqual(campos["Cesáreas (respuesta histórica)"], "1")
        self.assertEqual(campos["Abortos (respuesta histórica)"], "0")
        self.assertEqual(campos["Riesgo tromboembólico"], ["Otros"])
        for texto in (
            "Antecedente previo conservado", "Antecedente infeccioso anterior", "Riesgo histórico",
            "Detalle familiar anterior", "Decisión anterior",
        ):
            self.assertIn(texto, str(web))
        self.assertEqual(registro.datos_generales, original)

    def test_campos_sensibles_nuevos_conservan_el_permiso_de_antecedentes(self):
        registro = self.registro({"diagnostico_medico": "si", "condiciones_diagnosticadas": ["vih"]})
        usuario = Mock()
        usuario.tiene_permiso_erp.side_effect = lambda permiso, empresa: permiso == "puede_ver_historia_clinica"
        with patch("clinica.views.rol_clinico_v2", return_value=True), patch(
            "clinica.views.datos_paciente_limitados", return_value=True,
        ), patch("clinica.views._puede_historia_tipo", return_value=False):
            secciones = _secciones_seguras(
                registro, lambda lectura: _secciones_preconsulta_usuario(lectura, usuario, self.empresa),
            )
        self.assertNotIn("VIH", str(secciones))
        self.assertNotIn("antecedentes", [seccion["clave"] for seccion in secciones])

    def test_app_rechaza_estructura_anidada_en_nuevas_condiciones(self):
        registro = self.registro({"condiciones_diagnosticadas": [{"codigo": "vih"}]})
        with self.assertRaises(PermissionDenied):
            _secciones_seguras(registro, _secciones_preconsulta)

    def test_app_transporta_declaraciones_explicitas_de_las_preguntas_nuevas(self):
        declaraciones = {
            "alergias_farmacos_respuesta": "Niego tener alergias a medicamentos",
            "alergias_alimentos_respuesta": "Niego tener alergias a alimentos",
            "funciones_otras_alteraciones": "Niego otras alteraciones",
            "antecedentes_infecciosos_respuesta": "Niego antecedentes de enfermedades infecciosas",
            "gine_gestas_respuesta": "Niego embarazos previos",
        }
        registro = self.registro({nombre: "no" for nombre in declaraciones})
        registro.datos_generales["negativas_explicitas"] = {
            "version": 1,
            "respuestas": {nombre: {"valor": "no", "texto": texto} for nombre, texto in declaraciones.items()},
        }
        web = _secciones_preconsulta(registro)
        app = _secciones_seguras(registro, _secciones_preconsulta)
        self.assertEqual(web, app)
        for texto in declaraciones.values():
            self.assertIn(texto, str(app))


class AlertaCondicionesPdfTests(SimpleTestCase):
    def setUp(self):
        self.empresa = SimpleNamespace(pk=1)
        self.paciente = SimpleNamespace(pk=2, empresa_id=1)
        self.usuario = Mock()
        self.usuario.puede_acceder_empresa.return_value = True
        self.usuario.tiene_permiso_erp.return_value = True
        self.roles = patch("clinica.preconsulta_alertas.rol_clinico_v2", return_value=True)
        self.roles.start()
        self.addCleanup(self.roles.stop)

    def registro(self, general):
        return SimpleNamespace(pk=3, datos_generales={"formulario_general": general}, fecha_completada=None)

    def alerta(self, registros):
        with patch("clinica.preconsulta_alertas.PreconsultaClinica.objects.filter") as consulta:
            consulta.return_value.only.return_value.order_by.return_value.iterator.return_value = iter(registros)
            alerta = alerta_condiciones_paciente(self.usuario, self.empresa, self.paciente)
            consulta.assert_called_once_with(empresa=self.empresa, paciente=self.paciente, estado="completada")
            return alerta

    def test_alerta_muestra_solo_condiciones_de_ultima_respuesta_y_escapa_html(self):
        codigo, etiqueta = CONDICIONES_DIAGNOSTICADAS_CHOICES[0]
        alerta = self.alerta([self.registro({
            "diagnostico_medico": "si", "condiciones_diagnosticadas": [codigo, codigo, "otras"],
            "diagnostico_medico_detalle": "<script>alert('x')</script>",
        })])
        self.assertEqual(alerta["condiciones"], [etiqueta])
        html = render_to_string("clinica/includes/alerta_condiciones_paciente.html", {"alerta_condiciones": alerta})
        self.assertIn("Alerta médica", html)
        self.assertIn(etiqueta, html)
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.usuario.tiene_permiso_erp.assert_called_once_with("puede_ver_antecedentes_clinicos", self.empresa)

    def test_negativa_mas_reciente_retira_alerta_sin_borrar_historia_anterior(self):
        anterior = self.registro({"diagnostico_medico": "si", "condiciones_diagnosticadas": ["vih"]})
        self.assertIsNone(self.alerta([self.registro({"diagnostico_medico": "no"}), anterior]))
        self.assertEqual(anterior.datos_generales["formulario_general"]["condiciones_diagnosticadas"], ["vih"])

    def test_no_infiere_lista_nueva_de_un_diagnostico_historico(self):
        self.assertIsNone(self.alerta([self.registro({"diagnostico_medico": "si", "diagnostico_medico_detalle": "Histórico"})]))

    def test_ninguna_de_las_listadas_no_se_confunde_con_ausencia_de_diagnostico(self):
        alerta = self.alerta([self.registro({
            "diagnostico_medico": "si", "condiciones_diagnosticadas": ["ninguna_anteriores"],
            "diagnostico_medico_detalle": "Condición fuera del listado",
        })])
        self.assertEqual(alerta["condiciones"], [])
        self.assertEqual(alerta["detalle"], "Condición fuera del listado")

    def test_sin_permiso_o_con_empresa_incorrecta_no_consulta_diagnosticos(self):
        with patch("clinica.preconsulta_alertas.PreconsultaClinica.objects.filter") as consulta:
            self.usuario.tiene_permiso_erp.return_value = False
            self.assertIsNone(alerta_condiciones_paciente(self.usuario, self.empresa, self.paciente))
            self.usuario.tiene_permiso_erp.return_value = True
            self.paciente.empresa_id = 9
            self.assertIsNone(alerta_condiciones_paciente(self.usuario, self.empresa, self.paciente))
            self.paciente.empresa_id = 1
            self.usuario.puede_acceder_empresa.return_value = False
            self.assertIsNone(alerta_condiciones_paciente(self.usuario, self.empresa, self.paciente))
            consulta.assert_not_called()
