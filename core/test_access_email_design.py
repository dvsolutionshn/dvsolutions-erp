from datetime import datetime, timezone as datetime_timezone
from email import policy
from email.parser import BytesParser
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings

from .access_tokens import _crear_mensaje_acceso
from .models import Empresa, TokenAccesoUsuario, Usuario


@override_settings(
    DEFAULT_FROM_EMAIL="Oasis <oasis@example.test>",
    TIME_ZONE="America/Tegucigalpa",
)
class AccessEmailDesignTests(SimpleTestCase):
    def setUp(self):
        self.empresa = Empresa(nombre="Empresa de ejemplo")
        self.usuario = Usuario(
            first_name="Daniel", last_name="Varela", email="daniel@example.test", empresa=self.empresa,
        )
        # La fecha UTC cae al dia siguiente; el correo debe mostrar el dia local.
        self.token = SimpleNamespace(fecha_expiracion=datetime(2026, 10, 22, 1, 30, tzinfo=datetime_timezone.utc))
        self.url = "https://erp.example.test/acceso/establecer/enlace-de-prueba/"

    def correo(self, tipo=TokenAccesoUsuario.TIPO_INVITACION):
        return _crear_mensaje_acceso(self.usuario, self.token, tipo, self.url)

    def test_mime_incluye_texto_html_y_logo_png_inline_con_cid_unico(self):
        mensaje = self.correo()
        mime = BytesParser(policy=policy.default).parsebytes(mensaje.message().as_bytes())
        self.assertEqual(mime.get_content_type(), "multipart/related")
        partes = list(mime.iter_parts())
        self.assertEqual(partes[0].get_content_type(), "multipart/alternative")
        alternativas = list(partes[0].iter_parts())
        self.assertEqual([parte.get_content_type() for parte in alternativas], ["text/plain", "text/html"])
        self.assertIn(self.url, alternativas[0].get_content())
        self.assertIn(self.url, alternativas[1].get_content())
        self.assertEqual(partes[1].get_content_type(), "image/png")
        self.assertEqual(partes[1].get_content_disposition(), "inline")
        self.assertTrue(partes[1].get_payload(decode=True).startswith(b"\x89PNG\r\n\x1a\n"))
        cid = str(partes[1]["Content-ID"]).strip()
        self.assertTrue(cid.startswith("<") and cid.endswith(">"))
        self.assertIn(f'cid:{cid[1:-1]}', alternativas[1].get_content())
        self.assertNotEqual(cid, self.correo().attachments[0]["Content-ID"])
        self.assertEqual(mensaje.to, ["daniel@example.test"])
        self.assertEqual(mensaje.from_email, "Oasis <oasis@example.test>")

    def test_invitacion_y_recuperacion_mantienen_propositos_distintos(self):
        invitacion = self.correo()
        recuperacion = self.correo(TokenAccesoUsuario.TIPO_RECUPERACION)
        self.assertIn("Activar mi acceso", invitacion.alternatives[0].content)
        self.assertIn("Restablecer mi contraseña", recuperacion.alternatives[0].content)
        self.assertNotIn("Activar mi acceso", recuperacion.alternatives[0].content)
        self.assertIn("Oasis", invitacion.subject)
        self.assertIn("Empresa de ejemplo", invitacion.subject)

    def test_identidad_se_escapa_en_html_y_se_conserva_en_texto(self):
        self.usuario.first_name = '<img src=x onerror="alert(1)">'
        self.usuario.last_name = ""
        self.empresa.nombre = 'Diseño & Salud <élite>'
        mensaje = self.correo()
        html = mensaje.alternatives[0].content
        self.assertNotIn(self.usuario.first_name, html)
        self.assertIn("&lt;img", html)
        self.assertIn("Diseño &amp; Salud &lt;élite&gt;", html)
        self.assertIn(self.empresa.nombre, mensaje.body)
        self.assertIn(self.url, mensaje.body)

    def test_caducidad_muestra_fecha_local_y_zona_inequivoca(self):
        for tipo in (TokenAccesoUsuario.TIPO_INVITACION, TokenAccesoUsuario.TIPO_RECUPERACION):
            with self.subTest(tipo=tipo):
                mensaje = self.correo(tipo)
                for contenido in (mensaje.body, mensaje.alternatives[0].content):
                    self.assertIn("21 de octubre de 2026, 19:30", contenido)
                    self.assertIn("hora de Honduras", contenido)
                    self.assertIn("UTC-06:00", contenido)

    def test_sin_empresa_o_nombre_conserva_identidad_y_destino(self):
        self.usuario.empresa = None
        self.usuario.first_name = self.usuario.last_name = ""
        mensaje = self.correo()
        self.assertIn("Oasis", mensaje.subject)
        self.assertIn("daniel@example.test", mensaje.body)
        self.assertIn(self.url, mensaje.body)

    def logo(self, formato="PNG", dimensiones=(720, 180)):
        contenido = BytesIO()
        Image.new("RGBA", dimensiones, (0, 60, 140, 180)).save(contenido, format=formato)
        return SimpleUploadedFile("logo.empresa", contenido.getvalue())

    def test_logo_empresa_inline_tiene_cid_propio_y_conserva_proporciones(self):
        for tipo in (TokenAccesoUsuario.TIPO_INVITACION, TokenAccesoUsuario.TIPO_RECUPERACION):
            with self.subTest(tipo=tipo):
                self.empresa.logo = self.logo()
                mensaje = self.correo(tipo)
                mime = BytesParser(policy=policy.default).parsebytes(mensaje.message().as_bytes())
                partes = list(mime.iter_parts())
                self.assertEqual(len(partes), 3)
                imagen = partes[2]
                self.assertEqual(imagen.get_content_type(), "image/png")
                self.assertEqual(imagen.get_content_disposition(), "inline")
                cid = str(imagen["Content-ID"]).strip()
                self.assertNotEqual(cid, str(partes[1]["Content-ID"]).strip())
                html = mensaje.alternatives[0].content
                self.assertIn(f'src="cid:{cid[1:-1]}"', html)
                self.assertIn('alt="Logo de Empresa de ejemplo" width="180" height="45"', html)
                with Image.open(BytesIO(imagen.get_payload(decode=True))) as logo:
                    self.assertEqual(logo.size, (360, 90))
                self.assertIn(self.empresa.nombre, mensaje.body)
                self.assertIn(self.url, mensaje.body)

    def test_logo_webp_se_convierte_a_png(self):
        self.empresa.logo = self.logo("WEBP", (80, 80))
        mensaje = self.correo()
        self.assertEqual(mensaje.attachments[1].get_content_type(), "image/png")
        self.assertIn('width="64" height="64"', mensaje.alternatives[0].content)

    def test_logo_invalido_o_demasiado_grande_conserva_nombre_y_enlace(self):
        grande = BytesIO()
        Image.new("1", (4000, 4000)).save(grande, format="PNG")
        for contenido in (b"no es una imagen", b"0" * (5 * 1024 * 1024 + 1), grande.getvalue()):
            with self.subTest(bytes=len(contenido)):
                self.empresa.logo = SimpleUploadedFile("logo.png", contenido)
                mensaje = self.correo()
                self.assertEqual(len(mensaje.attachments), 1)
                self.assertIn(self.empresa.nombre, mensaje.alternatives[0].content)
                self.assertIn(self.url, mensaje.body)

    def test_archivo_logo_inaccesible_no_impide_preparar_invitacion(self):
        self.empresa.logo = "logos/no-disponible.png"
        for error in (FileNotFoundError, PermissionError):
            with self.subTest(error=error), patch.object(self.empresa.logo.storage, "open", side_effect=error):
                mensaje = self.correo()
                self.assertEqual(len(mensaje.attachments), 1)
                self.assertIn(self.empresa.nombre, mensaje.alternatives[0].content)
                self.assertIn(self.url, mensaje.body)
