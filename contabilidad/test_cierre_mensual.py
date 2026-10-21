"""Regresiones de permisos, cierre mensual y separación entre empresas."""

from datetime import date
from decimal import Decimal

from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.exceptions import PermissionDenied
from django.db.models import QuerySet
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import (
    Empresa,
    Modulo,
    PlanComercial,
    PlanModulo,
    RegistroAuditoria,
    RolSistema,
    Usuario,
)
from facturacion.models import (
    Cliente,
    CompraInventario,
    Factura,
    LineaCompraInventario,
    PagoComisionProveedor,
    PagoCompra,
    PagoFactura,
    Producto,
    Proveedor,
)

from . import views
from .cierre import revisar_cierre_periodo
from .forms import PeriodoContableForm
from .models import (
    AsientoContable,
    CuentaContable,
    CuentaFinanciera,
    LineaAsientoContable,
    MovimientoBancario,
    PeriodoContable,
)


class SeguridadCierreMensualTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        modulo, _ = Modulo.objects.get_or_create(
            codigo="contabilidad",
            defaults={"nombre": "Contabilidad", "es_comercial": True},
        )
        plan = PlanComercial.objects.create(
            nombre="Plan de cierre", codigo="plan-cierre", precio_mensual="199.00"
        )
        PlanModulo.objects.create(plan=plan, modulo=modulo, activo=True)
        cls.empresa = Empresa.objects.create(
            nombre="Empresa del contador",
            slug="empresa-cierre",
            rtn="08011999111111",
            estado_licencia="activa",
            plan_comercial=plan,
        )
        cls.otra_empresa = Empresa.objects.create(
            nombre="Otra empresa independiente",
            slug="otra-empresa-cierre",
            rtn="08011999222222",
            estado_licencia="activa",
            plan_comercial=plan,
        )
        cls.rol_contador = RolSistema.objects.create(
            nombre="Contador de cierre",
            codigo="contador-cierre",
            puede_contabilidad=True,
            puede_crear_asientos=True,
            puede_contabilizar_asientos=True,
        )
        cls.rol_preparador = RolSistema.objects.create(
            nombre="Preparador sin aprobación",
            codigo="preparador-cierre",
            puede_contabilidad=True,
            puede_crear_asientos=True,
        )
        cls.rol_consulta = RolSistema.objects.create(
            nombre="Consulta contable",
            codigo="consulta-cierre",
            puede_contabilidad=True,
        )
        cls.contador = Usuario.objects.create_user(
            username="contador-cierre", empresa=cls.empresa, rol_sistema=cls.rol_contador
        )
        cls.preparador = Usuario.objects.create_user(
            username="preparador-cierre", empresa=cls.empresa, rol_sistema=cls.rol_preparador
        )
        cls.consulta = Usuario.objects.create_user(
            username="consulta-cierre", empresa=cls.empresa, rol_sistema=cls.rol_consulta
        )
        cls.otro_contador = Usuario.objects.create_user(
            username="otro-contador-cierre",
            empresa=cls.otra_empresa,
            rol_sistema=cls.rol_contador,
        )
        cls.cuenta_debe = CuentaContable.objects.create(
            empresa=cls.empresa, codigo="110201", nombre="Banco", tipo="activo"
        )
        cls.cuenta_haber = CuentaContable.objects.create(
            empresa=cls.empresa, codigo="410101", nombre="Ingresos", tipo="ingreso"
        )
        cls.cuenta_otra_debe = CuentaContable.objects.create(
            empresa=cls.otra_empresa, codigo="110201", nombre="Otro banco", tipo="activo"
        )
        cls.cuenta_otra_haber = CuentaContable.objects.create(
            empresa=cls.otra_empresa, codigo="410101", nombre="Otros ingresos", tipo="ingreso"
        )
        cls.cuenta_financiera = CuentaFinanciera.objects.create(
            empresa=cls.empresa,
            nombre="Banco de la empresa",
            cuenta_contable=cls.cuenta_debe,
        )
        cls.cuenta_otra_financiera = CuentaFinanciera.objects.create(
            empresa=cls.otra_empresa,
            nombre="Banco de otra empresa",
            cuenta_contable=cls.cuenta_otra_debe,
        )
        cls.cliente = Cliente.objects.create(empresa=cls.empresa, nombre="Cliente de abril")

    def setUp(self):
        self.client.force_login(self.contador)

    def periodo(self, *, empresa=None, estado="abierto"):
        return PeriodoContable.objects.create(
            empresa=empresa or self.empresa, anio=2026, mes=4, estado=estado
        )

    def asiento(self, *, empresa=None, estado="borrador", debe="100.00", haber="100.00"):
        empresa = empresa or self.empresa
        propio = empresa.pk == self.empresa.pk
        asiento = AsientoContable.objects.create(
            empresa=empresa,
            fecha=date(2026, 4, 15),
            descripcion="Operación de abril",
            estado=estado,
        )
        LineaAsientoContable.objects.create(
            asiento=asiento,
            cuenta=self.cuenta_debe if propio else self.cuenta_otra_debe,
            debe=Decimal(debe),
        )
        LineaAsientoContable.objects.create(
            asiento=asiento,
            cuenta=self.cuenta_haber if propio else self.cuenta_otra_haber,
            haber=Decimal(haber),
        )
        return asiento

    def movimiento(self, *, empresa=None, conciliado=False):
        empresa = empresa or self.empresa
        propio = empresa.pk == self.empresa.pk
        return MovimientoBancario.objects.create(
            empresa=empresa,
            cuenta_financiera=self.cuenta_financiera if propio else self.cuenta_otra_financiera,
            fecha=date(2026, 4, 15),
            descripcion="Depósito de abril",
            credito=Decimal("100.00"),
            estado="contabilizado",
            asiento=self.asiento(empresa=empresa, estado="contabilizado"),
            conciliado=conciliado,
            conciliado_por=self.contador if conciliado else None,
            fecha_conciliacion=timezone.now() if conciliado else None,
        )

    def datos_asiento(self, *, asiento=None, **cambios):
        datos = {
            "fecha": "2026-04-15",
            "descripcion": "Descripción actualizada",
            "referencia": "REF-ABRIL",
            "origen_modulo": "manual",
            "estado": "borrador",
            "lineas-TOTAL_FORMS": "2",
            "lineas-INITIAL_FORMS": "2" if asiento else "0",
            "lineas-MIN_NUM_FORMS": "0",
            "lineas-MAX_NUM_FORMS": "1000",
            "lineas-0-cuenta": str(self.cuenta_debe.pk),
            "lineas-0-detalle": "Débito",
            "lineas-0-debe": "100.00",
            "lineas-0-haber": "0.00",
            "lineas-1-cuenta": str(self.cuenta_haber.pk),
            "lineas-1-detalle": "Crédito",
            "lineas-1-debe": "0.00",
            "lineas-1-haber": "100.00",
        }
        if asiento:
            for i, linea in enumerate(asiento.lineas.order_by("id")):
                datos[f"lineas-{i}-id"] = str(linea.pk)
        datos.update(cambios)
        return datos

    def ruta(self, nombre, objeto=None, *, empresa=None):
        args = [(empresa or self.empresa).slug]
        if objeto:
            args.append(objeto.pk)
        return reverse(nombre, args=args)

    def verificar_peticion_directa_denegada(self, vista, usuario, datos=None, **kwargs):
        """Comprueba la defensa de la vista sin depender del middleware de rutas."""
        request = RequestFactory().post("/contabilidad/prueba/", datos or {})
        request.user = usuario
        request.session = {}
        request._messages = FallbackStorage(request)
        empresa_slug = kwargs.pop("empresa_slug", self.empresa.slug)
        # Django convierte esta excepción en 403; RequestFactory omite ese manejador.
        with self.assertRaises(PermissionDenied):
            vista(request, empresa_slug=empresa_slug, **kwargs)

    def test_preparador_puede_guardar_borrador(self):
        self.client.force_login(self.preparador)
        respuesta = self.client.post(self.ruta("crear_asiento_contable"), self.datos_asiento())
        self.assertEqual(respuesta.status_code, 302)
        asiento = AsientoContable.objects.get(empresa=self.empresa)
        self.assertEqual(asiento.estado, "borrador")
        self.assertEqual(asiento.lineas.count(), 2)

    def test_creacion_no_contabiliza_sin_permiso_por_estado_ni_casilla(self):
        self.client.force_login(self.preparador)
        for cambios in ({"estado": "contabilizado"}, {"contabilizar_ahora": "on"}):
            with self.subTest(cambios=cambios):
                respuesta = self.client.post(
                    self.ruta("crear_asiento_contable"), self.datos_asiento(**cambios)
                )
                self.assertEqual(respuesta.status_code, 403)
                self.verificar_peticion_directa_denegada(
                    views.crear_asiento_contable,
                    self.preparador,
                    self.datos_asiento(**cambios),
                )
                self.assertFalse(AsientoContable.objects.filter(empresa=self.empresa).exists())
                self.assertFalse(LineaAsientoContable.objects.exists())

    def test_edicion_no_contabiliza_sin_permiso_ni_modifica_borrador(self):
        asiento = self.asiento()
        self.client.force_login(self.preparador)
        for cambios in ({"estado": "contabilizado"}, {"contabilizar_ahora": "on"}):
            with self.subTest(cambios=cambios):
                respuesta = self.client.post(
                    self.ruta("editar_asiento_contable", asiento),
                    self.datos_asiento(asiento=asiento, **cambios),
                )
                self.assertEqual(respuesta.status_code, 403)
                self.verificar_peticion_directa_denegada(
                    views.editar_asiento_contable,
                    self.preparador,
                    self.datos_asiento(asiento=asiento, **cambios),
                    asiento_id=asiento.pk,
                )
                asiento.refresh_from_db()
                self.assertEqual(asiento.descripcion, "Operación de abril")
                self.assertEqual(asiento.estado, "borrador")

    def test_consulta_no_edita_borrador_por_ruta_ni_por_vista(self):
        asiento = self.asiento()
        datos = self.datos_asiento(asiento=asiento)
        self.client.force_login(self.consulta)
        respuesta = self.client.post(self.ruta("editar_asiento_contable", asiento), datos)
        self.assertRedirects(respuesta, reverse("dashboard", args=[self.empresa.slug]), fetch_redirect_response=False)
        self.verificar_peticion_directa_denegada(
            views.editar_asiento_contable, self.consulta, datos, asiento_id=asiento.pk
        )
        asiento.refresh_from_db()
        self.assertEqual(asiento.descripcion, "Operación de abril")
        self.assertEqual(asiento.referencia, None)

    def test_preparador_puede_editar_borrador_en_mes_abierto(self):
        asiento = self.asiento()
        self.client.force_login(self.preparador)
        respuesta = self.client.post(
            self.ruta("editar_asiento_contable", asiento), self.datos_asiento(asiento=asiento)
        )
        self.assertEqual(respuesta.status_code, 302)
        asiento.refresh_from_db()
        self.assertEqual(asiento.descripcion, "Descripción actualizada")
        self.assertEqual(asiento.estado, "borrador")
        self.assertEqual(asiento.lineas.count(), 2)

    def test_edicion_no_saca_borrador_de_mes_cerrado_cambiando_la_fecha(self):
        asiento = self.asiento()
        self.periodo(estado="cerrado")
        respuesta = self.client.post(
            self.ruta("editar_asiento_contable", asiento),
            self.datos_asiento(asiento=asiento, fecha="2026-05-15"),
        )
        self.assertIn(respuesta.status_code, (200, 302))
        asiento.refresh_from_db()
        self.assertEqual(asiento.fecha, date(2026, 4, 15))
        self.assertEqual(asiento.descripcion, "Operación de abril")
        self.assertEqual(asiento.total_debe, Decimal("100.00"))

    def test_consulta_no_duplica_y_preparador_no_reversa_regresa_ni_contabiliza(self):
        asiento = self.asiento(estado="contabilizado")
        borrador = self.asiento()
        casos = (
            ("duplicar_asiento_contable", self.consulta, asiento),
            ("reversar_asiento_contable", self.preparador, asiento),
            ("regresar_asiento_borrador", self.preparador, asiento),
            ("contabilizar_asiento_contable", self.preparador, borrador),
        )
        for nombre, usuario, objetivo in casos:
            with self.subTest(accion=nombre):
                self.client.force_login(usuario)
                respuesta = self.client.post(self.ruta(nombre, objetivo))
                self.assertRedirects(respuesta, reverse("dashboard", args=[self.empresa.slug]), fetch_redirect_response=False)
                self.verificar_peticion_directa_denegada(
                    getattr(views, nombre), usuario, asiento_id=objetivo.pk
                )
                asiento.refresh_from_db()
                borrador.refresh_from_db()
                self.assertEqual(asiento.estado, "contabilizado")
                self.assertEqual(borrador.estado, "borrador")
                self.assertEqual(AsientoContable.objects.count(), 2)

    def test_crear_periodo_lo_guarda_abierto(self):
        respuesta = self.client.post(
            self.ruta("crear_periodo_contable"),
            {"anio": "2026", "mes": "4", "observacion": "Abril"},
        )
        self.assertEqual(respuesta.status_code, 302)
        periodo = PeriodoContable.objects.get(empresa=self.empresa, anio=2026, mes=4)
        self.assertEqual(periodo.estado, "abierto")
        self.assertIsNone(periodo.cerrado_por)
        self.assertIsNone(periodo.fecha_cierre)

    def test_crear_periodo_cerrado_se_rechaza_en_formulario_y_ruta(self):
        datos = {"anio": "2026", "mes": "4", "estado": "cerrado", "observacion": "Atajo"}
        form = PeriodoContableForm(datos, empresa=self.empresa)
        self.assertFalse(form.is_valid())
        respuesta = self.client.post(self.ruta("crear_periodo_contable"), datos)
        self.assertEqual(respuesta.status_code, 200)
        self.assertFalse(PeriodoContable.objects.filter(empresa=self.empresa).exists())

    def test_editar_periodo_no_cambia_estado_fechas_ni_empresa(self):
        periodo = self.periodo()
        respuesta = self.client.post(
            self.ruta("editar_periodo_contable", periodo),
            {
                "anio": "2027", "mes": "7", "estado": "cerrado",
                "empresa": self.otra_empresa.pk, "observacion": "Intento de traslado",
            },
        )
        self.assertIn(respuesta.status_code, (200, 302))
        periodo.refresh_from_db()
        self.assertEqual((periodo.anio, periodo.mes, periodo.estado), (2026, 4, "abierto"))
        self.assertEqual(periodo.empresa, self.empresa)
        self.assertIsNone(periodo.fecha_cierre)

    def test_editar_periodo_cerrado_no_lo_reabre_ni_borra_evidencia(self):
        periodo = self.periodo()
        self.client.post(self.ruta("cerrar_periodo_contable", periodo))
        periodo.refresh_from_db()
        fecha_original = periodo.fecha_cierre
        self.client.post(
            self.ruta("editar_periodo_contable", periodo),
            {"anio": "2026", "mes": "4", "estado": "abierto", "observacion": "Atajo"},
        )
        periodo.refresh_from_db()
        self.assertEqual(periodo.estado, "cerrado")
        self.assertEqual(periodo.cerrado_por, self.contador)
        self.assertEqual(periodo.fecha_cierre, fecha_original)

    def test_cierre_formal_rechaza_asientos_borrador(self):
        periodo = self.periodo()
        self.asiento()
        self.client.post(self.ruta("cerrar_periodo_contable", periodo))
        periodo.refresh_from_db()
        self.assertEqual(periodo.estado, "abierto")
        self.assertIsNone(periodo.fecha_cierre)

    def test_cierre_rechaza_desbalances_individuales_que_se_compensan(self):
        periodo = self.periodo()
        self.asiento(estado="contabilizado", debe="100.00", haber="90.00")
        self.asiento(estado="contabilizado", debe="90.00", haber="100.00")
        self.client.post(self.ruta("cerrar_periodo_contable", periodo))
        periodo.refresh_from_db()
        self.assertEqual(periodo.estado, "abierto")
        self.assertIsNone(periodo.cerrado_por)

    def test_cierre_rechaza_asiento_contabilizado_sin_lineas(self):
        periodo = self.periodo()
        AsientoContable.objects.create(
            empresa=self.empresa, fecha=date(2026, 4, 15),
            descripcion="Asiento sin detalle", estado="contabilizado",
        )
        self.client.post(self.ruta("cerrar_periodo_contable", periodo))
        periodo.refresh_from_db()
        self.assertEqual(periodo.estado, "abierto")

    def test_cierre_rechaza_lineas_con_cuentas_de_otra_empresa(self):
        periodo = self.periodo()
        asiento = self.asiento(estado="contabilizado")
        # Simula corrupción histórica previa; el formulario normal ya impide esta cuenta.
        QuerySet.update(asiento.lineas.filter(debe__gt=0), cuenta_id=self.cuenta_otra_debe.pk)
        self.client.post(self.ruta("cerrar_periodo_contable", periodo))
        periodo.refresh_from_db()
        self.assertEqual(periodo.estado, "abierto")

    def test_cierre_correcto_registra_responsable_y_fecha_sin_reescribirlos(self):
        periodo = self.periodo()
        self.asiento(estado="contabilizado")
        self.client.post(self.ruta("cerrar_periodo_contable", periodo))
        periodo.refresh_from_db()
        fecha_original = periodo.fecha_cierre
        self.assertEqual(periodo.estado, "cerrado")
        self.assertEqual(periodo.cerrado_por, self.contador)
        self.assertIsNotNone(fecha_original)
        self.client.force_login(Usuario.objects.create_user(
            username="segundo-contador-cierre", empresa=self.empresa, rol_sistema=self.rol_contador
        ))
        self.client.post(self.ruta("cerrar_periodo_contable", periodo))
        periodo.refresh_from_db()
        self.assertEqual(periodo.fecha_cierre, fecha_original)
        self.assertEqual(periodo.cerrado_por, self.contador)

    def test_cerrar_y_reabrir_requieren_permiso_de_contabilizar_en_vista(self):
        periodo = self.periodo()
        for vista in (views.cerrar_periodo_contable, views.abrir_periodo_contable):
            with self.subTest(accion=vista.__name__):
                self.verificar_peticion_directa_denegada(
                    vista, self.preparador, {"motivo_reapertura": "Revisión"}, periodo_id=periodo.pk
                )
                periodo.refresh_from_db()
                self.assertEqual(periodo.estado, "abierto")

    def test_reapertura_requiere_motivo_y_conserva_cierre_si_falta(self):
        periodo = self.periodo()
        self.client.post(self.ruta("cerrar_periodo_contable", periodo))
        periodo.refresh_from_db()
        fecha_original = periodo.fecha_cierre
        for datos in ({}, {"motivo_reapertura": "  "}):
            with self.subTest(datos=datos):
                self.client.post(self.ruta("abrir_periodo_contable", periodo), datos)
                periodo.refresh_from_db()
                self.assertEqual(periodo.estado, "cerrado")
                self.assertEqual(periodo.fecha_cierre, fecha_original)
                self.assertEqual(periodo.cerrado_por, self.contador)

    def test_reapertura_deja_motivo_usuario_y_cierre_anterior_en_auditoria(self):
        periodo = self.periodo()
        self.client.post(self.ruta("cerrar_periodo_contable", periodo))
        motivo = "Corregir clasificación de gasto respaldada por autorización"
        self.client.post(self.ruta("abrir_periodo_contable", periodo), {"motivo_reapertura": motivo})
        periodo.refresh_from_db()
        self.assertEqual(periodo.estado, "abierto")
        auditoria = RegistroAuditoria.objects.filter(
            empresa=self.empresa,
            usuario=self.contador,
            app_label="contabilidad",
            modelo="periodocontable",
            objeto_id=str(periodo.pk),
            motivo__contains=motivo,
        ).first()
        self.assertIsNotNone(auditoria)
        self.assertEqual(auditoria.cambios["estado"], {"anterior": "cerrado", "nuevo": "abierto"})
        self.assertEqual(auditoria.metodo_http, "POST")
        historial = RegistroAuditoria.objects.filter(
            empresa=self.empresa, app_label="contabilidad", modelo="periodocontable", objeto_id=str(periodo.pk)
        )
        self.assertTrue(any(
            registro.cambios.get("estado") == {"anterior": "abierto", "nuevo": "cerrado"}
            and registro.cambios.get("fecha_cierre", {}).get("nuevo")
            for registro in historial
        ))

    def test_empresa_ajena_no_cierra_ni_edita_periodo_mediante_slug_propio(self):
        periodo = self.periodo(empresa=self.otra_empresa)
        for nombre in ("cerrar_periodo_contable", "abrir_periodo_contable", "editar_periodo_contable"):
            with self.subTest(accion=nombre):
                respuesta = self.client.post(
                    self.ruta(nombre, periodo), {"anio": "2026", "mes": "4", "motivo_reapertura": "No autorizado"}
                )
                self.assertEqual(respuesta.status_code, 404)
                periodo.refresh_from_db()
                self.assertEqual(periodo.estado, "abierto")
                self.assertEqual(periodo.empresa, self.otra_empresa)

    def test_empresa_ajena_no_modifica_asientos_mediante_slug_propio(self):
        asiento = self.asiento(empresa=self.otra_empresa, estado="contabilizado")
        for nombre in (
            "editar_asiento_contable", "duplicar_asiento_contable", "reversar_asiento_contable",
            "regresar_asiento_borrador", "contabilizar_asiento_contable",
        ):
            with self.subTest(accion=nombre):
                respuesta = self.client.post(self.ruta(nombre, asiento), self.datos_asiento())
                self.assertEqual(respuesta.status_code, 404)
                asiento.refresh_from_db()
                self.assertEqual(asiento.estado, "contabilizado")
                self.assertEqual(AsientoContable.objects.count(), 1)

    def test_empresa_ajena_no_concilia_mediante_slug_propio(self):
        movimiento = self.movimiento(empresa=self.otra_empresa)
        for nombre in ("conciliar_movimiento_bancario", "desconciliar_movimiento_bancario"):
            with self.subTest(accion=nombre):
                respuesta = self.client.post(self.ruta(nombre, movimiento))
                self.assertEqual(respuesta.status_code, 404)
                movimiento.refresh_from_db()
                self.assertFalse(movimiento.conciliado)

    def test_usuario_no_accede_por_slug_de_empresa_ajena(self):
        periodo = self.periodo(empresa=self.otra_empresa)
        respuesta = self.client.post(self.ruta("cerrar_periodo_contable", periodo, empresa=self.otra_empresa))
        self.assertRedirects(respuesta, reverse("empresa_login", args=[self.otra_empresa.slug]), fetch_redirect_response=False)
        self.verificar_peticion_directa_denegada(
            views.cerrar_periodo_contable,
            self.contador,
            empresa_slug=self.otra_empresa.slug,
            periodo_id=periodo.pk,
        )
        periodo.refresh_from_db()
        self.assertEqual(periodo.estado, "abierto")

    def test_pendientes_de_otra_empresa_no_impiden_cierre_propio(self):
        periodo = self.periodo()
        self.asiento(empresa=self.otra_empresa)
        self.client.post(self.ruta("cerrar_periodo_contable", periodo))
        periodo.refresh_from_db()
        self.assertEqual(periodo.estado, "cerrado")

    def test_cierre_propio_no_bloquea_mismo_mes_en_otra_empresa(self):
        periodo = self.periodo()
        otro_periodo = self.periodo(empresa=self.otra_empresa)
        self.client.post(self.ruta("cerrar_periodo_contable", periodo))
        self.assertTrue(PeriodoContable.fecha_bloqueada(self.empresa, date(2026, 4, 15)))
        self.assertFalse(PeriodoContable.fecha_bloqueada(self.otra_empresa, date(2026, 4, 15)))
        asiento = self.asiento(empresa=self.otra_empresa)
        self.client.force_login(self.otro_contador)
        respuesta = self.client.post(self.ruta("contabilizar_asiento_contable", asiento, empresa=self.otra_empresa))
        self.assertEqual(respuesta.status_code, 302)
        asiento.refresh_from_db()
        otro_periodo.refresh_from_db()
        self.assertEqual(asiento.estado, "contabilizado")
        self.assertEqual(otro_periodo.estado, "abierto")

    def test_cierre_no_permite_conciliar_ni_desconciliar_movimiento_del_mes(self):
        movimiento_pendiente = self.movimiento()
        movimiento_conciliado = self.movimiento(conciliado=True)
        self.periodo(estado="cerrado")
        fecha_original = movimiento_conciliado.fecha_conciliacion
        self.client.post(self.ruta("conciliar_movimiento_bancario", movimiento_pendiente))
        self.client.post(self.ruta("desconciliar_movimiento_bancario", movimiento_conciliado))
        movimiento_pendiente.refresh_from_db()
        movimiento_conciliado.refresh_from_db()
        self.assertFalse(movimiento_pendiente.conciliado)
        self.assertTrue(movimiento_conciliado.conciliado)
        self.assertEqual(movimiento_conciliado.fecha_conciliacion, fecha_original)
        self.assertEqual(movimiento_conciliado.conciliado_por, self.contador)

    def test_cerrar_y_reabrir_requieren_post(self):
        periodo = self.periodo()
        for nombre in ("cerrar_periodo_contable", "abrir_periodo_contable"):
            with self.subTest(accion=nombre):
                respuesta = self.client.get(self.ruta(nombre, periodo))
                self.assertEqual(respuesta.status_code, 405)
                periodo.refresh_from_db()
                self.assertEqual(periodo.estado, "abierto")

    def factura_emitida_sin_asiento(self, *, empresa=None, fecha=None):
        empresa = empresa or self.empresa
        cliente = self.cliente if empresa.pk == self.empresa.pk else Cliente.objects.get_or_create(
            empresa=empresa, nombre="Cliente de otra empresa"
        )[0]
        factura = Factura.objects.create(
            empresa=empresa,
            cliente=cliente,
            vendedor=self.contador if empresa.pk == self.empresa.pk else self.otro_contador,
            fecha_emision=fecha or date(2026, 4, 15),
            moneda="HNL",
            estado="borrador",
        )
        # Reproduce un documento histórico cuyo proceso de contabilización no terminó.
        Factura.objects.filter(pk=factura.pk).update(
            estado="emitida", subtotal=Decimal("100.00"), total=Decimal("100.00"), total_lempiras=Decimal("100.00")
        )
        factura.refresh_from_db()
        return factura

    def test_cierre_bloquea_factura_emitida_sin_asiento(self):
        periodo = self.periodo()
        self.factura_emitida_sin_asiento()
        self.client.post(self.ruta("cerrar_periodo_contable", periodo))
        periodo.refresh_from_db()
        self.assertEqual(periodo.estado, "abierto")
        self.assertIsNone(periodo.fecha_cierre)

    def test_asiento_de_otra_empresa_no_resuelve_factura_sin_contabilizar(self):
        periodo = self.periodo()
        factura = self.factura_emitida_sin_asiento()
        ajeno = self.asiento(empresa=self.otra_empresa, estado="contabilizado")
        ajeno.documento_tipo = "factura"
        ajeno.documento_id = factura.pk
        ajeno.evento = "emision"
        ajeno.save(update_fields=["documento_tipo", "documento_id", "evento"])
        self.client.post(self.ruta("cerrar_periodo_contable", periodo))
        periodo.refresh_from_db()
        self.assertEqual(periodo.estado, "abierto")

    def test_factura_con_asiento_propio_correcto_permite_cierre(self):
        periodo = self.periodo()
        factura = self.factura_emitida_sin_asiento()
        asiento = self.asiento(estado="contabilizado")
        asiento.documento_tipo = "factura"
        asiento.documento_id = factura.pk
        asiento.evento = "emision"
        asiento.save(update_fields=["documento_tipo", "documento_id", "evento"])
        self.client.post(self.ruta("cerrar_periodo_contable", periodo))
        periodo.refresh_from_db()
        self.assertEqual(periodo.estado, "cerrado")

    def movimiento_pendiente(self, *, empresa=None, fecha=None, ingreso=True, descripcion="Movimiento pendiente"):
        empresa = empresa or self.empresa
        return MovimientoBancario.objects.create(
            empresa=empresa,
            cuenta_financiera=self.cuenta_financiera if empresa.pk == self.empresa.pk else self.cuenta_otra_financiera,
            fecha=fecha or date(2026, 4, 15),
            descripcion=descripcion,
            credito=Decimal("100.00") if ingreso else Decimal("0.00"),
            debito=Decimal("0.00") if ingreso else Decimal("100.00"),
        )

    def asiento_documento(self, documento, tipo, evento, *, empresa=None, monto="100.00"):
        asiento = self.asiento(empresa=empresa, estado="contabilizado", debe=monto, haber=monto)
        asiento.documento_tipo = tipo
        asiento.documento_id = documento.pk
        asiento.evento = evento
        asiento.save(update_fields=["documento_tipo", "documento_id", "evento"])
        return asiento

    def compra_aplicada_sin_pago(self):
        proveedor = Proveedor.objects.create(empresa=self.empresa, nombre="Proveedor de prueba")
        compra = CompraInventario.objects.create(
            empresa=self.empresa,
            proveedor=proveedor,
            proveedor_nombre=proveedor.nombre,
            fecha_documento=date(2026, 4, 15),
            condicion_pago="credito",
            estado="borrador",
        )
        producto = Producto.objects.create(
            empresa=self.empresa,
            nombre="Artículo de prueba",
            precio=Decimal("100.00"),
            controla_inventario=False,
        )
        LineaCompraInventario.objects.create(
            compra=compra, producto=producto, cantidad=Decimal("1.00"), costo_unitario=Decimal("100.00")
        )
        CompraInventario.objects.filter(pk=compra.pk).update(estado="aplicada")
        compra.refresh_from_db()
        return compra

    def test_filtros_bancarios_conservan_fechas_y_excluyen_otra_empresa(self):
        self.movimiento_pendiente(fecha=date(2026, 3, 31), descripcion="Fuera del mes anterior")
        inicial = self.movimiento_pendiente(fecha=date(2026, 4, 1), descripcion="Inicio del mes")
        final = self.movimiento_pendiente(fecha=date(2026, 4, 30), descripcion="Fin del mes")
        self.movimiento_pendiente(fecha=date(2026, 5, 1), descripcion="Fuera del mes siguiente")
        self.movimiento_pendiente(empresa=self.otra_empresa, descripcion="Movimiento ajeno oculto")
        respuesta = self.client.get(
            self.ruta("movimientos_bancarios"), {"desde": "2026-04-01", "hasta": "2026-04-30"}
        )
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.context["filtros"]["desde"], "2026-04-01")
        self.assertEqual(respuesta.context["filtros"]["hasta"], "2026-04-30")
        self.assertEqual(set(respuesta.context["movimientos"].values_list("pk", flat=True)), {inicial.pk, final.pk})
        self.assertContains(respuesta, 'name="desde" value="2026-04-01"')
        self.assertContains(respuesta, 'name="hasta" value="2026-04-30"')
        self.assertNotContains(respuesta, "Movimiento ajeno oculto")

    def test_filtro_bancario_fecha_invalida_muestra_error_sin_listar_movimientos(self):
        self.movimiento_pendiente()
        respuesta = self.client.get(self.ruta("movimientos_bancarios"), {"desde": "2026-02-31"})
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.context["movimientos"].count(), 0)
        self.assertContains(respuesta, "Indica fechas válidas")

    def test_enlaces_bancarios_rechazan_preparador_sin_permiso_defensivamente(self):
        deposito = self.movimiento_pendiente()
        debito = self.movimiento_pendiente(ingreso=False)
        factura = self.factura_emitida_sin_asiento()
        compra = self.compra_aplicada_sin_pago()
        casos = (
            (views.enlazar_movimiento_factura, deposito, {"factura": factura.pk}),
            (views.enlazar_movimiento_compra, debito, {"compra": compra.pk}),
        )
        for vista, movimiento, datos in casos:
            with self.subTest(accion=vista.__name__):
                self.verificar_peticion_directa_denegada(
                    vista, self.preparador, datos, movimiento_id=movimiento.pk
                )
                movimiento.refresh_from_db()
                self.assertIsNone(movimiento.asiento_id)
        self.assertFalse(PagoFactura.objects.exists())
        self.assertFalse(PagoCompra.objects.exists())
        self.assertFalse(AsientoContable.objects.exists())

    def test_enlazar_deposito_cerrado_no_crea_pago_y_responde_con_error_claro(self):
        factura = self.factura_emitida_sin_asiento()
        deposito = self.movimiento_pendiente()
        self.periodo(estado="cerrado")
        respuesta = self.client.post(
            self.ruta("enlazar_movimiento_factura", deposito), {"factura": factura.pk}, follow=True
        )
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "No se pudo completar la operación")
        self.assertContains(respuesta, "período contable está cerrado")
        self.assertFalse(PagoFactura.objects.exists())
        self.assertFalse(AsientoContable.objects.exists())
        deposito.refresh_from_db()
        self.assertIsNone(deposito.pago_factura_id)
        self.assertIsNone(deposito.asiento_id)
        self.assertEqual(deposito.estado, "pendiente")

    def test_enlazar_compra_cerrada_no_crea_pago_y_responde_con_error_claro(self):
        compra = self.compra_aplicada_sin_pago()
        debito = self.movimiento_pendiente(ingreso=False)
        self.periodo(estado="cerrado")
        respuesta = self.client.post(
            self.ruta("enlazar_movimiento_compra", debito), {"compra": compra.pk}, follow=True
        )
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "No se pudo completar la operación")
        self.assertContains(respuesta, "período contable está cerrado")
        self.assertFalse(PagoCompra.objects.exists())
        self.assertFalse(AsientoContable.objects.exists())
        debito.refresh_from_db()
        self.assertIsNone(debito.pago_compra_id)
        self.assertIsNone(debito.asiento_id)
        self.assertEqual(debito.estado, "pendiente")

    def test_cierre_detecta_cobro_con_solo_retencion_isr_o_isv_y_monto_cero(self):
        periodo = self.periodo()
        pagos = []
        for campo in ("retencion_isr", "retencion_isv"):
            with self.subTest(retencion=campo):
                factura = self.factura_emitida_sin_asiento()
                if campo == "retencion_isv":
                    Factura.objects.filter(pk=factura.pk).update(subtotal=Decimal("0.00"), impuesto=Decimal("100.00"))
                    factura.refresh_from_db()
                self.asiento_documento(factura, "factura", "emision")
                pago = PagoFactura.objects.create(
                    factura=factura, fecha=date(2026, 4, 15), monto=Decimal("0.00"), **{campo: Decimal("10.00")}
                )
                pagos.append(pago)
                revision = revisar_cierre_periodo(self.empresa, periodo)
                self.assertTrue(any(
                    documento["tipo"] == "Cobro" and documento["referencia"] == str(factura.pk)
                    for documento in revision["documentos_sin_asiento"]
                ))
        self.client.post(self.ruta("cerrar_periodo_contable", periodo))
        periodo.refresh_from_db()
        self.assertEqual(periodo.estado, "abierto")
        for pago in pagos:
            self.asiento_documento(pago, "pago_factura", "cobro", monto="10.00")
        self.client.post(self.ruta("cerrar_periodo_contable", periodo))
        periodo.refresh_from_db()
        self.assertEqual(periodo.estado, "cerrado")

    def pago_comision_sin_asiento(self, *, empresa=None, fecha=None):
        empresa = empresa or self.empresa
        factura = self.factura_emitida_sin_asiento(empresa=empresa)
        cuenta = self.cuenta_financiera if empresa.pk == self.empresa.pk else self.cuenta_otra_financiera
        proveedor = Proveedor.objects.create(empresa=empresa, nombre="Proveedor de comisión")
        cobro = PagoFactura.objects.create(
            factura=factura,
            fecha=date(2026, 4, 15),
            monto=Decimal("100.00"),
            proveedor_comision=proveedor,
            monto_comision=Decimal("10.00"),
        )
        self.asiento_documento(factura, "factura", "emision", empresa=empresa)
        self.asiento_documento(cobro, "pago_factura", "cobro", empresa=empresa)
        return PagoComisionProveedor.objects.create(
            comision_origen=cobro,
            fecha=fecha or date(2026, 4, 15),
            monto=Decimal("10.00"),
            cuenta_financiera=cuenta,
        )

    def test_cierre_detecta_pago_comision_proveedor_sin_asiento_y_enlace_correcto(self):
        periodo = self.periodo()
        pago = self.pago_comision_sin_asiento()
        revision = revisar_cierre_periodo(self.empresa, periodo)
        self.assertEqual(revision["control"]["documentos_sin_asiento"], 1)
        documento = revision["documentos_sin_asiento"][0]
        self.assertEqual(documento["tipo"], "Pago de comisión")
        self.assertEqual(documento["url"], reverse("ver_factura", args=[self.empresa.slug, pago.comision_origen.factura_id]))
        self.client.post(self.ruta("cerrar_periodo_contable", periodo))
        periodo.refresh_from_db()
        self.assertEqual(periodo.estado, "abierto")
        self.asiento_documento(pago, "pago_comision_proveedor", "egreso", monto="10.00")
        self.client.post(self.ruta("cerrar_periodo_contable", periodo))
        periodo.refresh_from_db()
        self.assertEqual(periodo.estado, "cerrado")

    def test_retenciones_y_comisiones_fuera_de_fecha_o_empresa_no_bloquean_cierre(self):
        periodo = self.periodo()
        factura = self.factura_emitida_sin_asiento()
        self.asiento_documento(factura, "factura", "emision")
        PagoFactura.objects.create(
            factura=factura, fecha=date(2026, 5, 15), monto=Decimal("0.00"), retencion_isr=Decimal("10.00")
        )
        factura_ajena = self.factura_emitida_sin_asiento(empresa=self.otra_empresa)
        PagoFactura.objects.create(
            factura=factura_ajena, fecha=date(2026, 4, 15), monto=Decimal("0.00"), retencion_isr=Decimal("10.00")
        )
        self.pago_comision_sin_asiento(fecha=date(2026, 5, 15))
        self.pago_comision_sin_asiento(empresa=self.otra_empresa)
        revision = revisar_cierre_periodo(self.empresa, periodo)
        self.assertEqual(revision["control"]["documentos_sin_asiento"], 0)
        self.client.post(self.ruta("cerrar_periodo_contable", periodo))
        periodo.refresh_from_db()
        self.assertEqual(periodo.estado, "cerrado")
