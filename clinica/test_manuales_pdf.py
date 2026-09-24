from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import ConfiguracionAvanzadaEmpresa, Empresa, EmpresaModulo, Modulo, RolSistema, UsuarioEmpresaPermiso
from crm.models import ConfiguracionCRM

from .models import EnvioManualPDF, ManualReceta, Paciente


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class ManualesPDFOperativosTests(TestCase):
    def setUp(self):
        self.empresa = Empresa.objects.create(
            nombre="Clínica Manuales",
            slug="clinica_manuales_operativos",
            rtn="MANUALES-001",
            tipo_solucion="clinica",
            estado_licencia="activa",
        )
        modulo, _ = Modulo.objects.get_or_create(codigo="clinica_medica", defaults={"nombre": "Clínica Médica"})
        EmpresaModulo.objects.create(empresa=self.empresa, modulo=modulo, activo=True)
        ConfiguracionAvanzadaEmpresa.objects.create(
            empresa=self.empresa,
            manuales_pdf_habilitados=True,
        )
        self.rol = RolSistema.objects.create(
            nombre="Enfermería manuales",
            codigo="enfermeria-manuales-test",
            activo=True,
            es_rol_clinico=True,
            usa_permisos_clinicos_granulares=True,
            puede_ver_pacientes=True,
            puede_ver_manuales_pdf=True,
            puede_enviar_manuales_pdf=True,
        )
        self.usuario = get_user_model().objects.create_user(
            username="enfermera-manuales",
            password="test",
            empresa=self.empresa,
        )
        UsuarioEmpresaPermiso.objects.create(
            usuario=self.usuario,
            empresa=self.empresa,
            rol_sistema=self.rol,
        )
        self.paciente = Paciente.objects.create(
            empresa=self.empresa,
            expediente_codigo="MAN-0001",
            nombre="Paciente Manual",
            identidad="0801199900101",
            correo="paciente@example.com",
            whatsapp="99998888",
        )
        self.client.force_login(self.usuario)

    def crear_manual(self, titulo, activo=True):
        return ManualReceta.objects.create(
            empresa=self.empresa,
            titulo=titulo,
            descripcion=f"Descripción de {titulo}",
            activo=activo,
            archivo=SimpleUploadedFile(
                f"{titulo.lower().replace(' ', '-')}.pdf",
                b"%PDF-1.4\nmanual\n%%EOF",
                content_type="application/pdf",
            ),
            creado_por=self.usuario,
        )

    def test_modulo_operativo_solo_muestra_manuales_activos_y_no_administra(self):
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            self.crear_manual("Manual activo")
            self.crear_manual("Manual oculto", activo=False)

            response = self.client.get(reverse("clinica_manuales_pdf", args=[self.empresa.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Manual activo")
        self.assertNotContains(response, "Manual oculto")
        self.assertContains(response, "Enviar seleccionados")
        self.assertNotContains(response, "Subir manual PDF")
        self.assertNotContains(response, "Editar")

    def test_usuario_de_envio_no_puede_abrir_administracion(self):
        response = self.client.get(reverse("clinica_manuales_recetas", args=[self.empresa.slug]))
        self.assertRedirects(
            response,
            reverse("dashboard", args=[self.empresa.slug]),
            fetch_redirect_response=False,
        )

    def test_busqueda_de_paciente_incluye_telefono_y_correo(self):
        response = self.client.get(
            reverse("clinica_pacientes_sugerencias", args=[self.empresa.slug]),
            {"q": "Paciente"},
        )
        self.assertEqual(response.status_code, 200)
        resultado = response.json()["results"][0]
        self.assertEqual(resultado["telefono"], "99998888")
        self.assertEqual(resultado["correo"], "paciente@example.com")

    def test_envia_varios_manuales_por_correo_y_registra_historial(self):
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            manual_uno = self.crear_manual("Cuidados postoperatorios")
            manual_dos = self.crear_manual("Indicaciones capilares")

            response = self.client.post(
                reverse("clinica_enviar_manuales_pdf", args=[self.empresa.slug]),
                {
                    "manuales": [str(manual_uno.id), str(manual_dos.id)],
                    "paciente_id": str(self.paciente.id),
                    "canal": "correo",
                },
            )

        self.assertRedirects(response, reverse("clinica_manuales_pdf", args=[self.empresa.slug]))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["paciente@example.com"])
        self.assertEqual(len(mail.outbox[0].attachments), 2)
        envio = EnvioManualPDF.objects.get()
        self.assertEqual(envio.empresa, self.empresa)
        self.assertEqual(envio.paciente, self.paciente)
        self.assertEqual(envio.enviado_por, self.usuario)
        self.assertEqual(envio.canal, EnvioManualPDF.CANAL_CORREO)
        self.assertEqual(envio.estado, EnvioManualPDF.ESTADO_ENVIADO)
        self.assertEqual(set(envio.manuales.values_list("id", flat=True)), {manual_uno.id, manual_dos.id})

    @patch("clinica.views.enviar_documento_whatsapp")
    @patch("clinica.views.subir_documento_whatsapp", side_effect=["media-1", "media-2"])
    def test_envia_varios_manuales_por_whatsapp_y_registra_historial(self, subir_mock, enviar_mock):
        ConfiguracionCRM.objects.create(empresa=self.empresa, whatsapp_activo=True)
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            manual_uno = self.crear_manual("Manual uno")
            manual_dos = self.crear_manual("Manual dos")

            response = self.client.post(
                reverse("clinica_enviar_manuales_pdf", args=[self.empresa.slug]),
                {
                    "manuales": [str(manual_uno.id), str(manual_dos.id)],
                    "paciente_id": str(self.paciente.id),
                    "canal": "whatsapp",
                },
            )

        self.assertRedirects(response, reverse("clinica_manuales_pdf", args=[self.empresa.slug]))
        self.assertEqual(subir_mock.call_count, 2)
        self.assertEqual(enviar_mock.call_count, 2)
        envio = EnvioManualPDF.objects.get()
        self.assertEqual(envio.canal, EnvioManualPDF.CANAL_WHATSAPP)
        self.assertEqual(envio.destinatario, "50499998888")
        self.assertEqual(envio.estado, EnvioManualPDF.ESTADO_ENVIADO)

    def test_rechaza_manual_de_otra_empresa_sin_enviar_ni_registrar(self):
        otra_empresa = Empresa.objects.create(
            nombre="Otra clínica",
            slug="otra_clinica_manual_operativo",
            rtn="MANUALES-002",
            tipo_solucion="clinica",
            estado_licencia="activa",
        )
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            manual_valido = self.crear_manual("Manual válido")
            manual_ajeno = ManualReceta.objects.create(
                empresa=otra_empresa,
                titulo="Manual ajeno",
                archivo=SimpleUploadedFile("ajeno.pdf", b"%PDF-1.4 ajeno", content_type="application/pdf"),
            )
            response = self.client.post(
                reverse("clinica_enviar_manuales_pdf", args=[self.empresa.slug]),
                {
                    "manuales": [str(manual_valido.id), str(manual_ajeno.id)],
                    "paciente_id": str(self.paciente.id),
                    "canal": "correo",
                },
            )

        self.assertRedirects(response, reverse("clinica_manuales_pdf", args=[self.empresa.slug]))
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(EnvioManualPDF.objects.exists())

    def test_advierte_si_el_paciente_no_tiene_el_contacto_elegido(self):
        self.paciente.correo = ""
        self.paciente.save(update_fields=["correo"])
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            manual = self.crear_manual("Manual sin contacto")
            response = self.client.post(
                reverse("clinica_enviar_manuales_pdf", args=[self.empresa.slug]),
                {"manuales": [str(manual.id)], "paciente_id": str(self.paciente.id), "canal": "correo"},
                follow=True,
            )
        self.assertContains(response, "no tiene correo electrónico registrado")
        self.assertFalse(EnvioManualPDF.objects.exists())


class PermisoAdministrarManualesPDFTests(TestCase):
    def setUp(self):
        self.empresa = Empresa.objects.create(
            nombre="Admin manual",
            slug="admin_manual",
            rtn="ADM-MAN-1",
            tipo_solucion="clinica",
        )
        modulo, _ = Modulo.objects.get_or_create(codigo="clinica_medica", defaults={"nombre": "Clínica Médica"})
        EmpresaModulo.objects.create(empresa=self.empresa, modulo=modulo, activo=True)
        ConfiguracionAvanzadaEmpresa.objects.create(
            empresa=self.empresa,
            manuales_pdf_habilitados=True,
        )
        self.rol = RolSistema.objects.create(
            nombre="Admin de manuales",
            codigo="admin-manuales-test",
            activo=True,
            es_rol_clinico=True,
            usa_permisos_clinicos_granulares=True,
            puede_administrar_manuales_pdf=True,
        )
        self.usuario = get_user_model().objects.create_user(
            username="admin-manuales",
            password="test",
            empresa=self.empresa,
        )
        UsuarioEmpresaPermiso.objects.create(usuario=self.usuario, empresa=self.empresa, rol_sistema=self.rol)
        self.client.force_login(self.usuario)

    def test_administrar_implica_poder_visualizar_el_pdf_sin_habilitar_envio(self):
        self.assertTrue(self.usuario.tiene_permiso_erp("puede_ver_manuales_pdf", self.empresa))
        self.assertFalse(self.usuario.tiene_permiso_erp("puede_enviar_manuales_pdf", self.empresa))

    def test_admin_de_manuales_accede_a_configuracion_sin_ver_otras_opciones_clinicas(self):
        response = self.client.get(reverse("clinica_configuracion", args=[self.empresa.slug]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Manuales PDF")
        self.assertNotContains(response, "Profesionales")
        self.assertNotContains(response, "Servicios y categorías de citas")

    def test_empresa_sin_configuracion_no_muestra_ni_abre_manuales_pdf(self):
        self.empresa.configuracion_avanzada.manuales_pdf_habilitados = False
        self.empresa.configuracion_avanzada.save(update_fields=["manuales_pdf_habilitados"])

        dashboard = self.client.get(reverse("dashboard", args=[self.empresa.slug]))
        modulo = self.client.get(reverse("clinica_manuales_pdf", args=[self.empresa.slug]))

        self.assertNotContains(dashboard, "Manuales PDF")
        self.assertEqual(modulo.status_code, 404)
