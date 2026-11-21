from types import SimpleNamespace

from django.core.exceptions import PermissionDenied
from django.template.loader import render_to_string
from django.test import SimpleTestCase
from django.utils import timezone

from .mobile_preconsultas import _secciones_seguras
from .models import CitaClinica, PreconsultaClinica
from .views import _secciones_preconsulta
from .preconsulta_respuestas import registro_inicial_sin_historia


class PreconsultaRespuestasTests(SimpleTestCase):
    def preconsulta(self):
        return PreconsultaClinica(
            id=5, empresa_id=2, paciente_id=3, estado="completada", fecha_completada=timezone.now(),
            motivo_consulta="Motivo del paciente QA", funciones_organicas="alterada",
            funciones_detalle="Detalle de funciones QA", revision_sistemas="alterada",
            revision_sistemas_detalle="Revisión por sistemas QA", antecedentes_hospitalarios=True,
            antecedentes_hospitalarios_detalle="Hospitalización anterior QA",
            antecedentes_personales_detalle="Antecedentes personales QA",
            medicamentos_habituales_detalle="Medicamento habitual QA", antecedentes_familiares_detalle="Antecedente familiar QA",
            dieta="Dieta conservada QA", ejercicio="Ejercicio conservado QA", habitos="Otros hábitos QA",
            alergias="Alergia conservada QA", consentimiento_datos=True,
            datos_generales={
                "nombres": "Nombre recibido QA", "apellidos": "Apellido recibido QA", "telefono": "Telefono recibido QA",
                "contacto_emergencia_completo": "Contacto recibido QA", "referido_por": "referencia",
                "referido_por_detalle": "Referencia recibida QA", "unused_private": {"secreto": "NO MOSTRAR"},
                "formulario_general": {
                    "funcion_apetito": "normal", "examen_peso": "71.5", "examen_talla": "168",
                    "examen_imc": "25.3", "examen_pa": "120/80", "examen_fc": "72", "examen_sato2": "98",
                    "gine_gestas": 0,
                },
            },
        )

    def test_lectura_web_y_app_conserva_respuestas_del_modelo_y_snapshot(self):
        preconsulta = self.preconsulta()
        web = _secciones_preconsulta(preconsulta)
        app = _secciones_seguras(preconsulta, _secciones_preconsulta)
        self.assertEqual(app, web)
        valores = [
            valor
            for seccion in web for campo in seccion["campos"]
            for valor in (campo["value"] if isinstance(campo["value"], list) else [campo["value"]])
        ]
        for respuesta in (
            preconsulta.motivo_consulta, preconsulta.funciones_detalle, preconsulta.revision_sistemas_detalle,
            preconsulta.antecedentes_hospitalarios_detalle, preconsulta.antecedentes_personales_detalle,
            preconsulta.medicamentos_habituales_detalle, preconsulta.antecedentes_familiares_detalle,
            preconsulta.dieta, preconsulta.ejercicio, preconsulta.habitos, preconsulta.alergias,
            "Nombre recibido QA", "Apellido recibido QA", "Telefono recibido QA", "Contacto recibido QA",
            "Referencia recibida QA", "71.5", "168", "25.3", "120/80", "72", "98", 0,
        ):
            self.assertIn(respuesta, valores)
        self.assertNotIn("NO MOSTRAR", str(web))
        motivos = next(seccion for seccion in web if seccion["clave"] == "motivo_consulta")
        self.assertEqual(motivos["campos"][0]["label"], "Motivo de Consulta")
        self.assertEqual(motivos["campos"][0]["value"], preconsulta.motivo_consulta)

    def test_snapshot_de_datos_generales_respeta_el_alcance_de_lectura(self):
        preconsulta = self.preconsulta()
        callback = lambda registro: _secciones_preconsulta(registro, incluir_datos_generales=False)
        web, app = callback(preconsulta), _secciones_seguras(preconsulta, callback)
        self.assertEqual(app, web)
        self.assertNotIn("Telefono recibido QA", str(web))
        self.assertNotIn("Contacto recibido QA", str(web))
        self.assertIn(preconsulta.motivo_consulta, str(web))
        self.assertIn(preconsulta.alergias, str(web))

    def test_respuestas_web_identifican_cita_y_no_inventan_un_vinculo(self):
        preconsulta = self.preconsulta()
        contexto = {
            "empresa": SimpleNamespace(slug="hospital_mia"), "paciente": SimpleNamespace(id=3),
            "bloques": [{"preconsulta": preconsulta, "secciones": _secciones_preconsulta(preconsulta)}],
        }
        sin_cita = render_to_string("clinica/includes/respuestas_preconsulta.html", contexto)
        self.assertIn("Cita vinculada · Sin registro", sin_cita)
        preconsulta.cita = CitaClinica(id=11, empresa_id=2, paciente_id=3, fecha_hora=timezone.now())
        con_cita = render_to_string("clinica/includes/respuestas_preconsulta.html", contexto)
        self.assertIn("Cita 11", con_cita)
        self.assertNotIn("Cita vinculada · Sin registro", con_cita)

    def test_modelo_sin_respuestas_no_inventa_valores_clinicos(self):
        self.assertEqual(_secciones_preconsulta(PreconsultaClinica()), [])

    def test_datos_generales_historicos_separados_tienen_etiquetas(self):
        preconsulta = PreconsultaClinica(datos_generales={"primer_nombre": "Nombre histórico QA", "telefono_emergencia": "Teléfono histórico QA"})
        seccion = next(item for item in _secciones_preconsulta(preconsulta) if item["clave"] == "datos_generales")
        self.assertEqual([campo["label"] for campo in seccion["campos"]], ["Primer nombre", "Telefono emergencia"])

    def test_app_no_interpreta_contactos_fuera_del_permiso_de_datos_generales(self):
        preconsulta = self.preconsulta()
        preconsulta.datos_generales["telefono"] = {"privado": "No exportar"}
        callback = lambda registro: _secciones_preconsulta(registro, incluir_datos_generales=False)
        secciones = _secciones_seguras(preconsulta, callback, incluir_datos_generales=False)
        self.assertNotIn("No exportar", str(secciones))
        with self.assertRaises(PermissionDenied):
            _secciones_seguras(preconsulta, _secciones_preconsulta)

    def test_lectores_conservan_estado_historico_y_contacto_declarado(self):
        preconsulta = self.preconsulta()
        preconsulta.datos_generales["redes_sociales"] = "Contacto social declarado QA"
        preconsulta.datos_generales["formulario_general"]["funciones_organicas_historico"] = "normal"
        web = _secciones_preconsulta(preconsulta)
        self.assertEqual(web, _secciones_seguras(preconsulta, _secciones_preconsulta))
        historico = next(campo for seccion in web for campo in seccion["campos"] if campo["label"] == "Estado histórico de funciones orgánicas")
        self.assertEqual(historico["value"], ["Normal"])
        self.assertIn("Contacto social declarado QA", str(web))
        limitado = _secciones_preconsulta(preconsulta, incluir_datos_generales=False)
        self.assertNotIn("Contacto social declarado QA", str(limitado))

    def test_registro_inicial_no_se_confunde_con_historia_clinica_respondida(self):
        preconsulta = PreconsultaClinica(datos_generales={
            "formulario_general_pendiente_doctor": True,
            "formulario_general": {"pendiente_doctor_desde_paso": 4, "motivo_categoria": ["cirugia_facial"]},
        })
        self.assertTrue(registro_inicial_sin_historia(preconsulta))
        preconsulta.datos_generales["formulario_general"]["gine_gestas"] = 0
        self.assertFalse(registro_inicial_sin_historia(preconsulta))
        preconsulta.datos_generales["formulario_general"].pop("gine_gestas")
        preconsulta.funciones_detalle = "Respuesta clínica recibida QA"
        self.assertFalse(registro_inicial_sin_historia(preconsulta))
