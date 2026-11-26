"""Completar internamente no borra respuestas del enlace sin un control editable."""

from copy import deepcopy
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlsplit

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Empresa, EmpresaModulo, Modulo, RolSistema, Usuario
from . import test_mobile_general_history as datos_internos
from . import test_preconsulta_paciente_existente as datos_publicos
from .models import CitaClinica, Paciente, PreconsultaClinica


class CompletarConservaRespuestasTests(TestCase):
    SLUGS = ("hospital_mia", "medical_spa", "luque_aestetic", "serviciosmedicos")
    TEXTO_MODELO = {
        "motivo_consulta": "MOTIVO RECIBIDO DEL ENLACE",
        "funciones_detalle": "DETALLE DE FUNCIONES RECIBIDO DEL ENLACE",
        "revision_sistemas": "alterada",
        "revision_sistemas_detalle": "REVISION RECIBIDA DEL ENLACE",
        "habitos": "HABITOS RECIBIDOS DEL ENLACE",
    }
    JSON_NO_EDITABLE = {
        "historia_mejorar": "MEJORA RECIBIDA DEL ENLACE",
        "historia_tiempo_preocupacion": "TIEMPO RECIBIDO DEL ENLACE",
        "historia_tratamientos_previos": "PREVIOS RECIBIDOS DEL ENLACE",
        "historia_expectativas": "EXPECTATIVAS RECIBIDAS DEL ENLACE",
        "examen_peso": "71.5", "examen_talla": "168", "examen_imc": "25.3",
        "examen_pa": "120/80", "examen_fc": "77", "examen_sato2": "98",
        "expectativas_realistas": ["si"],
        "legacy_json": {"texto": "CONSERVAR JSON HISTORICO"},
    }

    @classmethod
    def setUpTestData(cls):
        modulo, _ = Modulo.objects.get_or_create(codigo="clinica_medica", defaults={"nombre": "Clínica QA"})
        cls.doctora = Usuario.objects.create_user(username="completar-respuestas-qa", es_administrador_empresa=True)
        cls.casos = []
        for n, slug in enumerate(cls.SLUGS, 1):
            empresa = Empresa.objects.create(nombre=slug, slug=slug, rtn=f"COMPLETAR-QA-{n}", tipo_solucion="clinica", estado_licencia="activa")
            EmpresaModulo.objects.create(empresa=empresa, modulo=modulo, activo=True)
            cls.doctora.empresas_acceso.add(empresa)
            paciente = Paciente.objects.create(
                empresa=empresa, nombre=f"Ana Paciente {n}", primer_nombre="Ana", primer_apellido=f"Paciente{n}",
                identidad=f"08011992009{n:02d}", fecha_nacimiento=date(1992, 5, 10), sexo="femenino",
                estado_civil="soltero", telefono="99990001", expediente_codigo=f"COMPLETAR-QA-{n}",
            )
            anterior = PreconsultaClinica.objects.create(
                empresa=empresa, paciente=paciente, token_hash=f"COMPLETAR-ANTERIOR-{n}", tipo="general", estado="completada",
                fecha_expiracion=timezone.now() + timedelta(days=10), fecha_completada=timezone.now() - timedelta(days=8),
                motivo_consulta="MOTIVO DE OTRA CONSULTA CONSERVADO", consentimiento_datos=True,
                datos_generales={"formulario_general": {"funcion_sueno": "normal"}},
            )
            actual = PreconsultaClinica.objects.create(
                empresa=empresa, paciente=paciente, token_hash=f"COMPLETAR-ACTUAL-{n}", tipo="general", estado="completada",
                fecha_expiracion=timezone.now() + timedelta(days=10), fecha_completada=timezone.now() - timedelta(days=2),
                ip_completada="192.0.2.12", consentimiento_datos=True, funciones_organicas="alterada",
                **cls.TEXTO_MODELO,
                datos_generales={"legacy_root": {"texto": "CONSERVAR RAIZ HISTORICA"}, "formulario_general": {
                    **deepcopy(cls.JSON_NO_EDITABLE), "funcion_apetito": "normal", "funcion_sueno": "alterada",
                    "diagnostico_medico": "si", "diagnostico_medico_detalle": "CONDICION EDITABLE DEL ENLACE",
                    "procedimientos_interes": ["rinoplastia"], "motivo_categoria": ["cirugia_facial"],
                    "quirurgicos_operado": ["si"], "quirurgicos_detalle": "CIRUGIA EDITABLE DEL ENLACE",
                }},
            )
            cls.casos.append((empresa, paciente, anterior, actual))
        # La base clínica comparte los datos generales por identidad. Preparar
        # sus contrapartes evita confundir esa sincronización existente con un
        # paciente duplicado por el guardado del formulario.
        for _empresa, origen, _anterior, _actual in cls.casos:
            for destino, _paciente, _previamente, _reciente in cls.casos:
                if destino.pk != origen.empresa_id:
                    Paciente.objects.create(
                        empresa=destino, nombre=origen.nombre, primer_nombre=origen.primer_nombre,
                        primer_apellido=origen.primer_apellido, identidad=origen.identidad,
                        fecha_nacimiento=origen.fecha_nacimiento, sexo=origen.sexo,
                        expediente_codigo=f"COMPARTIDO-{origen.pk}-{destino.pk}",
                    )

    def setUp(self):
        self.client.force_login(self.doctora)

    def url(self, empresa, paciente, app=False):
        return reverse("clinica_completar_historia_clinica_paciente", args=[empresa.slug, paciente.pk]) + ("?app=1" if app else "")

    def datos_visibles(self, paciente, app=False):
        fixture = datos_internos.MobileGeneralHistoryIntegrationTests
        proxy = SimpleNamespace(FIELDS=fixture.FIELDS, MULTIPLE=fixture.MULTIPLE, paciente=paciente)
        return fixture.data(proxy, paciente, mobile=app)

    def test_terminar_conserva_respuestas_no_editables_fecha_ip_y_otra_consulta_en_web_y_app(self):
        for empresa, paciente, anterior, actual in self.casos:
            for app in (False, True):
                with self.subTest(empresa=empresa.slug, app=app):
                    anterior_antes = PreconsultaClinica.objects.values().get(pk=anterior.pk)
                    actual.refresh_from_db()
                    datos_antes = deepcopy(actual.datos_generales)
                    finalizacion_antes = (actual.fecha_completada, actual.ip_completada)
                    total = PreconsultaClinica.objects.count()
                    vista = self.client.get(self.url(empresa, paciente, app))
                    self.assertEqual(vista.status_code, 200)
                    self.assertContains(vista, "data-respuestas-recibidas-internas")
                    self.assertContains(vista, self.TEXTO_MODELO["motivo_consulta"])
                    self.assertContains(vista, self.TEXTO_MODELO["funciones_detalle"])
                    self.assertContains(vista, self.JSON_NO_EDITABLE["historia_expectativas"])
                    self.assertContains(vista, "MOTIVO DE OTRA CONSULTA CONSERVADO")
                    respuesta = self.client.post(self.url(empresa, paciente, app), self.datos_visibles(paciente, app))
                    self.assertEqual(respuesta.status_code, 302, getattr(respuesta, "context", None))
                    actual.refresh_from_db()
                    for nombre, valor in self.TEXTO_MODELO.items():
                        self.assertEqual(getattr(actual, nombre), valor, nombre)
                    for nombre, valor in self.JSON_NO_EDITABLE.items():
                        self.assertEqual(actual.datos_generales["formulario_general"][nombre], valor, nombre)
                    self.assertEqual(actual.datos_generales["legacy_root"], datos_antes["legacy_root"])
                    self.assertNotIn("funciones_detalle_historico", actual.datos_generales["formulario_general"])
                    self.assertEqual((actual.fecha_completada, actual.ip_completada), finalizacion_antes)
                    self.assertEqual(PreconsultaClinica.objects.values().get(pk=anterior.pk), anterior_antes)
                    self.assertEqual(PreconsultaClinica.objects.count(), total)
                    # Los campos que sí se editan mantienen su borrado deliberado.
                    self.assertNotIn("diagnostico_medico_detalle", actual.datos_generales["formulario_general"])
                    self.assertNotIn("quirurgicos_detalle", actual.datos_generales["formulario_general"])
                    self.assertEqual(actual.datos_generales["formulario_general"]["diagnostico_medico"], "no")
                    reabierta = self.client.get(self.url(empresa, paciente, app))
                    self.assertContains(reabierta, self.TEXTO_MODELO["motivo_consulta"])
                    self.assertContains(reabierta, self.JSON_NO_EDITABLE["historia_expectativas"])

    def test_lector_sin_edicion_no_puede_guardar_ni_abrir_formulario_interno(self):
        empresa, paciente, _anterior, actual = self.casos[0]
        rol = RolSistema.objects.create(
            nombre="Lector historia QA", codigo="completar-lector-qa", es_rol_clinico=True,
            usa_permisos_clinicos_granulares=True, puede_ver_historia_clinica=True,
        )
        lector = Usuario.objects.create_user(username="completar-lector-qa", empresa=empresa, rol_sistema=rol)
        self.client.force_login(lector)
        antes = PreconsultaClinica.objects.values().get(pk=actual.pk)
        for app in (False, True):
            for metodo in ("get", "post"):
                respuesta = getattr(self.client, metodo)(self.url(empresa, paciente, app), self.datos_visibles(paciente, app) if metodo == "post" else {})
                self.assertIn(respuesta.status_code, (302, 403))
        self.assertEqual(PreconsultaClinica.objects.values().get(pk=actual.pk), antes)

    def test_funciones_visibles_actualizan_el_agregado_y_conservan_el_detalle_recibido(self):
        empresa, paciente, _anterior, actual = self.casos[0]
        for app in (False, True):
            with self.subTest(app=app):
                PreconsultaClinica.objects.filter(pk=actual.pk).update(funciones_organicas="normal")
                datos = self.datos_visibles(paciente, app)
                datos["funcion_apetito"] = "alterada"
                respuesta = self.client.post(self.url(empresa, paciente, app), datos)
                self.assertEqual(respuesta.status_code, 302)
                actual.refresh_from_db()
                self.assertEqual(actual.funciones_organicas, "alterada")
                self.assertEqual(actual.datos_generales["formulario_general"]["funcion_apetito"], "alterada")
                self.assertEqual(actual.funciones_detalle, self.TEXTO_MODELO["funciones_detalle"])

    def test_error_al_sincronizar_paciente_revierte_el_guardado_completo(self):
        empresa, paciente, _anterior, actual = self.casos[0]
        antes = PreconsultaClinica.objects.values().get(pk=actual.pk)
        paciente_antes = Paciente.objects.values().get(pk=paciente.pk)
        with patch("clinica.views._actualizar_paciente_desde_preconsulta", side_effect=RuntimeError("Fallo sintético de sincronización")):
            with self.assertRaises(RuntimeError):
                self.client.post(self.url(empresa, paciente), self.datos_visibles(paciente))
        self.assertEqual(PreconsultaClinica.objects.values().get(pk=actual.pk), antes)
        self.assertEqual(Paciente.objects.values().get(pk=paciente.pk), paciente_antes)

    def test_completar_interno_no_cierra_el_enlace_pendiente_y_ambos_envios_se_conservan(self):
        for empresa, paciente, _anterior, _actual in self.casos:
            cita = CitaClinica.objects.create(
                empresa=empresa, paciente=paciente, fecha_hora=timezone.now() + timedelta(days=2), motivo="CITA QA CONSERVADA",
            )
            for app in (False, True):
                with self.subTest(empresa=empresa.slug, app=app):
                    paciente.preconsultas.all().delete()
                    total_pacientes, total_citas = Paciente.objects.count(), CitaClinica.objects.count()
                    generada = self.client.post(reverse("clinica_generar_enlace_preconsulta", args=[empresa.slug, paciente.pk]), {"cita_id": cita.pk})
                    self.assertEqual(generada.status_code, 200)
                    publico = generada.context["preconsulta"]
                    enlace = urlsplit(generada.context["enlace_publico"]).path
                    if app:
                        # Algunos enlaces históricos copiaban esta marca del
                        # registro inicial: no prueba que sean formularios internos.
                        publico.datos_generales["formulario_general_pendiente_doctor"] = True
                        publico.save(update_fields=["datos_generales"])
                    antes = PreconsultaClinica.objects.values().get(pk=publico.pk)
                    vista = self.client.get(self.url(empresa, paciente, app))
                    self.assertEqual(vista.status_code, 200)
                    self.assertEqual(PreconsultaClinica.objects.filter(paciente=paciente).count(), 1)
                    self.assertEqual(PreconsultaClinica.objects.values().get(pk=publico.pk), antes)
                    datos = self.datos_visibles(paciente, app)
                    invalida = self.client.post(self.url(empresa, paciente, app), {**datos, "identidad": "INVALIDA QA"})
                    self.assertEqual(invalida.status_code, 200)
                    self.assertEqual(PreconsultaClinica.objects.filter(paciente=paciente).count(), 1)
                    self.assertEqual(PreconsultaClinica.objects.values().get(pk=publico.pk), antes)
                    guardada = self.client.post(self.url(empresa, paciente, app), datos)
                    self.assertEqual(guardada.status_code, 302)
                    self.assertEqual(PreconsultaClinica.objects.values().get(pk=publico.pk), antes)
                    interno = paciente.preconsultas.exclude(pk=publico.pk).get()
                    self.assertIs(interno.datos_generales["registro_interno"], True)
                    self.assertEqual(interno.estado, "completada")
                    self.assertEqual(interno.cita_id, cita.pk)
                    self.assertNotEqual(interno.token_hash, publico.token_hash)
                    self.assertEqual(interno.creada_por_id, self.doctora.pk)
                    self.assertEqual(interno.intentos_envio, 0)
                    self.assertEqual(self.client.post(self.url(empresa, paciente, app), datos).status_code, 302)
                    self.assertEqual(PreconsultaClinica.objects.filter(paciente=paciente).count(), 2)
                    publico.refresh_from_db()
                    self.assertTrue(publico.vigente)
                    cliente_publico = Client()
                    self.assertContains(cliente_publico.get(enlace), "Motivo de Consulta")
                    respuestas = datos_publicos.FormularioHistoriaPacienteExistenteTests.respuestas(self)
                    respuestas.update(version_declaracion="1", riesgo_tromboembolico=["ninguno"])
                    self.assertContains(cliente_publico.post(enlace, respuestas), "Información recibida")
                    publico.refresh_from_db()
                    self.assertEqual(publico.motivo_consulta, respuestas["motivo_consulta"])
                    self.assertEqual(publico.funciones_detalle, respuestas["funciones_detalle"])
                    self.assertEqual(publico.cita_id, cita.pk)
                    self.assertEqual(PreconsultaClinica.objects.filter(paciente=paciente).count(), 2)
                    historial = self.client.get(reverse("clinica_historial_clinico_consolidado", args=[empresa.slug, paciente.pk]), {"app": "1"} if app else {})
                    self.assertContains(historial, respuestas["motivo_consulta"])
                    self.assertContains(historial, respuestas["funciones_detalle"])
                    self.assertEqual(Paciente.objects.count(), total_pacientes)
                    self.assertEqual(CitaClinica.objects.count(), total_citas)

    def test_completar_no_reactiva_un_enlace_revocado_con_marca_heredada(self):
        empresa, paciente, _anterior, actual = self.casos[0]
        PreconsultaClinica.objects.filter(pk=actual.pk).update(
            estado="revocada", datos_generales={"formulario_general_pendiente_doctor": True},
        )
        antes = PreconsultaClinica.objects.values().get(pk=actual.pk)
        total = PreconsultaClinica.objects.count()
        respuesta = self.client.post(self.url(empresa, paciente), self.datos_visibles(paciente))
        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(PreconsultaClinica.objects.values().get(pk=actual.pk), antes)
        self.assertEqual(PreconsultaClinica.objects.count(), total + 1)
        self.assertEqual(paciente.preconsultas.order_by("-fecha_creacion", "-id").first().estado, "completada")
