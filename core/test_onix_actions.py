import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse
from django.utils import timezone

from facturacion.models import Cliente, Factura, LineaFactura, Producto, TipoImpuesto

from core.models import (
    AccionOnix,
    ConfiguracionOnix,
    ConversacionOnix,
    Empresa,
    EmpresaModulo,
    Modulo,
    RolSistema,
    Usuario,
)
from core.onix import _ejecutar_herramienta


class OnixInvoiceActionTests(TestCase):
    def setUp(self):
        self.empresa = Empresa.objects.create(
            nombre="Demo 1",
            slug="demo_1",
            rtn="08011999007701",
        )
        modulo = Modulo.objects.create(nombre="Facturacion", codigo="facturacion")
        EmpresaModulo.objects.create(empresa=self.empresa, modulo=modulo, activo=True)
        self.usuario = Usuario.objects.create_user(
            username="onix_facturador",
            password="pass12345",
            empresa=self.empresa,
            es_administrador_empresa=True,
        )
        self.configuracion = ConfiguracionOnix.objects.create(
            empresa=self.empresa,
            activo=True,
            herramientas_consulta_activas=True,
            herramientas_accion_activas=True,
        )
        self.conversacion = ConversacionOnix.objects.create(
            empresa=self.empresa,
            usuario=self.usuario,
        )
        self.impuesto = TipoImpuesto.objects.create(nombre="ISV 15", porcentaje="15.00", activo=True)
        self.cliente = Cliente.objects.create(
            empresa=self.empresa,
            nombre="Cliente Mensual",
            rtn="08011999007702",
        )
        self.producto = Producto.objects.create(
            empresa=self.empresa,
            nombre="Servicio mensual",
            codigo="MENSUAL",
            precio="1000.00",
            controla_inventario=False,
            tipo_item="servicio",
            impuesto_predeterminado=self.impuesto,
        )

    def _argumentos(self):
        return {
            "cliente_id": self.cliente.id,
            "moneda": "HNL",
            "tipo_cambio": None,
            "fecha_emision": None,
            "fecha_vencimiento": None,
            "items": [
                {
                    "producto_id": self.producto.id,
                    "cantidad": "2",
                    "precio_unitario": None,
                    "descuento_porcentaje": "0",
                    "comentario": "Mensualidad",
                }
            ],
        }

    def _preparar(self):
        return _ejecutar_herramienta(
            "preparar_factura",
            self._argumentos(),
            empresa=self.empresa,
            usuario=self.usuario,
            conversacion=self.conversacion,
        )

    def test_preparar_factura_genera_vista_previa_sin_crear_documento(self):
        resultado = self._preparar()

        self.assertTrue(resultado["ok"])
        self.assertTrue(resultado["requires_confirmation"])
        self.assertEqual(Factura.objects.count(), 0)
        accion = AccionOnix.objects.get()
        self.assertEqual(accion.estado, AccionOnix.ESTADO_PENDIENTE)
        self.assertEqual(resultado["action"]["client"]["name"], self.cliente.nombre)
        self.assertEqual(resultado["action"]["subtotal"], "2000.00")
        self.assertEqual(resultado["action"]["tax"], "300.00")
        self.assertEqual(resultado["action"]["total"], "2300.00")

    def test_confirmar_accion_crea_un_solo_borrador_aunque_se_reintente(self):
        resultado = self._preparar()
        accion_id = resultado["action"]["id"]
        self.client.login(username=self.usuario.username, password="pass12345")
        url = reverse("asistente_accion", args=[self.empresa.slug, accion_id])

        primera = self.client.post(url, {"decision": "confirmar"})
        segunda = self.client.post(url, {"decision": "confirmar"})

        self.assertEqual(primera.status_code, 200)
        self.assertEqual(segunda.status_code, 200)
        self.assertEqual(Factura.objects.count(), 1)
        factura = Factura.objects.get()
        self.assertEqual(factura.empresa, self.empresa)
        self.assertEqual(factura.cliente, self.cliente)
        self.assertEqual(factura.estado, "borrador")
        self.assertEqual(factura.total, 2300)
        self.assertEqual(LineaFactura.objects.get().comentario, "Mensualidad")
        self.assertEqual(primera.json()["action"]["result"]["invoice_id"], factura.id)
        self.assertEqual(segunda.json()["action"]["result"]["invoice_id"], factura.id)

    def test_descartar_accion_no_crea_factura(self):
        resultado = self._preparar()
        self.client.login(username=self.usuario.username, password="pass12345")

        response = self.client.post(
            reverse("asistente_accion", args=[self.empresa.slug, resultado["action"]["id"]]),
            {"decision": "cancelar"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["action"]["status"], AccionOnix.ESTADO_CANCELADA)
        self.assertEqual(Factura.objects.count(), 0)

    def test_vista_previa_vencida_no_crea_factura_y_conserva_el_estado(self):
        resultado = self._preparar()
        accion = AccionOnix.objects.get(pk=resultado["action"]["id"])
        accion.expira_en = timezone.now() - timedelta(seconds=1)
        accion.save(update_fields=["expira_en"])
        self.client.login(username=self.usuario.username, password="pass12345")

        response = self.client.post(
            reverse("asistente_accion", args=[self.empresa.slug, accion.id]),
            {"decision": "confirmar"},
        )

        self.assertEqual(response.status_code, 400)
        accion.refresh_from_db()
        self.assertEqual(accion.estado, AccionOnix.ESTADO_EXPIRADA)
        self.assertEqual(Factura.objects.count(), 0)

    def test_preparacion_rechaza_producto_de_otra_empresa(self):
        otra_empresa = Empresa.objects.create(
            nombre="Empresa privada",
            slug="empresa-privada-onix",
            rtn="08011999007703",
        )
        producto_privado = Producto.objects.create(
            empresa=otra_empresa,
            nombre="Producto privado",
            codigo="PRIVADO",
            precio="999.00",
            controla_inventario=False,
            tipo_item="servicio",
            impuesto_predeterminado=self.impuesto,
        )
        argumentos = self._argumentos()
        argumentos["items"][0]["producto_id"] = producto_privado.id

        resultado = _ejecutar_herramienta(
            "preparar_factura",
            argumentos,
            empresa=self.empresa,
            usuario=self.usuario,
            conversacion=self.conversacion,
        )

        self.assertIn("otra empresa", resultado["error"].lower())
        self.assertEqual(AccionOnix.objects.count(), 0)
        self.assertEqual(Factura.objects.count(), 0)

    def test_confirmacion_vuelve_a_validar_permiso_de_facturacion(self):
        resultado = self._preparar()
        rol_sin_permiso = RolSistema.objects.create(
            nombre="Consulta",
            codigo="consulta-onix",
            puede_facturas=True,
            puede_ver_facturas=True,
            puede_crear_facturas=False,
        )
        self.usuario.es_administrador_empresa = False
        self.usuario.rol_sistema = rol_sin_permiso
        self.usuario.save(update_fields=["es_administrador_empresa", "rol_sistema"])
        self.client.login(username=self.usuario.username, password="pass12345")

        response = self.client.post(
            reverse("asistente_accion", args=[self.empresa.slug, resultado["action"]["id"]]),
            {"decision": "confirmar"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertIn("permiso", response.json()["error"].lower())
        self.assertEqual(Factura.objects.count(), 0)

    def test_otro_usuario_no_puede_confirmar_la_accion(self):
        resultado = self._preparar()
        intruso = Usuario.objects.create_user(
            username="otro_usuario_onix",
            password="pass12345",
            empresa=self.empresa,
            es_administrador_empresa=True,
        )
        self.client.login(username=intruso.username, password="pass12345")

        response = self.client.post(
            reverse("asistente_accion", args=[self.empresa.slug, resultado["action"]["id"]]),
            {"decision": "confirmar"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(Factura.objects.count(), 0)

    @override_settings(
        ONIX_ENABLED=True,
        OPENAI_API_KEY="test-key",
        ONIX_ALLOWED_COMPANY_SLUGS=["demo_1"],
        ONIX_MODEL="gpt-5.6-luna",
        ONIX_TRIAL_MODE=True,
        ONIX_TRIAL_MONTHLY_TOKEN_LIMIT=100000,
    )
    @patch("openai.OpenAI")
    def test_ia_puede_solicitar_la_preparacion_y_devuelve_tarjeta(self, openai_client):
        llamada = SimpleNamespace(
            type="function_call",
            name="preparar_factura",
            arguments=json.dumps(self._argumentos()),
            call_id="call_factura_onix",
        )
        uso = SimpleNamespace(
            input_tokens=30,
            output_tokens=10,
            total_tokens=40,
            input_tokens_details=SimpleNamespace(cached_tokens=0),
        )
        openai_client.return_value.responses.create.side_effect = [
            SimpleNamespace(id="resp_accion_1", output=[llamada], output_text="", usage=uso),
            SimpleNamespace(
                id="resp_accion_2",
                output=[],
                output_text="Prepare la vista previa. Revisala y confirma para crear el borrador.",
                usage=uso,
            ),
        ]
        self.client.login(username=self.usuario.username, password="pass12345")

        response = self.client.post(
            reverse("asistente_consulta", args=[self.empresa.slug]),
            {
                "pregunta": "Prepara una factura mensual para Cliente Mensual con dos servicios",
                "pagina": f"/{self.empresa.slug}/dashboard/",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["assistant_mode"], "ai")
        self.assertEqual(len(payload["actions"]), 1)
        self.assertEqual(payload["actions"][0]["total"], "2300.00")
        self.assertEqual(Factura.objects.count(), 0)
        primera_llamada = openai_client.return_value.responses.create.call_args_list[0].kwargs
        self.assertIn("preparar_factura", [tool["name"] for tool in primera_llamada["tools"]])
        self.assertFalse(primera_llamada["parallel_tool_calls"])
