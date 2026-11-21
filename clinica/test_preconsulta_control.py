from datetime import timedelta

from django.core.exceptions import PermissionDenied
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from core.audit_context import reset_audit_request, set_audit_request
from core.models import Empresa, EmpresaModulo, Modulo, RegistroAuditoria, RolSistema, Usuario

from .models import CitaClinica, InvitacionRegistroPaciente, Paciente, PreconsultaClinica
from .preconsulta_control import (
    control_preconsultas,
    estado_control_preconsulta,
    puede_controlar_preconsultas,
    registrar_actividad_preconsulta,
)
from .tokens import hash_token_preconsulta


class PreconsultaControlTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresas, cls.enlaces = {}, {}
        modulo, _ = Modulo.objects.get_or_create(codigo="clinica_medica", defaults={"nombre": "Clínica"})
        cls.recepcion = RolSistema.objects.create(
            codigo="control-preconsulta-recepcion-qa", nombre="Recepción QA",
            es_rol_clinico=True, usa_permisos_clinicos_granulares=True,
            usa_permisos_clinicos_v2=True, usa_permisos_acciones_v3=True,
            solo_tarjetas_pacientes=True, puede_ver_pacientes=True,
            puede_enviar_enlace_historia_clinica=True,
        )
        cls.medico = RolSistema.objects.create(
            codigo="control-preconsulta-medico-qa", nombre="Médico QA",
            es_rol_clinico=True, usa_permisos_clinicos_granulares=True,
            usa_permisos_clinicos_v2=True, usa_permisos_acciones_v3=True,
            puede_ver_pacientes=True, puede_ver_datos_generales_paciente=True,
            puede_ver_historia_clinica=True,
        )
        cls.sender = Usuario.objects.create_user(username="control-enlaces-recepcion-qa", rol_sistema=cls.recepcion)
        cls.reader = Usuario.objects.create_user(username="control-enlaces-medico-qa", rol_sistema=cls.medico)
        cls.denied = Usuario.objects.create_user(username="control-enlaces-sin-permiso-qa")
        for slug in ("hospital_mia", "medical_spa", "luque_aestetic", "serviciosmedicos"):
            empresa = Empresa.objects.create(nombre=slug, slug=slug, rtn=f"PRECONTROL-{slug}", tipo_solucion="clinica")
            EmpresaModulo.objects.create(empresa=empresa, modulo=modulo, activo=True)
            for usuario in (cls.sender, cls.reader, cls.denied):
                usuario.empresas_acceso.add(empresa)
            paciente = Paciente.objects.create(
                empresa=empresa, nombre=f"Paciente control {slug}", expediente_codigo=f"CONTROL-{slug}",
            )
            cita = CitaClinica.objects.create(
                empresa=empresa, paciente=paciente, fecha_hora=timezone.now(), motivo="Atención QA",
            )
            enlace = PreconsultaClinica.objects.create(
                empresa=empresa, paciente=paciente, cita=cita, creada_por=cls.sender,
                token_hash=hash_token_preconsulta(f"PRIVATE-TOKEN-{slug}"), token_preview=f"CODE-{slug}"[:16],
                fecha_expiracion=timezone.now() + timedelta(days=1),
                motivo_consulta=f"PRIVATE-RESPONSE-{slug}", ip_completada="192.0.2.89",
            )
            cls.empresas[slug] = empresa
            cls.enlaces[slug] = enlace

    def request(self, usuario, empresa, app=False, method="get", **filtros):
        ruta = reverse("clinica_control_enlaces_historia", args=[empresa.slug])
        datos = {**({"app": "1"} if app else {}), **filtros}
        request = getattr(RequestFactory(), method)(ruta, datos)
        request.user = usuario
        return request

    def test_registra_apertura_progreso_y_errores_sin_completar_por_abrir(self):
        enlace = self.enlaces["hospital_mia"]
        self.assertEqual(estado_control_preconsulta(enlace)[0], "pendiente")
        self.assertIsNone(enlace.fecha_primera_apertura)
        registrar_actividad_preconsulta(enlace, "apertura")
        self.assertEqual(enlace.cantidad_aperturas, 1)
        self.assertEqual(estado_control_preconsulta(enlace)[0], "abierto")
        enlace.refresh_from_db()
        self.assertEqual(enlace.estado, "pendiente")
        self.assertIsNone(enlace.fecha_completada)
        registrar_actividad_preconsulta(enlace, "progreso", paso=1)
        self.assertEqual(estado_control_preconsulta(enlace)[0], "en_progreso")
        registrar_actividad_preconsulta(enlace, "intento", resultado="validacion")
        self.assertEqual(estado_control_preconsulta(enlace)[0], "validacion")
        registrar_actividad_preconsulta(enlace, "intento", resultado="error_tecnico")
        self.assertEqual(estado_control_preconsulta(enlace)[0], "error_tecnico")
        self.assertEqual(enlace.intentos_envio, 2)
        enlace.estado = "completada"
        enlace.fecha_completada = timezone.now()
        enlace.save(update_fields=["estado", "fecha_completada"])
        ultima_actividad = enlace.fecha_ultima_actividad
        registrar_actividad_preconsulta(enlace, "apertura")
        enlace.refresh_from_db()
        self.assertEqual(enlace.cantidad_aperturas, 1)
        self.assertEqual(enlace.fecha_ultima_actividad, ultima_actividad)
        self.assertEqual(estado_control_preconsulta(enlace)[0], "completado")

    def test_recepcion_solo_ve_enlaces_propios_y_metadata_en_cuatro_empresas(self):
        for slug, empresa in self.empresas.items():
            otro_paciente = Paciente.objects.create(
                empresa=empresa, nombre=f"NO-MOSTRAR-A-RECEPCION-{slug}", expediente_codigo=f"OCULTO-{slug}",
            )
            PreconsultaClinica.objects.create(
                empresa=empresa, paciente=otro_paciente, creada_por=self.reader,
                token_hash=hash_token_preconsulta(f"PRIVATE-OTHER-{slug}"), token_preview="PRIVATE-OTHER",
                fecha_expiracion=timezone.now() + timedelta(days=1), estado="completada",
            )
            for app in (False, True):
                with self.subTest(empresa=slug, app=app):
                    respuesta = control_preconsultas(self.request(self.sender, empresa, app), empresa)
                    contenido = respuesta.content.decode()
                    self.assertEqual(respuesta.status_code, 200)
                    self.assertIn(f"Paciente control {slug}", contenido)
                    self.assertIn("Cita vinculada", contenido)
                    self.assertIn("Sin registro", contenido)
                    self.assertNotIn(otro_paciente.nombre, contenido)
                    self.assertNotIn("Ver respuestas recibidas", contenido)
                    self.assertNotIn(f"PRIVATE-RESPONSE-{slug}", contenido)
                    self.assertNotIn(self.enlaces[slug].token_hash, contenido)
                    self.assertNotIn("192.0.2.89", contenido)
                    for otro_slug in set(self.empresas) - {slug}:
                        self.assertNotIn(f"Paciente control {otro_slug}", contenido)

    def test_lector_clinico_tiene_enlace_a_respuestas_solo_de_su_empresa(self):
        for slug, empresa in self.empresas.items():
            enlace = self.enlaces[slug]
            enlace.estado = "completada"
            enlace.fecha_completada = timezone.now()
            enlace.save(update_fields=["estado", "fecha_completada"])
            detalle = reverse("clinica_preconsulta_detalle", args=[slug, enlace.paciente_id, enlace.pk])
            for app in (False, True):
                with self.subTest(empresa=slug, app=app):
                    respuesta = control_preconsultas(self.request(self.reader, empresa, app), empresa)
                    contenido = respuesta.content.decode()
                    self.assertIn("Ver respuestas recibidas", contenido)
                    self.assertIn(f'href="{detalle}{"?app=1" if app else ""}"', contenido)
                    self.assertNotIn(f"PRIVATE-RESPONSE-{slug}", contenido)

    def test_historia_convertida_conserva_actividad_de_invitacion_existente(self):
        empresa = self.empresas["hospital_mia"]
        enlace = self.enlaces[empresa.slug]
        apertura = timezone.now() - timedelta(days=1)
        InvitacionRegistroPaciente.objects.create(
            empresa=empresa, paciente=enlace.paciente, preconsulta=enlace,
            token_hash=hash_token_preconsulta("legacy-conversion-qa"), token_preview="LEGACY",
            fecha_expiracion=timezone.now() + timedelta(days=1),
            fecha_primera_apertura=apertura, fecha_ultima_actividad=apertura,
            cantidad_aperturas=3, paso_maximo=2,
        )
        self.assertEqual(estado_control_preconsulta(enlace)[0], "en_progreso")
        respuesta = control_preconsultas(self.request(self.reader, empresa), empresa)
        self.assertIn(timezone.localtime(apertura).strftime("%d/%m/%Y %H:%M"), respuesta.content.decode())
        enlace.refresh_from_db()
        self.assertIsNone(enlace.fecha_primera_apertura)
        self.assertIsNone(enlace.fecha_ultima_actividad)

    def test_deniega_sin_permiso_empresa_ajena_y_post(self):
        empresa = self.empresas["hospital_mia"]
        self.assertFalse(puede_controlar_preconsultas(self.denied, empresa))
        for request in (
            self.request(self.denied, empresa),
            self.request(self.reader, empresa, method="post"),
        ):
            with self.assertRaises(PermissionDenied):
                control_preconsultas(request, empresa)
        ajeno = Usuario.objects.create_user(username="control-empresa-ajena-qa", rol_sistema=self.medico)
        self.assertFalse(puede_controlar_preconsultas(ajeno, empresa))

    def test_cita_de_otro_paciente_importada_no_se_muestra(self):
        empresa = self.empresas["hospital_mia"]
        enlace = self.enlaces[empresa.slug]
        PreconsultaClinica.objects.filter(pk=enlace.pk).update(cita=self.enlaces["medical_spa"].cita)
        with self.assertRaises(PermissionDenied):
            control_preconsultas(self.request(self.reader, empresa), empresa)

    def test_telemetria_no_copia_token_publico_ni_respuestas_a_auditoria(self):
        enlace = self.enlaces["hospital_mia"]
        anteriores = RegistroAuditoria.objects.count()
        request = RequestFactory().post("/clinica/preconsulta/PRIVATE-BEARER-TOKEN/actividad/", {"paso": 2})
        contexto = set_audit_request(request)
        try:
            registrar_actividad_preconsulta(enlace, "progreso", paso=2)
        finally:
            reset_audit_request(contexto)
        enlace.refresh_from_db()
        self.assertEqual(enlace.paso_maximo, 2)
        self.assertEqual(RegistroAuditoria.objects.count(), anteriores)
