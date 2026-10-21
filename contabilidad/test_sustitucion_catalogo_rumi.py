"""Sustitución aislada de RUMI y continuidad de los registros anteriores."""

from copy import deepcopy
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase

from core.models import Empresa, Usuario
from facturacion.models import Cliente, Proveedor

from .catalogo_rumi_roles import ALIAS, ROLES, resolver_cuenta_rumi, usa_catalogo_rumi
from .models import (
    AsientoContable, ClasificacionMovimientoBanco, ConfiguracionContableEmpresa,
    CuentaContable, CuentaFinanciera, LineaAsientoContable, MovimientoBancario,
    RegistroSustitucionCatalogoRumi, ReglaClasificacionBanco,
    VinculoCuentaOperativaRumi,
)
from .services import (
    CATALOGO_BASE_HONDURAS, CUENTAS_BASE, asegurar_cuenta_contable_cliente,
    asegurar_cuenta_contable_proveedor, cargar_catalogo_base_honduras,
    obtener_cuenta_operativa, obtener_o_crear_cuenta_base,
)
from .sustitucion_catalogo_rumi import (
    aplicar_sustitucion, leer_catalogo_para_sustitucion, preparar_sustitucion,
)
from .test_importacion_catalogo import archivo_excel, libro_rumi


def catalogo_pequeno_rumi():
    filas = [
        ("1", "Activo", "activo", "", False, True),
        ("2", "Pasivo", "pasivo", "", False, True),
        ("3", "Patrimonio", "patrimonio", "", False, True),
        ("4", "Activos y Pasivos Contingentes", "orden", "", False, False),
        ("5", "Ingresos", "ingreso", "", False, True),
        ("6", "Gastos", "gasto", "", False, True),
        ("7", "Cuentas de Orden", "orden", "", False, False),
        ("101", "Efectivo y bancos", "activo", "1", False, True),
        ("10101", "Caja", "activo", "101", False, True),
        ("10101001", "Caja chica", "activo", "10101", True, True),
        ("202", "Cuentas por pagar", "pasivo", "2", False, True),
        ("20201", "Proveedores y acreedores", "pasivo", "202", False, True),
        ("20201001", "Proveedores nacionales", "pasivo", "20201", True, True),
        ("501", "Ingresos financieros", "ingreso", "5", False, True),
        ("50103", "Ganancias cambiarias", "ingreso", "501", False, True),
        ("50103001", "Ganancia por diferencia de cambio", "ingreso", "50103", True, False),
    ]
    return [{
        "codigo": codigo, "nombre": nombre, "tipo": tipo, "codigo_padre": padre,
        "acepta_movimientos": movimientos, "activa": activa,
        "descripcion": "Catálogo RUMI de prueba; activación revisada en la propuesta.",
        "hoja": "Catalogo_RUMI", "fila": fila,
    } for fila, (codigo, nombre, tipo, padre, movimientos, activa) in enumerate(filas, start=7)]


def estado_empresa(empresa):
    return {
        "cuentas": list(CuentaContable.objects.filter(empresa=empresa).order_by("pk").values()),
        "financieras": list(CuentaFinanciera.objects.filter(empresa=empresa).order_by("pk").values()),
        "clasificaciones": list(ClasificacionMovimientoBanco.objects.filter(empresa=empresa).order_by("pk").values()),
        "configuracion": list(ConfiguracionContableEmpresa.objects.filter(empresa=empresa).order_by("pk").values()),
        "vinculos": list(VinculoCuentaOperativaRumi.objects.filter(empresa=empresa).order_by("pk").values()),
        "registros": list(RegistroSustitucionCatalogoRumi.objects.filter(empresa=empresa).order_by("pk").values()),
    }


class SustitucionCatalogoRumiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.rumi = Empresa.objects.create(nombre="RUMI", slug="rumi", rtn="08011999271001")
        cls.otra = Empresa.objects.create(nombre="Empresa independiente", slug="otra-sustitucion", rtn="08011999271002")
        cls.usuario = Usuario.objects.create_user(username="administrador-sustitucion-rumi", empresa=cls.rumi)
        cargar_catalogo_base_honduras(cls.rumi)
        cargar_catalogo_base_honduras(cls.otra)

    def setUp(self):
        self.datos = catalogo_pequeno_rumi()

    def cuenta_base(self, codigo):
        return CuentaContable.objects.get(empresa=self.rumi, codigo=codigo)

    def aplicar(self):
        plan = preparar_sustitucion(self.rumi, self.datos)
        return aplicar_sustitucion(self.rumi, self.datos, firma=plan["firma"], usuario=self.usuario)

    def test_revision_no_escribe_y_detecta_catalogo_accidental_sin_uso(self):
        anterior = estado_empresa(self.rumi)
        plan = preparar_sustitucion(self.rumi, self.datos)
        self.assertEqual(plan["eliminadas"], len(CATALOGO_BASE_HONDURAS))
        self.assertEqual(plan["conservadas"], 0)
        self.assertEqual(len(plan["financieras_eliminar_ids"]), 2)
        self.assertEqual(len(plan["clasificaciones_eliminar_ids"]), 8)
        self.assertEqual(plan["importables"], len(self.datos))
        self.assertEqual(estado_empresa(self.rumi), anterior)

    def test_retira_base_sin_uso_importa_jerarquia_y_enlaza_proveedores(self):
        ids_anteriores = set(CuentaContable.objects.filter(empresa=self.rumi).values_list("pk", flat=True))
        resultado = self.aplicar()
        self.assertEqual(resultado["eliminadas"], 106)
        self.assertEqual(resultado["importadas"], len(self.datos))
        self.assertFalse(CuentaContable.objects.filter(empresa=self.rumi, pk__in=ids_anteriores).exists())
        self.assertFalse(CuentaFinanciera.objects.filter(empresa=self.rumi).exists())
        self.assertFalse(ClasificacionMovimientoBanco.objects.filter(empresa=self.rumi).exists())
        config = ConfiguracionContableEmpresa.objects.get(empresa=self.rumi)
        self.assertTrue(config.catalogo_rumi_instalado)
        self.assertEqual(config.cuenta_proveedores.codigo, "20201001")
        self.assertEqual(config.cuenta_proveedores.cuenta_padre.codigo, "20201")
        self.assertEqual(config.cuenta_proveedores.cuenta_padre.cuenta_padre.codigo, "202")
        self.assertIsNone(config.cuenta_caja_id)
        self.assertFalse(config.isv_compras_recuperable)
        self.assertEqual(self.cuenta_base("4").tipo, "orden")
        self.assertEqual(self.cuenta_base("5").tipo, "ingreso")

    def test_respaldo_identifica_usuario_y_conserva_estado_anterior_completo(self):
        antigua = self.cuenta_base("111001")
        ConfiguracionContableEmpresa.objects.create(empresa=self.rumi, cuenta_clientes=antigua)
        plan = preparar_sustitucion(self.rumi, self.datos)
        resultado = aplicar_sustitucion(self.rumi, self.datos, firma=plan["firma"], usuario=self.usuario)
        registro = RegistroSustitucionCatalogoRumi.objects.get(pk=resultado["registro_id"])
        self.assertEqual(registro.empresa_id, self.rumi.pk)
        self.assertEqual(registro.usuario_id, self.usuario.pk)
        self.assertEqual(registro.respaldo, plan["respaldo"])
        self.assertEqual(registro.resumen["importadas"], len(self.datos))
        self.assertIn(antigua.pk, [cuenta["id"] for cuenta in registro.respaldo["cuentas"]])
        config = ConfiguracionContableEmpresa.objects.get(empresa=self.rumi)
        self.assertIsNone(config.cuenta_clientes_id)

    def test_condicionadas_siguen_inactivas_y_no_se_enlazan_por_tener_codigo_sugerido(self):
        resultado = self.aplicar()
        ganancia = self.cuenta_base("50103001")
        self.assertFalse(ganancia.activa)
        self.assertTrue(ganancia.acepta_movimientos)
        self.assertFalse(VinculoCuentaOperativaRumi.objects.filter(empresa=self.rumi, clave="ganancia_cambiaria").exists())
        rol = next(rol for rol in resultado["roles"] if rol["clave"] == "ganancia_cambiaria")
        self.assertEqual(rol["estado"], "Condicionada: activar y revisar")

    def test_otra_empresa_conserva_cuentas_financieras_clasificaciones_y_configuracion(self):
        caja_otra = CuentaContable.objects.get(empresa=self.otra, codigo="1101")
        ConfiguracionContableEmpresa.objects.create(empresa=self.otra, cuenta_caja=caja_otra)
        anterior = estado_empresa(self.otra)
        self.aplicar()
        self.assertEqual(estado_empresa(self.otra), anterior)
        self.assertFalse(RegistroSustitucionCatalogoRumi.objects.filter(empresa=self.otra).exists())

    def test_partidas_historicas_conservan_ids_tipos_importes_y_raices_sin_reclasificarse(self):
        venta, costo = self.cuenta_base("410101"), self.cuenta_base("5101")
        raiz_ingreso, raiz_costo = self.cuenta_base("4"), self.cuenta_base("5")
        asiento = AsientoContable.objects.create(
            empresa=self.rumi, fecha=date(2026, 9, 30), descripcion="Histórico antes del catálogo RUMI",
        )
        LineaAsientoContable.objects.create(asiento=asiento, cuenta=venta, haber=Decimal("123.45"))
        LineaAsientoContable.objects.create(asiento=asiento, cuenta=costo, debe=Decimal("123.45"))
        asiento.estado = "contabilizado"
        asiento.save(update_fields=["estado"])
        partidas = list(asiento.lineas.order_by("pk").values())
        self.aplicar()
        self.assertEqual(list(asiento.lineas.order_by("pk").values()), partidas)
        for cuenta, tipo in ((venta, "ingreso"), (costo, "costo"), (raiz_ingreso, "ingreso"), (raiz_costo, "costo")):
            cuenta.refresh_from_db()
            self.assertEqual(cuenta.tipo, tipo)
            self.assertEqual(cuenta.codigo, f"HN-HIST-{cuenta.pk}")
        self.assertEqual(self.cuenta_base("4").tipo, "orden")
        self.assertEqual(self.cuenta_base("5").tipo, "ingreso")
        asiento.refresh_from_db()
        self.assertEqual(asiento.estado, "contabilizado")
        self.assertEqual(asiento.total_debe, Decimal("123.45"))
        self.assertEqual(asiento.total_haber, Decimal("123.45"))

    def test_movimiento_bancario_conserva_cuenta_financiera_e_importes_originales(self):
        financiera = CuentaFinanciera.objects.get(empresa=self.rumi, nombre="Banco Principal HNL")
        movimiento = MovimientoBancario.objects.create(
            empresa=self.rumi, cuenta_financiera=financiera, fecha=date(2026, 9, 30),
            descripcion="Depósito histórico", credito=Decimal("200.00"), saldo=Decimal("200.00"),
        )
        antes = MovimientoBancario.objects.filter(pk=movimiento.pk).values().get()
        cuenta_id = financiera.cuenta_contable_id
        self.aplicar()
        financiera.refresh_from_db()
        self.assertEqual(financiera.cuenta_contable_id, cuenta_id)
        self.assertEqual(financiera.cuenta_contable.codigo, f"HN-HIST-{cuenta_id}")
        self.assertEqual(MovimientoBancario.objects.filter(pk=movimiento.pk).values().get(), antes)

    def test_clasificacion_con_regla_conserva_enlace_y_cuenta_historica(self):
        clasificacion = ClasificacionMovimientoBanco.objects.get(empresa=self.rumi, nombre="Comisiones Bancarias")
        regla = ReglaClasificacionBanco.objects.create(
            empresa=self.rumi, nombre="Regla existente", texto_busqueda="COMISION", clasificacion=clasificacion,
        )
        cuenta_id = clasificacion.cuenta_contable_id
        self.aplicar()
        regla.refresh_from_db()
        clasificacion.refresh_from_db()
        self.assertEqual(regla.clasificacion_id, clasificacion.pk)
        self.assertEqual(clasificacion.cuenta_contable_id, cuenta_id)
        self.assertEqual(clasificacion.cuenta_contable.codigo, f"HN-HIST-{cuenta_id}")

    def test_auxiliares_existentes_conservan_cuenta_y_ancestros(self):
        cliente = Cliente.objects.create(empresa=self.rumi, nombre="Cliente anterior", rtn="08011999100001")
        proveedor = Proveedor.objects.create(empresa=self.rumi, nombre="Proveedor anterior")
        cuenta_cliente = asegurar_cuenta_contable_cliente(cliente)
        cuenta_proveedor = asegurar_cuenta_contable_proveedor(proveedor)
        padres = [(cuenta_cliente, cuenta_cliente.cuenta_padre_id), (cuenta_proveedor, cuenta_proveedor.cuenta_padre_id)]
        self.aplicar()
        cliente.refresh_from_db()
        proveedor.refresh_from_db()
        self.assertEqual(cliente.cuenta_contable_id, cuenta_cliente.pk)
        self.assertEqual(proveedor.cuenta_contable_id, cuenta_proveedor.pk)
        for cuenta, padre_id in padres:
            cuenta.refresh_from_db()
            self.assertEqual(cuenta.cuenta_padre_id, padre_id)
            self.assertEqual(cuenta.cuenta_padre.codigo, f"HN-HIST-{padre_id}")

    def test_cuentas_base_modificadas_por_usuario_no_se_eliminan(self):
        nombre = self.cuenta_base("1101")
        nombre.nombre = "Caja revisada por el contador"
        nombre.save(update_fields=["nombre"])
        inactiva = self.cuenta_base("120101")
        inactiva.activa = False
        inactiva.save(update_fields=["activa"])
        permiso = self.cuenta_base("120102")
        permiso.acepta_movimientos = False
        permiso.save(update_fields=["acepta_movimientos"])
        flujo = self.cuenta_base("120103")
        flujo.actividad_flujo = "financiamiento"
        flujo.save(update_fields=["actividad_flujo"])
        jerarquia = self.cuenta_base("120104")
        jerarquia.cuenta_padre = self.cuenta_base("1202")
        jerarquia.save(update_fields=["cuenta_padre"])
        originales = {
            cuenta.pk: CuentaContable.objects.filter(pk=cuenta.pk).values().get()
            for cuenta in (nombre, inactiva, permiso, flujo, jerarquia)
        }
        self.aplicar()
        for pk, original in originales.items():
            actual = CuentaContable.objects.filter(pk=pk).values().get()
            self.assertEqual(actual, original)

    def test_configuracion_propia_de_caja_tiene_prioridad_y_se_conserva(self):
        propia = CuentaContable.objects.create(empresa=self.rumi, codigo="CAJA-RUMI", nombre="Caja real", tipo="activo")
        ConfiguracionContableEmpresa.objects.create(empresa=self.rumi, cuenta_caja=propia)
        self.aplicar()
        config = ConfiguracionContableEmpresa.objects.get(empresa=self.rumi)
        self.assertEqual(config.cuenta_caja_id, propia.pk)
        self.assertEqual(resolver_cuenta_rumi(self.rumi, "caja"), propia)

    def test_colision_con_codigo_propio_bloquea_sin_mutaciones(self):
        CuentaContable.objects.create(
            empresa=self.rumi, codigo="20201001", nombre="Cuenta propia existente", tipo="pasivo",
        )
        anterior = estado_empresa(self.rumi)
        with self.assertRaisesMessage(ValidationError, "cuentas propias"):
            preparar_sustitucion(self.rumi, self.datos)
        self.assertEqual(estado_empresa(self.rumi), anterior)

    def test_nombre_modificado_en_raiz_colisionada_no_se_sobrescribe(self):
        raiz = self.cuenta_base("4")
        raiz.nombre = "Ingresos personalizados por el contador"
        raiz.save(update_fields=["nombre"])
        anterior = estado_empresa(self.rumi)
        with self.assertRaisesMessage(ValidationError, "cuentas propias"):
            preparar_sustitucion(self.rumi, self.datos)
        self.assertEqual(estado_empresa(self.rumi), anterior)

    def test_firma_antigua_por_cambio_de_configuracion_bloquea_confirmacion(self):
        config = ConfiguracionContableEmpresa.objects.create(empresa=self.rumi)
        plan = preparar_sustitucion(self.rumi, self.datos)
        config.isv_compras_recuperable = True
        config.save(update_fields=["isv_compras_recuperable"])
        cambiado = estado_empresa(self.rumi)
        with self.assertRaisesMessage(ValidationError, "cambiaron desde la revisión"):
            aplicar_sustitucion(self.rumi, self.datos, firma=plan["firma"])
        self.assertEqual(estado_empresa(self.rumi), cambiado)

    def test_firma_antigua_por_identificar_banco_sin_movimientos_bloquea_confirmacion(self):
        plan = preparar_sustitucion(self.rumi, self.datos)
        financiera = CuentaFinanciera.objects.get(empresa=self.rumi, nombre="Banco Principal HNL")
        financiera.numero = "001-1234"
        financiera.save(update_fields=["numero"])
        cambiado = estado_empresa(self.rumi)
        with self.assertRaisesMessage(ValidationError, "cambiaron desde la revisión"):
            aplicar_sustitucion(self.rumi, self.datos, firma=plan["firma"])
        self.assertEqual(estado_empresa(self.rumi), cambiado)

    def test_fallo_al_importar_revierte_retiro_enlaces_configuracion_y_respaldo(self):
        anterior = estado_empresa(self.rumi)
        plan = preparar_sustitucion(self.rumi, self.datos)
        with patch("contabilidad.sustitucion_catalogo_rumi.aplicar_catalogo_cuentas", side_effect=RuntimeError("Error de importación")):
            with self.assertRaisesMessage(RuntimeError, "Error de importación"):
                aplicar_sustitucion(self.rumi, self.datos, firma=plan["firma"])
        self.assertEqual(estado_empresa(self.rumi), anterior)

    def test_rechaza_empresa_distinta_incluso_si_objeto_en_memoria_simula_slug_rumi(self):
        self.otra.slug = "rumi"
        anterior = estado_empresa(self.otra)
        for accion in (
            lambda: preparar_sustitucion(self.otra, self.datos),
            lambda: aplicar_sustitucion(self.otra, self.datos, firma="firma falsa"),
        ):
            with self.assertRaisesMessage(ValidationError, "únicamente en RUMI"):
                accion()
        self.assertEqual(estado_empresa(self.otra), anterior)

    def test_repetir_sustitucion_no_elimina_el_catalogo_ya_instalado(self):
        self.aplicar()
        anterior = estado_empresa(self.rumi)
        with self.assertRaisesMessage(ValidationError, "ya utiliza su catálogo"):
            preparar_sustitucion(self.rumi, self.datos)
        self.assertEqual(estado_empresa(self.rumi), anterior)

    def test_lector_rumi_acepta_raices_ocupadas_antes_de_retirar_base(self):
        anterior = estado_empresa(self.rumi)
        catalogo = leer_catalogo_para_sustitucion(self.rumi, archivo_excel(libro_rumi()))
        self.assertEqual(len(catalogo["datos"]), 14)
        self.assertEqual(catalogo["hoja"], "Catalogo_RUMI")
        self.assertEqual(estado_empresa(self.rumi), anterior)

    def test_validacion_sigue_rechazando_duplicados_del_archivo(self):
        self.datos.append(deepcopy(self.datos[0]))
        anterior = estado_empresa(self.rumi)
        with self.assertRaisesMessage(ValidationError, "duplicado"):
            preparar_sustitucion(self.rumi, self.datos)
        self.assertEqual(estado_empresa(self.rumi), anterior)

    def test_respaldo_confirmado_no_puede_sobrescribirse_ni_eliminarse(self):
        resultado = self.aplicar()
        registro = RegistroSustitucionCatalogoRumi.objects.get(pk=resultado["registro_id"])
        respaldo = deepcopy(registro.respaldo)
        registro.respaldo = {}
        with self.assertRaisesMessage(ValidationError, "inmutable"):
            registro.save(update_fields=["respaldo"])
        with self.assertRaisesMessage(ValidationError, "inmutable"):
            RegistroSustitucionCatalogoRumi.objects.filter(pk=registro.pk).update(respaldo={})
        with self.assertRaisesMessage(ValidationError, "Conserve el respaldo"):
            registro.delete()
        with self.assertRaisesMessage(ValidationError, "Conserve el respaldo"):
            RegistroSustitucionCatalogoRumi.objects.filter(pk=registro.pk).delete()
        registro.refresh_from_db()
        self.assertEqual(registro.respaldo, respaldo)

    def test_nuevo_auxiliar_proveedor_usa_rumi_y_cliente_sin_rol_no_recrea_honduras(self):
        self.aplicar()
        proveedor = Proveedor.objects.create(empresa=self.rumi, nombre="Proveedor nuevo RUMI")
        cuenta = asegurar_cuenta_contable_proveedor(proveedor)
        self.assertEqual(cuenta.cuenta_padre.codigo, "20201001")
        self.assertTrue(cuenta.codigo.startswith("20201001."))
        cliente = Cliente.objects.create(empresa=self.rumi, nombre="Cliente sin mapeo", rtn="08011999100002")
        cantidad = CuentaContable.objects.filter(empresa=self.rumi).count()
        with self.assertRaisesMessage(ValidationError, "Configure la cuenta"):
            asegurar_cuenta_contable_cliente(cliente)
        self.assertEqual(CuentaContable.objects.filter(empresa=self.rumi).count(), cantidad)
        self.assertFalse(CuentaContable.objects.filter(empresa=self.rumi, codigo__in=["111001", "210101"]).exists())
        with self.assertRaisesMessage(ValidationError, "No se puede volver a cargar"):
            cargar_catalogo_base_honduras(self.rumi)


class ResolucionCuentasRumiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.rumi = Empresa.objects.create(nombre="RUMI", slug="rumi", rtn="08011999272001")
        cls.otra = Empresa.objects.create(nombre="Otra resolución", slug="otra-resolucion-rumi", rtn="08011999272002")
        cls.configuracion = ConfiguracionContableEmpresa.objects.create(empresa=cls.rumi, catalogo_rumi_instalado=True)

    def cuenta(self, codigo, tipo="activo", empresa=None, **opciones):
        return CuentaContable.objects.create(
            empresa=empresa or self.rumi, codigo=codigo, nombre=f"Cuenta {codigo}", tipo=tipo, **opciones,
        )

    def test_todos_los_roles_actuales_tienen_resolucion_explicita(self):
        self.assertEqual(set(CUENTAS_BASE) - set(ALIAS), set(ROLES) - {"entradas_inventario"})
        for indice, (clave, definicion) in enumerate(ROLES.items()):
            with self.subTest(clave=clave):
                tipos = definicion["tipos"]
                tipo = "gasto" if clave in {"compras", "costo_ventas"} else tipos[0]
                cuenta = self.cuenta(f"R-ROL-{indice:02d}", tipo=tipo)
                VinculoCuentaOperativaRumi.objects.create(empresa=self.rumi, clave=clave, cuenta=cuenta)
                self.assertEqual(resolver_cuenta_rumi(self.rumi, clave), cuenta)

    def test_alias_ventas_e_isv_usan_cuentas_generales_sin_inventar_distinciones(self):
        ventas = self.cuenta("VENTAS-R", tipo="ingreso")
        isv = self.cuenta("ISV-R", tipo="pasivo")
        self.configuracion.cuenta_ventas = ventas
        self.configuracion.cuenta_isv_por_pagar = isv
        self.configuracion.save()
        for alias, canonical in ALIAS.items():
            self.assertEqual(resolver_cuenta_rumi(self.rumi, alias), ventas if canonical == "ventas" else isv)
        self.assertEqual(CuentaContable.objects.filter(empresa=self.rumi).count(), 2)
        self.assertFalse(VinculoCuentaOperativaRumi.objects.exists())

    def test_configuracion_explicita_prevalece_y_cuenta_invalida_no_cae_al_vinculo(self):
        enlace = self.cuenta("CAJA-ENLACE")
        preferida = self.cuenta("CAJA-CONFIG")
        VinculoCuentaOperativaRumi.objects.create(empresa=self.rumi, clave="caja", cuenta=enlace)
        self.configuracion.cuenta_caja = preferida
        self.configuracion.save()
        self.assertEqual(resolver_cuenta_rumi(self.rumi, "caja"), preferida)
        CuentaContable.objects.filter(pk=preferida.pk).update(activa=False)
        with self.assertRaisesMessage(ValidationError, "inactiva"):
            resolver_cuenta_rumi(self.rumi, "caja")

    def test_roles_faltantes_bloquean_servicios_sin_recrear_cuentas_honduras(self):
        for resolver in (resolver_cuenta_rumi, obtener_cuenta_operativa, obtener_o_crear_cuenta_base):
            for clave in ("clientes", "inventario", "ventas_15", "ganancia_cambiaria", "costo_ventas"):
                with self.subTest(resolver=resolver.__name__, clave=clave):
                    with self.assertRaisesMessage(ValidationError, "Configure la cuenta"):
                        resolver(self.rumi, clave)
        self.assertFalse(CuentaContable.objects.filter(empresa=self.rumi).exists())

    def test_desactivar_o_reclasificar_cuenta_enlazada_bloquea_uso_posterior(self):
        cuenta = self.cuenta("GANANCIA-R", tipo="ingreso")
        VinculoCuentaOperativaRumi.objects.create(empresa=self.rumi, clave="ganancia_cambiaria", cuenta=cuenta)
        CuentaContable.objects.filter(pk=cuenta.pk).update(activa=False)
        with self.assertRaisesMessage(ValidationError, "inactiva"):
            resolver_cuenta_rumi(self.rumi, "ganancia_cambiaria")
        CuentaContable.objects.filter(pk=cuenta.pk).update(activa=True, tipo="activo")
        with self.assertRaisesMessage(ValidationError, "debe ser de tipo ingreso"):
            resolver_cuenta_rumi(self.rumi, "ganancia_cambiaria")

    def test_vinculo_rechaza_otras_empresas_tipos_y_cuentas_sin_movimientos(self):
        ajena = self.cuenta("CAJA-AJENA", empresa=self.otra)
        gasto = self.cuenta("GASTO", tipo="gasto")
        agrupadora = self.cuenta("AGRUPADORA", acepta_movimientos=False)
        inactiva = self.cuenta("INACTIVA", activa=False)
        for cuenta in (ajena, gasto, agrupadora, inactiva):
            with self.subTest(cuenta=cuenta.codigo), self.assertRaises(ValidationError):
                VinculoCuentaOperativaRumi.objects.create(empresa=self.rumi, clave="caja", cuenta=cuenta)
        with self.assertRaisesMessage(ValidationError, "únicamente para la empresa RUMI"):
            VinculoCuentaOperativaRumi.objects.create(empresa=self.otra, clave="caja", cuenta=ajena)
        self.assertFalse(VinculoCuentaOperativaRumi.objects.exists())

    def test_instalacion_y_resolucion_comprueban_slug_persistido(self):
        self.assertTrue(usa_catalogo_rumi(self.rumi))
        self.otra.slug = "rumi"
        with self.assertRaisesMessage(ValidationError, "solo puede instalarse"):
            ConfiguracionContableEmpresa.objects.create(empresa=self.otra, catalogo_rumi_instalado=True)
        ConfiguracionContableEmpresa.objects.create(empresa=self.otra)
        # Un indicador inválido anterior no habilita el catálogo en otra empresa.
        ConfiguracionContableEmpresa.objects.filter(empresa=self.otra).update(catalogo_rumi_instalado=True)
        self.assertFalse(usa_catalogo_rumi(self.otra))
        with self.assertRaisesMessage(ValidationError, "no está instalado"):
            resolver_cuenta_rumi(self.otra, "caja")

    def test_catalogo_instalado_no_se_desactiva_por_edicion_del_modelo(self):
        self.configuracion.catalogo_rumi_instalado = False
        with self.assertRaisesMessage(ValidationError, "No se puede desactivar"):
            self.configuracion.full_clean()
        with self.assertRaisesMessage(ValidationError, "No se puede desactivar"):
            self.configuracion.save()
        self.configuracion.refresh_from_db()
        self.assertTrue(self.configuracion.catalogo_rumi_instalado)
        self.assertTrue(usa_catalogo_rumi(self.rumi))

    def test_configuracion_abierta_antes_de_instalar_no_reactiva_fallback_honduras(self):
        from .forms import ConfiguracionContableEmpresaForm

        # Representa una instancia cargada por el formulario anterior, todavía con
        # el indicador inicial falso y las cuentas opcionales vacías.
        ConfiguracionContableEmpresa.objects.filter(pk=self.configuracion.pk).update(catalogo_rumi_instalado=False)
        anterior = ConfiguracionContableEmpresa.objects.get(pk=self.configuracion.pk)
        actual = ConfiguracionContableEmpresa.objects.get(pk=self.configuracion.pk)
        actual.catalogo_rumi_instalado = True
        actual.save()
        formulario_anterior = ConfiguracionContableEmpresaForm(data={}, instance=anterior, empresa=self.rumi)
        self.assertFalse(formulario_anterior.is_valid())
        self.assertIn("Vuelva a abrir la configuración", " ".join(formulario_anterior.non_field_errors()))
        anterior.isv_compras_recuperable = True
        with self.assertRaisesMessage(ValidationError, "Vuelva a abrir la configuración"):
            anterior.save()
        actual.refresh_from_db()
        self.assertTrue(actual.catalogo_rumi_instalado)
        self.assertFalse(actual.isv_compras_recuperable)
        self.assertTrue(usa_catalogo_rumi(self.rumi))
        self.assertFalse(CuentaContable.objects.filter(empresa=self.rumi).exists())
