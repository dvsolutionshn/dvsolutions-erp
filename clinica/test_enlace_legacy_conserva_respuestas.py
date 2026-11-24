"""Los enlaces ya emitidos conservan las respuestas que no renderiza la UI actual."""

from copy import deepcopy
from urllib.parse import urlsplit

from django.test import Client, TestCase
from django.urls import reverse

from . import test_completar_conserva_respuestas as datos_internos
from . import test_preconsulta_paciente_existente as datos_publicos
from .models import Paciente, PreconsultaClinica


class EnlaceLegacyConservaRespuestasTests(TestCase):
    SLUGS = datos_internos.CompletarConservaRespuestasTests.SLUGS
    TEXTO_MODELO = datos_internos.CompletarConservaRespuestasTests.TEXTO_MODELO
    JSON_NO_EDITABLE = datos_internos.CompletarConservaRespuestasTests.JSON_NO_EDITABLE
    COLUMNAS = {
        "revision_sistemas": "alterada",
        "revision_sistemas_detalle": "REVISION LEGACY RECIBIDA EN ENLACE",
        "habitos": "HABITOS LEGACY RECIBIDOS EN ENLACE",
    }
    RESPUESTAS = {
        "historia_mejorar": "MEJORA LEGACY RECIBIDA EN ENLACE",
        "historia_tiempo_preocupacion": "TIEMPO LEGACY RECIBIDO EN ENLACE",
        "historia_tratamientos_previos": "PREVIOS LEGACY RECIBIDOS EN ENLACE",
        "historia_expectativas": "EXPECTATIVAS LEGACY RECIBIDAS EN ENLACE",
        "examen_peso": "72.345", "examen_talla": "169.234", "examen_imc": "25.456",
        "examen_pa": "121/81", "examen_fc": "78", "examen_sato2": "97",
    }

    @classmethod
    def setUpTestData(cls):
        datos_internos.CompletarConservaRespuestasTests.setUpTestData.__func__(cls)

    def setUp(self):
        self.client.force_login(self.doctora)

    def sembrar_enlace_heredado(self, empresa, paciente):
        respuesta = self.client.post(reverse("clinica_generar_enlace_preconsulta", args=[empresa.slug, paciente.pk]))
        self.assertEqual(respuesta.status_code, 200)
        registro = respuesta.context["preconsulta"]
        datos = deepcopy(registro.datos_generales)
        datos["formulario_general"] = {**self.RESPUESTAS, "legacy_extra": {"privado": "EXTRA FUERA DEL LECTOR"}}
        datos["formulario_general_pendiente_doctor"] = True
        PreconsultaClinica.objects.filter(pk=registro.pk).update(
            **self.COLUMNAS, datos_generales=datos, sin_datos_previos=False,
        )
        registro.refresh_from_db()
        return registro, urlsplit(respuesta.context["enlace_publico"]).path

    def respuestas_publicas(self):
        return datos_publicos.FormularioHistoriaPacienteExistenteTests.respuestas(self)

    def test_enlaces_anteriores_guardan_los_campos_fuera_del_html_y_se_leen_en_historial(self):
        for empresa, paciente, _anterior, actual in self.casos:
            with self.subTest(empresa=empresa.slug):
                otra_consulta = PreconsultaClinica.objects.values().get(pk=actual.pk)
                registro, enlace = self.sembrar_enlace_heredado(empresa, paciente)
                total_formularios, total_pacientes = PreconsultaClinica.objects.count(), Paciente.objects.count()
                publico = Client()
                pagina = publico.get(enlace)
                self.assertContains(pagina, "Motivo de Consulta")
                for nombre in (*self.COLUMNAS, *self.RESPUESTAS):
                    self.assertNotContains(pagina, f'name="{nombre}"')
                datos = self.respuestas_publicas()
                self.assertContains(publico.post(enlace, datos), "Información recibida")
                registro.refresh_from_db()
                self.assertEqual(registro.estado, "completada")
                for nombre, valor in self.COLUMNAS.items():
                    self.assertEqual(getattr(registro, nombre), valor, nombre)
                for nombre, valor in self.RESPUESTAS.items():
                    self.assertEqual(registro.datos_generales["formulario_general"][nombre], valor, nombre)
                self.assertEqual(registro.datos_generales["formulario_general"]["legacy_extra"], {"privado": "EXTRA FUERA DEL LECTOR"})
                for app in (False, True):
                    historial = self.client.get(reverse("clinica_historial_clinico_consolidado", args=[empresa.slug, paciente.pk]), {"app": "1"} if app else {})
                    for valor in (self.COLUMNAS["revision_sistemas_detalle"], self.COLUMNAS["habitos"], *self.RESPUESTAS.values()):
                        self.assertContains(historial, valor)
                    self.assertNotContains(historial, "EXTRA FUERA DEL LECTOR")
                guardado = PreconsultaClinica.objects.values().get(pk=registro.pk)
                self.assertContains(publico.post(enlace, {**datos, "habitos": "Intento de sobrescribir"}), "Información recibida")
                self.assertEqual(PreconsultaClinica.objects.values().get(pk=registro.pk), guardado)
                self.assertEqual(PreconsultaClinica.objects.values().get(pk=actual.pk), otra_consulta)
                self.assertEqual(PreconsultaClinica.objects.count(), total_formularios)
                self.assertEqual(Paciente.objects.count(), total_pacientes)

    def test_cliente_anterior_puede_borrar_explicitamente_y_error_no_pierde_el_snapshot(self):
        empresa, paciente, _anterior, actual = self.casos[0]
        otra_consulta = PreconsultaClinica.objects.values().get(pk=actual.pk)
        registro, enlace = self.sembrar_enlace_heredado(empresa, paciente)
        datos = {**self.respuestas_publicas(), **{nombre: "" for nombre in (*self.COLUMNAS, *self.RESPUESTAS)}}
        publico = Client()
        error = publico.post(enlace, {**datos, "alergias_detalle": ""})
        self.assertEqual(error.status_code, 200)
        self.assertIn("alergias_detalle", error.context["form"].errors)
        registro.refresh_from_db()
        self.assertEqual(registro.estado, "pendiente")
        for nombre, valor in self.COLUMNAS.items():
            self.assertEqual(getattr(registro, nombre), valor)
        for nombre, valor in self.RESPUESTAS.items():
            self.assertEqual(registro.datos_generales["formulario_general"][nombre], valor)
        self.assertContains(publico.post(enlace, datos), "Información recibida")
        registro.refresh_from_db()
        for nombre in self.COLUMNAS:
            self.assertEqual(getattr(registro, nombre), "", nombre)
        for nombre in self.RESPUESTAS:
            self.assertNotIn(nombre, registro.datos_generales["formulario_general"])
        self.assertEqual(PreconsultaClinica.objects.values().get(pk=actual.pk), otra_consulta)
