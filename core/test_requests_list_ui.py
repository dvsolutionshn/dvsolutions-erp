from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from .models import SolicitudComercial, Usuario


class RequestsListPresentationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = Usuario.objects.create_superuser(username="requests-list-admin")
        cls.member = Usuario.objects.create_user(username="requests-list-member")
        cls.request_a = SolicitudComercial.objects.create(
            nombre_contacto="Contacto solicitudes UI", empresa_interesada="Empresa prospecto UI",
            rtn_empresa="REQUESTS-UI", correo="contacto@example.test", telefono="9999-0000",
            servicio_interes="erp", mensaje="Solicitamos una prueba del sistema.", solicita_prueba=True,
        )
        cls.request_b = SolicitudComercial.objects.create(
            nombre_contacto="Contacto demo UI", correo="demo@example.test", servicio_interes="web",
            mensaje='<script>alert("mensaje")</script>\nSegunda línea del mensaje.', estado="demo",
        )

    def test_listado_conserva_datos_completos_orden_y_resumen_sin_modificar_solicitudes(self):
        self.client.force_login(self.admin)
        before = list(SolicitudComercial.objects.order_by("pk").values())
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse("superadmin_solicitudes"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual([
            query["sql"] for query in queries
            if query["sql"].lstrip().split(maxsplit=1)[0].upper()
            in {"INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "ALTER", "DROP"}
        ], [])
        self.assertEqual(list(SolicitudComercial.objects.order_by("pk").values()), before)
        self.assertEqual([item.pk for item in response.context["solicitudes"]], [self.request_b.pk, self.request_a.pk])
        self.assertEqual(response.context["resumen"], {"total": 2, "nuevas": 1, "prueba": 1, "demo": 1})
        for value in (self.request_a.nombre_contacto, self.request_a.empresa_interesada,
                      self.request_a.rtn_empresa, self.request_a.correo, self.request_a.telefono,
                      self.request_a.mensaje, "Segunda línea del mensaje."):
            self.assertContains(response, value)
        self.assertContains(response, "&lt;script&gt;")
        self.assertNotContains(response, '<script>alert("mensaje")</script>')
        self.assertEqual(response.context["estados_solicitudes"], SolicitudComercial.ESTADO_CHOICES)
        self.assertEqual(response.context["servicios_solicitudes"], SolicitudComercial.SERVICIO_CHOICES)

    def test_listado_sigue_restringido_a_superadministradores(self):
        url = reverse("superadmin_solicitudes")
        self.assertRedirects(self.client.get(url), f"{reverse('superadmin_login')}?next={url}")
        self.client.force_login(self.member)
        self.assertRedirects(self.client.get(url), reverse("superadmin_login"))
