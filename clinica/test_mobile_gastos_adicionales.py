import json
import re
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core import mail
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import Empresa, EmpresaModulo, Modulo, RegistroAuditoria, RolSistema, Usuario, UsuarioEmpresaPermiso
from crm.models import ConfiguracionCRM
from facturacion.models import CAI, Factura, MovimientoInventario, PagoFactura, Producto, TipoImpuesto
from .models import GastoAdicional, Paciente, ProfesionalSalud
from .services_gastos_adicionales import convertir_gasto_adicional
from . import views_gastos_adicionales as views


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class MobileGastosAdicionalesIntegrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.today = timezone.localdate()
        cls.empresas = {}
        cls.pacientes = {}
        cls.profesionales = {}
        modulos = {}
        for codigo in ("clinica_medica", "facturacion", "gastos_adicionales", "agenda_citas"):
            modulos[codigo], _ = Modulo.objects.get_or_create(codigo=codigo, defaults={"nombre": codigo})
        for indice, slug in enumerate(("hospital_mia", "medical_spa", "serviciosmedicos", "luque_aestetic"), start=1):
            empresa = Empresa.objects.create(
                nombre=f"Clínica móvil {slug}", slug=slug, rtn=f"GAM-{indice}",
                tipo_solucion="clinica", estado_licencia="activa",
            )
            cls.empresas[slug] = empresa
            for modulo in modulos.values():
                EmpresaModulo.objects.get_or_create(empresa=empresa, modulo=modulo, defaults={"activo": True})
            cls.pacientes[slug] = Paciente.objects.create(
                empresa=empresa, nombre=f"Paciente {slug}", identidad=f"080119999100{indice}",
                expediente_codigo=f"GAM-{indice}", correo=f"paciente-{indice}@example.com",
                telefono=f"5049999000{indice}", whatsapp=f"5049999000{indice}",
            )
            cls.profesionales[slug] = ProfesionalSalud.objects.create(empresa=empresa, nombre="Dr. Luis González")
        cls.empresa = cls.empresas["hospital_mia"]
        cls.paciente = cls.pacientes[cls.empresa.slug]
        cls.rol = RolSistema.objects.create(
            nombre="Clínica móvil GA", codigo="mobile-ga-regression",
            es_rol_clinico=True, usa_permisos_clinicos_granulares=True,
            puede_citas=True, puede_ver_calendario=True, puede_clinica=True,
            puede_pacientes=True, puede_ver_pacientes=True, puede_ver_datos_generales_paciente=True,
            puede_facturas=True, puede_ver_facturas=True, puede_crear_facturas=True,
            puede_editar_facturas=True, puede_punto_venta=True, puede_inventario=True,
            puede_productos=True, puede_ver_gastos_adicionales=True, puede_crear_gastos_adicionales=True,
            puede_editar_gastos_adicionales=True, puede_enviar_gastos_adicionales=True,
            puede_convertir_gastos_adicionales_factura=True,
        )
        cls.usuario = Usuario.objects.create_user(
            username="mobile-ga-user", password="test-password", empresa=cls.empresa, rol_sistema=cls.rol,
        )
        cls.usuario.empresas_acceso.add(*cls.empresas.values())
        cls.impuesto = TipoImpuesto.objects.create(nombre="ISV móvil GA", porcentaje=15)
        cls.producto = Producto.objects.create(
            empresa=cls.empresa, nombre="Material y servicio adicional", codigo="MGA-01", precio=Decimal("115.00"),
            tipo_item="servicio", controla_inventario=False, impuesto_predeterminado=cls.impuesto,
        )
        cls.producto_spa = Producto.objects.create(
            empresa=cls.empresas["medical_spa"], nombre="Servicio compartido SPA", precio=Decimal("230.00"),
            tipo_item="servicio", controla_inventario=False, impuesto_predeterminado=cls.impuesto,
        )
        cls.cai = CAI.objects.create(
            empresa=cls.empresa, numero_cai="CAI-MOBILE-GA", establecimiento="001", punto_emision="001",
            tipo_documento="01", rango_inicial=1, rango_final=100, correlativo_actual=0,
            fecha_activacion=cls.today-timedelta(days=30), fecha_limite=cls.today+timedelta(days=365),
        )

    def setUp(self):
        self.client.force_login(self.usuario)

    def url(self, nombre, gasto=None, empresa=None, mobile=True):
        kwargs = {"empresa_slug": (empresa or self.empresa).slug}
        if gasto is not None:
            kwargs["gasto_id"] = gasto.pk
        return reverse(nombre, kwargs=kwargs) + ("?app=1" if mobile else "")

    def datos(self, empresa=None, **overrides):
        empresa = empresa or self.empresa
        datos = {
            "app": "1", "paciente": self.pacientes[empresa.slug].pk, "fecha": self.today.isoformat(),
            "tipo_cirugia": "rinoplastia", "profesional": "", "observacion": "Material adicional para recuperación.",
            "lineas": json.dumps([{"producto_id": self.producto.pk, "cantidad": "2.00", "precio_unitario": "115.00"}]),
            "accion": "guardar",
        }
        datos.update(overrides)
        return datos

    def crear(self, empresa=None, mobile=True, **overrides):
        empresa = empresa or self.empresa
        datos = self.datos(empresa, **overrides)
        if not mobile:
            datos.pop("app")
        response = self.client.post(self.url("clinica_gasto_adicional_crear", empresa=empresa, mobile=mobile), datos)
        self.assertEqual(response.status_code, 302)
        return GastoAdicional.objects.filter(empresa=empresa).latest("pk")

    def test_four_clinics_share_mobile_views_and_keep_web_templates(self):
        for empresa in self.empresas.values():
            with self.subTest(empresa=empresa.slug):
                gasto = self.crear(empresa=empresa)
                for route, mobile_template, web_template in (
                    ("clinica_gastos_adicionales", "mobile_gastos_list.html", "gastos_adicionales.html"),
                    ("clinica_gasto_adicional_crear", "mobile_gastos_form.html", "gastos_adicionales_form.html"),
                    ("clinica_gasto_adicional_editar", "mobile_gastos_form.html", "gastos_adicionales_form.html"),
                    ("clinica_gasto_adicional_detalle", "mobile_gastos_detail.html", "gastos_adicionales_detalle.html"),
                ):
                    documento = gasto if route in ("clinica_gasto_adicional_editar", "clinica_gasto_adicional_detalle") else None
                    mobile = self.client.get(self.url(route, documento, empresa))
                    self.assertEqual(mobile.status_code, 200)
                    self.assertTemplateUsed(mobile, "clinica/"+mobile_template)
                    self.assertIn("no-store", mobile["Cache-Control"])
                    web = self.client.get(self.url(route, documento, empresa, mobile=False))
                    self.assertEqual(web.status_code, 200)
                    self.assertTemplateUsed(web, "clinica/"+web_template)
                    self.assertTemplateNotUsed(web, "clinica/"+mobile_template)

    def test_mobile_list_filters_and_patient_search_remain_company_isolated(self):
        propio = self.crear()
        ajeno = self.crear(empresa=self.empresas["medical_spa"])
        filters = {"paciente": self.paciente.nombre, "numero": propio.numero, "desde": self.today.isoformat(),
                   "hasta": self.today.isoformat(), "estado": "pendiente", "app": "1"}
        response = self.client.get(self.url("clinica_gastos_adicionales"), filters)
        self.assertEqual(response.status_code, 200)
        self.assertEqual([gasto.pk for gasto in response.context["gastos"]], [propio.pk])
        self.assertNotContains(response, ajeno.paciente.nombre)
        for route in ("clinica_gasto_adicional_detalle", "clinica_gasto_adicional_editar", "clinica_gasto_adicional_pdf"):
            with self.subTest(route=route):
                self.assertEqual(self.client.get(self.url(route, ajeno)).status_code, 404)
        pacientes = self.client.get(self.url("clinica_gastos_adicionales_pacientes_buscar")).json()["results"]
        self.assertEqual({paciente["id"] for paciente in pacientes}, {self.paciente.pk})
        self.assertEqual(self.client.get(self.url("clinica_gastos_adicionales"), {
            "app": "1", "paciente_id": ajeno.paciente_id,
        }).status_code, 404)
        productos = self.client.get(self.url("clinica_gastos_adicionales_productos_buscar")).json()["results"]
        self.assertIn(self.producto_spa.pk, {producto["id"] for producto in productos})

    def test_mobile_create_and_edit_use_the_web_json_lines_and_same_totals(self):
        web = self.crear(mobile=False)
        mobile = self.crear()
        fields = ("paciente_id", "fecha", "profesional_id", "tipo_cirugia", "tipo_cirugia_nombre",
                  "profesional_nombre", "observacion", "precio_incluye_impuesto", "subtotal", "total")
        self.assertEqual(tuple(getattr(mobile, campo) for campo in fields), tuple(getattr(web, campo) for campo in fields))
        self.assertEqual(list(mobile.lineas.values_list("producto_id", "cantidad", "precio_unitario", "subtotal")),
                         list(web.lineas.values_list("producto_id", "cantidad", "precio_unitario", "subtotal")))
        response = self.client.post(self.url("clinica_gasto_adicional_editar", mobile), self.datos(
            lineas=json.dumps([{"producto_id": self.producto.pk, "cantidad": "3.00", "precio_unitario": "115.00"}]),
            observacion="Nota actualizada desde el celular", total="99999.99",
        ))
        self.assertRedirects(response, self.url("clinica_gasto_adicional_detalle", mobile), fetch_redirect_response=False)
        mobile.refresh_from_db()
        self.assertEqual(mobile.total, Decimal("345.00"))
        self.assertEqual(mobile.lineas.get().cantidad, Decimal("3.00"))
        self.assertEqual(mobile.actualizado_por, self.usuario)
        self.assertEqual(mobile.observacion, "Nota actualizada desde el celular")
        self.assertTrue(RegistroAuditoria.objects.filter(
            empresa=self.empresa, app_label="clinica", modelo="gastoadicional", objeto_id=str(mobile.pk),
            usuario=self.usuario, accion="modificar",
        ).exists())
        detail = self.client.get(self.url("clinica_gasto_adicional_detalle", mobile))
        self.assertContains(detail, "Historial del gasto")
        self.assertTrue(detail.context["mobile_gasto_audit"])
        self.assertContains(detail, "Nota actualizada desde el celular")
        self.assertFalse(Factura.objects.exists())
        self.assertFalse(MovimientoInventario.objects.exists())
        self.cai.refresh_from_db()
        self.assertEqual(self.cai.correlativo_actual, 0)

    def test_invalid_mobile_post_preserves_patient_quantities_notes_and_saved_document(self):
        gasto = self.crear()
        submitted_lines = [{"producto_id": self.producto.pk, "cantidad": "2.75", "precio_unitario": "115.00"}]
        nota = "Esta observación debe mantenerse mientras se corrige la cirugía."
        response = self.client.post(self.url("clinica_gasto_adicional_editar", gasto), self.datos(
            tipo_cirugia="", observacion=nota, lineas=json.dumps(submitted_lines),
        ))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "clinica/mobile_gastos_form.html")
        self.assertIn("tipo_cirugia", response.context["form"].errors)
        self.assertEqual(response.context["paciente_inicial"]["id"], self.paciente.pk)
        self.assertEqual(response.context["lineas_iniciales"][0]["cantidad"], "2.75")
        self.assertEqual(response.context["form"]["observacion"].value(), nota)
        self.assertContains(response, nota)
        gasto.refresh_from_db()
        self.assertEqual(gasto.total, Decimal("230.00"))
        self.assertEqual(gasto.lineas.get().cantidad, Decimal("2.00"))
        self.assertNotEqual(gasto.observacion, nota)

    def test_mobile_professional_is_readonly_local_and_cirugia_required(self):
        candy = ProfesionalSalud.objects.create(empresa=self.empresa, nombre="Dra. Candy Luque")
        response = self.client.get(self.url("clinica_gasto_adicional_crear"))
        self.assertTrue(response.context["form"].fields["profesional"].disabled)
        self.assertContains(response, "Dr. Luis González")
        invalid = self.client.post(self.url("clinica_gasto_adicional_crear"), self.datos(tipo_cirugia="inventada"))
        self.assertEqual(invalid.status_code, 200)
        self.assertIn("tipo_cirugia", invalid.context["form"].errors)
        self.assertFalse(GastoAdicional.objects.exists())
        gasto = self.crear(profesional=candy.pk)
        self.assertEqual(gasto.profesional_id, self.profesionales[self.empresa.slug].pk)
        self.assertEqual(gasto.tipo_cirugia_nombre, "Rinoplastia")
        self.assertEqual(gasto.profesional_nombre, "Dr. Luis González")

    def test_reader_actions_hidden_and_backend_denies_mutations_without_middleware(self):
        gasto = self.crear()
        for field in ("puede_crear_gastos_adicionales", "puede_editar_gastos_adicionales",
                      "puede_enviar_gastos_adicionales", "puede_convertir_gastos_adicionales_factura"):
            setattr(self.rol, field, False)
        self.rol.save()
        detalle = self.client.get(self.url("clinica_gasto_adicional_detalle", gasto))
        self.assertEqual(detalle.status_code, 200)
        for flag in ("can_create_gasto", "can_edit_gasto", "can_send_gasto", "can_convert_gasto"):
            self.assertFalse(detalle.context[flag])
        for key in ("app_gasto_edit_url", "app_gasto_send_email_url", "app_gasto_send_whatsapp_url", "app_gasto_convert_url"):
            self.assertNotContains(detalle, f'action="{detalle.context[key]}"')
            self.assertNotContains(detalle, f'href="{detalle.context[key]}"')
        for sheet in ("gastoActionsSheet", "gastoWhatsAppSheet", "gastoEmailSheet", "gastoConvertSheet"):
            self.assertNotContains(detalle, f'id="{sheet}"')
        for route, function in (
            ("clinica_gasto_adicional_crear", views.crear), ("clinica_gasto_adicional_editar", views.editar),
            ("clinica_gasto_adicional_enviar_correo", views.enviar_correo),
            ("clinica_gasto_adicional_enviar_whatsapp", views.enviar_whatsapp),
            ("clinica_gasto_adicional_convertir", views.convertir),
        ):
            with self.subTest(route=route):
                documento = None if function == views.crear else gasto
                response = self.client.post(self.url(route, documento), self.datos())
                self.assertIn(response.status_code, (302, 403))
                request = RequestFactory().post(self.url(route, documento), self.datos())
                request.user = self.usuario
                kwargs = {"empresa_slug": self.empresa.slug}
                if documento:
                    kwargs["gasto_id"] = documento.pk
                with self.assertRaises(PermissionDenied):
                    function(request, **kwargs)
        gasto.refresh_from_db()
        self.assertIsNone(gasto.factura_id)

    def test_creator_without_history_uses_own_mobile_form_after_saving(self):
        rol = RolSistema.objects.create(nombre="Solo crear GA móvil", codigo="mobile-ga-creator", puede_crear_gastos_adicionales=True)
        UsuarioEmpresaPermiso.objects.create(usuario=self.usuario, empresa=self.empresa, rol_sistema=rol)
        response = self.client.post(self.url("clinica_gasto_adicional_crear"), self.datos(accion="pdf"))
        self.assertRedirects(response, self.url("clinica_gasto_adicional_crear"), fetch_redirect_response=False)
        self.assertEqual(GastoAdicional.objects.filter(empresa=self.empresa).count(), 1)
        page = self.client.get(response.url)
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "Guardar y generar PDF")
        self.assertFalse(page.context["can_view_gastos"])
        self.assertIn(self.client.get(self.url("clinica_gastos_adicionales")).status_code, (302, 403))

    @patch("clinica.views_gastos_adicionales.generar_pdf_bytes", return_value=b"%PDF-shared-gastos-engine")
    def test_mobile_pdf_uses_same_bytes_permissions_and_generate_action_keeps_mobile(self, pdf_mock):
        gasto = self.crear()
        mobile = self.client.get(self.url("clinica_gasto_adicional_pdf", gasto))
        web = self.client.get(self.url("clinica_gasto_adicional_pdf", gasto, mobile=False))
        self.assertEqual(mobile.content, web.content)
        self.assertEqual(mobile.content, b"%PDF-shared-gastos-engine")
        self.assertEqual(mobile["Content-Type"], "application/pdf")
        self.assertEqual(pdf_mock.call_count, 2)
        self.assertEqual(pdf_mock.call_args.args[0], self.empresa)
        response = self.client.post(self.url("clinica_gasto_adicional_editar", gasto), self.datos(accion="pdf"))
        self.assertRedirects(response, self.url("clinica_gasto_adicional_detalle", gasto)+"&pdf=1", fetch_redirect_response=False)
        detail = self.client.get(response.url)
        self.assertTrue(detail.context["mobile_gasto_pdf_requested"])
        self.rol.puede_ver_gastos_adicionales = False
        self.rol.save(update_fields=["puede_ver_gastos_adicionales"])
        blocked = self.client.get(self.url("clinica_gasto_adicional_pdf", gasto))
        self.assertIn(blocked.status_code, (302, 403))
        self.assertEqual(pdf_mock.call_count, 2)

    def test_mobile_conversion_calls_original_service_once_per_post_and_is_idempotent(self):
        gasto = self.crear()
        original = (gasto.total, gasto.observacion, list(gasto.lineas.values_list("pk", "cantidad", "precio_unitario")))
        with patch("clinica.views_gastos_adicionales.convertir_gasto_adicional", wraps=convertir_gasto_adicional) as service:
            first = self.client.post(self.url("clinica_gasto_adicional_convertir", gasto), {"app": "1"})
            gasto.refresh_from_db()
            factura = gasto.factura
            expected = reverse("editar_factura", args=[self.empresa.slug, factura.pk])+"?app=1"
            self.assertRedirects(first, expected, fetch_redirect_response=False)
            second = self.client.post(self.url("clinica_gasto_adicional_convertir", gasto), {"app": "1"})
            self.assertRedirects(second, expected, fetch_redirect_response=False)
            self.assertEqual(service.call_count, 2)
        self.assertEqual(Factura.objects.count(), 1)
        self.assertEqual(factura.estado, "borrador")
        self.assertFalse(factura.numero_factura)
        self.assertEqual(factura.cliente.empresa_id, self.empresa.pk)
        self.assertEqual(factura.total, gasto.total)
        self.assertFalse(PagoFactura.objects.exists())
        self.assertFalse(MovimientoInventario.objects.exists())
        self.cai.refresh_from_db()
        self.assertEqual(self.cai.correlativo_actual, 0)
        frozen = self.client.post(self.url("clinica_gasto_adicional_editar", gasto), self.datos(observacion="No guardar después de convertir"))
        self.assertRedirects(frozen, self.url("clinica_gasto_adicional_detalle", gasto), fetch_redirect_response=False)
        gasto.refresh_from_db()
        self.assertEqual((gasto.total, gasto.observacion, list(gasto.lineas.values_list("pk", "cantidad", "precio_unitario"))), original)

    def test_conversion_requires_existing_invoice_review_permissions_and_active_module(self):
        gasto = self.crear()
        for permiso in ("puede_crear_facturas", "puede_editar_facturas", "puede_ver_facturas"):
            with self.subTest(permiso=permiso):
                setattr(self.rol, permiso, False)
                self.rol.save(update_fields=[permiso])
                detail = self.client.get(self.url("clinica_gasto_adicional_detalle", gasto))
                self.assertFalse(detail.context["can_convert_gasto"])
                with patch("clinica.views_gastos_adicionales.convertir_gasto_adicional") as service:
                    self.assertEqual(self.client.post(self.url("clinica_gasto_adicional_convertir", gasto), {"app": "1"}).status_code, 403)
                    service.assert_not_called()
                setattr(self.rol, permiso, True)
                self.rol.save(update_fields=[permiso])
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo__codigo="facturacion").update(activo=False)
        self.assertFalse(self.client.get(self.url("clinica_gasto_adicional_detalle", gasto)).context["can_convert_gasto"])
        self.assertEqual(self.client.post(self.url("clinica_gasto_adicional_convertir", gasto), {"app": "1"}).status_code, 403)
        self.assertFalse(Factura.objects.exists())

    @patch("clinica.views_gastos_adicionales.generar_pdf_bytes", return_value=b"%PDF-gastos-send")
    def test_mobile_email_and_whatsapp_reuse_existing_transports_and_keep_mobile_return(self, pdf_mock):
        gasto = self.crear()
        email = self.client.post(self.url("clinica_gasto_adicional_enviar_correo", gasto), {"app": "1"})
        self.assertRedirects(email, self.url("clinica_gasto_adicional_detalle", gasto), fetch_redirect_response=False)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.paciente.correo])
        self.assertEqual(mail.outbox[0].attachments[0].content, b"%PDF-gastos-send")
        config = ConfiguracionCRM.objects.create(empresa=self.empresa, whatsapp_activo=True)
        with patch("clinica.views_gastos_adicionales.subir_documento_whatsapp", return_value="mobile-ga-media") as upload, \
             patch("clinica.views_gastos_adicionales.enviar_documento_whatsapp") as send:
            response = self.client.post(self.url("clinica_gasto_adicional_enviar_whatsapp", gasto), {"app": "1"})
            self.assertRedirects(response, self.url("clinica_gasto_adicional_detalle", gasto), fetch_redirect_response=False)
            self.assertEqual(upload.call_args.args[0], config)
            self.assertEqual(send.call_args.args[:3], (config, self.paciente.whatsapp, "mobile-ga-media"))
        self.assertEqual(pdf_mock.call_count, 2)
        for route in ("clinica_gasto_adicional_enviar_correo", "clinica_gasto_adicional_enviar_whatsapp", "clinica_gasto_adicional_convertir"):
            self.assertIn(self.client.get(self.url(route, gasto)).status_code, (302, 405))

    def test_send_failures_keep_mobile_destination_and_do_not_use_other_company_config(self):
        gasto = self.crear()
        ConfiguracionCRM.objects.create(empresa=self.empresas["medical_spa"], whatsapp_activo=True)
        with patch("clinica.views_gastos_adicionales.subir_documento_whatsapp") as upload:
            response = self.client.post(self.url("clinica_gasto_adicional_enviar_whatsapp", gasto), {"app": "1"})
            self.assertRedirects(response, self.url("clinica_gasto_adicional_detalle", gasto), fetch_redirect_response=False)
            upload.assert_not_called()
        with patch("clinica.views_gastos_adicionales.generar_pdf_bytes") as pdf_mock:
            invalid = self.client.post(self.url("clinica_gasto_adicional_enviar_correo", gasto), {"app": "1", "email": "no-es-email"})
            self.assertRedirects(invalid, self.url("clinica_gasto_adicional_detalle", gasto), fetch_redirect_response=False)
            pdf_mock.assert_not_called()
        self.assertEqual(len(mail.outbox), 0)

    def test_additive_modules_hub_preserves_trained_home_cards_navigation_and_existing_urls(self):
        response = self.client.get(reverse("agenda_mobile", args=[self.empresa.slug]))
        self.assertEqual(response.status_code, 200)
        contenido = response.content.decode()
        self.assertTrue(response.context["puede_ver_modulos_app"])
        self.assertContains(response, 'id="appMoreModulesEntry"', count=1)
        self.assertContains(response, 'id="mas-funciones-app"', count=1)
        self.assertEqual(
            re.findall(r'class="mobile-home-card ([^\"]+)"', contenido)[:5],
            ["patients", "invoice", "invoice-list", "inventory", "calendar"],
        )
        for panel in ("inicio", "pacientes-app", "patient-profile-app", "factura-app", "facturas-app",
                      "productos-app", "producto-detalle-app", "agenda-app"):
            self.assertContains(response, f'id="{panel}"', count=1)
        navegacion = re.search(r'<nav class="bottom">(.*?)</nav>', contenido, re.S).group(1)
        for label in ("Inicio", "Pacientes", "Citas", "Facturar"):
            self.assertIn(f"<span>{label}</span>", navegacion)
        self.assertIn('class="fab" type="button" data-open-editor', navegacion)
        self.assertNotIn("mas-funciones-app", navegacion)
        for panel in ("pacientes-app", "factura-app", "agenda-app"):
            self.assertIn(f'href="#{panel}" data-app-open="{panel}"', navegacion)
        for route in ("agenda_crear_paciente_rapido", "agenda_buscar_pacientes", "punto_venta"):
            self.assertIn(reverse(route, args=[self.empresa.slug]), contenido)
        gastos = next(item for item in response.context["modulos_app"] if item["nombre"] == "Gastos Adicionales")
        self.assertEqual(gastos["estado"], "disponible")
        self.assertEqual(gastos["url"], self.url("clinica_gastos_adicionales"))
        for modulo in response.context["modulos_app"]:
            if modulo["estado"] == "pendiente":
                self.assertFalse(modulo.get("url"))
            elif modulo["url"].startswith(reverse("agenda_mobile", args=[self.empresa.slug])+"#"):
                target = modulo["url"].split("#", 1)[1]
                anchors = [tag for tag in re.findall(r"<a\b[^>]*>", contenido)
                           if f'href="{modulo["url"]}"' in tag]
                self.assertTrue(any(f'data-app-open="{target}"' in tag for tag in anchors), target)

    def test_modules_endpoint_is_clinic_scoped_private_data_free_and_company_role_filtered(self):
        for empresa in self.empresas.values():
            with self.subTest(empresa=empresa.slug):
                response = self.client.get(reverse("agenda_mobile_modules", args=[empresa.slug]))
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertEqual(payload["version"], 1)
                self.assertEqual(payload["empresa"]["slug"], empresa.slug)
                gastos = next(item for item in payload["modulos"] if item["nombre"] == "Gastos Adicionales")
                self.assertEqual(gastos["url"], self.url("clinica_gastos_adicionales", empresa=empresa))
                self.assertNotIn(self.pacientes[empresa.slug].nombre, response.content.decode())
                self.assertNotIn(self.pacientes[empresa.slug].correo, response.content.decode())
        rol = RolSistema.objects.create(
            nombre="Solo agenda móvil", codigo="mobile-ga-modules-agenda-only",
            es_rol_clinico=True, usa_permisos_clinicos_granulares=True,
            puede_citas=True, puede_ver_calendario=True,
        )
        spa = self.empresas["medical_spa"]
        UsuarioEmpresaPermiso.objects.create(usuario=self.usuario, empresa=spa, rol_sistema=rol)
        spa_endpoint = reverse("agenda_mobile_modules", args=[spa.slug])
        limitado = self.client.get(spa_endpoint)
        self.assertEqual(limitado.status_code, 200)
        self.assertNotIn("Gastos Adicionales", {modulo["nombre"] for modulo in limitado.json()["modulos"]})
        pagina = self.client.get(reverse("agenda_mobile", args=[spa.slug]))
        self.assertFalse(any(modulo["nombre"] == "Gastos Adicionales" for modulo in pagina.context["modulos_app"]))
        self.assertNotContains(pagina, f'href="{self.url("clinica_gastos_adicionales", empresa=spa)}"')
        self.assertIn(self.client.get(self.url("clinica_gastos_adicionales", empresa=spa)).status_code, (302, 403))
        rol.puede_crear_gastos_adicionales = True
        rol.save(update_fields=["puede_crear_gastos_adicionales"])
        creator_payload = self.client.get(spa_endpoint).json()
        creator_entry = next(modulo for modulo in creator_payload["modulos"] if modulo["nombre"] == "Gastos Adicionales")
        self.assertEqual(creator_entry["url"], self.url("clinica_gasto_adicional_crear", empresa=spa))
        rol.refresh_from_db()
        self.assertFalse(rol.puede_ver_gastos_adicionales)
        self.assertFalse(rol.puede_editar_gastos_adicionales)
        self.client.logout()
        self.assertIn(self.client.get(spa_endpoint).status_code, (302, 401, 403))

    def test_disabled_gastos_module_or_other_clinic_is_not_advertised_or_authorized(self):
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo__codigo="gastos_adicionales").update(activo=False)
        response = self.client.get(reverse("agenda_mobile", args=[self.empresa.slug]))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(any(modulo["nombre"] == "Gastos Adicionales" for modulo in response.context["modulos_app"]))
        self.assertIn(self.client.get(self.url("clinica_gastos_adicionales")).status_code, (302, 404))
        fuera = Empresa.objects.create(nombre="Clínica fuera de la fase inicial", slug="otra_clinica", rtn="GA-MOBILE-OTHER", tipo_solucion="clinica")
        self.usuario.empresas_acceso.add(fuera)
        for modulo in Modulo.objects.filter(codigo__in=("clinica_medica", "facturacion", "agenda_citas", "gastos_adicionales")):
            EmpresaModulo.objects.get_or_create(empresa=fuera, modulo=modulo, defaults={"activo": True})
        app = self.client.get(reverse("agenda_mobile", args=[fuera.slug]))
        self.assertEqual(app.status_code, 200)
        self.assertFalse(app.context["puede_ver_modulos_app"])
        self.assertNotContains(app, 'id="appMoreModulesEntry"')
        self.assertNotContains(app, 'id="mas-funciones-app"')
        self.assertIn(self.client.get(reverse("agenda_mobile_modules", args=[fuera.slug])).status_code, (403, 404))
        self.assertIn(self.client.get(self.url("clinica_gastos_adicionales", empresa=fuera)).status_code, (302, 404))

    def test_registry_does_not_link_unavailable_inventory_or_pos_and_rejects_post(self):
        self.rol.puede_inventario = False
        self.rol.puede_productos = True
        self.rol.puede_crear_facturas = True
        self.rol.puede_ver_facturas = False
        self.rol.puede_punto_venta = False
        self.rol.puede_crear_citas = True
        self.rol.save()
        endpoint = reverse("agenda_mobile_modules", args=[self.empresa.slug])
        modulos = {modulo["key"]: modulo for modulo in self.client.get(endpoint).json()["modulos"]}
        self.assertEqual(modulos["inventario"]["estado"], "pendiente")
        self.assertFalse(modulos["inventario"]["url"])
        self.assertEqual(modulos["facturacion"]["estado"], "pendiente")
        self.assertFalse(modulos["facturacion"]["url"])
        app = self.client.get(reverse("agenda_mobile", args=[self.empresa.slug]))
        self.assertNotContains(app, 'id="productos-app"')
        self.assertContains(app, 'class="mobile-home-card invoice" href="#factura-app"')
        blocked = self.client.post(endpoint, {"puede_inventario": "1", "puede_punto_venta": "1"})
        self.assertEqual(blocked.status_code, 405)
        self.rol.refresh_from_db()
        self.assertFalse(self.rol.puede_inventario)
        self.assertFalse(self.rol.puede_punto_venta)
