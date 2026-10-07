from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import close_old_connections
from django.db.models.deletion import ProtectedError
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature
from django.utils import timezone

from core.models import Empresa, RolSistema, Usuario
from contabilidad.models import AsientoContable
from facturacion.models import CAI, Cliente, Factura, MovimientoInventario, PagoFactura, Producto, TipoImpuesto
from facturacion.services_clientes_compartidos import suspender_sincronizacion_clientes_compartidos

from .models import GastoAdicional, LineaGastoAdicional, Paciente, ProfesionalSalud
from .services_gastos_adicionales import convertir_gasto_adicional


class GastoAdicionalModeloTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(nombre="Hospital MIA", slug="hospital_mia", rtn="GA001")
        cls.otra = Empresa.objects.create(nombre="Medical Spa", slug="medical_spa", rtn="GA002")
        cls.usuario = Usuario.objects.create_superuser(username="ga_admin", password=None, empresa=cls.empresa)
        cls.paciente = Paciente.objects.create(
            empresa=cls.empresa, expediente_codigo="GA-EXP-1", nombre="Ana Paciente", identidad="0801199911111",
        )
        cls.paciente_otro = Paciente.objects.create(
            empresa=cls.otra, expediente_codigo="GA-EXP-1", nombre="Paciente de otra empresa", identidad="0801199922222",
        )
        cls.isv = TipoImpuesto.objects.create(nombre="ISV 15", porcentaje="15.00")
        cls.exento = TipoImpuesto.objects.create(nombre="Exento", porcentaje="0.00")
        cls.producto = Producto.objects.create(
            empresa=cls.empresa, nombre="Insumo clínico", precio="115.00", impuesto_predeterminado=cls.isv,
        )
        cls.servicio = Producto.objects.create(
            empresa=cls.empresa, nombre="Servicio clínico exento", precio="100.00", tipo_item="servicio",
            controla_inventario=False, impuesto_predeterminado=cls.exento,
        )
        cls.cai = CAI.objects.create(
            empresa=cls.empresa, numero_cai="GA-TEST-CAI", establecimiento="001", punto_emision="001",
            tipo_documento="01", rango_inicial=1, rango_final=100, correlativo_actual=0,
            fecha_activacion=timezone.localdate() - timedelta(days=1),
            fecha_limite=timezone.localdate() + timedelta(days=30),
        )

    def _gasto(self, **overrides):
        gasto = GastoAdicional.objects.create(
            empresa=overrides.pop("empresa", self.empresa), paciente=overrides.pop("paciente", self.paciente),
            creado_por=self.usuario, actualizado_por=self.usuario, observacion="Material usado en recuperación.",
            **overrides,
        )
        LineaGastoAdicional.objects.create(
            gasto=gasto, producto=self.producto, cantidad="2.00", precio_unitario="115.00",
        )
        LineaGastoAdicional.objects.create(
            gasto=gasto, producto=self.servicio, cantidad="1.00", precio_unitario="100.00",
        )
        gasto.calcular_totales()
        gasto.save()
        return gasto

    def test_numeracion_interna_por_empresa_no_consume_cai(self):
        primero = self._gasto()
        segundo = self._gasto()
        otra = GastoAdicional.objects.create(empresa=self.otra, paciente=self.paciente_otro)
        self.assertEqual((primero.numero, segundo.numero, otra.numero), ("GA-000001", "GA-000002", "GA-000001"))
        self.assertEqual(primero.total, Decimal("330.00"))
        self.assertEqual(primero.subtotal, Decimal("330.00"))
        self.assertEqual(primero.estado, "pendiente")
        self.cai.refresh_from_db()
        self.assertEqual(self.cai.correlativo_actual, 0)
        self.assertFalse(Factura.objects.exists())

    def test_numeracion_empresa_autor_y_snapshot_no_se_pueden_reescribir(self):
        gasto = self._gasto()
        gasto.numero = "GA-000099"
        with self.assertRaises(ValidationError):
            gasto.save()
        gasto.refresh_from_db()
        gasto.empresa = self.otra
        with self.assertRaises(ValidationError):
            gasto.save()
        gasto.refresh_from_db()
        gasto.precio_incluye_impuesto = False
        with self.assertRaises(ValidationError):
            gasto.save()

    def test_aislamiento_paciente_profesional_producto_y_factura(self):
        with self.assertRaises(ValidationError):
            GastoAdicional.objects.create(empresa=self.empresa, paciente=self.paciente_otro)
        profesional = ProfesionalSalud.objects.create(empresa=self.otra, nombre="Profesional externo")
        with self.assertRaises(ValidationError):
            GastoAdicional.objects.create(empresa=self.empresa, paciente=self.paciente, profesional=profesional)
        gasto = self._gasto()
        empresa_catalogo_ajeno = Empresa.objects.create(nombre="Empresa ajena al catálogo GA", slug="catalogo_ajeno_ga", rtn="GA003")
        producto_otro = Producto.objects.create(empresa=empresa_catalogo_ajeno, nombre="Otro producto", precio="10.00")
        with self.assertRaises(ValidationError):
            LineaGastoAdicional.objects.create(gasto=gasto, producto=producto_otro, cantidad=1, precio_unitario=10)
        cliente = self._cliente_sin_compartir(self.otra, "Cliente externo", "0801999999999")
        factura = Factura.objects.create(empresa=self.otra, cliente=cliente)
        gasto.factura = factura
        with self.assertRaises(ValidationError):
            gasto.save()

    def _cliente_sin_compartir(self, empresa, nombre, identidad):
        with suspender_sincronizacion_clientes_compartidos():
            return Cliente.objects.create(empresa=empresa, nombre=nombre, rtn=identidad)

    def test_cantidad_precio_y_totales_validan_precision_y_limites(self):
        gasto = GastoAdicional.objects.create(empresa=self.empresa, paciente=self.paciente)
        for cantidad, precio in (("0", "1"), ("-1", "1"), ("1", "-1"), ("0.001", "1"), ("1", "1.001"), ("99999999.99", "9999999999.99")):
            with self.subTest(cantidad=cantidad, precio=precio), self.assertRaises(ValidationError):
                LineaGastoAdicional.objects.create(
                    gasto=gasto, producto=self.producto, cantidad=cantidad, precio_unitario=precio,
                )
        linea = LineaGastoAdicional.objects.create(gasto=gasto, producto=self.producto, cantidad="1.25", precio_unitario="0.00")
        self.assertEqual(linea.subtotal, Decimal("0.00"))

    def test_conversion_copia_y_reutiliza_motor_impuestos_sin_efectos_fiscales(self):
        gasto = self._gasto()
        factura, creada = convertir_gasto_adicional(gasto, self.usuario)
        self.assertTrue(creada)
        self.assertEqual(factura.estado, "borrador")
        self.assertIsNone(factura.numero_factura)
        self.assertIsNone(factura.cai_id)
        self.assertEqual(factura.subtotal, Decimal("300.00"))
        self.assertEqual(factura.impuesto, Decimal("30.00"))
        self.assertEqual(factura.total, Decimal("330.00"))
        lineas = list(factura.lineas.order_by("id"))
        self.assertEqual(lineas[0].producto_id, self.producto.pk)
        self.assertEqual(lineas[0].cantidad, Decimal("2.00"))
        self.assertEqual(lineas[0].precio_unitario, Decimal("115.00"))
        self.assertTrue(lineas[0].precio_incluye_impuesto)
        self.assertEqual(lineas[0].impuesto_id, self.isv.pk)
        self.assertEqual(lineas[1].impuesto_id, self.exento.pk)
        self.assertIn(gasto.numero, lineas[0].comentario)
        self.assertIn(gasto.observacion, lineas[0].comentario)
        self.assertFalse(PagoFactura.objects.filter(factura=factura).exists())
        self.assertFalse(MovimientoInventario.objects.exists())
        self.assertFalse(AsientoContable.objects.exists())
        self.cai.refresh_from_db()
        self.assertEqual(self.cai.correlativo_actual, 0)
        gasto.refresh_from_db()
        self.assertEqual(gasto.estado, "pendiente")
        self.assertEqual(gasto.convertido_por_id, self.usuario.pk)
        self.assertIsNotNone(gasto.fecha_conversion)
        self.assertEqual(factura.gasto_adicional_origen.pk, gasto.pk)
        self.assertEqual(gasto.total, Decimal("330.00"))

    def test_factura_solo_emite_usando_motor_fiscal_existente(self):
        gasto = self._gasto()
        factura, _ = convertir_gasto_adicional(gasto, self.usuario)
        factura.emitir()
        factura.refresh_from_db()
        self.cai.refresh_from_db()
        gasto.refresh_from_db()
        self.assertEqual(factura.numero_factura, "001-001-01-00000001")
        self.assertEqual(self.cai.correlativo_actual, 1)
        self.assertEqual(gasto.estado, "facturado")
        self.assertEqual(gasto.get_estado_display(), "Facturado")
        self.assertEqual(gasto.total, Decimal("330.00"))

    def test_conversion_idempotente_conserva_autor_y_momento(self):
        gasto = self._gasto()
        factura, _ = convertir_gasto_adicional(gasto, self.usuario)
        gasto.refresh_from_db()
        momento = gasto.fecha_conversion
        segundo, creada = convertir_gasto_adicional(gasto, self.usuario)
        self.assertFalse(creada)
        self.assertEqual(segundo.pk, factura.pk)
        gasto.refresh_from_db()
        self.assertEqual(gasto.fecha_conversion, momento)
        self.assertEqual(Factura.objects.count(), 1)

    def test_clinicas_contado_convertidas_siempre_son_borrador_sin_cai(self):
        for indice, slug in enumerate(("medical_spa", "luque_aestetic", "serviciosmedicos"), start=1):
            with self.subTest(empresa=slug):
                empresa, _ = Empresa.objects.get_or_create(slug=slug, defaults={"nombre": slug, "rtn": f"GA-CONTADO-{indice}"})
                paciente = Paciente.objects.create(
                    empresa=empresa, nombre=f"Paciente {slug}", identidad=f"080199999900{indice}", expediente_codigo="CONTADO-1",
                )
                producto = Producto.objects.create(empresa=empresa, nombre="Producto contado", precio="115.00", impuesto_predeterminado=self.isv)
                gasto = GastoAdicional.objects.create(empresa=empresa, paciente=paciente, creado_por=self.usuario)
                LineaGastoAdicional.objects.create(gasto=gasto, producto=producto, cantidad=1, precio_unitario="115.00")
                gasto.calcular_totales()
                gasto.save()
                factura, creada = convertir_gasto_adicional(gasto, self.usuario)
                self.assertTrue(creada)
                self.assertEqual(factura.estado, "borrador")
                self.assertEqual(factura.total, Decimal("115.00"))
                self.assertIsNone(factura.cai_id)
                self.assertIsNone(factura.numero_factura)
                self.assertFalse(factura.pagos_facturacion.exists())

    def test_conversion_no_crea_ni_modifica_clientes_o_pacientes_de_otra_empresa(self):
        gasto = self._gasto()
        pacientes = Paciente.objects.count()
        factura, _ = convertir_gasto_adicional(gasto, self.usuario)
        self.assertEqual(factura.cliente.empresa_id, self.empresa.pk)
        self.paciente.refresh_from_db()
        self.assertEqual(self.paciente.cliente_id, factura.cliente_id)
        self.assertEqual(Cliente.objects.count(), 1)
        self.assertEqual(Paciente.objects.count(), pacientes)
        self.assertFalse(Cliente.objects.filter(empresa=self.otra).exists())
        self.paciente_otro.refresh_from_db()
        self.assertIsNone(self.paciente_otro.cliente_id)

    def test_conversion_reutiliza_cliente_existente_sin_actualizar_ficha(self):
        cliente = self._cliente_sin_compartir(self.empresa, self.paciente.nombre, self.paciente.identidad)
        cliente.telefono = "99990000"
        # Deja la ficha distinta sin disparar señales; la conversión no debe sincronizarla.
        Cliente.objects.filter(pk=cliente.pk).update(telefono=cliente.telefono)
        self.paciente.refresh_from_db()
        self.paciente.telefono = "88881111"
        self.paciente.save(update_fields=["telefono"])
        gasto = self._gasto()
        with patch("facturacion.services_clientes_compartidos._datos_generales") as compartidos:
            factura, _ = convertir_gasto_adicional(gasto, self.usuario)
        cliente.refresh_from_db()
        self.assertEqual(factura.cliente_id, cliente.pk)
        self.assertEqual(cliente.telefono, "99990000")
        compartidos.assert_not_called()
        self.assertEqual(Cliente.objects.count(), 1)

    def test_conversion_rechaza_cliente_vinculado_a_otra_empresa(self):
        externo = self._cliente_sin_compartir(self.otra, "Persona externa", "0801999999999")
        self.paciente.cliente = externo
        self.paciente.save(update_fields=["cliente"])
        gasto = self._gasto()
        with self.assertRaises(ValidationError):
            convertir_gasto_adicional(gasto, self.usuario)
        self.assertFalse(Factura.objects.exists())

    def test_impuesto_real_actual_y_snapshot_historico_precio_descripcion(self):
        gasto = self._gasto()
        self.producto.nombre = "Nuevo nombre del catálogo"
        self.producto.precio = Decimal("999.00")
        self.producto.impuesto_predeterminado = self.exento
        self.producto.save()
        factura, _ = convertir_gasto_adicional(gasto, self.usuario)
        linea = factura.lineas.order_by("id").first()
        original = gasto.lineas.order_by("id").first()
        self.assertEqual(original.descripcion, "Insumo clínico")
        self.assertEqual(original.precio_unitario, Decimal("115.00"))
        self.assertEqual(linea.descripcion_manual, original.descripcion)
        self.assertEqual(linea.precio_unitario, original.precio_unitario)
        self.assertEqual(linea.impuesto_id, self.exento.pk)
        self.assertEqual(linea.impuesto_monto, Decimal("0.00"))

    def test_impuesto_sin_configurar_no_inventa_isv_ni_crea_factura(self):
        gasto = self._gasto()
        self.producto.impuesto_predeterminado = None
        self.producto.save()
        with self.assertRaisesMessage(ValidationError, "Configure el impuesto"):
            convertir_gasto_adicional(gasto, self.usuario)
        self.assertFalse(Factura.objects.exists())
        self.assertFalse(Cliente.objects.exists())

    def test_documento_vinculado_conserva_original_y_no_permite_borrar_factura(self):
        gasto = self._gasto()
        factura, _ = convertir_gasto_adicional(gasto, self.usuario)
        gasto.refresh_from_db()
        gasto.observacion = "Cambio posterior"
        with self.assertRaises(ValidationError):
            gasto.save()
        linea = gasto.lineas.first()
        linea.cantidad = Decimal("9.00")
        with self.assertRaises(ValidationError):
            linea.save()
        with self.assertRaises(ValidationError):
            linea.delete()
        with self.assertRaises(ValidationError):
            gasto.delete()
        with self.assertRaises(ProtectedError):
            factura.delete()

    def test_fallo_al_copiar_lineas_revierte_factura_vinculo_y_cliente(self):
        gasto = self._gasto()
        original_lineas = list(gasto.lineas.values("producto_id", "descripcion", "cantidad", "precio_unitario", "subtotal"))
        with patch("clinica.services_gastos_adicionales.LineaFactura.objects.create", side_effect=ValidationError("Fallo de prueba")):
            with self.assertRaises(ValidationError):
                convertir_gasto_adicional(gasto, self.usuario)
        gasto.refresh_from_db()
        self.paciente.refresh_from_db()
        self.assertIsNone(gasto.factura_id)
        self.assertIsNone(gasto.fecha_conversion)
        self.assertIsNone(self.paciente.cliente_id)
        self.assertFalse(Factura.objects.exists())
        self.assertFalse(Cliente.objects.exists())
        self.assertEqual(list(gasto.lineas.values("producto_id", "descripcion", "cantidad", "precio_unitario", "subtotal")), original_lineas)
        self.cai.refresh_from_db()
        self.assertEqual(self.cai.correlativo_actual, 0)

    def test_conversion_valida_permisos_y_acceso_empresa_en_servicio(self):
        gasto = self._gasto()
        rol = RolSistema.objects.create(nombre="Sin conversión GA", codigo="sin-conversion-ga")
        usuario = Usuario.objects.create_user(username="ga_sin_permiso", password=None, empresa=self.empresa, rol_sistema=rol)
        with self.assertRaises(PermissionDenied):
            convertir_gasto_adicional(gasto, usuario)
        externo = Usuario.objects.create_user(username="ga_otro_admin", password=None, empresa=self.otra, es_administrador_empresa=True)
        with self.assertRaises(PermissionDenied):
            convertir_gasto_adicional(gasto, externo)
        self.assertFalse(Factura.objects.exists())

    def test_conversion_requiere_cada_permiso_de_revision_y_facturacion(self):
        gasto = self._gasto()
        permisos = (
            "puede_convertir_gastos_adicionales_factura", "puede_crear_facturas",
            "puede_editar_facturas", "puede_ver_facturas",
        )
        rol = RolSistema.objects.create(nombre="Operador GA", codigo="operador-ga", **{permiso: True for permiso in permisos})
        usuario = Usuario.objects.create_user(username="ga_operador", password=None, empresa=self.empresa, rol_sistema=rol)
        for permiso in permisos:
            with self.subTest(permiso=permiso):
                setattr(rol, permiso, False)
                rol.save(update_fields=[permiso])
                with self.assertRaises(PermissionDenied):
                    convertir_gasto_adicional(gasto, usuario)
                setattr(rol, permiso, True)
                rol.save(update_fields=[permiso])
        self.assertFalse(Factura.objects.exists())
        factura, creada = convertir_gasto_adicional(gasto, usuario)
        self.assertTrue(creada)
        self.assertEqual(factura.vendedor_id, usuario.pk)

    def test_contexto_suspender_sincronizacion_se_restablece_incluso_con_error(self):
        from facturacion.services_clientes_compartidos import sincronizar_cliente_compartido
        cliente = self._cliente_sin_compartir(self.empresa, self.paciente.nombre, self.paciente.identidad)
        try:
            with suspender_sincronizacion_clientes_compartidos():
                self.assertEqual(sincronizar_cliente_compartido(cliente)["creados"], 0)
                raise RuntimeError("Fin de operación")
        except RuntimeError:
            pass
        self.assertEqual(sincronizar_cliente_compartido(cliente)["creados"], 1)
        self.assertTrue(Cliente.objects.filter(empresa=self.otra).exists())


class GastoAdicionalConcurrenciaTests(TransactionTestCase):
    @skipUnlessDBFeature("has_select_for_update")
    def test_creaciones_concurrentes_tienen_correlativos_distintos(self):
        empresa = Empresa.objects.create(nombre="Hospital MIA", slug="hospital_mia", rtn="GA-CONC")
        paciente = Paciente.objects.create(empresa=empresa, nombre="Paciente concurrente", expediente_codigo="CONC", identidad="0801199900000")

        def crear_documento(_):
            close_old_connections()
            try:
                return GastoAdicional.objects.create(empresa_id=empresa.pk, paciente_id=paciente.pk).numero
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=3) as executor:
            numeros = list(executor.map(crear_documento, range(3)))
        self.assertEqual(set(numeros), {"GA-000001", "GA-000002", "GA-000003"})

    @skipUnlessDBFeature("has_select_for_update")
    def test_conversiones_concurrentes_generan_una_sola_factura(self):
        empresa = Empresa.objects.create(nombre="Hospital MIA", slug="hospital_mia", rtn="GA-CONC-CONV")
        paciente = Paciente.objects.create(empresa=empresa, nombre="Paciente concurrente", expediente_codigo="CONC", identidad="0801199900000")
        usuario = Usuario.objects.create_superuser(username="ga_concurrente", password=None, empresa=empresa)
        impuesto = TipoImpuesto.objects.create(nombre="Exento concurrente", porcentaje=0)
        producto = Producto.objects.create(empresa=empresa, nombre="Producto concurrente", precio=100, impuesto_predeterminado=impuesto)
        gasto = GastoAdicional.objects.create(empresa=empresa, paciente=paciente)
        LineaGastoAdicional.objects.create(gasto=gasto, producto=producto, cantidad=1, precio_unitario=100)
        gasto.calcular_totales()
        gasto.save()

        def convertir_documento(_):
            close_old_connections()
            try:
                factura, creada = convertir_gasto_adicional(gasto, usuario)
                return factura.pk, creada
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            resultados = list(executor.map(convertir_documento, range(2)))
        self.assertEqual(len({factura_id for factura_id, _ in resultados}), 1)
        self.assertEqual(sum(creada for _, creada in resultados), 1)
        self.assertEqual(Factura.objects.count(), 1)
