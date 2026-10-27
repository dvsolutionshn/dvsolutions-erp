from unittest.mock import patch
import inspect
import importlib
import re

from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase

from core.access import (
    permiso_contabilidad_accion,
    permiso_crm_accion,
    permiso_rrhh_accion,
    permiso_tecnicentro_accion,
)
from core.clinical_permissions import (
    TODOS_LOS_PERMISOS_CLINICOS,
    permiso_agenda_granular,
    permiso_clinica_granular,
    permiso_facturacion_clinico_granular,
    permiso_historia_por_tipo,
)
from core.middleware import EmpresaAccessMiddleware
from core.models import Empresa, RolSistema, Usuario, UsuarioEmpresaPermiso


class GranularActionResolverV3Tests(SimpleTestCase):
    @staticmethod
    def _rutas_mutables(urlpatterns):
        rutas = []
        for pattern in urlpatterns:
            callback = inspect.unwrap(pattern.callback)
            source = inspect.getsource(callback)
            if "@require_safe" in source:
                continue
            if "POST" not in source and "require_POST" not in source:
                continue
            ruta = re.sub(r"<[^>]+>", "1", str(pattern.pattern)).strip("/")
            rutas.append((ruta, callback.__name__))
        return rutas

    def test_contabilidad_separa_todas_las_familias_de_escritura(self):
        casos = {
            "configuracion/": "puede_configurar_contabilidad",
            "periodos/crear/": "puede_crear_periodos_contables",
            "periodos/4/editar/": "puede_editar_periodos_contables",
            "periodos/4/cerrar/": "puede_cerrar_periodos_contables",
            "periodos/4/abrir/": "puede_reabrir_periodos_contables",
            "clasificaciones-compras/crear/": "puede_administrar_clasificaciones_contables",
            "bancos/cuentas/crear/": "puede_administrar_cuentas_financieras",
            "bancos/importar/": "puede_importar_movimientos_bancarios",
            "bancos/reglas/crear/": "puede_administrar_reglas_bancarias",
            "bancos/movimientos/clasificar-lote/": "puede_clasificar_movimientos_bancarios",
            "bancos/movimientos/4/enlazar-factura/": "puede_enlazar_movimientos_bancarios",
            "bancos/conciliacion/4/conciliar/": "puede_conciliar_movimientos_bancarios",
            "cuentas/crear/": "puede_crear_cuentas_contables",
            "cuentas/4/editar/": "puede_editar_cuentas_contables",
            "cuentas/importar/": "puede_importar_catalogo_cuentas",
            "asientos/crear/": "puede_crear_asientos",
            "asientos/4/editar/": "puede_editar_asientos",
            "asientos/4/duplicar/": "puede_duplicar_asientos",
            "asientos/4/contabilizar/": "puede_contabilizar_asientos",
            "asientos/4/reversar/": "puede_reversar_asientos",
            "asientos/4/regresar-borrador/": "puede_regresar_asientos_borrador",
            "asientos/4/regularizar-moneda/": "puede_regularizar_asientos",
            "reportes/balance-general/excel/": "puede_exportar_reportes_contables",
        }
        for ruta, esperado in casos.items():
            with self.subTest(ruta=ruta):
                self.assertEqual(permiso_contabilidad_accion(ruta, "POST", v3=True), esperado)

    def test_rrhh_crm_y_taller_separan_sus_acciones(self):
        rrhh = {
            "empleados/crear/": "puede_crear_empleados",
            "empleados/4/editar/": "puede_editar_empleados",
            "empleados/4/eliminar/": "puede_eliminar_empleados",
            "planillas/crear/": "puede_crear_planillas",
            "planillas/4/generar/": "puede_generar_planillas",
            "planillas/4/cerrar/": "puede_cerrar_planillas",
            "planillas/4/reabrir/": "puede_reabrir_planillas",
            "planillas/4/pagar/": "puede_pagar_planillas",
            "planillas/detalle/8/editar/": "puede_editar_planillas",
            "movimientos/": "puede_gestionar_movimientos_planilla",
            "vacaciones/": "puede_gestionar_vacaciones",
            "voucher/8/email/": "puede_enviar_vouchers_planilla",
        }
        for ruta, esperado in rrhh.items():
            self.assertEqual(permiso_rrhh_accion(ruta, "POST"), esperado)
        crm = {
            "plantillas/": "puede_administrar_plantillas_crm",
            "campanias/crear/": "puede_crear_campanias",
            "campanias/4/preparar-envios/": "puede_preparar_campanias",
            "campanias/4/enviar-prueba-masiva/": "puede_enviar_pruebas_campanias",
            "campanias/4/enviar-whatsapp-api/": "puede_enviar_campanias",
            "configuracion/conectar-whatsapp-meta/": "puede_conectar_whatsapp_crm",
        }
        for ruta, esperado in crm.items():
            self.assertEqual(permiso_crm_accion(ruta, "POST"), esperado)
        taller = {
            "recepcion/": "puede_crear_recepciones_taller",
            "agenda/": "puede_crear_citas_taller",
            "agenda/4/estado/": "puede_cambiar_estado_citas_taller",
            "recepcion/4/inspeccion/": "puede_registrar_inspecciones_taller",
            "ordenes/4/estado/": "puede_cambiar_estado_ordenes_taller",
            "ordenes/4/asignar/": "puede_asignar_ordenes_taller",
            "diagnosticos/4/": "puede_editar_diagnosticos_taller",
            "diagnosticos/4/evidencia/": "puede_agregar_evidencias_taller",
        }
        for ruta, esperado in taller.items():
            self.assertEqual(permiso_tecnicentro_accion(ruta, "POST", {}), esperado)

    def test_clinica_agenda_y_facturacion_separan_acciones_antes_agrupadas(self):
        self.assertEqual(permiso_agenda_granular("7/reagendar-whatsapp/", "POST", v3=True), "puede_reagendar_citas")
        self.assertEqual(permiso_agenda_granular("7/cancelar-whatsapp/", "POST", v3=True), "puede_cancelar_citas")
        self.assertEqual(permiso_agenda_granular("7/estado/", "POST", v3=True), "puede_cambiar_estado_citas")
        self.assertEqual(permiso_historia_por_tipo("capilar", crear=True), "puede_crear_capilar")
        self.assertEqual(permiso_historia_por_tipo("capilar", editar=True), "puede_editar_capilar")
        self.assertEqual(
            permiso_clinica_granular("pacientes/7/planes-tratamiento/", "POST", v2=True, v3=True),
            "puede_crear_planes_tratamiento",
        )
        self.assertEqual(permiso_facturacion_clinico_granular("cotizaciones/crear/", "GET", v3=True), "puede_crear_cotizaciones")
        self.assertEqual(permiso_facturacion_clinico_granular("recibos/4/editar/", "GET", v3=True), "puede_editar_recibos")
        self.assertEqual(
            permiso_facturacion_clinico_granular("inventario/compras/4/pago/8/revertir/", "POST", v3=True),
            "puede_revertir_pagos_proveedores",
        )

    def test_catalogo_cubre_todos_los_permisos_vigentes_y_solo_excluye_legados(self):
        modelo = {field.name for field in RolSistema._meta.fields if field.name.startswith("puede_")}
        catalogo = set(TODOS_LOS_PERMISOS_CLINICOS)
        self.assertEqual(
            modelo - catalogo,
            {"puede_citas", "puede_clinica", "puede_expediente_clinico", "puede_pacientes", "puede_tratamientos_clinicos"},
        )

    def test_cada_vista_mutable_de_modulos_operativos_tiene_resolutor(self):
        from contabilidad.urls import urlpatterns as contabilidad_urls
        from rrhh.urls import urlpatterns as rrhh_urls
        from crm.urls import urlpatterns as crm_urls
        from tecnicentro.urls import urlpatterns as taller_urls

        modulos = (
            (contabilidad_urls, lambda ruta: permiso_contabilidad_accion(ruta, "POST", v3=True)),
            (rrhh_urls, lambda ruta: permiso_rrhh_accion(ruta, "POST")),
            (crm_urls, lambda ruta: permiso_crm_accion(ruta, "POST")),
            (taller_urls, lambda ruta: permiso_tecnicentro_accion(ruta, "POST", {})),
        )
        faltantes = []
        for urls, resolver in modulos:
            for ruta, vista in self._rutas_mutables(urls):
                if not resolver(ruta):
                    faltantes.append(f"{vista}:{ruta}")
        self.assertEqual(faltantes, [])

    def test_cada_vista_mutable_clinica_agenda_y_facturacion_tiene_permiso(self):
        from facturacion.urls import urlpatterns as facturacion_urls
        from clinica.urls import urlpatterns as clinica_urls
        from crm.agenda_urls import urlpatterns as agenda_urls

        modulos = (
            (facturacion_urls, lambda ruta: permiso_facturacion_clinico_granular(ruta, "POST", v3=True)),
            (clinica_urls, lambda ruta: permiso_clinica_granular(ruta, "POST", v2=True, v3=True)),
            (agenda_urls, lambda ruta: permiso_agenda_granular(ruta, "POST", {}, v3=True)),
        )
        faltantes = []
        for urls, resolver in modulos:
            for ruta, vista in self._rutas_mutables(urls):
                permiso = resolver(ruta)
                if not permiso or permiso == "__denegar__":
                    faltantes.append(f"{vista}:{ruta}:{permiso}")
        self.assertEqual(faltantes, [])


class GranularActionMiddlewareV3Tests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.empresa = Empresa.objects.create(
            nombre="Hospital Mia", slug="hospital_mia", rtn="08011999000001", tipo_solucion="clinica",
        )
        self.rol = RolSistema.objects.create(
            nombre="Acciones V3", codigo="acciones-v3-test", es_rol_clinico=True,
            usa_permisos_clinicos_granulares=True, usa_permisos_clinicos_v2=True,
            usa_permisos_acciones_v3=True,
            puede_contabilidad=True, puede_rrhh=True, puede_empleados=True,
            puede_crm=True, puede_campanias=True, puede_tecnicentro=True, puede_operacion_taller=True,
        )
        self.usuario = Usuario.objects.create_user(username="acciones-v3", password="test", empresa=self.empresa)
        UsuarioEmpresaPermiso.objects.create(usuario=self.usuario, empresa=self.empresa, rol_sistema=self.rol)
        self.middleware = EmpresaAccessMiddleware(lambda request: HttpResponse(status=204))

    def request(self, modulo, suffix, *, field=None):
        request = self.factory.post(f"/{self.empresa.slug}/dashboard/{modulo}/{suffix}", {"dato": "1"})
        request.user = self.usuario
        SessionMiddleware(lambda req: None).process_request(request)
        request.session.save()
        request._messages = FallbackStorage(request)
        if field:
            setattr(self.rol, field, True)
            self.rol.save(update_fields=[field])
        with patch.object(Empresa, "tiene_modulo_activo", return_value=True):
            return self.middleware(request)

    def test_backend_exige_permiso_exacto_y_no_el_permiso_general(self):
        casos = (
            ("contabilidad", "asientos/4/reversar/", "puede_reversar_asientos"),
            ("rrhh", "empleados/crear/", "puede_crear_empleados"),
            ("crm", "campanias/4/enviar-whatsapp-api/", "puede_enviar_campanias"),
            ("tecnicentro", "ordenes/4/asignar/", "puede_asignar_ordenes_taller"),
        )
        for modulo, suffix, field in casos:
            with self.subTest(field=field):
                self.assertEqual(self.request(modulo, suffix).status_code, 302)
                self.assertEqual(self.request(modulo, suffix, field=field).status_code, 204)

    def test_migraciones_conservan_los_accesos_de_roles_existentes(self):
        legado = RolSistema.objects.create(
            nombre="Legado V2", codigo="legado-v2-backfill", puede_contabilidad=True,
            puede_planillas=True, puede_campanias=True, puede_editar_citas=True,
            puede_editar_planes_tratamiento=True, puede_crear_facturas=True,
        )
        migracion_v3 = importlib.import_module("core.migrations.0060_permisos_acciones_v3")
        migracion_facturacion = importlib.import_module("core.migrations.0061_permisos_facturacion_v3")
        from django.apps import apps

        migracion_v3.conservar_permisos_existentes(apps, None)
        migracion_facturacion.conservar_permisos_facturacion(apps, None)
        legado.refresh_from_db()
        self.assertTrue(legado.puede_reabrir_periodos_contables)
        self.assertTrue(legado.puede_pagar_planillas)
        self.assertTrue(legado.puede_enviar_campanias)
        self.assertTrue(legado.puede_reagendar_citas)
        self.assertTrue(legado.puede_crear_planes_tratamiento)
        self.assertTrue(legado.puede_crear_cotizaciones)
