from django.test import TestCase
from django.urls import reverse

from facturacion import tests as fixtures


class ModoPagoPorEmpresaTests(TestCase):
    setUp = fixtures.FacturacionTests.setUp
    crear_factura_con_linea = fixtures.FacturacionTests.crear_factura_con_linea

    def test_empresa_general_muestra_selector_de_metodo_en_pago_modal(self):
        factura = self.crear_factura_con_linea()

        response = self.client.get(
            reverse("registrar_pago", args=[self.empresa.slug, factura.id]) + "?modal=1"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<h3>Registrar pago</h3>", html=True)
        self.assertContains(response, "Seleccione metodo")
        self.assertNotContains(response, "<h3>Pago rapido</h3>", html=True)
        self.assertNotContains(response, '<div class="erp-info-value">Pago mixto</div>', html=True)

    def test_empresas_clinicas_conservan_pago_mixto_predeterminado(self):
        factura = self.crear_factura_con_linea()

        for slug in (
            "hospital_mia",
            "serviciosmedicos",
            "luque_aestetic",
            "medical_spa",
        ):
            with self.subTest(empresa=slug):
                self.empresa.slug = slug
                self.empresa.save(update_fields=["slug"])

                response = self.client.get(
                    reverse("registrar_pago", args=[slug, factura.id]) + "?modal=1"
                )

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "<h3>Pago rapido</h3>", html=True)
                self.assertContains(response, '<div class="erp-info-value">Pago mixto</div>', html=True)

    def test_empresa_general_edita_pago_con_formulario_simple(self):
        factura = self.crear_factura_con_linea()
        pago = factura.pagos_facturacion.create(
            fecha=factura.fecha_emision,
            monto="25.00",
            metodo="efectivo",
        )

        response = self.client.get(
            reverse("editar_pago_factura", args=[self.empresa.slug, factura.id, pago.id]) + "?modal=1"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Seleccione metodo")
        self.assertNotContains(response, '<div class="erp-info-value">Pago mixto</div>', html=True)
