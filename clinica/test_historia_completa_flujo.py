from unittest.mock import patch
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Empresa, EmpresaModulo, Modulo, RegistroAuditoria, Usuario

from .models import CitaClinica, HistoriaClinicaEspecialidad, Paciente, PreconsultaClinica
from .preconsulta_control import estado_control_preconsulta
from . import test_preconsulta_paciente_existente as datos_publicos


class HistoriaCompletaFlujoTests(TestCase):
    respuestas = datos_publicos.FormularioHistoriaPacienteExistenteTests.respuestas

    @classmethod
    def setUpTestData(cls):
        modulo, _ = Modulo.objects.get_or_create(codigo="clinica_medica", defaults={"nombre": "Clínica"})
        cls.usuario = Usuario.objects.create_user(username="doctor-formulario-qa", es_administrador_empresa=True)
        cls.casos = []
        for slug in ("hospital_mia", "medical_spa", "luque_aestetic", "serviciosmedicos"):
            empresa = Empresa.objects.create(nombre=slug, slug=slug, rtn=f"FORM-{slug}", tipo_solucion="clinica")
            EmpresaModulo.objects.create(empresa=empresa, modulo=modulo, activo=True)
            cls.usuario.empresas_acceso.add(empresa)
            paciente = Paciente.objects.create(
                empresa=empresa, nombre=f"Paciente QA {slug}", expediente_codigo=f"FORM-{slug}",
                identidad="0801199500001", sexo="femenino", alergias="Dato previo conservado",
            )
            cita = CitaClinica.objects.create(
                empresa=empresa, paciente=paciente, fecha_hora=timezone.now() + timezone.timedelta(days=2),
                motivo="Motivo de la agenda anterior",
            )
            cls.casos.append((empresa, paciente, cita))

    def setUp(self):
        self.client.force_login(self.usuario)

    def generar(self, empresa, paciente, cita=None):
        respuesta = self.client.post(
            reverse("clinica_generar_enlace_preconsulta", args=[empresa.slug, paciente.pk]),
            {"cita_id": cita.pk} if cita else {},
        )
        self.assertEqual(respuesta.status_code, 200)
        return respuesta.context["preconsulta"], urlsplit(respuesta.context["enlace_publico"]).path

    def test_enlace_respuestas_expediente_bitacora_y_reenvio_en_cuatro_clinicas(self):
        for empresa, paciente, cita in self.casos:
            with self.subTest(empresa=empresa.slug):
                total_pacientes = Paciente.objects.count()
                total_citas = CitaClinica.objects.count()
                preconsulta, enlace = self.generar(empresa, paciente, cita)
                self.assertEqual(preconsulta.cita_id, cita.pk)
                self.assertIsNone(preconsulta.fecha_primera_apertura)
                publico = Client()
                pagina = publico.get(enlace)
                self.assertLess(pagina.content.index(b"Motivo de Consulta"), pagina.content.index("Funciones orgánicas generales".encode()))
                preconsulta.refresh_from_db()
                self.assertEqual(preconsulta.estado, "pendiente")
                self.assertEqual(estado_control_preconsulta(preconsulta)[0], "abierto")
                actividad = reverse("clinica_preconsulta_actividad", args=[enlace.rstrip("/").split("/")[-1]])
                self.assertEqual(publico.post(actividad, {"evento": "progreso", "paso": "2"}).status_code, 200)
                preconsulta.refresh_from_db()
                self.assertEqual(estado_control_preconsulta(preconsulta)[0], "en_progreso")

                datos = self.respuestas()
                respuesta = publico.post(enlace, datos)
                self.assertContains(respuesta, "Información recibida")
                preconsulta.refresh_from_db()
                self.assertEqual(preconsulta.estado, "completada")
                self.assertEqual(preconsulta.ultimo_resultado, "completado")
                self.assertEqual(preconsulta.intentos_envio, 1)
                token = enlace.rstrip("/").split("/")[-1]
                auditorias = RegistroAuditoria.objects.filter(modelo="preconsultaclinica", objeto_id=str(preconsulta.pk))
                for auditoria in auditorias:
                    self.assertNotIn(token, auditoria.ruta)
                    self.assertNotIn(token, auditoria.motivo)
                self.assertEqual(preconsulta.motivo_consulta, datos["motivo_consulta"])
                self.assertEqual(preconsulta.funciones_detalle, datos["funciones_detalle"])
                self.assertEqual(preconsulta.antecedentes_familiares_detalle, datos["antecedentes_familiares_detalle"])
                self.assertEqual(preconsulta.antecedentes_hospitalarios_detalle, datos["antecedentes_hospitalarios_detalle"])
                self.assertEqual(preconsulta.alergias, datos["alergias_detalle"])
                self.assertEqual(preconsulta.medicamentos_habituales_detalle, datos["medicamentos_detalle"])
                general = preconsulta.datos_generales["formulario_general"]
                for campo in ("gine_menarca", "gine_gestas", "gine_mamografia", "diagnostico_medico_detalle", "quirurgicos_detalle", "decision_cirugia", "evaluacion_psicologica_detalle"):
                    self.assertEqual(general[campo], datos[campo], campo)

                detalle = reverse("clinica_preconsulta_detalle", args=[empresa.slug, paciente.pk, preconsulta.pk])
                for app in ({}, {"app": "1"}):
                    original = self.client.get(detalle, app)
                    for texto in (datos["motivo_consulta"], datos["funciones_detalle"], datos["alergias_detalle"], datos["medicamentos_detalle"], datos["quirurgicos_detalle"], datos["antecedentes_hospitalarios_detalle"], datos["gine_menarca"]):
                        self.assertContains(original, texto)
                    historial = reverse("clinica_historial_clinico_consolidado", args=[empresa.slug, paciente.pk])
                    motivo = self.client.get(historial, {**app, "seccion": "motivo_consulta"})
                    self.assertContains(motivo, datos["motivo_consulta"])
                    funciones = self.client.get(historial, {**app, "seccion": "funciones_organicas"})
                    self.assertContains(funciones, datos["funciones_detalle"])
                    bitacora = self.client.get(reverse("clinica_control_enlaces_historia", args=[empresa.slug]), app)
                    self.assertContains(bitacora, "Completado")
                    self.assertContains(bitacora, paciente.nombre)
                    self.assertContains(bitacora, detalle)

                fecha = preconsulta.fecha_completada
                self.assertContains(publico.post(enlace, {**datos, "motivo_consulta": "Intento de sobrescritura"}), "Información recibida")
                preconsulta.refresh_from_db()
                self.assertEqual(preconsulta.fecha_completada, fecha)
                self.assertEqual(preconsulta.motivo_consulta, datos["motivo_consulta"])
                self.assertEqual(preconsulta.intentos_envio, 1)
                self.assertEqual(Paciente.objects.count(), total_pacientes)
                self.assertEqual(CitaClinica.objects.count(), total_citas)
                self.assertFalse(HistoriaClinicaEspecialidad.objects.exists())
                paciente.refresh_from_db()
                self.assertEqual(paciente.alergias, "Dato previo conservado")
                cita.refresh_from_db()
                self.assertEqual(cita.motivo, "Motivo de la agenda anterior")

    def test_error_de_validacion_conserva_respuestas_y_admite_reintento(self):
        empresa, paciente, cita = self.casos[0]
        preconsulta, enlace = self.generar(empresa, paciente, cita)
        datos = self.respuestas()
        rechazado = Client().post(enlace, {**datos, "alergias_detalle": ""})
        self.assertContains(rechazado, datos["motivo_consulta"])
        self.assertIn("alergias_detalle", rechazado.context["form"].errors)
        preconsulta.refresh_from_db()
        self.assertEqual(preconsulta.estado, "pendiente")
        self.assertIsNone(preconsulta.fecha_completada)
        self.assertEqual(preconsulta.ultimo_resultado, "validacion")
        self.assertContains(Client().post(enlace, datos), "Información recibida")

    def test_error_de_guardado_no_informa_exito_y_permite_reintentar(self):
        empresa, paciente, cita = self.casos[0]
        guardar_original = PreconsultaClinica.save
        for error in (ValidationError("Validación temporal de prueba"), RuntimeError("Fallo temporal de prueba")):
            with self.subTest(error=type(error).__name__):
                preconsulta, enlace = self.generar(empresa, paciente, cita)

                def guardar(registro, *args, **kwargs):
                    if registro.estado == "completada":
                        raise error
                    return guardar_original(registro, *args, **kwargs)

                with patch.object(PreconsultaClinica, "save", guardar):
                    respuesta = Client().post(enlace, self.respuestas())
                self.assertNotContains(respuesta, "Información recibida")
                self.assertContains(respuesta, self.respuestas()["motivo_consulta"])
                self.assertTrue(respuesta.context["form"].non_field_errors())
                preconsulta.refresh_from_db()
                self.assertEqual(preconsulta.estado, "pendiente")
                self.assertIsNone(preconsulta.fecha_completada)
                self.assertContains(Client().post(enlace, self.respuestas()), "Información recibida")

    def test_cita_de_otro_paciente_o_empresa_no_se_admite(self):
        empresa, paciente, cita = self.casos[0]
        otra = self.casos[1][2]
        respuesta = self.client.post(reverse("clinica_generar_enlace_preconsulta", args=[empresa.slug, paciente.pk]), {"cita_id": otra.pk})
        self.assertEqual(respuesta.status_code, 404)
        self.assertFalse(PreconsultaClinica.objects.exists())

    def test_el_expediente_excluye_respuestas_de_otra_empresa(self):
        empresa, paciente, _cita = self.casos[0]
        PreconsultaClinica.objects.create(
            empresa=self.casos[1][0], paciente=paciente, token_hash="a" * 64, token_preview="qa-ajeno",
            fecha_expiracion=timezone.now() + timezone.timedelta(days=7), estado="completada",
            fecha_completada=timezone.now(), motivo_consulta="Respuesta de otra empresa QA",
        )
        historial = reverse("clinica_historial_clinico_consolidado", args=[empresa.slug, paciente.pk])
        self.assertNotContains(self.client.get(historial, {"seccion": "motivo_consulta"}), "Respuesta de otra empresa QA")

    def test_varias_citas_piden_elegir_y_conservan_respuestas_anteriores(self):
        empresa, paciente, cita = self.casos[0]
        segunda = CitaClinica.objects.create(empresa=empresa, paciente=paciente, fecha_hora=cita.fecha_hora + timezone.timedelta(days=5))
        seleccion = self.client.post(reverse("clinica_generar_enlace_preconsulta", args=[empresa.slug, paciente.pk]))
        self.assertContains(seleccion, 'name="cita_id"')
        self.assertFalse(PreconsultaClinica.objects.exists())
        primera, enlace = self.generar(empresa, paciente, cita)
        self.assertContains(Client().post(enlace, self.respuestas()), "Información recibida")
        siguiente, enlace_nuevo = self.generar(empresa, paciente, segunda)
        self.assertContains(Client().post(enlace_nuevo, {**self.respuestas(), "motivo_consulta": "Motivo de la segunda consulta"}), "Información recibida")
        primera.refresh_from_db()
        self.assertEqual(primera.cita_id, cita.pk)
        self.assertEqual(primera.motivo_consulta, self.respuestas()["motivo_consulta"])
        siguiente.refresh_from_db()
        self.assertEqual(siguiente.cita_id, segunda.pk)
        self.assertEqual(siguiente.motivo_consulta, "Motivo de la segunda consulta")
