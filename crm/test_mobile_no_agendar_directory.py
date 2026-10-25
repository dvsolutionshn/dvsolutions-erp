from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from clinica.models import Paciente
from clinica.restricciones_citas import MENSAJE_NO_AGENDAR
from core.models import ConfiguracionAvanzadaEmpresa, Empresa, EmpresaModulo, Modulo, RolSistema, Usuario

from .models import CitaCliente


class MobileNoAgendarDirectoryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            nombre="Hospital Mía", slug="hospital_mia", tipo_solucion="clinica", estado_licencia="activa", rtn="MIA-PWA-NO-AGENDAR",
        )
        cls.otra = Empresa.objects.create(
            nombre="Luque", slug="luque_aestetic", tipo_solucion="clinica", estado_licencia="activa", rtn="LQ-PWA-NO-AGENDAR",
        )
        for codigo in ("clinica_medica", "agenda_citas", "crm_marketing"):
            modulo, _ = Modulo.objects.get_or_create(codigo=codigo, defaults={"nombre": codigo})
            for empresa in (cls.empresa, cls.otra):
                EmpresaModulo.objects.create(empresa=empresa, modulo=modulo, activo=True)
        for empresa in (cls.empresa, cls.otra):
            ConfiguracionAvanzadaEmpresa.objects.create(empresa=empresa)
        cls.rol = RolSistema.objects.create(
            nombre="Superadministrador", codigo="nombre-sin-privilegio",
            puede_clinica=True, puede_pacientes=True, puede_citas=True, puede_crm=True,
            puede_expediente_clinico=True,
        )
        cls.normal = Usuario.objects.create_user(
            username="normal-pwa-restriccion", empresa=cls.empresa, rol_sistema=cls.rol,
        )
        cls.dueno = Usuario.objects.create_superuser(username="dueno-pwa-restriccion", empresa=cls.empresa)
        cls.admin = Usuario.objects.create_user(
            username="admin-pwa-restriccion", empresa=cls.empresa,
            es_administrador_empresa=True, rol_sistema=cls.rol,
        )
        cls.paciente = Paciente.objects.create(
            empresa=cls.empresa, nombre="Paciente restringido PWA", expediente_codigo="MIA-PWA-R",
            identidad="0801199000791", sexo="femenino",
        )
        cls.libre = Paciente.objects.create(
            empresa=cls.empresa, nombre="Paciente disponible PWA", expediente_codigo="MIA-PWA-L",
            identidad="0801199000792", sexo="masculino",
        )
        cls.cita = CitaCliente.objects.create(
            empresa=cls.empresa, paciente=cls.paciente, titulo="Consulta existente",
            fecha_hora=timezone.now() + timedelta(days=7), estado="confirmada",
        )
        Paciente.objects.filter(pk=cls.paciente.pk).update(
            no_agendar=True, motivo_no_agendar="Motivo confidencial PWA 79",
        )

    def _payload(self, usuario, empresa=None):
        self.client.force_login(usuario)
        response = self.client.get(reverse("agenda_mobile", args=[(empresa or self.empresa).slug]))
        self.assertEqual(response.status_code, 200)
        return response, {fila["id"]: fila for fila in response.context["pacientes_app_payload"]}

    def test_dueno_y_admin_ven_acciones_y_motivo_en_directorio_real_pwa(self):
        for usuario in (self.dueno, self.admin):
            with self.subTest(usuario=usuario.username):
                _, payload = self._payload(usuario)
                restringido = payload[self.paciente.pk]
                self.assertTrue(restringido["no_agendar"])
                self.assertEqual(restringido["motivo_no_agendar"], "Motivo confidencial PWA 79")
                self.assertEqual(
                    restringido["habilitar_citas_url"],
                    reverse("clinica_habilitar_citas_paciente", args=[self.empresa.slug, self.paciente.pk])
                    + "?app=1&origen=agenda",
                )
                self.assertTrue(restringido["historial_restricciones_url"])
                self.assertEqual(restringido["restriccion_url"], "")
                self.assertEqual(
                    payload[self.libre.pk]["restriccion_url"],
                    reverse("clinica_restringir_citas_paciente", args=[self.empresa.slug, self.libre.pk])
                    + "?app=1&origen=agenda",
                )

    def test_nombre_del_rol_no_concede_acciones_ni_expone_motivo(self):
        respuesta, payload = self._payload(self.normal)
        restringido = payload[self.paciente.pk]
        self.assertTrue(restringido["no_agendar"])
        self.assertNotIn("motivo_no_agendar", restringido)
        self.assertEqual(restringido["restriccion_mensaje"], MENSAJE_NO_AGENDAR)
        self.assertEqual(restringido["habilitar_citas_url"], "")
        self.assertEqual(restringido["restriccion_url"], "")
        self.assertEqual(restringido["historial_restricciones_url"], "")
        self.assertEqual(payload[self.libre.pk]["restriccion_url"], "")
        self.assertNotContains(respuesta, "Motivo confidencial PWA 79")

    def test_permiso_para_marcar_no_concede_quitar_ni_ver_motivo(self):
        self.rol.puede_restringir_citas_paciente = True
        self.rol.save(update_fields=["puede_restringir_citas_paciente"])
        _, payload = self._payload(self.normal)
        self.assertTrue(payload[self.libre.pk]["restriccion_url"])
        self.assertEqual(payload[self.paciente.pk]["habilitar_citas_url"], "")
        self.assertTrue(payload[self.paciente.pk]["historial_restricciones_url"])
        self.assertNotIn("motivo_no_agendar", payload[self.paciente.pk])

    def test_permiso_para_motivo_no_concede_cambio_estado(self):
        self.rol.puede_ver_motivo_restriccion_paciente = True
        self.rol.save(update_fields=["puede_ver_motivo_restriccion_paciente"])
        _, payload = self._payload(self.normal)
        restringido = payload[self.paciente.pk]
        self.assertIn("Motivo confidencial PWA 79", restringido["restriccion_mensaje"])
        self.assertEqual(restringido["motivo_no_agendar"], "Motivo confidencial PWA 79")
        self.assertEqual(restringido["habilitar_citas_url"], "")
        self.assertEqual(payload[self.libre.pk]["restriccion_url"], "")

    def test_directorio_conserva_cita_futura_y_estado_restringido(self):
        _, payload = self._payload(self.dueno)
        self.assertIsNotNone(payload[self.paciente.pk]["proxima_cita"])
        self.cita.refresh_from_db()
        self.paciente.refresh_from_db()
        self.assertEqual(self.cita.estado, "confirmada")
        self.assertTrue(self.paciente.no_agendar)
        self.assertTrue(self.paciente.activo)
        self.assertEqual(CitaCliente.objects.filter(paciente=self.paciente).count(), 1)

    def test_paciente_de_empresa_local_no_hereda_restriccion_de_agenda_central(self):
        paciente_local = Paciente.objects.create(
            empresa=self.otra, nombre="Paciente local", expediente_codigo="LQ-PWA-R",
            identidad=self.paciente.identidad, sexo="femenino",
        )
        _, payload = self._payload(self.dueno, self.otra)
        local = payload[paciente_local.pk]
        self.assertFalse(local["no_agendar"])
        self.assertTrue(local["agenda_no_agendar"])
        self.assertEqual(local["agenda_paciente_id"], self.paciente.pk)
        self.assertIn("Motivo confidencial PWA 79", local["agenda_restriccion_mensaje"])
        self.assertEqual(local["motivo_no_agendar"], "")
        self.assertEqual(
            local["restriccion_url"],
            reverse("clinica_restringir_citas_paciente", args=[self.otra.slug, paciente_local.pk])
            + "?app=1&origen=agenda",
        )
        paciente_local.refresh_from_db()
        self.assertFalse(paciente_local.no_agendar)
