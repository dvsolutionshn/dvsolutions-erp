from datetime import date
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace

from PIL import Image
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from unittest.mock import patch

from core.models import Empresa, EmpresaModulo, Modulo, RolSistema, Usuario
from facturacion.models import Cliente

from .models import CampaniaMarketing, ConfiguracionCRM, EnvioCampania, PlantillaMensaje
from .services import subir_media_whatsapp


class CRMTests(TestCase):
    def setUp(self):
        self.empresa = Empresa.objects.create(
            nombre="Hospital Mia",
            slug="hospital_mia",
            rtn="08011999111113",
            estado_licencia="activa",
        )
        self.modulo, _ = Modulo.objects.get_or_create(
            codigo="crm_marketing",
            defaults={"nombre": "CRM, Marketing y Agenda", "es_comercial": True},
        )
        EmpresaModulo.objects.create(empresa=self.empresa, modulo=self.modulo, activo=True)
        self.modulo_citas, _ = Modulo.objects.get_or_create(
            codigo="agenda_citas",
            defaults={"nombre": "Citas", "es_comercial": True},
        )
        EmpresaModulo.objects.create(empresa=self.empresa, modulo=self.modulo_citas, activo=True)
        self.rol = RolSistema.objects.create(
            nombre="CRM Total",
            codigo="crm-total",
            puede_crm=True,
            puede_campanias=True,
            puede_citas=True,
            puede_configuracion_crm=True,
        )
        self.usuario = Usuario.objects.create_user(
            username="crmuser",
            password="pass12345",
            empresa=self.empresa,
            rol_sistema=self.rol,
        )

    def test_dashboard_crm_responde_para_empresa_con_modulo(self):
        self.client.login(username="crmuser", password="pass12345")
        response = self.client.get(reverse("crm_dashboard", args=[self.empresa.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "CRM y Marketing")

    def test_agenda_citas_responde_como_modulo_separado(self):
        self.client.login(username="crmuser", password="pass12345")
        response = self.client.get(reverse("agenda_citas", args=[self.empresa.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Calendario de Citas")

    def test_preparar_envios_de_campania_crea_whatsapp_por_cliente(self):
        cliente = Cliente.objects.create(
            empresa=self.empresa,
            nombre="Paciente Demo",
            telefono_whatsapp="99999999",
            fecha_nacimiento=date(1990, 4, 18),
            acepta_promociones=True,
        )
        plantilla = PlantillaMensaje.objects.create(
            empresa=self.empresa,
            nombre="Promo Test",
            tipo="promocion",
            canal="whatsapp",
            mensaje="Hola {{cliente}}, promocion especial de {{empresa}}.",
        )
        campania = CampaniaMarketing.objects.create(
            empresa=self.empresa,
            nombre="Campania Abril",
            plantilla=plantilla,
            audiencia="promociones",
            fecha_programada=timezone.now(),
        )

        self.client.login(username="crmuser", password="pass12345")
        response = self.client.post(reverse("crm_preparar_envios_campania", args=[self.empresa.slug, campania.id]))

        self.assertRedirects(response, reverse("crm_ver_campania", args=[self.empresa.slug, campania.id]))
        envio = EnvioCampania.objects.get(campania=campania, cliente=cliente)
        self.assertIn("Paciente Demo", envio.mensaje)
        self.assertIn("50499999999", envio.whatsapp_url)

    @patch("crm.views.enviar_plantilla_marketing_whatsapp")
    def test_enviar_campania_por_api_actualiza_envios(self, mock_enviar):
        mock_enviar.return_value = {"messages": [{"id": "wamid.test"}]}
        cliente = Cliente.objects.create(
            empresa=self.empresa,
            nombre="Paciente API",
            telefono_whatsapp="99999998",
            acepta_promociones=True,
        )
        plantilla = PlantillaMensaje.objects.create(
            empresa=self.empresa,
            nombre="Promo API",
            tipo="promocion",
            canal="whatsapp",
            mensaje="Hola {{cliente}}, tenemos una promocion.",
        )
        campania = CampaniaMarketing.objects.create(
            empresa=self.empresa,
            nombre="Campania API",
            plantilla=plantilla,
            audiencia="promociones",
        )
        EnvioCampania.objects.create(
            campania=campania,
            cliente=cliente,
            canal="whatsapp",
            mensaje="Hola Paciente API, tenemos una promocion.",
            estado="preparado",
        )
        config, _ = ConfiguracionCRM.objects.get_or_create(empresa=self.empresa)
        config.whatsapp_activo = True
        config.whatsapp_phone_number_id = "123"
        config.whatsapp_token = "token-test"
        config.save()

        self.client.login(username="crmuser", password="pass12345")
        response = self.client.post(reverse("crm_enviar_campania_whatsapp_api", args=[self.empresa.slug, campania.id]))

        self.assertRedirects(response, reverse("crm_ver_campania", args=[self.empresa.slug, campania.id]))
        envio = EnvioCampania.objects.get(campania=campania, cliente=cliente)
        self.assertEqual(envio.estado, "enviado")
        mock_enviar.assert_called_once()

    @patch("crm.views.enviar_plantilla_whatsapp")
    def test_enviar_campania_prueba_masiva_usa_hello_world(self, mock_enviar):
        mock_enviar.return_value = {"messages": [{"id": "wamid.hello"}]}
        cliente = Cliente.objects.create(
            empresa=self.empresa,
            nombre="Paciente Demo",
            telefono_whatsapp="99999996",
            acepta_promociones=True,
        )
        plantilla = PlantillaMensaje.objects.create(
            empresa=self.empresa,
            nombre="Promo Interna",
            tipo="promocion",
            canal="whatsapp",
            mensaje="Mensaje interno.",
        )
        campania = CampaniaMarketing.objects.create(
            empresa=self.empresa,
            nombre="Campania Demo",
            plantilla=plantilla,
            audiencia="promociones",
        )
        EnvioCampania.objects.create(
            campania=campania,
            cliente=cliente,
            canal="whatsapp",
            mensaje="Mensaje interno.",
            estado="preparado",
        )
        config, _ = ConfiguracionCRM.objects.get_or_create(empresa=self.empresa)
        config.whatsapp_activo = True
        config.whatsapp_phone_number_id = "123"
        config.whatsapp_token = "token-test"
        config.whatsapp_plantilla_prueba = "hello_world"
        config.whatsapp_idioma_plantilla = "en_US"
        config.save()

        self.client.login(username="crmuser", password="pass12345")
        response = self.client.post(reverse("crm_enviar_campania_plantilla_prueba", args=[self.empresa.slug, campania.id]))

        self.assertRedirects(response, reverse("crm_ver_campania", args=[self.empresa.slug, campania.id]))
        envio = EnvioCampania.objects.get(campania=campania, cliente=cliente)
        self.assertEqual(envio.estado, "enviado")
        mock_enviar.assert_called_once_with(config, "99999996", nombre_plantilla="hello_world", idioma="en_US")

    @patch("crm.views.enviar_plantilla_marketing_whatsapp")
    @patch("crm.views.subir_media_whatsapp")
    def test_enviar_campania_por_api_usa_imagen_si_existe(self, mock_subir, mock_enviar_plantilla):
        mock_subir.return_value = "media-test"
        mock_enviar_plantilla.return_value = {"messages": [{"id": "wamid.image"}]}
        cliente = Cliente.objects.create(
            empresa=self.empresa,
            nombre="Paciente Imagen",
            telefono_whatsapp="99999997",
            acepta_promociones=True,
        )
        plantilla = PlantillaMensaje.objects.create(
            empresa=self.empresa,
            nombre="Promo Imagen",
            tipo="promocion",
            canal="whatsapp",
            mensaje="Promocion con imagen.",
            imagen_promocional="crm/promociones/demo.png",
        )
        campania = CampaniaMarketing.objects.create(
            empresa=self.empresa,
            nombre="Campania Imagen",
            plantilla=plantilla,
            audiencia="promociones",
        )
        EnvioCampania.objects.create(
            campania=campania,
            cliente=cliente,
            canal="whatsapp",
            mensaje="Promocion con imagen.",
            estado="preparado",
        )
        config, _ = ConfiguracionCRM.objects.get_or_create(empresa=self.empresa)
        config.whatsapp_activo = True
        config.whatsapp_phone_number_id = "123"
        config.whatsapp_token = "token-test"
        config.save()

        self.client.login(username="crmuser", password="pass12345")
        response = self.client.post(reverse("crm_enviar_campania_whatsapp_api", args=[self.empresa.slug, campania.id]))

        self.assertRedirects(response, reverse("crm_ver_campania", args=[self.empresa.slug, campania.id]))
        mock_subir.assert_called_once()
        mock_enviar_plantilla.assert_called_once()
        self.assertEqual(mock_enviar_plantilla.call_args.kwargs["media_id"], "media-test")

    @patch("crm.services._post_multipart")
    def test_subir_media_acepta_ruta_de_imagefield(self, mock_post):
        mock_post.return_value = {"id": "media-test"}
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temporal:
            temporal.write(b"\x89PNG\r\n\x1a\n")
            ruta_imagen = Path(temporal.name)
        fake_file = SimpleNamespace(path=str(ruta_imagen))
        config, _ = ConfiguracionCRM.objects.get_or_create(empresa=self.empresa)
        config.whatsapp_phone_number_id = "123"

        try:
            media_id = subir_media_whatsapp(config, fake_file)
        finally:
            ruta_imagen.unlink(missing_ok=True)

        self.assertEqual(media_id, "media-test")
        mock_post.assert_called_once()

    @patch("crm.services._post_multipart")
    def test_subir_media_optimiza_imagen_grande(self, mock_post):
        mock_post.return_value = {"id": "media-optimizada"}
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temporal:
            ruta_imagen = Path(temporal.name)
        try:
            imagen = Image.frombytes("RGB", (2200, 2200), os.urandom(2200 * 2200 * 3))
            imagen.save(ruta_imagen, format="PNG")
            fake_file = SimpleNamespace(path=str(ruta_imagen))
            config, _ = ConfiguracionCRM.objects.get_or_create(empresa=self.empresa)
            config.whatsapp_phone_number_id = "123"

            media_id = subir_media_whatsapp(config, fake_file)

            self.assertEqual(media_id, "media-optimizada")
            _, kwargs = mock_post.call_args
            self.assertEqual(kwargs, {})
            args = mock_post.call_args.args
            self.assertEqual(args[5], "image/jpeg")
            self.assertTrue(Path(args[4]).name.endswith(".jpg"))
        finally:
            ruta_imagen.unlink(missing_ok=True)
