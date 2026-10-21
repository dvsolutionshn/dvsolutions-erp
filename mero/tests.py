import json

from django.test import TestCase
from django.urls import reverse

from .models import PedidoMero, PuestoMero


class MeroDemoTests(TestCase):
    def test_demo_carga_y_crea_catalogo(self):
        response = self.client.get(reverse("mero:demo"))
        self.assertEqual(response.status_code, 200)
        puesto = PuestoMero.objects.get(slug="baleadas-dona-maria")
        self.assertEqual(puesto.productos.count(), 6)

    def test_flujo_pedido_cocina_entrega(self):
        estado = self.client.get(reverse("mero:estado")).json()
        producto = estado["productos"][0]
        response = self.client.post(
            reverse("mero:crear_pedido"),
            data=json.dumps({
                "vehiculo": "Pickup blanco",
                "tipo_atencion": "carro",
                "items": [{"producto_id": producto["id"], "cantidad": 2}],
            }),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        pedido_id = response.json()["pedido"]["id"]
        pedido = PedidoMero.objects.get(pk=pedido_id)
        self.assertEqual(pedido.estado, "nuevo")
        self.assertEqual(pedido.tipo_atencion, "carro")
        self.assertEqual(str(pedido.total), "50.00")

        for estado_pedido in ("preparando", "listo", "entregado"):
            response = self.client.post(
                reverse("mero:actualizar_pedido", args=[pedido_id]),
                data=json.dumps({"estado": estado_pedido}),
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 200)
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, "entregado")

    def test_no_crea_pedido_vacio(self):
        response = self.client.post(
            reverse("mero:crear_pedido"),
            data=json.dumps({"items": []}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
