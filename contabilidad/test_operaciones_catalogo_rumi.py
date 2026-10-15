from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase

from core.models import Empresa
from facturacion.models import Cliente, Proveedor
from .models import (
    ClasificacionMovimientoBanco,
    ConfiguracionContableEmpresa,
    CuentaContable,
    CuentaFinanciera,
    VinculoCuentaOperativaRumi,
)
from .services import (
    _siguiente_codigo_auxiliar,
    asegurar_clasificaciones_bancarias_base_honduras,
    asegurar_cuenta_contable_cliente,
    asegurar_cuenta_contable_proveedor,
    asegurar_cuentas_financieras_base_honduras,
    cargar_catalogo_base_honduras,
    obtener_cuenta_operativa,
    obtener_o_crear_cuenta_base,
)


class OperacionesCatalogoRumiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.rumi = Empresa.objects.create(nombre="RUMI", slug="rumi", rtn="08011999199001")
        cls.otra = Empresa.objects.create(nombre="Otra empresa", slug="otra-operativa", rtn="08011999199002")
        cls.configuracion = ConfiguracionContableEmpresa.objects.create(
            empresa=cls.rumi, catalogo_rumi_instalado=True
        )
        cls.caja = cls.crear_cuenta("10101002", "Caja general", "activo")
        cls.clientes = cls.crear_cuenta("10205001", "Cuentas por cobrar comerciales", "activo")
        cls.proveedores = cls.crear_cuenta("20109001", "Cuentas por pagar proveedores", "pasivo")
        for clave, cuenta in (("caja", cls.caja), ("clientes", cls.clientes), ("proveedores", cls.proveedores)):
            VinculoCuentaOperativaRumi.objects.create(empresa=cls.rumi, clave=clave, cuenta=cuenta)

    @classmethod
    def crear_cuenta(cls, codigo, nombre, tipo, **kwargs):
        return CuentaContable.objects.create(
            empresa=cls.rumi, codigo=codigo, nombre=nombre, tipo=tipo, **kwargs
        )

    def test_resuelve_cuentas_rumi_sin_recrear_codigos_honduras(self):
        total = CuentaContable.objects.count()
        for funcion in (obtener_o_crear_cuenta_base, obtener_cuenta_operativa):
            for clave, cuenta in (("caja", self.caja), ("clientes", self.clientes), ("proveedores", self.proveedores)):
                with self.subTest(funcion=funcion.__name__, clave=clave):
                    self.assertEqual(funcion(self.rumi, clave).pk, cuenta.pk)
        self.assertEqual(CuentaContable.objects.count(), total)
        self.assertFalse(CuentaContable.objects.filter(empresa=self.rumi, codigo__in=["1101", "111001", "210101"]).exists())

    def test_rol_sin_enlace_no_crea_cuenta_por_defecto(self):
        total = CuentaContable.objects.count()
        for funcion in (obtener_o_crear_cuenta_base, obtener_cuenta_operativa):
            with self.subTest(funcion=funcion.__name__), self.assertRaises(ValidationError):
                funcion(self.rumi, "bancos")
        self.assertEqual(CuentaContable.objects.count(), total)
        self.assertFalse(CuentaContable.objects.filter(empresa=self.rumi, codigo="110201").exists())

    def test_catalogo_honduras_se_rechaza_antes_de_escribir(self):
        total = CuentaContable.objects.count()
        with self.assertRaisesMessage(ValidationError, "catálogo propio"):
            cargar_catalogo_base_honduras(self.rumi)
        self.assertEqual(CuentaContable.objects.count(), total)
        self.assertFalse(CuentaFinanciera.objects.filter(empresa=self.rumi).exists())
        self.assertFalse(ClasificacionMovimientoBanco.objects.filter(empresa=self.rumi).exists())

    def test_generadores_base_no_reactivan_ni_reenlazan_configuracion_rumi(self):
        cuenta_base = self.crear_cuenta("1101", "Caja anterior", "activo")
        financiera = CuentaFinanciera.objects.create(
            empresa=self.rumi, nombre="Caja General", tipo="caja", cuenta_contable=self.caja,
            institucion="Caja RUMI", activa=False,
        )
        clasificacion = ClasificacionMovimientoBanco.objects.create(
            empresa=self.rumi, nombre="Cobro de Clientes", tipo="ingreso",
            cuenta_contable=self.clientes, activa=False,
        )
        self.crear_cuenta("1110", "Clientes anteriores", "activo")
        self.assertEqual(asegurar_cuentas_financieras_base_honduras(self.rumi), {"creadas": 0, "existentes": 0})
        self.assertEqual(asegurar_clasificaciones_bancarias_base_honduras(self.rumi), {"creadas": 0, "existentes": 0})
        financiera.refresh_from_db()
        clasificacion.refresh_from_db()
        self.assertFalse(financiera.activa)
        self.assertEqual(financiera.cuenta_contable_id, self.caja.pk)
        self.assertNotEqual(financiera.cuenta_contable_id, cuenta_base.pk)
        self.assertEqual(financiera.institucion, "Caja RUMI")
        self.assertFalse(clasificacion.activa)
        self.assertEqual(clasificacion.cuenta_contable_id, self.clientes.pk)

    def test_auxiliares_nuevos_se_crean_bajo_cuenta_enlazada_por_empresa(self):
        cliente = Cliente.objects.create(empresa=self.rumi, nombre="Cliente RUMI", rtn="08011999199003")
        proveedor = Proveedor.objects.create(empresa=self.rumi, nombre="Proveedor RUMI")
        cuenta_cliente = asegurar_cuenta_contable_cliente(cliente)
        cuenta_proveedor = asegurar_cuenta_contable_proveedor(proveedor)
        self.assertEqual(cuenta_cliente.cuenta_padre_id, self.clientes.pk)
        self.assertEqual(cuenta_cliente.codigo, f"{self.clientes.codigo}.0001")
        self.assertEqual(cuenta_proveedor.cuenta_padre_id, self.proveedores.pk)
        self.assertEqual(cuenta_proveedor.codigo, f"{self.proveedores.codigo}.0001")
        cliente.refresh_from_db()
        proveedor.refresh_from_db()
        self.assertEqual(cliente.cuenta_contable_id, cuenta_cliente.pk)
        self.assertEqual(proveedor.cuenta_contable_id, cuenta_proveedor.pk)
        self.assertEqual(asegurar_cuenta_contable_cliente(cliente).pk, cuenta_cliente.pk)
        self.assertEqual(asegurar_cuenta_contable_proveedor(proveedor).pk, cuenta_proveedor.pk)
        self.assertFalse(CuentaContable.objects.filter(empresa=self.otra).exists())

    def test_segunda_instancia_del_cliente_reutiliza_auxiliar_guardado(self):
        cliente = Cliente.objects.create(empresa=self.rumi, nombre="Cliente cargado dos veces", rtn="08011999199004")
        copia = Cliente.objects.get(pk=cliente.pk)
        cuenta = asegurar_cuenta_contable_cliente(cliente)
        total = CuentaContable.objects.count()
        self.assertEqual(asegurar_cuenta_contable_cliente(copia).pk, cuenta.pk)
        self.assertEqual(copia.cuenta_contable_id, cuenta.pk)
        self.assertEqual(CuentaContable.objects.count(), total)

    def test_auxiliares_validos_existentes_conservan_codigo_y_padre(self):
        anterior_cliente = self.crear_cuenta("111001.0001", "Cliente histórico", "activo")
        anterior_proveedor = self.crear_cuenta("210101.0001", "Proveedor histórico", "pasivo")
        cliente = Cliente.objects.create(
            empresa=self.rumi, nombre="Cliente histórico", rtn="08011999199005", cuenta_contable=anterior_cliente
        )
        proveedor = Proveedor.objects.create(
            empresa=self.rumi, nombre="Proveedor histórico", cuenta_contable=anterior_proveedor
        )
        self.assertEqual(asegurar_cuenta_contable_cliente(cliente).pk, anterior_cliente.pk)
        self.assertEqual(asegurar_cuenta_contable_proveedor(proveedor).pk, anterior_proveedor.pk)
        anterior_cliente.refresh_from_db()
        anterior_proveedor.refresh_from_db()
        self.assertEqual(anterior_cliente.codigo, "111001.0001")
        self.assertEqual(anterior_proveedor.codigo, "210101.0001")
        self.assertIsNone(anterior_cliente.cuenta_padre_id)
        self.assertIsNone(anterior_proveedor.cuenta_padre_id)

    def test_auxiliar_inactivo_no_se_sustituye_ni_reactiva(self):
        anterior = self.crear_cuenta("111001.0099", "Cliente inactivo", "activo", activa=False)
        cliente = Cliente.objects.create(
            empresa=self.rumi, nombre="Cliente inactivo", rtn="08011999199006", cuenta_contable=anterior
        )
        total = CuentaContable.objects.count()
        with self.assertRaisesMessage(ValidationError, "inactiva"):
            asegurar_cuenta_contable_cliente(cliente)
        cliente.refresh_from_db()
        anterior.refresh_from_db()
        self.assertEqual(cliente.cuenta_contable_id, anterior.pk)
        self.assertFalse(anterior.activa)
        self.assertEqual(CuentaContable.objects.count(), total)

    def test_auxiliar_rechaza_cuenta_de_otra_empresa(self):
        ajena = CuentaContable.objects.create(empresa=self.otra, codigo="111001", nombre="Ajena", tipo="activo")
        cliente = Cliente.objects.create(empresa=self.rumi, nombre="Cliente sin enlace", rtn="08011999199007")
        Cliente.objects.filter(pk=cliente.pk).update(cuenta_contable=ajena)
        cliente.refresh_from_db()
        with self.assertRaisesMessage(ValidationError, "pertenecer a su empresa"):
            asegurar_cuenta_contable_cliente(cliente)
        self.assertEqual(cliente.cuenta_contable_id, ajena.pk)

    def test_codigo_padre_largo_se_rechaza_sin_crear_ni_enlazar(self):
        larga = self.crear_cuenta("1" * 26, "Padre largo", "activo")
        VinculoCuentaOperativaRumi.objects.filter(empresa=self.rumi, clave="clientes").update(cuenta=larga)
        cliente = Cliente.objects.create(empresa=self.rumi, nombre="Cliente de padre largo", rtn="08011999199008")
        total = CuentaContable.objects.count()
        with self.assertRaisesMessage(ValidationError, "demasiado largo"):
            asegurar_cuenta_contable_cliente(cliente)
        cliente.refresh_from_db()
        self.assertIsNone(cliente.cuenta_contable_id)
        self.assertEqual(CuentaContable.objects.count(), total)

    def test_generador_respeta_maximo_y_salta_codigos_ocupados(self):
        padre = self.crear_cuenta("1" * 25, "Padre límite", "activo")
        self.crear_cuenta(f"{padre.codigo}.0001", "Auxiliar existente", "activo", cuenta_padre=padre)
        codigo = _siguiente_codigo_auxiliar(padre)
        self.assertEqual(codigo, f"{padre.codigo}.0002")
        self.assertEqual(len(codigo), 30)

    def test_otras_empresas_mantienen_cuentas_automaticas_honduras(self):
        cuenta = obtener_o_crear_cuenta_base(self.otra, "caja")
        self.assertEqual(cuenta.codigo, "1101")
        self.assertEqual(cuenta.empresa_id, self.otra.pk)
        self.assertEqual(obtener_cuenta_operativa(self.otra, "caja").pk, cuenta.pk)
        resultado = cargar_catalogo_base_honduras(self.otra)
        self.assertGreater(resultado["creadas"], 0)
        self.assertGreater(resultado["cuentas_financieras_creadas"], 0)
        self.assertEqual(CuentaContable.objects.filter(empresa=self.rumi).count(), 3)

    def test_rumi_sin_instalacion_conserva_flujo_anterior_hasta_reemplazo(self):
        ConfiguracionContableEmpresa.objects.filter(empresa=self.rumi).update(catalogo_rumi_instalado=False)
        cuenta = obtener_o_crear_cuenta_base(self.rumi, "caja")
        self.assertEqual(cuenta.codigo, "1101")

    def test_creadores_bloquean_empresa_antes_de_revisar_modo_catalogo(self):
        from .catalogo_rumi_roles import usa_catalogo_rumi

        bloquear = Empresa.objects.select_for_update
        acciones = (
            lambda: obtener_o_crear_cuenta_base(self.rumi, "caja"),
            lambda: obtener_cuenta_operativa(self.rumi, "caja"),
            lambda: asegurar_cuentas_financieras_base_honduras(self.rumi),
            lambda: asegurar_clasificaciones_bancarias_base_honduras(self.rumi),
            lambda: cargar_catalogo_base_honduras(self.rumi),
        )
        for accion in acciones:
            eventos = []

            def bloquear_empresa(*args, **kwargs):
                eventos.append("bloquear_empresa")
                return bloquear(*args, **kwargs)

            def revisar_modo(empresa):
                eventos.append("revisar_modo")
                return usa_catalogo_rumi(empresa)

            with self.subTest(accion=accion), patch(
                "core.models.Empresa.objects.select_for_update", side_effect=bloquear_empresa,
            ), patch("contabilidad.catalogo_rumi_roles.usa_catalogo_rumi", side_effect=revisar_modo):
                try:
                    accion()
                except ValidationError:
                    # La carga Honduras se rechaza después de adquirir el mismo bloqueo.
                    pass
            self.assertEqual(eventos[:2], ["bloquear_empresa", "revisar_modo"])
