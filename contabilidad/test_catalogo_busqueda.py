"""La consulta del catálogo conserva la jerarquía y el límite de empresa."""

from django.test import TestCase
from django.urls import reverse

from core.models import Empresa, Modulo, PlanComercial, PlanModulo, RolSistema, Usuario
from .catalogo_consulta import ordenar_catalogo
from .models import CuentaContable


class CatalogoBusquedaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        modulo, _ = Modulo.objects.get_or_create(codigo="contabilidad", defaults={"nombre": "Contabilidad"})
        plan = PlanComercial.objects.create(nombre="Consulta catálogo", codigo="consulta-catalogo-busqueda", precio_mensual="199")
        PlanModulo.objects.create(plan=plan, modulo=modulo, activo=True)
        cls.empresa = Empresa.objects.create(nombre="Catálogo propio", slug="catalogo-busqueda", rtn="08011999199021", estado_licencia="activa", plan_comercial=plan)
        cls.otra = Empresa.objects.create(nombre="Catálogo ajeno", slug="otro-catalogo-busqueda", rtn="08011999199022", estado_licencia="activa", plan_comercial=plan)
        rol = RolSistema.objects.create(nombre="Consulta catálogo", codigo="consulta-catalogo-busqueda", puede_contabilidad=True, puede_catalogo_cuentas=True)
        cls.usuario = Usuario.objects.create_user(username="consulta-catalogo-busqueda", empresa=cls.empresa, rol_sistema=rol)
        cls.padre = CuentaContable.objects.create(empresa=cls.empresa, codigo="9", nombre="Bancos", tipo="activo", acepta_movimientos=False)
        cls.hija = CuentaContable.objects.create(empresa=cls.empresa, codigo="1", nombre="Banco Atlántida", tipo="activo", cuenta_padre=cls.padre)
        cls.nieta = CuentaContable.objects.create(empresa=cls.empresa, codigo="100", nombre="Cuenta de ahorro", tipo="activo", cuenta_padre=cls.hija, activa=False)
        cls.ajena = CuentaContable.objects.create(empresa=cls.otra, codigo="SECRETO", nombre="Banco privado de otra empresa", tipo="activo")

    def test_orden_por_relacion_padre_no_por_prefijo_y_sin_consultas_por_nivel(self):
        with self.assertNumQueries(1):
            cuentas = ordenar_catalogo(CuentaContable.objects.filter(empresa=self.empresa).order_by("codigo", "nombre", "id"))
            self.assertEqual([cuenta.pk for cuenta in cuentas], [self.padre.pk, self.hija.pk, self.nieta.pk])
            self.assertEqual([cuenta.nivel_catalogo for cuenta in cuentas], [0, 1, 2])
            self.assertEqual(cuentas[2].padre_catalogo.nombre, "Banco Atlántida")
        self.assertFalse(cuentas[2].activa)

    def test_vista_publica_solo_catalogo_propio_con_busqueda_y_jerarquia(self):
        self.client.force_login(self.usuario)
        respuesta = self.client.get(reverse("catalogo_cuentas", args=[self.empresa.slug]))
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "Buscar por código o nombre")
        self.assertContains(respuesta, 'data-account-name="Banco Atlántida"')
        self.assertContains(respuesta, 'data-account-parent="%s"' % self.padre.pk)
        self.assertNotContains(respuesta, self.ajena.nombre)
        self.assertEqual(respuesta.context["resumen"]["total"], 3)
        self.assertEqual(respuesta.context["resumen"]["activas"], 2)

    def test_usuario_no_consulta_catalogo_de_otra_empresa(self):
        self.client.force_login(self.usuario)
        respuesta = self.client.get(reverse("catalogo_cuentas", args=[self.otra.slug]))
        self.assertIn(respuesta.status_code, (302, 403, 404))
        self.assertNotContains(respuesta, self.ajena.nombre, status_code=respuesta.status_code)

    def test_vinculo_historico_ajeno_no_publica_nombre_y_ciclo_no_bloquea_consulta(self):
        CuentaContable.objects.filter(pk=self.padre.pk).update(cuenta_padre=self.ajena)
        self.client.force_login(self.usuario)
        respuesta = self.client.get(reverse("catalogo_cuentas", args=[self.empresa.slug]))
        self.assertEqual(respuesta.status_code, 200)
        self.assertNotContains(respuesta, self.ajena.nombre)
        self.assertIsNone(respuesta.context["cuentas"][0].padre_catalogo)
        CuentaContable.objects.filter(pk=self.padre.pk).update(cuenta_padre=self.nieta)
        cuentas = ordenar_catalogo(CuentaContable.objects.filter(empresa=self.empresa).order_by("codigo"))
        self.assertEqual(len(cuentas), 3)
        self.assertEqual(len({cuenta.pk for cuenta in cuentas}), 3)
