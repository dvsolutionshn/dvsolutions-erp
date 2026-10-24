"""Trazabilidad del actor en POST reales y consulta historica del Kardex."""
import copy
import json
from datetime import timedelta
from decimal import Decimal

import html5lib
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.audit_context import audit_scope
from core.models import ConfiguracionAvanzadaEmpresa, Empresa, EmpresaModulo, Modulo, RegistroAuditoria, RolSistema, Usuario
from .models import CAI, BodegaInventario, Cliente, CompraInventario, EntradaInventarioDocumento, ExistenciaLoteBodega, Factura, InventarioProducto, LoteInventario, MovimientoInventario, MovimientoLoteBodega, PagoFactura, Producto, TipoImpuesto
from .services_clientes_compartidos import suspender_sincronizacion_clientes_compartidos


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class KardexUsuarioIntegrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(nombre="Kardex actores", slug="hospital_mia", rtn="KDX-ACTOR", tipo_solucion="clinica", estado_licencia="activa")
        cls.simple = Empresa.objects.create(nombre="Kardex simple", slug="kardex_simple", rtn="KDX-SIMPLE", estado_licencia="activa")
        cls.otra = Empresa.objects.create(nombre="Kardex externo", slug="kardex_externo", rtn="KDX-EXTERNO")
        permisos = ("inventario", "ajustar_inventario", "transferir_inventario", "compras", "crear_compras", "aplicar_compras", "facturas", "ver_facturas", "crear_facturas", "punto_venta")
        rol = RolSistema.objects.create(nombre="Operador Kardex", codigo="kdx-actor", **{f"puede_{p}": True for p in permisos})
        cls.actor = Usuario.objects.create_user(username="kdx-operador", empresa=cls.empresa, rol_sistema=rol, first_name="Ana", last_name="Operadora")
        cls.creador = Usuario.objects.create_user(username="kdx-creador", empresa=cls.empresa, rol_sistema=rol, first_name="Carlos", last_name="Historico")
        cls.lector = Usuario.objects.create_user(username="kdx-lector", empresa=cls.empresa, rol_sistema=rol, first_name="Lucia", last_name="Lectora")
        for user in (cls.actor, cls.creador, cls.lector):
            user.empresas_acceso.add(cls.simple)
        cls.impuesto = TipoImpuesto.objects.create(nombre="Exento Kardex", porcentaje=0)
        cls.productos, cls.bodegas = {}, {}
        for empresa in (cls.empresa, cls.simple, cls.otra):
            for codigo in ("facturacion", "punto_venta", "clinica_medica"):
                modulo, _ = Modulo.objects.get_or_create(codigo=codigo, defaults={"nombre": codigo})
                EmpresaModulo.objects.create(empresa=empresa, modulo=modulo)
            ConfiguracionAvanzadaEmpresa.objects.create(empresa=empresa, usa_inventario_farmaceutico=True, usa_bodegas_internas=True, ventas_solo_desde_vitrina=False)
            cls.bodegas[empresa.pk] = [BodegaInventario.objects.create(empresa=empresa, nombre=nombre, tipo=tipo) for nombre, tipo in (("Bodega General" if empresa == cls.empresa else "Bodega principal", "principal"), ("Bodega Hospital" if empresa == cls.empresa else "Bodega provisional", "provisional"), ("Vitrina", "vitrina"))]
            producto = Producto.objects.create(empresa=empresa, nombre=f"Producto {empresa.slug}", codigo=f"KDX-{empresa.pk}", precio=10, costo_promedio=2, controla_inventario=True, impuesto_predeterminado=cls.impuesto)
            cls.productos[empresa.pk] = producto
            InventarioProducto.objects.create(empresa=empresa, producto=producto, existencias=10, valor_contable=20)
        cls.producto = cls.productos[cls.empresa.pk]
        cls.origen, cls.destino, cls.vitrina = cls.bodegas[cls.empresa.pk]
        cls.lotes = []
        for dias, cantidad in ((30, 3), (90, 7)):
            lote = LoteInventario.objects.create(empresa=cls.empresa, producto=cls.producto, numero_lote=f"FEFO-{dias}", fecha_vencimiento=timezone.localdate() + timedelta(days=dias))
            ExistenciaLoteBodega.objects.create(empresa=cls.empresa, bodega=cls.origen, lote=lote, cantidad=cantidad)
            cls.lotes.append(lote)
        with suspender_sincronizacion_clientes_compartidos():
            cls.cliente = Cliente.objects.create(empresa=cls.empresa, nombre="Cliente Kardex", rtn="0801199000001")
        CAI.objects.create(empresa=cls.empresa, numero_cai="KDX-CAI", uso_documento="factura", establecimiento="001", punto_emision="001", tipo_documento="01", rango_inicial=1, rango_final=100, correlativo_actual=0, fecha_activacion=timezone.localdate() - timedelta(days=1), fecha_limite=timezone.localdate() + timedelta(days=365))

    def setUp(self):
        self.client.force_login(self.actor)

    def post(self, name, data, empresa=None, args=()):
        data = {**data, "usuario": str(self.creador.pk), "usuario_id": str(self.creador.pk)}
        return self.client.post(reverse(name, args=[(empresa or self.empresa).slug, *args]), data)

    def marca(self):
        return tuple(set(model.objects.values_list("pk", flat=True)) for model in (MovimientoInventario, MovimientoLoteBodega))

    def nuevos(self, antes, generales=1, lotes=1, empresa=None):
        empresa = empresa or self.empresa
        resultado = []
        for model, ids, cantidad in zip((MovimientoInventario, MovimientoLoteBodega), antes, (generales, lotes)):
            movimientos = list(model.objects.exclude(pk__in=ids).order_by("pk"))
            self.assertEqual(len(movimientos), cantidad, model.__name__)
            self.assertEqual([m.usuario_id for m in movimientos], [self.actor.pk] * cantidad)
            self.assertTrue(all(m.empresa_id == empresa.pk for m in movimientos))
            resultado.append(movimientos)
        visibles = resultado[0] + [m for m in resultado[1] if m.tipo in ("traslado_salida", "traslado_entrada")]
        for mobile in (False, True):
            response, tree = self.vista(mobile, empresa)
            es_mobile = "facturacion/mobile_kardex_list.html" in [t.name for t in response.templates]
            for movimiento in visibles:
                self.assertEqual(self.fila(tree, movimiento, es_mobile)["Usuario"], self.actor.get_full_name())
        return resultado

    def saldo(self, esperado, producto=None):
        self.assertEqual(InventarioProducto.objects.get(producto=producto or self.producto).existencias, Decimal(str(esperado)))

    def lineas(self, prefix, **campos):
        data = {f"{prefix}-{key}": value for key, value in {"TOTAL_FORMS": "1", "INITIAL_FORMS": "0", "MIN_NUM_FORMS": "0", "MAX_NUM_FORMS": "1000"}.items()}
        data.update({f"{prefix}-0-{key}": value for key, value in {"producto": str(self.producto.pk), "cantidad": "5", **campos}.items()})
        return data

    def compra(self, estado):
        return self.post("crear_compra", {"proveedor_nombre": "Proveedor sintetico", "referencia_documento": "COMPRA-ACTOR", "fecha_documento": timezone.localdate().isoformat(), "estado": estado, "condicion_pago": "credito", "dias_credito": "0", "metodo_pago": "efectivo", **self.lineas("lineas_compra", costo_unitario="2", descuento_porcentaje="0")})

    def test_compra_aplicada_y_confirmacion_borrador_por_otro_usuario(self):
        antes = self.marca()
        self.assertEqual(self.compra("aplicada").status_code, 302)
        generales, lotes = self.nuevos(antes)
        self.assertEqual(generales[0].bodega_id, self.vitrina.pk)
        self.assertEqual(lotes[0].bodega_id, self.vitrina.pk)
        self.saldo(15)
        self.client.force_login(self.creador)
        antes = self.marca()
        self.assertEqual(self.compra("borrador").status_code, 302)
        self.assertEqual(self.marca(), antes)
        borrador = CompraInventario.objects.get(estado="borrador")
        self.client.force_login(self.actor)
        self.assertEqual(self.post("aplicar_compra", {}, args=(borrador.pk,)).status_code, 302)
        self.nuevos(antes)
        borrador.refresh_from_db()
        self.assertEqual(borrador.estado, "aplicada")
        self.saldo(20)
        antes = self.marca()
        self.assertEqual(self.post("aplicar_compra", {}, args=(borrador.pk,)).status_code, 302)
        self.assertEqual(self.marca(), antes)
        self.saldo(20)

    def test_entrada_simple_y_documento_formal(self):
        producto = self.productos[self.simple.pk]
        bodega = self.bodegas[self.simple.pk][0]
        antes = self.marca()
        self.assertEqual(self.post("entrada_inventario", {"producto": producto.pk, "bodega": bodega.pk, "cantidad": "4", "referencia": "ENT-SIMPLE"}, empresa=self.simple).status_code, 302)
        for movimientos in self.nuevos(antes, empresa=self.simple):
            self.assertEqual((movimientos[0].usuario_id, movimientos[0].bodega_id, movimientos[0].empresa_id), (self.actor.pk, bodega.pk, self.simple.pk))
        self.saldo(14, producto)
        self.assertEqual(ExistenciaLoteBodega.objects.get(bodega=bodega, lote__producto=producto).cantidad, Decimal("4"))
        antes = self.marca()
        self.assertEqual(self.post("crear_entrada_inventario_documento", {"referencia": "ENT-FORMAL", "fecha_documento": timezone.localdate().isoformat(), "estado": "aplicada", **self.lineas("lineas_entrada")}).status_code, 302)
        generales, _ = self.nuevos(antes)
        self.assertEqual(generales[0].entrada_documento_id, EntradaInventarioDocumento.objects.get(referencia="ENT-FORMAL").pk)
        self.saldo(15)

    def assert_fefo(self, antes):
        generales, lotes = self.nuevos(antes, lotes=2)
        self.assertEqual([(m.lote_id, m.cantidad, m.existencia_anterior, m.existencia_resultante) for m in lotes], [(self.lotes[0].pk, Decimal("3"), Decimal("3"), Decimal("0")), (self.lotes[1].pk, Decimal("2"), Decimal("7"), Decimal("5"))])
        self.assertEqual((generales[0].existencia_anterior, generales[0].existencia_resultante), (Decimal("10"), Decimal("5")))
        self.assertEqual(list(ExistenciaLoteBodega.objects.filter(lote__in=self.lotes).order_by("lote_id").values_list("cantidad", flat=True)), [Decimal("0"), Decimal("5")])
        self.saldo(5)
        return generales[0]

    def test_factura_fefo_actor_distinto_del_vendedor(self):
        antes = self.marca()
        response = self.post("crear_factura", {"cliente": self.cliente.pk, "fecha_emision": timezone.localdate().isoformat(), "vendedor": self.creador.pk, "tipo_cambio": "1.0000", "moneda": "HNL", "estado": "emitida", **self.lineas("lineas", precio_unitario="10", descuento_porcentaje="0", impuesto=self.impuesto.pk)})
        self.assertEqual(response.status_code, 302)
        movimiento = self.assert_fefo(antes)
        self.assertEqual(Factura.objects.get(pk=movimiento.factura_id).vendedor_id, self.creador.pk)

    def test_pos_fefo_conserva_cobro_y_actor_ignora_payload(self):
        antes = self.marca()
        payload = {"cliente_id": self.cliente.pk, "metodo": "efectivo", "usuario": self.creador.pk, "usuario_id": self.creador.pk, "items": [{"producto_id": self.producto.pk, "cantidad": "5", "precio_unitario": "10"}]}
        self.assertEqual(self.post("punto_venta", {"payload": json.dumps(payload)}).status_code, 302)
        movimiento = self.assert_fefo(antes)
        self.assertTrue(PagoFactura.objects.filter(factura_id=movimiento.factura_id, monto=50).exists())

    def test_traslado_normal_y_rapido_conservan_stock_general(self):
        existencia = ExistenciaLoteBodega.objects.get(lote=self.lotes[0], bodega=self.origen)
        for endpoint, cantidad in (("traslado_inventario_farmaceutico", "1"), ("traslado_rapido_farmaceutico", "2")):
            with self.subTest(endpoint=endpoint):
                antes = self.marca()
                inventario = InventarioProducto.objects.get(producto=self.producto)
                valores = (inventario.existencias, inventario.valor_contable)
                self.assertEqual(self.post(endpoint, {"existencia": existencia.pk, "bodega_destino": self.destino.pk, "ruta": "principal_provisional", "cantidad": cantidad}).status_code, 302)
                _, lotes = self.nuevos(antes, generales=0, lotes=2)
                self.assertEqual([(m.tipo, m.bodega_id) for m in lotes], [("traslado_salida", self.origen.pk), ("traslado_entrada", self.destino.pk)])
                self.assertEqual(lotes[0].referencia, lotes[1].referencia)
                self.assertEqual(sum(ExistenciaLoteBodega.objects.filter(lote=self.lotes[0]).values_list("cantidad", flat=True)), Decimal("3"))
                inventario.refresh_from_db()
                self.assertEqual((inventario.existencias, inventario.valor_contable), valores)

    def test_regalia_sin_factura_registra_todos_los_lotes(self):
        antes = self.marca()
        self.assertEqual(self.post("regalias_inventario", {"producto_id": [str(self.producto.pk)], "cantidad": ["5"], "bodega": self.origen.pk}).status_code, 302)
        movimiento = self.assert_fefo(antes)
        self.assertEqual(movimiento.tipo, "regalia")
        self.assertFalse(Factura.objects.exists())

    def test_entrada_rapida_json_ignora_usuario(self):
        antes = self.marca()
        payload = {"producto_id": self.producto.pk, "bodega_id": self.destino.pk, "numero_lote": "APP-ACTOR", "fecha_vencimiento": (timezone.localdate() + timedelta(days=60)).isoformat(), "cantidad": "2", "usuario": self.creador.pk, "usuario_id": self.creador.pk}
        response = self.client.post(reverse("entrada_inventario_rapida_app", args=[self.empresa.slug]), json.dumps(payload), content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.nuevos(antes)
        self.saldo(12)

    def test_ajustes_manuales_positivo_negativo_y_salida_fefo(self):
        producto = self.productos[self.simple.pk]
        for tipo, cantidad, saldo in (("ajuste_entrada", "4", 14), ("ajuste_salida", "2", 12)):
            antes = self.marca()
            self.assertEqual(self.post("ajustar_inventario", {"producto": producto.pk, "tipo_ajuste": tipo, "cantidad": cantidad}, empresa=self.simple).status_code, 302)
            movimientos, _ = self.nuevos(antes, lotes=0, empresa=self.simple)
            self.assertEqual(movimientos[0].tipo, tipo)
            self.saldo(saldo, producto)
        antes = self.marca()
        self.assertEqual(self.post("ajustar_inventario", {"producto": self.producto.pk, "tipo_ajuste": "ajuste_salida", "cantidad": "5"}).status_code, 302)
        self.assert_fefo(antes)

    def historico(self, lote=False, **extra):
        datos = dict(empresa=self.empresa, tipo="traslado_salida" if lote else "entrada", cantidad=Decimal("7.25"), existencia_anterior=Decimal("81.75"), existencia_resultante=Decimal("74.50"), referencia=f"HIST-{MovimientoInventario.objects.count()}-{MovimientoLoteBodega.objects.count()}")
        datos.update({"lote": self.lotes[0], "bodega": self.origen} if lote else {"producto": self.producto})
        return (MovimientoLoteBodega if lote else MovimientoInventario).objects.create(**{**datos, **extra})

    def vista(self, mobile, empresa=None):
        empresa = empresa or self.empresa
        url = reverse("kardex_inventario", args=[empresa.slug])
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(url, {"app": "1"} if mobile else {})
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "facturacion/mobile_kardex_list.html" if mobile and empresa == self.empresa else "facturacion/kardex_premium.html")
        self.assertFalse([q["sql"] for q in queries if q["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "REPLACE"))])
        return response, html5lib.parse(response.content.decode(), namespaceHTMLElements=False)

    def fila(self, tree, movimiento, mobile):
        if mobile:
            origen = "lote" if isinstance(movimiento, MovimientoLoteBodega) else "general"
            article = tree.find(f".//article[@id='kardex-{origen}-{movimiento.pk}']")
            self.assertIsNotNone(article)
            return {" ".join(div.find("dt").itertext()): " ".join(div.find("dd").itertext()).strip() for div in article.findall(".//div") if div.find("dt") is not None and div.find("dd") is not None}
        headers = ["".join(th.itertext()).strip() for th in tree.findall(".//table/thead/tr/th")]
        for tr in tree.findall(".//table/tbody/tr"):
            row = dict(zip(headers, [" ".join(td.itertext()).strip() for td in tr.findall("td")]))
            if row.get("Referencia") == movimiento.referencia and row.get("Bodega") == (movimiento.bodega.nombre if movimiento.bodega_id else "General"):
                return row
        self.fail(f"No aparece {movimiento.referencia}")

    def test_desktop_mobile_autor_historico_username_sin_registro_y_snapshots(self):
        with audit_scope(user=self.creador):
            historicos = [self.historico(), self.historico(lote=True)]
        sin_nombre = Usuario.objects.create_user(username="autor-sin-nombre", empresa=self.empresa)
        with audit_scope(user=self.creador):
            historicos.append(self.historico(usuario=sin_nombre, existencia_anterior=Decimal("12"), existencia_resultante=Decimal("19.25")))
        historicos.append(self.historico())
        self.client.force_login(self.lector)
        for mobile in (False, True):
            response, tree = self.vista(mobile)
            for movimiento, autor in zip(historicos, (self.creador.get_full_name(), self.creador.get_full_name(), sin_nombre.username, "Sin registro")):
                self.assertEqual(self.fila(tree, movimiento, mobile)["Usuario"], autor)
                movimiento.refresh_from_db()
                self.assertEqual(movimiento.usuario_id, sin_nombre.pk if movimiento == historicos[2] else None)
            salida = self.fila(tree, historicos[0], mobile)
            entrada = self.fila(tree, historicos[2], mobile)
            self.assertEqual([salida[k] for k in ("Entrada", "Salida", "Antes", "Saldo")], ["0.00", "7.25", "81.75", "74.50"])
            self.assertEqual([entrada[k] for k in ("Entrada", "Salida", "Antes", "Saldo")], ["7.25", "0.00", "12.00", "19.25"])
            self.assertEqual(response.context["resumen"]["total_movimientos"], 4)
        self.saldo(10)

    def test_auditoria_empresa_accion_timestamp_reciclado_y_conflicto_ignorados(self):
        with audit_scope(user=self.creador):
            movimiento = self.historico(lote=True)
        audit = RegistroAuditoria.objects.get(app_label="facturacion", modelo="movimientolotebodega", objeto_id=str(movimiento.pk), accion=RegistroAuditoria.ACCION_CREAR)
        original = copy.deepcopy(audit.cambios)
        reciclado = copy.deepcopy(original)
        reciclado["fecha_creacion"]["nuevo"] = (movimiento.fecha_creacion - timedelta(days=1)).isoformat()
        self.client.force_login(self.lector)
        for cambios in ({"empresa_id": self.otra.pk}, {"accion": RegistroAuditoria.ACCION_MODIFICAR}, {"cambios": reciclado}):
            RegistroAuditoria.objects.filter(pk=audit.pk).update(**cambios)
            for mobile in (False, True):
                _, tree = self.vista(mobile)
                self.assertEqual(self.fila(tree, movimiento, mobile)["Usuario"], "Sin registro")
            RegistroAuditoria.objects.filter(pk=audit.pk).update(empresa=self.empresa, accion=RegistroAuditoria.ACCION_CREAR, cambios=original)
        RegistroAuditoria.objects.create(empresa=self.empresa, usuario=self.actor, accion=RegistroAuditoria.ACCION_CREAR, modulo="facturacion", app_label="facturacion", modelo="movimientolotebodega", objeto_id=str(movimiento.pk), cambios=original)
        for mobile in (False, True):
            _, tree = self.vista(mobile)
            self.assertEqual(self.fila(tree, movimiento, mobile)["Usuario"], "Sin registro")
        movimiento.refresh_from_db()
        self.assertIsNone(movimiento.usuario_id)

    def test_aislamiento_empresa_bodega_y_actor_fk_prevalece(self):
        with audit_scope(user=self.creador):
            propio = self.historico(usuario=self.actor, bodega=self.origen)
        destino = self.historico(lote=True, tipo="traslado_entrada", bodega=self.destino)
        extranjero = self.historico(empresa=self.otra, producto=self.productos[self.otra.pk], referencia="HIST-EMPRESA-EXTERNA")
        self.client.force_login(self.lector)
        for mobile in (False, True):
            response, tree = self.vista(mobile)
            self.assertEqual(self.fila(tree, propio, mobile)["Usuario"], self.actor.get_full_name())
            self.assertEqual(self.fila(tree, propio, mobile)["Bodega"], self.origen.nombre)
            self.assertEqual(self.fila(tree, destino, mobile)["Bodega"], self.destino.nombre)
            self.assertNotContains(response, extranjero.referencia)
            self.assertEqual(response.context["resumen"]["total_movimientos"], 2)
        self.client.force_login(self.actor)
        antes = self.marca()
        existencia = ExistenciaLoteBodega.objects.get(lote=self.lotes[0], bodega=self.origen)
        response = self.post("traslado_inventario_farmaceutico", {"existencia": existencia.pk, "bodega_destino": self.bodegas[self.otra.pk][0].pk, "cantidad": "1"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.marca(), antes)
        existencia.refresh_from_db()
        self.assertEqual(existencia.cantidad, Decimal("3"))
        self.saldo(10)
