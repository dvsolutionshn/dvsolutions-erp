from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse
from django.test import RequestFactory, TestCase
from django.urls import reverse
from unittest.mock import patch

from core.middleware import EmpresaAccessMiddleware
from core.models import Empresa, RegistroAuditoria, RolSistema, Usuario, UsuarioEmpresaPermiso
from core.clinical_permissions import (
    permiso_agenda_granular,
    permiso_clinica_granular,
    permiso_historia_por_tipo,
    rol_clinico_granular,
)


class ClinicalPermissionsPilotTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.clinica = Empresa.objects.create(
            nombre="Hospital Mia", slug="hospital_mia", rtn="08011999111111", tipo_solucion="clinica",
        )
        self.erp = Empresa.objects.create(
            nombre="ERP fuera del piloto", slug="erp_fuera_piloto", rtn="08011999222222", tipo_solucion="erp",
        )
        self.role = RolSistema.objects.create(
            nombre="Cobros", codigo="cobros-piloto-prueba", es_rol_clinico=True,
            usa_permisos_clinicos_granulares=True, usa_permisos_clinicos_v2=True,
            puede_registrar_pagos_clientes=True,
        )
        self.user = Usuario.objects.create_user(
            username="operador-piloto", password="pass12345", empresa=self.clinica,
        )
        UsuarioEmpresaPermiso.objects.create(
            usuario=self.user, empresa=self.clinica, rol_sistema=self.role, activo=True,
        )

    def _request(self, empresa, suffix):
        request = self.factory.get(f"/{empresa.slug}/dashboard/facturacion/{suffix}")
        request.user = self.user
        SessionMiddleware(lambda req: None).process_request(request)
        request.session.save()
        request._messages = FallbackStorage(request)
        return request

    def _module_request(self, empresa, module, suffix, *, data=None):
        path = f"/{empresa.slug}/dashboard/{module}/{suffix}"
        request = self.factory.post(path, data or {}) if data is not None else self.factory.get(path)
        request.user = self.user
        SessionMiddleware(lambda req: None).process_request(request)
        request.session.save()
        request._messages = FallbackStorage(request)
        return request

    def test_motor_granular_solo_se_activa_en_los_cuatro_slugs(self):
        self.assertEqual(rol_clinico_granular(self.user, self.clinica), self.role)
        UsuarioEmpresaPermiso.objects.create(
            usuario=self.user, empresa=self.erp, rol_sistema=self.role, activo=True,
        )
        self.assertIsNone(rol_clinico_granular(self.user, self.erp))

    def test_registrar_pago_no_concede_editar_pago_en_clinica(self):
        middleware = EmpresaAccessMiddleware(lambda request: HttpResponse(status=204))
        with patch.object(Empresa, "tiene_modulo_activo", return_value=True):
            response = middleware(self._request(self.clinica, "1/pago/2/editar/"))
        self.assertEqual(response.status_code, 302)

        self.role.puede_editar_pagos_clientes = True
        self.role.save(update_fields=["puede_editar_pagos_clientes"])
        with patch.object(Empresa, "tiene_modulo_activo", return_value=True):
            response = middleware(self._request(self.clinica, "1/pago/2/editar/"))
        self.assertEqual(response.status_code, 204)

    def test_ver_productos_no_concede_eliminarlos_en_clinica(self):
        self.role.puede_productos = True
        self.role.save(update_fields=["puede_productos"])
        middleware = EmpresaAccessMiddleware(lambda request: HttpResponse(status=204))
        with patch.object(Empresa, "tiene_modulo_activo", return_value=True):
            response = middleware(self._request(self.clinica, "productos/1/eliminar/"))
        self.assertEqual(response.status_code, 302)

        self.role.puede_eliminar_productos = True
        self.role.save(update_fields=["puede_eliminar_productos"])
        with patch.object(Empresa, "tiene_modulo_activo", return_value=True):
            response = middleware(self._request(self.clinica, "productos/1/eliminar/"))
        self.assertEqual(response.status_code, 204)

    def test_duplicar_rol_copia_permisos_lo_deja_inactivo_y_audita_empresa(self):
        admin = Usuario.objects.create_user(
            username="admin-piloto", password="pass12345", empresa=self.clinica,
            puede_administrar_usuarios_clinicos=True,
        )
        self.client.force_login(admin)
        response = self.client.post(reverse("rol_clinico_duplicar", args=[self.clinica.slug, self.role.pk]))
        copia = RolSistema.objects.filter(nombre__startswith="Copia de Cobros", es_rol_clinico=True).get()

        self.assertRedirects(response, reverse("rol_clinico_editar", args=[self.clinica.slug, copia.pk]))
        self.assertFalse(copia.activo)
        self.assertTrue(copia.puede_registrar_pagos_clientes)
        self.assertTrue(
            RegistroAuditoria.objects.filter(
                empresa=self.clinica, usuario=admin, modelo="rolsistema", objeto_id=str(copia.pk), accion="crear",
            ).exists()
        )

    def test_agenda_separa_crear_editar_bloqueos_y_marcas_sensibles(self):
        self.assertEqual(
            permiso_agenda_granular("disponibilidad/guardar/", "POST", {}),
            "puede_crear_bloqueos_agenda",
        )
        self.assertEqual(
            permiso_agenda_granular("disponibilidad/guardar/", "POST", {"bloqueo_id": "7"}),
            "puede_editar_bloqueos_agenda",
        )
        self.assertEqual(
            permiso_agenda_granular("disponibilidad/7/eliminar/", "POST", {}),
            "puede_eliminar_bloqueos_agenda",
        )

    def test_historia_resuelve_especialidades_y_archivo_de_anexo_por_accion(self):
        self.assertEqual(permiso_historia_por_tipo("enfermeria"), "puede_ver_enfermeria")
        self.assertEqual(
            permiso_historia_por_tipo("enfermeria", editar=True),
            "puede_escribir_enfermeria",
        )

    def test_v2_exige_permiso_especifico_para_bloqueo_y_acepta_historia_autorizada(self):
        middleware = EmpresaAccessMiddleware(lambda request: HttpResponse(status=204))
        with patch.object(Empresa, "tiene_modulo_activo", return_value=True):
            denied = middleware(self._module_request(
                self.clinica, "citas", "disponibilidad/guardar/", data={"fecha": "2026-11-01"},
            ))
        self.assertEqual(denied.status_code, 302)

        self.role.puede_crear_bloqueos_agenda = True
        self.role.puede_ver_enfermeria = True
        self.role.save(update_fields=["puede_crear_bloqueos_agenda", "puede_ver_enfermeria"])
        with patch.object(Empresa, "tiene_modulo_activo", return_value=True):
            allowed_block = middleware(self._module_request(
                self.clinica, "citas", "disponibilidad/guardar/", data={"fecha": "2026-11-01"},
            ))
            allowed_history = middleware(self._module_request(
                self.clinica, "clinica", "pacientes/8/historias/consolidado/",
            ))
        self.assertEqual(allowed_block.status_code, 204)
        self.assertEqual(allowed_history.status_code, 204)
        self.assertEqual(
            permiso_clinica_granular("pacientes/8/anexos/examen/2/archivo/archivo/", "GET", v2=True),
            "puede_descargar_anexos_clinicos",
        )
        self.assertEqual(
            permiso_clinica_granular("pacientes/8/historias/nueva/enfermeria/", "POST", v2=True),
            "puede_escribir_enfermeria",
        )
