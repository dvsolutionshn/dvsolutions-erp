import json
import os
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch
from unittest import skipIf

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import Empresa, EmpresaModulo, Modulo, RolSistema, Usuario
from crm.models import ConfiguracionCRM
from facturacion.models import CAI, Cliente, Factura, MovimientoInventario, PagoFactura, Producto, TipoImpuesto
from facturacion.services_clientes_compartidos import suspender_sincronizacion_clientes_compartidos
from .models import GastoAdicional, Paciente, ProfesionalSalud
from .services_gastos_adicionales import convertir_gasto_adicional


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class GastosAdicionalesFlujoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(nombre="Mia Medical Spa", slug="medical_spa", rtn="GAV-1", tipo_solucion="clinica")
        cls.otra = Empresa.objects.create(nombre="Hospital MIA", slug="hospital_mia", rtn="GAV-2", tipo_solucion="clinica")
        for codigo in ("clinica_medica", "facturacion", "gastos_adicionales"):
            modulo, _ = Modulo.objects.get_or_create(codigo=codigo, defaults={"nombre": codigo})
            for empresa in (cls.empresa, cls.otra):
                EmpresaModulo.objects.get_or_create(empresa=empresa, modulo=modulo)
        cls.usuario = Usuario.objects.create_superuser(username="ga-flujo", password=None, empresa=cls.empresa)
        with suspender_sincronizacion_clientes_compartidos():
            cls.cliente = Cliente.objects.create(empresa=cls.empresa, nombre="Ana García", rtn="0801199990001", correo="ana@example.com", telefono="50499999999")
        cls.paciente = Paciente.objects.get(empresa=cls.empresa, cliente=cls.cliente)
        cls.paciente_otro = Paciente.objects.create(empresa=cls.otra, nombre="Paciente externo", identidad="0801199990002", expediente_codigo="OTRO-001")
        cls.profesional_otro = ProfesionalSalud.objects.create(empresa=cls.otra, nombre="Profesional externo")
        cls.impuesto = TipoImpuesto.objects.create(nombre="ISV de prueba", porcentaje=15)
        cls.producto = Producto.objects.create(empresa=cls.empresa, nombre="Terapia de recuperación", codigo="TER-01", precio="115.00", tipo_item="servicio", controla_inventario=False, impuesto_predeterminado=cls.impuesto)
        cls.producto_otro = Producto.objects.create(empresa=cls.otra, nombre="Producto externo", precio=10, impuesto_predeterminado=cls.impuesto)
        cls.cai = CAI.objects.create(empresa=cls.empresa, numero_cai="GA-FLUJO-CAI", establecimiento="001", punto_emision="001", tipo_documento="01", rango_inicial=1, rango_final=100, correlativo_actual=0, fecha_activacion=timezone.localdate()-timedelta(days=1), fecha_limite=timezone.localdate()+timedelta(days=30))

    def setUp(self):
        self.client.force_login(self.usuario)

    def url(self, nombre, gasto=None, empresa=None):
        kwargs = {"empresa_slug": (empresa or self.empresa).slug}
        if gasto:
            kwargs["gasto_id"] = gasto.pk
        return reverse(nombre, kwargs=kwargs)

    def datos(self, **overrides):
        data = {"paciente": self.paciente.pk, "fecha": timezone.localdate().isoformat(), "profesional": "", "tipo_cirugia": "rinoplastia", "observacion": "Material y atención adicional.", "lineas": json.dumps([{"producto_id": self.producto.pk, "cantidad": "2.00", "precio_unitario": "115.00"}]), "accion": "guardar"}
        data.update(overrides)
        return data

    def crear(self):
        response = self.client.post(self.url("clinica_gasto_adicional_crear"), self.datos())
        self.assertEqual(response.status_code, 302)
        return GastoAdicional.objects.get(empresa=self.empresa)

    def test_creacion_historial_busquedas_y_expediente(self):
        gasto = self.crear()
        self.assertEqual(gasto.numero, "GA-000001")
        self.assertEqual(gasto.total, Decimal("230.00"))
        self.assertEqual(gasto.creado_por, self.usuario)
        self.assertFalse(Factura.objects.exists())
        self.cai.refresh_from_db()
        self.assertEqual(self.cai.correlativo_actual, 0)
        response = self.client.get(self.url("clinica_gastos_adicionales"), {"paciente": "Ana", "numero": "GA-000001", "desde": gasto.fecha.isoformat(), "hasta": gasto.fecha.isoformat(), "estado": "pendiente"})
        self.assertContains(response, "GA-000001")
        self.assertEqual(response.context["gastos"].paginator.count, 1)
        response = self.client.get(reverse("clinica_paciente_detalle", kwargs={"empresa_slug": self.empresa.slug, "paciente_id": self.paciente.pk}))
        self.assertContains(response, "GA-000001")
        pacientes = [dato["id"] for dato in self.client.get(self.url("clinica_gastos_adicionales_pacientes_buscar")).json()["results"]]
        self.assertIn(self.paciente.pk, pacientes)
        self.assertNotIn(self.paciente_otro.pk, pacientes)
        productos = [dato["id"] for dato in self.client.get(self.url("clinica_gastos_adicionales_productos_buscar")).json()["results"]]
        self.assertIn(self.producto.pk, productos)
        self.assertIn(self.producto_otro.pk, productos)

    def test_modulo_independiente_funciona_con_clinica_general_desactivada(self):
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo__codigo="clinica_medica").update(activo=False)
        response = self.client.get(reverse("dashboard", kwargs={"slug": self.empresa.slug}))
        self.assertContains(response, "Gastos Adicionales")
        self.assertEqual(self.client.get(self.url("clinica_gasto_adicional_crear")).status_code, 200)
        self.assertEqual(self.client.get(self.url("clinica_gastos_adicionales_productos_buscar")).status_code, 200)
        self.crear()

    def test_selecciones_ajenas_rechazadas_sin_documento_parcial(self):
        empresa_ajena = Empresa.objects.create(nombre="Catálogo ajeno", slug="catalogo_ajeno_ga", rtn="GAV-3")
        producto_ajeno = Producto.objects.create(empresa=empresa_ajena, nombre="Producto no compartido", precio=10)
        for cambio in ({"paciente": self.paciente_otro.pk}, {"lineas": json.dumps([{"producto_id": producto_ajeno.pk, "cantidad": 1, "precio_unitario": 10}])}):
            with self.subTest(cambio=cambio):
                response = self.client.post(self.url("clinica_gasto_adicional_crear"), self.datos(**cambio))
                self.assertEqual(response.status_code, 200)
                self.assertFalse(GastoAdicional.objects.exists())
        # El profesional es un dato fijo: un ID manipulado se ignora en el
        # servidor, sin impedir la captura ni asociar personal de otra empresa.
        response = self.client.post(self.url("clinica_gasto_adicional_crear"), self.datos(profesional=self.profesional_otro.pk))
        self.assertEqual(response.status_code, 302)
        gasto = GastoAdicional.objects.get(empresa=self.empresa)
        self.assertIsNone(gasto.profesional_id)
        for route in ("clinica_gasto_adicional_detalle", "clinica_gasto_adicional_editar", "clinica_gasto_adicional_pdf"):
            self.assertEqual(self.client.get(self.url(route, gasto, self.otra)).status_code, 404)
        self.assertEqual(self.client.post(self.url("clinica_gasto_adicional_convertir", gasto, self.otra)).status_code, 404)

    def test_importes_invalidos_no_crean_gasto(self):
        for cantidad, precio in (("0", "10"), ("-1", "10"), ("NaN", "10"), ("1.001", "10"), ("1", "-2"), ("1", "Infinity"), ("1", "2.001"), ("99999999.99", "9999999999.99")):
            with self.subTest(cantidad=cantidad, precio=precio):
                detalle = json.dumps([{"producto_id": self.producto.pk, "cantidad": cantidad, "precio_unitario": precio}])
                response = self.client.post(self.url("clinica_gasto_adicional_crear"), self.datos(lineas=detalle))
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["errores_lineas"])
                self.assertFalse(GastoAdicional.objects.exists())

    def test_ids_manipulados_no_generan_errores_de_servidor(self):
        for valor in ("²", "٩", "99999999999999999999999999", "0", True, None):
            with self.subTest(valor=valor):
                detalle = json.dumps([{"producto_id": valor, "cantidad": "1", "precio_unitario": "10"}])
                response = self.client.post(self.url("clinica_gasto_adicional_crear"), self.datos(lineas=detalle))
                self.assertEqual(response.status_code, 200)
                self.assertFalse(GastoAdicional.objects.exists())
        self.assertEqual(self.client.get(self.url("clinica_gasto_adicional_crear"), {"paciente": "²"}).status_code, 404)
        self.assertEqual(self.client.get(self.url("clinica_gastos_adicionales"), {"paciente_id": "²"}).status_code, 404)

    def test_edicion_y_post_doble_conversion_no_modifican_original(self):
        gasto = self.crear()
        self.client.post(self.url("clinica_gasto_adicional_editar", gasto), self.datos(observacion="Observación revisada."))
        gasto.refresh_from_db()
        self.assertEqual(gasto.observacion, "Observación revisada.")
        original = (gasto.numero, gasto.total, list(gasto.lineas.values_list("id", "cantidad", "precio_unitario")))
        conversion = self.client.post(self.url("clinica_gasto_adicional_convertir", gasto))
        gasto.refresh_from_db()
        self.assertRedirects(conversion, reverse("editar_factura", kwargs={"empresa_slug": self.empresa.slug, "factura_id": gasto.factura_id}), fetch_redirect_response=False)
        self.assertEqual(gasto.factura.estado, "borrador")
        self.assertEqual(gasto.estado, "pendiente")
        self.assertFalse(gasto.factura.numero_factura)
        self.assertFalse(PagoFactura.objects.exists())
        self.assertFalse(MovimientoInventario.objects.exists())
        self.client.post(self.url("clinica_gasto_adicional_convertir", gasto))
        self.assertEqual(Factura.objects.count(), 1)
        self.client.post(self.url("clinica_gasto_adicional_editar", gasto), self.datos(observacion="No debe cambiar"))
        gasto.refresh_from_db()
        self.assertEqual((gasto.numero, gasto.total, list(gasto.lineas.values_list("id", "cantidad", "precio_unitario"))), original)
        self.assertEqual(gasto.observacion, "Observación revisada.")
        self.assertContains(self.client.get(self.url("clinica_gasto_adicional_detalle", gasto)), "Abrir factura")

    def _datos_editar_factura(self, factura, estado):
        linea = factura.lineas.get()
        return {"cliente": factura.cliente_id, "fecha_emision": factura.fecha_emision.isoformat(), "fecha_vencimiento": "", "vendedor": self.usuario.pk, "tipo_cambio": "1.0000", "moneda": "HNL", "estado": estado, "orden_compra_exenta": "", "registro_exonerado": "", "registro_sag": "", "motivo_auditoria": "Revisión del gasto adicional", "lineas-TOTAL_FORMS": "1", "lineas-INITIAL_FORMS": "1", "lineas-MIN_NUM_FORMS": "0", "lineas-MAX_NUM_FORMS": "1000", "lineas-0-id": linea.pk, "lineas-0-factura": factura.pk, "lineas-0-producto": self.producto.pk, "lineas-0-descripcion_manual": linea.descripcion_manual, "lineas-0-cantidad": "2.00", "lineas-0-precio_unitario": "115.00", "lineas-0-descuento_porcentaje": "0", "lineas-0-comentario": linea.comentario, "lineas-0-impuesto": self.impuesto.pk}

    def test_revision_contado_guarda_borrador_y_emite_solo_confirmando_flujo_existente(self):
        gasto = self.crear()
        factura, _ = convertir_gasto_adicional(gasto, self.usuario)
        editar_url = reverse("editar_factura", kwargs={"empresa_slug": self.empresa.slug, "factura_id": factura.pk})
        response = self.client.get(editar_url)
        self.assertEqual(response.context["form"].initial["estado"], "borrador")
        self.assertEqual(response.context["form"].fields["estado"].choices, [("borrador", "Borrador"), ("emitida", "Emitida")])
        response = self.client.post(editar_url, self._datos_editar_factura(factura, "borrador"))
        self.assertEqual(response.status_code, 302, response.context["form"].errors if response.status_code == 200 else "")
        factura.refresh_from_db()
        self.cai.refresh_from_db()
        self.assertEqual(factura.estado, "borrador")
        self.assertEqual(self.cai.correlativo_actual, 0)
        self.assertFalse(PagoFactura.objects.exists())
        response = self.client.post(editar_url, self._datos_editar_factura(factura, "emitida"))
        self.assertEqual(response.status_code, 302)
        factura.refresh_from_db()
        gasto.refresh_from_db()
        self.cai.refresh_from_db()
        self.assertEqual(factura.estado, "emitida")
        self.assertEqual(self.cai.correlativo_actual, 1)
        self.assertEqual(gasto.estado, "facturado")
        self.assertEqual(gasto.total, Decimal("230.00"))
        self.assertEqual(factura.impuesto, Decimal("30.00"))
        self.assertEqual(self.client.get(self.url("clinica_gastos_adicionales"), {"estado": "facturado"}).context["gastos"].paginator.count, 1)

    def test_crear_sin_ver_y_editar_sin_ver_regresan_a_accion_permitida(self):
        for accion in ("crear", "editar"):
            with self.subTest(accion=accion):
                gasto = self.crear() if accion == "editar" else None
                rol = RolSistema.objects.create(nombre=accion, codigo=f"ga-{accion}-solo", **{f"puede_{accion}_gastos_adicionales": True})
                usuario = Usuario.objects.create_user(username=f"ga-{accion}-solo", empresa=self.empresa, rol_sistema=rol)
                self.client.force_login(usuario)
                route = "clinica_gasto_adicional_crear" if accion == "crear" else "clinica_gasto_adicional_editar"
                response = self.client.post(self.url(route, gasto), self.datos())
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.url, self.url(route, gasto))
                self.assertEqual(self.client.get(response.url).status_code, 200)
                self.client.force_login(self.usuario)
                GastoAdicional.objects.all().delete()

    def test_pdf_y_envios_reutilizan_transporte_y_exigen_post(self):
        gasto = self.crear()
        with patch("clinica.views_gastos_adicionales.generar_pdf_bytes", return_value=b"%PDF-test"):
            response = self.client.get(self.url("clinica_gasto_adicional_pdf", gasto))
            self.assertEqual(response["Content-Type"], "application/pdf")
            self.client.post(self.url("clinica_gasto_adicional_enviar_correo", gasto))
            self.assertEqual(len(mail.outbox), 1)
            self.assertEqual(mail.outbox[0].to, [self.paciente.correo])
            self.assertEqual(mail.outbox[0].attachments[0].filename, "GA-000001.pdf")
            ConfiguracionCRM.objects.create(empresa=self.empresa, whatsapp_activo=True)
            with patch("clinica.views_gastos_adicionales.subir_documento_whatsapp", return_value="media-ga") as subir, patch("clinica.views_gastos_adicionales.enviar_documento_whatsapp") as enviar:
                self.client.post(self.url("clinica_gasto_adicional_enviar_whatsapp", gasto))
                self.assertEqual(subir.call_count, 1)
                self.assertEqual(enviar.call_args.args[2], "media-ga")
        for route in ("clinica_gasto_adicional_enviar_correo", "clinica_gasto_adicional_enviar_whatsapp", "clinica_gasto_adicional_convertir"):
            self.assertIn(self.client.get(self.url(route, gasto)).status_code, (302, 405))

    def test_envio_sin_permiso_de_historial_regresa_al_panel(self):
        gasto = self.crear()
        rol = RolSistema.objects.create(nombre="GA solo enviar", codigo="ga-solo-enviar", puede_enviar_gastos_adicionales=True)
        usuario = Usuario.objects.create_user(username="ga-solo-enviar", empresa=self.empresa, rol_sistema=rol)
        self.client.force_login(usuario)
        with patch("clinica.views_gastos_adicionales.generar_pdf_bytes", return_value=b"%PDF-test"):
            response = self.client.post(self.url("clinica_gasto_adicional_enviar_correo", gasto))
        self.assertRedirects(response, reverse("dashboard", kwargs={"slug": self.empresa.slug}), fetch_redirect_response=False)
        self.assertEqual(len(mail.outbox), 1)

    def test_revision_permite_producto_original_inactivado(self):
        gasto = self.crear()
        factura, _ = convertir_gasto_adicional(gasto, self.usuario)
        self.producto.activo = False
        self.producto.save(update_fields=["activo"])
        url = reverse("editar_factura", kwargs={"empresa_slug": self.empresa.slug, "factura_id": factura.pk})
        response = self.client.post(url, self._datos_editar_factura(factura, "borrador"))
        self.assertEqual(response.status_code, 302)
        factura.refresh_from_db()
        self.assertEqual(factura.estado, "borrador")

    @skipIf(os.name == "nt" and "native\\poppler" in os.environ.get("PATH", "").lower(), "DLL de Poppler y GTK en PATH entran en conflicto; ejecutar con PATH de GTK para validar el PDF.")
    def test_pdf_real_contiene_datos_documento_y_no_cai(self):
        from io import BytesIO
        from pypdf import PdfReader
        gasto = self.crear()
        response = self.client.get(self.url("clinica_gasto_adicional_pdf", gasto))
        self.assertEqual(response.status_code, 200)
        texto = "\n".join(pagina.extract_text() for pagina in PdfReader(BytesIO(response.content)).pages)
        for esperado in ("GASTOS ADICIONALES", "GA-000001", "Ana García", "Terapia de recuperación", "230", "No constituye factura fiscal"):
            self.assertIn(esperado, texto)
        self.assertNotIn(self.cai.numero_cai, texto)
