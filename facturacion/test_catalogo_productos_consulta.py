from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from core.models import Empresa, EmpresaModulo, Modulo, RolSistema, Usuario

from .models import Producto


class CatalogoProductosConsultaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            nombre="Clínica de prueba de catálogo", slug="hospital_mia",
            rtn="CATALOGO-CONSULTA", tipo_solucion="clinica", estado_licencia="activa",
        )
        modulo, _ = Modulo.objects.get_or_create(
            codigo="facturacion", defaults={"nombre": "Facturación"},
        )
        EmpresaModulo.objects.create(empresa=cls.empresa, modulo=modulo, activo=True)
        cls.producto = Producto.objects.create(
            empresa=cls.empresa,
            nombre="Producto de recepción",
            codigo="CONSULTA-1",
            precio=Decimal("34.50"),
            descripcion="Detalle íntegro. " * 8 + "Final de la descripción comercial.",
        )
        rol = RolSistema.objects.create(
            nombre="Recepción de prueba", codigo="recepcion-catalogo-consulta",
            es_rol_clinico=True, usa_permisos_clinicos_granulares=True,
            usa_permisos_clinicos_v2=True, usa_permisos_acciones_v3=True,
            puede_productos=True,
        )
        cls.usuario = Usuario.objects.create_user(
            username="recepcion-catalogo-consulta", empresa=cls.empresa, rol_sistema=rol,
        )

    def url(self, vista, *args, app=False):
        ruta = reverse(vista, args=[self.empresa.slug, *args])
        return f"{ruta}?app=1" if app else ruta

    def test_web_y_app_muestran_descripcion_completa_y_bloquean_gestion(self):
        self.client.force_login(self.usuario)
        for app in (False, True):
            with self.subTest(app=app):
                respuesta = self.client.get(self.url("productos_facturacion", app=app))
                self.assertContains(respuesta, self.producto.nombre)
                self.assertContains(respuesta, self.producto.descripcion)
                for vista, args in (
                    ("crear_producto_facturacion", ()),
                    ("editar_producto_facturacion", (self.producto.pk,)),
                    ("eliminar_producto_facturacion", (self.producto.pk,)),
                    ("codigos_barras_productos", ()),
                    ("categorias_farmaceuticas", ()),
                ):
                    with self.subTest(vista=vista):
                        ruta = self.url(vista, *args, app=app)
                        self.assertNotContains(respuesta, self.url(vista, *args))
                        if vista != "eliminar_producto_facturacion":
                            self.assertIn(self.client.get(ruta).status_code, (302, 403))
                        post = self.client.post(ruta, {
                            "producto_id": self.producto.pk,
                            "motivo": "Motivo sintético para baja",
                            "codigo": "CAMBIO-NO-AUTORIZADO",
                            **({"app": "1"} if app else {}),
                        })
                        self.assertIn(post.status_code, (302, 403))
        self.producto.refresh_from_db()
        self.assertEqual(self.producto.codigo, "CONSULTA-1")
        self.assertFalse(self.producto.eliminado)
