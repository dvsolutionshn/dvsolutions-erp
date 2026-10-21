"""Regresiones del registro de lotes y su movimiento inicial de inventario."""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db.models.query import QuerySet
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from core.models import Empresa, EmpresaModulo, Modulo

from .forms import RegistrarLoteInventarioForm
from .lotes import parse_vencimiento_lote_rapido
from .models import (
    BodegaInventario,
    ExistenciaLoteBodega,
    InventarioProducto,
    LoteInventario,
    MovimientoInventario,
    MovimientoLoteBodega,
    Producto,
    Proveedor,
)


DUPLICADO = "Este número de lote ya está registrado para este producto"


class VencimientoLoteRapidoTests(SimpleTestCase):
    def test_entrada_vacia_no_inventa_una_fecha(self):
        for valor in (None, "", "   "):
            with self.subTest(valor=valor):
                self.assertIsNone(parse_vencimiento_lote_rapido(valor))

    def test_formatos_publicados_y_fecha_de_la_captura(self):
        casos = {
            "ago-27": date(2027, 8, 31),
            "agos-27": date(2027, 8, 31),
            "dic-26": date(2026, 12, 31),
            "febrero-28": date(2028, 2, 29),
            " FEBRERO / 2027 ": date(2027, 2, 28),
            "junio/15/2031": date(2031, 6, 15),
        }
        for valor, esperado in casos.items():
            with self.subTest(valor=valor):
                self.assertEqual(parse_vencimiento_lote_rapido(valor), esperado)

    def test_formatos_invalidos_siempre_son_validation_error(self):
        for valor in (
            "texto", "foo-27", "ene-abc", "febrero/31/2031",
            "junio/0/2031", "junio/32/2031", "ene-0000", "ene-10000",
            "ene-999999999999999999999999999999999", "junio/15/0",
            "junio/15/10000", "junio/15/2031/extra",
        ):
            with self.subTest(valor=valor):
                with self.assertRaises(ValidationError):
                    parse_vencimiento_lote_rapido(valor)


class LoteFixturesMixin:
    """Datos pequeños e independientes de la suite general de facturación."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.empresa = Empresa.objects.create(
            nombre="Medical Spa pruebas de lotes", slug="medical_spa", rtn="08011999123456",
        )
        cls.otra_empresa = Empresa.objects.create(
            nombre="Otra empresa de lotes", slug="otra_lotes", rtn="08011999123457",
        )
        modulo, _ = Modulo.objects.get_or_create(
            codigo="facturacion", defaults={"nombre": "Facturación"},
        )
        EmpresaModulo.objects.create(empresa=cls.empresa, modulo=modulo, activo=True)
        cls.usuario = get_user_model().objects.create_user(
            username="admin_lotes", password="pass", empresa=cls.empresa,
            is_superuser=True, is_staff=True,
        )
        cls.producto = Producto.objects.create(
            empresa=cls.empresa, nombre="Medicamento de prueba", codigo="LOTE-TEST",
            precio=Decimal("10.00"), controla_inventario=True,
        )
        cls.otro_producto = Producto.objects.create(
            empresa=cls.otra_empresa, nombre="Medicamento ajeno", precio=Decimal("10.00"),
        )
        cls.bodega = BodegaInventario.objects.create(
            empresa=cls.empresa, nombre="Bodega General", tipo="principal",
        )
        cls.otra_bodega = BodegaInventario.objects.create(
            empresa=cls.otra_empresa, nombre="Bodega ajena", tipo="principal",
        )
        cls.bodega_inactiva = BodegaInventario.objects.create(
            empresa=cls.empresa, nombre="Bodega cerrada", tipo="otra", activa=False,
        )
        cls.proveedor = Proveedor.objects.create(empresa=cls.empresa, nombre="Proveedor activo")
        cls.otro_proveedor = Proveedor.objects.create(
            empresa=cls.otra_empresa, nombre="Proveedor ajeno",
        )
        cls.proveedor_inactivo = Proveedor.objects.create(
            empresa=cls.empresa, nombre="Proveedor inactivo", activo=False,
        )
        cls.inventario = InventarioProducto.objects.create(
            empresa=cls.empresa, producto=cls.producto, existencias=Decimal("7.00"),
        )

    def datos_lote(self, **cambios):
        datos = {
            "producto": str(self.producto.pk),
            "numero_lote": "LOTE-NUEVO",
            "fecha_vencimiento": "2031-06-15",
            "fecha_vencimiento_rapida": "",
            "bodega": str(self.bodega.pk),
            "cantidad": "10.25",
            "proveedor": "",
            "observacion": "INGRESO DE PRODUCTO",
        }
        datos.update(cambios)
        return datos

    def assert_sin_movimiento_nuevo(self):
        self.assertFalse(LoteInventario.objects.filter(empresa=self.empresa).exists())
        self.assertFalse(ExistenciaLoteBodega.objects.filter(empresa=self.empresa).exists())
        self.assertFalse(MovimientoLoteBodega.objects.filter(empresa=self.empresa).exists())
        self.assertFalse(MovimientoInventario.objects.filter(empresa=self.empresa).exists())
        self.inventario.refresh_from_db()
        self.assertEqual(self.inventario.existencias, Decimal("7.00"))


class RegistrarLoteInventarioFormTests(LoteFixturesMixin, TestCase):
    def formulario(self, **cambios):
        return RegistrarLoteInventarioForm(self.datos_lote(**cambios), empresa=self.empresa)

    def test_proveedor_es_opcional_y_sin_proveedor_significa_null(self):
        for valor in ("", "Sin proveedor", "  Sin proveedor  "):
            with self.subTest(proveedor=valor):
                form = self.formulario(proveedor=valor)
                self.assertTrue(form.is_valid(), form.errors)
                self.assertIsNone(form.cleaned_data["proveedor"])
        datos = self.datos_lote()
        datos.pop("proveedor")
        form = RegistrarLoteInventarioForm(datos, empresa=self.empresa)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.cleaned_data["proveedor"])

    def test_proveedor_activo_de_la_empresa_es_aceptado(self):
        form = self.formulario(proveedor=str(self.proveedor.pk))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["proveedor"], self.proveedor)

    def test_campos_obligatorios_tienen_errores_por_campo(self):
        for campo in ("producto", "numero_lote", "fecha_vencimiento", "bodega", "cantidad"):
            with self.subTest(campo=campo):
                form = self.formulario(**{campo: ""})
                self.assertFalse(form.is_valid())
                self.assertIn(campo, form.errors)

    def test_cantidad_debe_ser_positiva_finita_y_caber_en_decimal(self):
        for cantidad in (
            "0", "-1", "-0.01", "NaN", "sNaN", "Infinity", "-Infinity",
            "abc", "0.001", "10000000000.00", "1e100000", "",
        ):
            with self.subTest(cantidad=cantidad):
                form = self.formulario(cantidad=cantidad)
                self.assertFalse(form.is_valid())
                self.assertIn("cantidad", form.errors)

    def test_cantidad_minima_es_valida(self):
        form = self.formulario(cantidad="0.01")
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["cantidad"], Decimal("0.01"))

    def test_fecha_iso_y_latam_se_convierten_a_date(self):
        for valor in ("2031-06-15", "15/06/2031"):
            with self.subTest(fecha=valor):
                form = self.formulario(fecha_vencimiento=valor)
                self.assertTrue(form.is_valid(), form.errors)
                self.assertEqual(form.cleaned_data["fecha_vencimiento"], date(2031, 6, 15))

    def test_fecha_rapida_completa_fecha_vacia(self):
        form = self.formulario(fecha_vencimiento="", fecha_vencimiento_rapida="junio/15/2031")
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["fecha_vencimiento"], date(2031, 6, 15))

    def test_fecha_invalida_no_se_oculta_con_la_fecha_rapida(self):
        form = self.formulario(fecha_vencimiento="31/02/2031", fecha_vencimiento_rapida="jun-31")
        self.assertFalse(form.is_valid())
        self.assertIn("fecha_vencimiento", form.errors)

    def test_fecha_rapida_invalida_tiene_error_especifico(self):
        form = self.formulario(fecha_vencimiento="", fecha_vencimiento_rapida="ene-10000")
        self.assertFalse(form.is_valid())
        self.assertIn("fecha_vencimiento_rapida", form.errors)

    def test_producto_bodega_y_proveedor_ajenos_se_rechazan(self):
        for campo, objeto in (
            ("producto", self.otro_producto), ("bodega", self.otra_bodega),
            ("proveedor", self.otro_proveedor),
        ):
            with self.subTest(campo=campo):
                form = self.formulario(**{campo: str(objeto.pk)})
                self.assertFalse(form.is_valid())
                self.assertIn(campo, form.errors)

    def test_bodega_y_proveedor_inactivos_se_rechazan(self):
        for campo, objeto in (("bodega", self.bodega_inactiva), ("proveedor", self.proveedor_inactivo)):
            with self.subTest(campo=campo):
                form = self.formulario(**{campo: str(objeto.pk)})
                self.assertFalse(form.is_valid())
                self.assertIn(campo, form.errors)

    def test_producto_inactivo_eliminado_o_sin_inventario_se_rechaza(self):
        for campo, valor in (("activo", False), ("eliminado", True), ("controla_inventario", False)):
            with self.subTest(campo=campo):
                original = getattr(self.producto, campo)
                setattr(self.producto, campo, valor)
                self.producto.save(update_fields=[campo])
                form = self.formulario()
                self.assertFalse(form.is_valid())
                self.assertIn("producto", form.errors)
                setattr(self.producto, campo, original)
                self.producto.save(update_fields=[campo])

    def test_ids_malformados_y_fuera_del_rango_no_lanzan_excepciones(self):
        for campo in ("producto", "bodega", "proveedor"):
            for valor in ("abc", "1.2", "-1", "99999999999999999999999999999999999"):
                with self.subTest(campo=campo, valor=valor):
                    form = self.formulario(**{campo: valor})
                    self.assertFalse(form.is_valid())
                    self.assertIn(campo, form.errors)

    def test_numero_lote_largo_se_rechaza_y_espacios_se_recortan(self):
        form = self.formulario(numero_lote="X" * 81)
        self.assertFalse(form.is_valid())
        self.assertIn("numero_lote", form.errors)
        form = self.formulario(numero_lote="  LOTE-NUEVO  ", observacion="  Entrada manual  ")
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["numero_lote"], "LOTE-NUEVO")
        self.assertEqual(form.cleaned_data["observacion"], "Entrada manual")

    def test_duplicado_por_empresa_producto_y_numero_tiene_mensaje_exacto(self):
        LoteInventario.objects.create(
            empresa=self.empresa, producto=self.producto, numero_lote="LOTE-NUEVO",
            fecha_vencimiento=date(2030, 1, 1),
        )
        form = self.formulario(numero_lote=" LOTE-NUEVO ")
        self.assertFalse(form.is_valid())
        self.assertEqual(list(form.errors["numero_lote"]), [DUPLICADO])

    def test_mismo_numero_en_otro_producto_no_es_duplicado(self):
        producto = Producto.objects.create(
            empresa=self.empresa, nombre="Segundo medicamento", precio=Decimal("10.00"),
        )
        LoteInventario.objects.create(
            empresa=self.empresa, producto=producto, numero_lote="LOTE-NUEVO",
            fecha_vencimiento=date(2030, 1, 1),
        )
        form = self.formulario()
        self.assertTrue(form.is_valid(), form.errors)


class RegistrarLoteInventarioHTTPTests(LoteFixturesMixin, TestCase):
    def setUp(self):
        self.client.force_login(self.usuario)
        self.url = reverse("crear_lote_inventario", args=[self.empresa.slug])

    def post_lote(self, **cambios):
        return self.client.post(self.url, self.datos_lote(**cambios))

    def assert_formulario_con_error(self, response, campo=None):
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "facturacion/crear_lote_inventario.html")
        self.assertTrue(response.context["form"].errors)
        if campo:
            self.assertIn(campo, response.context["form"].errors)
        self.assertNotContains(response, "Traceback")
        self.assertNotContains(response, "Error del servidor (500)")

    def test_get_preselecciona_producto_y_bodega(self):
        response = self.client.get(self.url, {"producto": str(self.producto.pk)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["form"]["producto"].value(), self.producto.pk)
        self.assertEqual(response.context["form"]["bodega"].value(), self.bodega.pk)

    def test_get_producto_malformado_extremo_o_ajeno_muestra_error(self):
        for valor in ("abc", "99999999999999999999999999999999999", str(self.otro_producto.pk)):
            with self.subTest(producto=valor):
                response = self.client.get(self.url, {"producto": valor})
                self.assert_formulario_con_error(response, "producto")
        self.assert_sin_movimiento_nuevo()

    def test_registro_exitoso_guarda_lote_saldos_y_dos_movimientos(self):
        response = self.post_lote(proveedor=str(self.proveedor.pk))
        self.assertRedirects(
            response, reverse("inventario_farmaceutico", args=[self.empresa.slug]),
            fetch_redirect_response=False,
        )
        lote = LoteInventario.objects.get(empresa=self.empresa, numero_lote="LOTE-NUEVO")
        self.assertEqual(lote.producto, self.producto)
        self.assertEqual(lote.fecha_vencimiento, date(2031, 6, 15))
        self.assertEqual(lote.proveedor, self.proveedor)
        existencia = ExistenciaLoteBodega.objects.get(lote=lote, bodega=self.bodega)
        self.assertEqual(existencia.cantidad, Decimal("10.25"))
        movimiento_lote = MovimientoLoteBodega.objects.get(lote=lote)
        movimiento = MovimientoInventario.objects.get(producto=self.producto)
        for item in (movimiento_lote, movimiento):
            self.assertEqual(item.cantidad, Decimal("10.25"))
            self.assertEqual(item.bodega, self.bodega)
            self.assertEqual(item.usuario, self.usuario)
            self.assertEqual(item.observacion, "INGRESO DE PRODUCTO")
        self.assertEqual(movimiento_lote.existencia_anterior, Decimal("0.00"))
        self.assertEqual(movimiento_lote.existencia_resultante, Decimal("10.25"))
        self.assertEqual(movimiento.existencia_anterior, Decimal("7.00"))
        self.assertEqual(movimiento.existencia_resultante, Decimal("17.25"))
        self.inventario.refresh_from_db()
        self.assertEqual(self.inventario.existencias, Decimal("17.25"))

    def test_proveedor_vacio_y_sin_proveedor_guardan_null(self):
        for indice, valor in enumerate(("", "Sin proveedor")):
            with self.subTest(proveedor=valor):
                numero = f"SIN-PROVEEDOR-{indice}"
                response = self.post_lote(numero_lote=numero, proveedor=valor)
                self.assertEqual(response.status_code, 302)
                self.assertIsNone(LoteInventario.objects.get(numero_lote=numero).proveedor_id)
        datos = self.datos_lote(numero_lote="SIN-CLAVE-PROVEEDOR")
        datos.pop("proveedor")
        response = self.client.post(self.url, datos)
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(LoteInventario.objects.get(numero_lote="SIN-CLAVE-PROVEEDOR").proveedor_id)

    def test_duplicado_no_cambia_lote_existencia_ni_vencimiento(self):
        lote = LoteInventario.objects.create(
            empresa=self.empresa, producto=self.producto, numero_lote="LOTE-NUEVO",
            fecha_vencimiento=date(2029, 1, 1), proveedor=self.proveedor,
        )
        existencia = ExistenciaLoteBodega.objects.create(
            empresa=self.empresa, bodega=self.bodega, lote=lote, cantidad=Decimal("7.00"),
        )
        response = self.post_lote()
        self.assert_formulario_con_error(response, "numero_lote")
        self.assertContains(response, DUPLICADO)
        lote.refresh_from_db()
        existencia.refresh_from_db()
        self.inventario.refresh_from_db()
        self.assertEqual(lote.fecha_vencimiento, date(2029, 1, 1))
        self.assertEqual(lote.proveedor, self.proveedor)
        self.assertEqual(existencia.cantidad, Decimal("7.00"))
        self.assertEqual(self.inventario.existencias, Decimal("7.00"))
        self.assertEqual(LoteInventario.objects.filter(empresa=self.empresa).count(), 1)
        self.assertFalse(MovimientoLoteBodega.objects.exists())
        self.assertFalse(MovimientoInventario.objects.exists())

    def test_cantidad_cero_negativa_no_finita_y_extrema_muestra_error(self):
        for valor in ("0", "-3", "NaN", "Infinity", "1e100000", "10000000000.00"):
            with self.subTest(cantidad=valor):
                response = self.post_lote(cantidad=valor)
                self.assert_formulario_con_error(response, "cantidad")
                self.assertEqual(response.context["form"]["cantidad"].value(), valor)
        self.assert_sin_movimiento_nuevo()

    def test_fecha_invalida_o_vacia_muestra_error(self):
        for valor in ("", "31/02/2031", "abc", "10000-06-15"):
            with self.subTest(fecha=valor):
                response = self.post_lote(fecha_vencimiento=valor)
                self.assert_formulario_con_error(response, "fecha_vencimiento")
                if valor:
                    self.assertContains(response, valor)
        self.assert_sin_movimiento_nuevo()

    def test_fecha_latam_sigue_visible_como_fecha_iso_tras_error_en_otro_campo(self):
        for valor in ("15/06/2031", " 15/06/2031 "):
            with self.subTest(fecha=valor):
                response = self.post_lote(fecha_vencimiento=valor, cantidad="0")
                self.assert_formulario_con_error(response, "cantidad")
                self.assertEqual(response.context["form"].data["fecha_vencimiento"], valor)
                self.assertContains(response, 'name="fecha_vencimiento" value="2031-06-15"')
        self.assert_sin_movimiento_nuevo()

    def test_fecha_rapida_invalida_no_provoca_500(self):
        response = self.post_lote(fecha_vencimiento="", fecha_vencimiento_rapida="junio/31/2031")
        self.assert_formulario_con_error(response, "fecha_vencimiento_rapida")
        self.assertContains(response, "junio/31/2031")
        self.assert_sin_movimiento_nuevo()

    def test_producto_bodega_y_proveedor_ajenos_no_modifican_stock(self):
        for campo, objeto in (
            ("producto", self.otro_producto), ("bodega", self.otra_bodega),
            ("proveedor", self.otro_proveedor),
        ):
            with self.subTest(campo=campo):
                response = self.post_lote(**{campo: str(objeto.pk)})
                self.assert_formulario_con_error(response, campo)
        self.assert_sin_movimiento_nuevo()
        self.assertFalse(LoteInventario.objects.filter(empresa=self.otra_empresa).exists())

    def test_post_ids_malformados_no_provoca_500(self):
        for campo in ("producto", "bodega", "proveedor"):
            for valor in ("abc", "99999999999999999999999999999999999"):
                with self.subTest(campo=campo, valor=valor):
                    response = self.post_lote(**{campo: valor})
                    self.assert_formulario_con_error(response, campo)
        self.assert_sin_movimiento_nuevo()

    def test_error_conserva_datos_y_muestra_resumen_y_error_de_campo(self):
        datos = self.datos_lote(
            cantidad="0", numero_lote="LOTE-ESCRITO", proveedor=str(self.proveedor.pk),
            observacion="Observación del usuario", fecha_vencimiento_rapida="junio/15/2031",
            next="/medical_spa/dashboard/facturacion/inventario/",
        )
        response = self.client.post(self.url, datos)
        self.assert_formulario_con_error(response, "cantidad")
        form = response.context["form"]
        self.assertTrue(form.is_bound)
        for campo in datos:
            self.assertEqual(form.data[campo], datos[campo])
        for valor in ("LOTE-ESCRITO", "Observación del usuario", "junio/15/2031", datos["next"]):
            self.assertContains(response, valor)
        mensaje = str(form.errors["cantidad"][0])
        self.assertGreaterEqual(response.content.decode().count(mensaje), 2)
        self.assert_sin_movimiento_nuevo()

    def test_saldo_acumulado_extremo_se_rechaza_sin_lote_parcial(self):
        self.inventario.existencias = Decimal("9999999999.99")
        self.inventario.save(update_fields=["existencias"])
        response = self.post_lote(cantidad="0.01")
        self.assert_formulario_con_error(response, "cantidad")
        self.assertFalse(LoteInventario.objects.exists())
        self.assertFalse(ExistenciaLoteBodega.objects.exists())
        self.assertFalse(MovimientoInventario.objects.exists())
        self.inventario.refresh_from_db()
        self.assertEqual(self.inventario.existencias, Decimal("9999999999.99"))

    def test_fallo_movimiento_por_lote_revierte_lote_y_existencia_escritos(self):
        def fallar_despues_de_escribir(**kwargs):
            lote = LoteInventario.objects.get(numero_lote="LOTE-NUEVO")
            existencia = ExistenciaLoteBodega.objects.get(lote=lote, bodega=self.bodega)
            self.assertEqual(existencia.cantidad, Decimal("10.25"))
            raise ValidationError({"cantidad": "PRIVATE-VALIDATION-DETAIL"})

        with patch.object(MovimientoLoteBodega.objects, "create", side_effect=fallar_despues_de_escribir) as crear:
            with self.assertLogs("facturacion.views", level="WARNING") as logs:
                response = self.post_lote()
        crear.assert_called_once()
        self.assert_formulario_con_error(response, "cantidad")
        self.assertNotContains(response, "PRIVATE-VALIDATION-DETAIL")
        self.assertIn("PRIVATE-VALIDATION-DETAIL", "\n".join(logs.output))
        self.assert_sin_movimiento_nuevo()

    def test_fallo_movimiento_general_revierte_ambos_saldos_y_movimiento_lote(self):
        def fallar_despues_de_escribir(**kwargs):
            lote = LoteInventario.objects.get(numero_lote="LOTE-NUEVO")
            self.assertEqual(ExistenciaLoteBodega.objects.get(lote=lote).cantidad, Decimal("10.25"))
            self.assertEqual(MovimientoLoteBodega.objects.filter(lote=lote).count(), 1)
            self.inventario.refresh_from_db()
            self.assertEqual(self.inventario.existencias, Decimal("17.25"))
            raise IntegrityError("PRIVATE-SQL-DETAIL")

        with patch.object(MovimientoInventario.objects, "create", side_effect=fallar_despues_de_escribir) as crear:
            with self.assertLogs("facturacion.views", level="WARNING") as logs:
                response = self.post_lote()
        crear.assert_called_once()
        self.assert_formulario_con_error(response)
        self.assertNotContains(response, "PRIVATE-SQL-DETAIL")
        self.assertIn("PRIVATE-SQL-DETAIL", "\n".join(logs.output))
        self.assert_sin_movimiento_nuevo()

    def test_value_error_del_servicio_se_registra_y_no_se_expone(self):
        with patch("facturacion.views._registrar_movimiento_inventario", side_effect=ValueError("PRIVATE-ID-DETAIL")):
            with self.assertLogs("facturacion.views", level="WARNING") as logs:
                response = self.post_lote()
        self.assert_formulario_con_error(response)
        self.assertNotContains(response, "PRIVATE-ID-DETAIL")
        self.assertIn("PRIVATE-ID-DETAIL", "\n".join(logs.output))
        self.assert_sin_movimiento_nuevo()

    def test_integrity_error_real_de_duplicado_se_recupera_despues_de_rollback(self):
        lote = LoteInventario.objects.create(
            empresa=self.empresa, producto=self.producto, numero_lote="LOTE-NUEVO",
            fecha_vencimiento=date(2029, 1, 1), proveedor=self.proveedor,
        )
        exists_original = QuerySet.exists
        comprobaciones_lote = []

        def ocultar_duplicado_hasta_insert(queryset):
            if queryset.model is LoteInventario:
                comprobaciones_lote.append(queryset)
                # Simular que un alta concurrente apareció después de validar.
                if len(comprobaciones_lote) <= 2:
                    return False
            return exists_original(queryset)

        with patch.object(QuerySet, "exists", ocultar_duplicado_hasta_insert):
            with self.assertLogs("facturacion.views", level="WARNING") as logs:
                response = self.post_lote()
        self.assertEqual(len(comprobaciones_lote), 3)
        self.assert_formulario_con_error(response, "numero_lote")
        self.assertEqual(list(response.context["form"].errors["numero_lote"]), [DUPLICADO])
        self.assertIn("IntegrityError", "\n".join(logs.output))
        self.assertNotContains(response, "UNIQUE constraint")
        self.assertEqual(LoteInventario.objects.filter(empresa=self.empresa).count(), 1)
        lote.refresh_from_db()
        self.inventario.refresh_from_db()
        self.assertEqual(lote.fecha_vencimiento, date(2029, 1, 1))
        self.assertEqual(lote.proveedor, self.proveedor)
        self.assertEqual(self.inventario.existencias, Decimal("7.00"))
        self.assertFalse(ExistenciaLoteBodega.objects.exists())
        self.assertFalse(MovimientoLoteBodega.objects.exists())
        self.assertFalse(MovimientoInventario.objects.exists())

    def test_error_inesperado_se_registra_con_exception_y_codigo_referencia(self):
        with patch("facturacion.views._registrar_movimiento_inventario", side_effect=RuntimeError("PRIVATE-TRACEBACK-DETAIL")):
            with patch("facturacion.views.logger.exception") as exception:
                response = self.post_lote()
        exception.assert_called_once()
        self.assert_formulario_con_error(response)
        self.assertContains(response, "No fue posible registrar el lote.")
        self.assertContains(response, "Código de referencia:")
        self.assertNotContains(response, "PRIVATE-TRACEBACK-DETAIL")
        referencia = exception.call_args.args[-1]
        self.assertRegex(referencia, r"^[A-F0-9]{12}$")
        self.assertContains(response, referencia)
        self.assertEqual(response.context["form"]["observacion"].value(), "INGRESO DE PRODUCTO")
        self.assert_sin_movimiento_nuevo()

    def test_error_get_de_preparacion_se_registra_y_muestra_formulario(self):
        with patch("facturacion.views._asegurar_bodegas_farmaceuticas", side_effect=RuntimeError("PRIVATE-GET-DETAIL")):
            with patch("facturacion.views.logger.exception") as exception:
                response = self.client.get(self.url)
        exception.assert_called_once()
        self.assert_formulario_con_error(response)
        self.assertContains(response, "Código de referencia:")
        self.assertNotContains(response, "PRIVATE-GET-DETAIL")
        self.assert_sin_movimiento_nuevo()
