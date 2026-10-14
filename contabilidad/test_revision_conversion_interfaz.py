from datetime import date
from decimal import Decimal
from io import StringIO

from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.urls import reverse

from core.models import RolSistema, Usuario
from facturacion.models import Cliente, Factura
from . import test_regularizacion_moneda as pruebas_legado
from .historicos_usd import celda_csv_segura
from .models import AsientoContable, CuentaContable, PeriodoContable, RegularizacionMonedaFactura, RevisionConversionAsiento
from .revision_conversion_forms import PartidasRevisionFormSet
from .revision_conversion_views import historicos_usd, exportar_historicos_usd_csv, revisar_conversion_asiento


class SeguridadExportacionHistoricosTests(SimpleTestCase):
    def test_texto_de_documento_no_se_exporta_como_formula(self):
        self.assertEqual(celda_csv_segura("=SUM(A1:A2)"), "'=SUM(A1:A2)")
        self.assertEqual(celda_csv_segura("  @documento"), "'  @documento")
        self.assertEqual(celda_csv_segura(Decimal("-2.50")), "-2.50")


class RevisionConversionInterfazTests(TestCase):
    def setUp(self):
        pruebas_legado.RegularizacionMonedaInterfazTests.setUp(self)
        self.url_bandeja = reverse("historicos_usd", args=[self.empresa.slug])
        self.url_revision = reverse("revisar_conversion_asiento", args=[self.empresa.slug, self.asiento.pk])
        self.url_csv = reverse("exportar_historicos_usd_csv", args=[self.empresa.slug])

    def datos_partidas(self, **cambios):
        lineas = list(self.asiento.lineas.order_by("pk"))
        datos = {"fecha": "2026-05-05", "motivo": "Facturas y comprobantes respaldan importes originales y tasa histórica",
            "accion": "revisar", "partidas-TOTAL_FORMS": str(len(lineas)), "partidas-INITIAL_FORMS": str(len(lineas)),
            "partidas-MIN_NUM_FORMS": "0", "partidas-MAX_NUM_FORMS": "500"}
        for indice, linea in enumerate(lineas):
            datos.update({f"partidas-{indice}-linea_id": str(linea.pk), f"partidas-{indice}-moneda": "USD",
                f"partidas-{indice}-importe_original": str(linea.debe + linea.haber), f"partidas-{indice}-tipo_cambio": "25"})
        datos.update(cambios)
        return datos

    def preparar(self, **cambios):
        response = self.client.post(self.url_revision, self.datos_partidas(**cambios))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["lista_para_confirmar"], response.context["form"].errors)
        return response.context["revision_token"]

    def test_consultas_y_csv_no_modifican_datos_y_aislan_empresa(self):
        AsientoContable.objects.create(empresa=self.otra, fecha=date(2026, 4, 1), estado="contabilizado",
            descripcion="Fuente privada de otra empresa", conversion_verificada=False)
        originales = list(self.asiento.lineas.values("pk", "cuenta_id", "debe", "haber"))
        response = self.client.get(self.url_bandeja)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Revisión de registros históricos en dólares")
        self.assertNotContains(response, "Fuente privada de otra empresa")
        self.assertEqual(response.context["resumen"]["pendiente"], 1)
        csv = self.client.get(self.url_csv)
        self.assertEqual(csv.status_code, 200)
        self.assertIn(self.factura.numero_factura, csv.content.decode("utf-8-sig"))
        self.assertNotIn("otra-revision-moneda", csv.content.decode("utf-8-sig"))
        self.assertEqual(list(self.asiento.lineas.values("pk", "cuenta_id", "debe", "haber")), originales)
        self.assertFalse(RevisionConversionAsiento.objects.exists())

    def test_filtros_invalidos_no_producen_error_del_servidor(self):
        response = self.client.get(self.url_bandeja, {"desde": "fecha inválida"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors)
        self.assertEqual(self.client.get(self.url_csv, {"desde": "2026-05-05", "hasta": "2026-01-01"}).status_code, 400)

    def test_bandeja_pagina_asientos_sin_cargar_todos(self):
        for indice in range(35):
            AsientoContable.objects.create(empresa=self.empresa, fecha=date(2026, 4, 1), estado="contabilizado",
                descripcion=f"Respaldo sin fuente {indice}", conversion_verificada=False)
        response = self.client.get(self.url_bandeja, {"estado": "inconsistente"})
        self.assertEqual(len(response.context["filas"]), 30)
        self.assertEqual(response.context["pagina"].paginator.count, 35)

    def test_vistas_exigen_permisos_aunque_se_omita_middleware(self):
        rol = RolSistema.objects.create(nombre="Sin revisión ni reportes", codigo="sin-revision-reportes", puede_contabilidad=True)
        usuario = Usuario.objects.create_user(username="sin-revision", empresa=self.empresa, rol_sistema=rol)
        for vista in (historicos_usd, exportar_historicos_usd_csv):
            request = RequestFactory().get(self.url_bandeja)
            request.user = usuario
            with self.assertRaises(PermissionDenied):
                vista(request, empresa_slug=self.empresa.slug)
        request = RequestFactory().post(self.url_revision, self.datos_partidas())
        request.user = usuario
        with self.assertRaises(PermissionDenied):
            revisar_conversion_asiento(request, empresa_slug=self.empresa.slug, asiento_id=self.asiento.pk)

    def test_asiento_y_cuenta_ajenos_se_rechazan(self):
        ajeno = AsientoContable.objects.create(empresa=self.otra, fecha=date(2026, 4, 1), estado="contabilizado",
            descripcion="Asiento de otra empresa", conversion_verificada=False)
        url = reverse("revisar_conversion_asiento", args=[self.empresa.slug, ajeno.pk])
        self.assertEqual(self.client.get(url).status_code, 404)
        response = self.client.post(self.url_revision, self.datos_partidas(cuenta_contrapartida=self.cuenta_otra.pk))
        self.assertIn("cuenta_contrapartida", response.context["form"].errors)
        self.assertFalse(RevisionConversionAsiento.objects.exists())

    def test_fuente_inconsistente_no_expone_cliente_de_otra_empresa(self):
        cliente = Cliente.objects.create(empresa=self.otra, nombre="Cliente privado extranjero")
        Factura.objects.filter(pk=self.factura.pk).update(cliente_id=cliente.pk)
        response = self.client.get(self.url_bandeja, {"estado": "inconsistente"})
        self.assertEqual(response.context["pagina"].paginator.count, 1)
        self.assertNotContains(response, cliente.nombre)
        response = self.client.get(self.url_revision)
        self.assertContains(response, "Resolver los vínculos del documento")
        self.assertNotContains(response, cliente.nombre)

    def test_formulario_inicia_conservando_hnl_y_no_registra_por_get(self):
        response = self.client.get(self.url_revision)
        self.assertEqual(response.status_code, 200)
        for form in response.context["formset"]:
            self.assertEqual(form["moneda"].value(), "HNL")
            self.assertEqual(form["tipo_cambio"].value(), Decimal("1"))
        self.assertFalse(RevisionConversionAsiento.objects.exists())

    def test_partidas_omitidas_repetidas_o_hnl_cambiado_se_rechazan(self):
        lineas = list(self.asiento.lineas.order_by("pk"))
        datos = self.datos_partidas(**{"partidas-1-linea_id": str(lineas[0].pk)})
        formset = PartidasRevisionFormSet(datos, prefix="partidas", lineas=lineas)
        self.assertFalse(formset.is_valid())
        self.assertIn("exactamente una vez", str(formset.non_form_errors()))
        datos = self.datos_partidas(**{"partidas-0-moneda": "HNL", "partidas-0-tipo_cambio": "1", "partidas-0-importe_original": "114"})
        response = self.client.post(self.url_revision, datos)
        self.assertFalse(response.context["formset"].is_valid())
        self.assertFalse(RevisionConversionAsiento.objects.exists())

    def test_preview_muestra_antes_objetivo_y_diferencia_sin_registrar(self):
        self.preparar()
        response = self.client.post(self.url_revision, self.datos_partidas())
        self.assertContains(response, "Registro anterior HNL")
        self.assertContains(response, "Objetivo revisado HNL")
        self.assertContains(response, "2875.00")
        self.assertFalse(RevisionConversionAsiento.objects.exists())

    def test_confirmacion_exige_token_usuario_y_partidas_inmutables(self):
        response = self.client.post(self.url_revision, self.datos_partidas(accion="registrar", confirmar="1"))
        self.assertContains(response, "La vista previa falta o ha vencido")
        token = self.preparar()
        response = self.client.post(self.url_revision, self.datos_partidas(accion="registrar", confirmar="1",
            revision_token=token, cuenta_contrapartida=self.contrapartida.pk, **{"partidas-1-tipo_cambio": "26"}))
        self.assertContains(response, "Los datos o las cuentas cambiaron")
        self.assertFalse(RevisionConversionAsiento.objects.exists())

    def test_token_no_autoriza_a_otro_usuario_y_detecta_cambio_de_cuenta(self):
        token = self.preparar()
        otro_usuario = Usuario.objects.create_user(username="otro-contador-revision", empresa=self.empresa, rol_sistema=self.rol)
        self.client.force_login(otro_usuario)
        response = self.client.post(self.url_revision, self.datos_partidas(accion="registrar", confirmar="1", revision_token=token))
        self.assertContains(response, "Los datos o las cuentas cambiaron")
        self.client.force_login(self.usuario)
        cuenta = CuentaContable.objects.create(empresa=self.empresa, codigo="11998", nombre="Otra cuenta de cliente", tipo="activo")
        cargo = self.asiento.lineas.get(debe__gt=0)
        cargo.cuenta = cuenta
        cargo.save()
        response = self.client.post(self.url_revision, self.datos_partidas(accion="registrar", confirmar="1", revision_token=token))
        self.assertContains(response, "Los datos o las cuentas cambiaron")
        self.assertFalse(RevisionConversionAsiento.objects.exists())

    def test_documento_sin_asiento_se_diagnostica_sin_reconstruirlo(self):
        self.asiento.delete()
        cantidad = AsientoContable.objects.filter(empresa=self.empresa).count()
        response = self.client.get(self.url_bandeja, {"estado": "sin_asiento"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["pagina"].paginator.count, 1)
        self.assertContains(response, "Documento sin asiento contabilizado")
        self.assertEqual(AsientoContable.objects.filter(empresa=self.empresa).count(), cantidad)

    def test_distingue_regularizacion_de_saldo_y_revision_integral(self):
        from .regularizacion_moneda import regularizar_factura_historica

        regularizar_factura_historica(asiento=self.asiento, fecha=date(2026, 5, 5), tipo_cambio=25,
            cuenta_contrapartida=self.contrapartida, motivo="Respaldo de la tasa y saldo del cliente histórico", usuario=self.usuario)
        response = self.client.get(self.url_bandeja, {"estado": "parcial"})
        self.assertEqual(response.context["resumen"]["parcial"], 1)
        self.assertEqual(response.context["resumen"]["integral"], 0)
        self.assertContains(response, "Saldo de cliente regularizado; partidas históricas conservadas")

    def test_registra_ajuste_abierto_y_preserva_origen_cerrado(self):
        originales = list(self.asiento.lineas.values("pk", "cuenta_id", "debe", "haber"))
        PeriodoContable.objects.create(empresa=self.empresa, anio=2026, mes=4, estado="cerrado")
        token = self.preparar()
        response = self.client.post(self.url_revision, self.datos_partidas(accion="registrar", confirmar="1", revision_token=token))
        self.assertRedirects(response, self.url_revision)
        revision = RevisionConversionAsiento.objects.get(empresa=self.empresa, origen=self.asiento)
        self.assertEqual(revision.ajuste.total_debe, Decimal("2760.00"))
        self.assertEqual(list(self.asiento.lineas.values("pk", "cuenta_id", "debe", "haber")), originales)
        response = self.client.get(self.url_bandeja, {"estado": "integral"})
        self.assertEqual(response.context["resumen"]["integral"], 1)
        self.assertContains(response, "Partidas revisadas")

    def test_diagnostico_terminal_es_solo_lectura_y_empresa_obligatoria(self):
        salida = StringIO()
        call_command("revisar_historicos_usd", empresa=self.empresa.slug, stdout=salida)
        self.assertIn(self.empresa.slug, salida.getvalue())
        self.assertNotIn(self.otra.slug, salida.getvalue())
        self.assertFalse(RevisionConversionAsiento.objects.exists())
