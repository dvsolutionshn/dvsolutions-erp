from datetime import timedelta

from django.core.exceptions import PermissionDenied
from django.db import connection
from django.http import Http404
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.models import Empresa, EmpresaModulo, Modulo, RolSistema, Usuario, UsuarioEmpresaPermiso
from .mobile_histories import autorizar_historias_app, permiso_historia_app
from .models import (
    AntecedenteAdicionalHistorial, AntecedenteAdicionalPaciente, ClasificacionAlopecia,
    ExpedienteEvento, HistoriaClinicaEspecialidad, Paciente, PreconsultaClinica, ProfesionalSalud,
)
from . import views


class MobileHistoryActionPermissionsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.modulo, _ = Modulo.objects.get_or_create(codigo="clinica_medica", defaults={"nombre": "Clínica"})
        cls.usuario = Usuario.objects.create_user(username="clinical-area-actor")
        cls.creador = Usuario.objects.create_user(username="clinical-original-creator")
        cls.casos = []
        for numero, slug in enumerate(("hospital_mia", "medical_spa", "luque_aestetic", "serviciosmedicos"), 1):
            empresa = Empresa.objects.create(nombre=slug, slug=slug, rtn=f"PERM-AREA-{numero}", tipo_solucion="clinica")
            EmpresaModulo.objects.create(empresa=empresa, modulo=cls.modulo)
            cls.usuario.empresas_acceso.add(empresa)
            paciente = Paciente.objects.create(
                empresa=empresa, nombre=f"Paciente permisos {numero}", expediente_codigo=f"AREA-{numero}",
                identidad=f"990000000{numero:04d}", telefono=f"9988{numero:04d}",
            )
            profesional = ProfesionalSalud.objects.create(empresa=empresa, usuario=cls.usuario, nombre=f"Autor de sesión {numero}")
            anterior = ProfesionalSalud.objects.create(empresa=empresa, usuario=cls.creador, nombre=f"Autor original {numero}")
            historias = {}
            for tipo in ("medicina_estetica", "cirugia_plastica", "capilar", "enfermeria"):
                historias[tipo] = HistoriaClinicaEspecialidad.objects.create(
                    empresa=empresa, paciente=paciente, tipo=tipo, profesional=anterior,
                    creado_por=cls.creador, actualizado_por=cls.creador, estado="borrador",
                    plan_tratamiento=f"Nota original {tipo} {numero}",
                )
            PreconsultaClinica.objects.create(
                empresa=empresa, paciente=paciente, tipo="general", estado="completada",
                token_hash=f"area-permissions-{numero}", fecha_expiracion=timezone.now() + timedelta(days=7),
                fecha_completada=timezone.now(), motivo_consulta=f"Respuesta previa reservada {numero}",
            )
            cls.casos.append((empresa, paciente, profesional, anterior, historias))

    def setUp(self):
        self.client.force_login(self.usuario)

    def asignar(self, empresa, version=3, **permisos):
        rol = RolSistema.objects.create(
            codigo=f"acciones-area-{RolSistema.objects.count()}", nombre="Acciones exactas del área",
            es_rol_clinico=True, usa_permisos_clinicos_granulares=True,
            usa_permisos_clinicos_v2=version >= 2, usa_permisos_acciones_v3=version >= 3, **permisos,
        )
        UsuarioEmpresaPermiso.objects.update_or_create(usuario=self.usuario, empresa=empresa, defaults={"rol_sistema": rol})
        return rol

    def url(self, empresa, paciente, tipo="medicina_estetica", historia=None):
        ruta = "clinica_editar_historia_especialidad" if historia else "clinica_crear_historia_especialidad"
        return reverse(ruta, args=[empresa.slug, paciente.pk, historia.pk if historia else tipo]) + "?app=1"

    def datos(self):
        return {
            "app": "1", "fecha_atencion": "2026-11-04T11:00", "estado": "borrador",
            "plan_tratamiento": "Texto nuevo del profesional autenticado", "observaciones": "Seguimiento de prueba",
        }

    def comprobar_lectura_acotada(self, consultas, historia=None):
        selects = [consulta["sql"] for consulta in consultas if consulta["sql"].lstrip().upper().startswith("SELECT")]
        self.assertFalse(any(f'"{PreconsultaClinica._meta.db_table}"' in sql for sql in selects))
        historias = [sql for sql in selects if f'"{HistoriaClinicaEspecialidad._meta.db_table}"' in sql]
        if historia is None:
            self.assertEqual(historias, [])
        else:
            self.assertTrue(historias)
            self.assertTrue(all(f'"{HistoriaClinicaEspecialidad._meta.db_table}"."id" = {historia.pk}' in sql for sql in historias))

    def comprobar_sin_consultas_historicas(self, consultas):
        selects = [consulta["sql"] for consulta in consultas if consulta["sql"].lstrip().upper().startswith("SELECT")]
        for modelo in (
            PreconsultaClinica, HistoriaClinicaEspecialidad, AntecedenteAdicionalPaciente,
            AntecedenteAdicionalHistorial, ClasificacionAlopecia, ExpedienteEvento,
        ):
            self.assertFalse(any(f'"{modelo._meta.db_table}"' in sql for sql in selects), modelo._meta.label)

    def test_creador_v3_de_un_area_trabaja_sin_lectura_ni_edicion_de_otras_historias(self):
        for empresa, paciente, profesional, anterior, historias in self.casos:
            with self.subTest(empresa=empresa.slug):
                rol = self.asignar(empresa, puede_crear_medicina_estetica=True)
                with CaptureQueriesContext(connection) as consultas:
                    pagina = self.client.get(self.url(empresa, paciente))
                self.assertEqual(pagina.status_code, 200)
                self.comprobar_lectura_acotada(consultas)
                self.assertEqual([item["codigo"] for item in pagina.context["mobile_history_create_types"]], ["medicina_estetica"])
                self.assertFalse(pagina.context["can_view_history"])
                self.assertFalse(pagina.context.get("ultima_preconsulta"))
                self.assertFalse(pagina.context.get("respuestas_paciente_recibidas"))
                self.assertNotIn("profesional", pagina.context["form"].fields)
                self.assertNotContains(pagina, "Respuesta previa reservada")
                self.assertNotContains(pagina, paciente.identidad)
                self.assertNotContains(pagina, paciente.telefono)
                self.assertNotContains(pagina, historias["cirugia_plastica"].plan_tratamiento)
                for otro in (self.url(empresa, paciente, "cirugia_plastica"), self.url(empresa, paciente, historia=historias["medicina_estetica"])):
                    self.assertIn(self.client.get(otro).status_code, (302, 403))
                total = HistoriaClinicaEspecialidad.objects.count()
                guardada = self.client.post(self.url(empresa, paciente), self.datos())
                self.assertEqual(guardada.status_code, 302)
                nueva = HistoriaClinicaEspecialidad.objects.filter(empresa=empresa, paciente=paciente).latest("pk")
                self.assertEqual(HistoriaClinicaEspecialidad.objects.count(), total + 1)
                self.assertEqual(nueva.profesional_id, profesional.pk)
                self.assertEqual((nueva.creado_por_id, nueva.actualizado_por_id), (self.usuario.pk, self.usuario.pk))
                rol.refresh_from_db()
                self.assertFalse(rol.puede_ver_historia_clinica)
                self.assertFalse(rol.puede_editar_historia_clinica)

    def test_edicion_por_area_v2_y_v3_no_requiere_permiso_generico_ni_lectura_ajena(self):
        for version in (2, 3):
            for empresa, paciente, profesional, anterior, historias in self.casos:
                with self.subTest(version=version, empresa=empresa.slug):
                    rol = self.asignar(empresa, version, puede_editar_medicina_estetica=True)
                    historia = historias["medicina_estetica"]
                    creador = historia.creado_por_id
                    with CaptureQueriesContext(connection) as consultas:
                        pagina = self.client.get(self.url(empresa, paciente, historia=historia))
                    self.assertEqual(pagina.status_code, 200)
                    self.comprobar_lectura_acotada(consultas, historia)
                    self.assertTrue(pagina.context["can_edit_history"])
                    self.assertFalse(pagina.context["can_view_history"])
                    self.assertFalse(pagina.context.get("historias_previas"))
                    self.assertFalse(pagina.context.get("respuestas_paciente_recibidas"))
                    self.assertNotContains(pagina, "Respuesta previa reservada")
                    self.assertNotContains(pagina, paciente.identidad)
                    self.assertNotContains(pagina, paciente.telefono)
                    self.assertNotContains(pagina, historias["cirugia_plastica"].plan_tratamiento)
                    with CaptureQueriesContext(connection) as consultas_web:
                        web = self.client.get(self.url(empresa, paciente, historia=historia).split("?")[0])
                    self.assertEqual(web.status_code, 200)
                    self.comprobar_lectura_acotada(consultas_web, historia)
                    self.assertFalse(web.context.get("historias_previas"))
                    self.assertFalse(web.context.get("ultima_preconsulta"))
                    self.assertNotContains(web, paciente.identidad)
                    self.assertNotContains(web, paciente.telefono)
                    self.assertNotContains(web, "Respuesta previa reservada")
                    self.assertIn(self.client.get(self.url(empresa, paciente, historia=historias["cirugia_plastica"])).status_code, (302, 403))
                    self.assertIn(self.client.post(self.url(empresa, paciente, historia=historias["cirugia_plastica"]), self.datos()).status_code, (302, 403))
                    guardada = self.client.post(self.url(empresa, paciente, historia=historia), self.datos())
                    self.assertEqual(guardada.status_code, 302)
                    historia.refresh_from_db()
                    self.assertEqual(historia.plan_tratamiento, self.datos()["plan_tratamiento"])
                    self.assertEqual(historia.profesional_id, profesional.pk)
                    self.assertEqual(historia.creado_por_id, creador)
                    self.assertEqual(historia.actualizado_por_id, self.usuario.pk)
                    # V2 conserva su acción conjunta de crear/editar; V3 las separa.
                    creacion = self.client.get(self.url(empresa, paciente))
                    self.assertEqual(creacion.status_code == 200, version == 2)
                    rol.refresh_from_db()
                    self.assertFalse(rol.puede_ver_medicina_estetica)
                    self.assertFalse(rol.puede_editar_historia_clinica)

    def test_lectura_del_area_no_muestra_acciones_de_crear_ni_editar(self):
        for empresa, paciente, _profesional, _anterior, historias in self.casos:
            with self.subTest(empresa=empresa.slug):
                self.asignar(empresa, puede_ver_medicina_estetica=True)
                for ruta in (self.url(empresa, paciente), self.url(empresa, paciente, historia=historias["medicina_estetica"])):
                    self.assertIn(self.client.get(ruta).status_code, (302, 403))
                lista = reverse("clinica_historias_especialidad", args=[empresa.slug, paciente.pk]) + "?app=1&tipo=medicina_estetica"
                lectura = self.client.get(lista)
                self.assertEqual(lectura.status_code, 200)
                self.assertContains(lectura, historias["medicina_estetica"].plan_tratamiento)
                self.assertNotContains(lectura, historias["cirugia_plastica"].plan_tratamiento)
                self.assertNotContains(lectura, "Respuesta previa reservada")
                self.assertNotContains(lectura, paciente.identidad)
                self.assertNotContains(lectura, paciente.telefono)
                otro_tipo = lista.replace("tipo=medicina_estetica", "tipo=cirugia_plastica")
                self.assertIn(self.client.get(otro_tipo).status_code, (302, 403))
                request = RequestFactory().get(otro_tipo)
                request.user = self.usuario
                with self.assertRaises(PermissionDenied):
                    views.historias_especialidad(request, empresa.slug, paciente.pk)
                self.assertFalse(lectura.context["mobile_history_create_types"])
                self.assertTrue(all(not nota.get("edit_url") for nota in lectura.context["mobile_history_notes"]))

    def test_escritor_sin_lectura_no_abre_consolidado_web_app_ni_consulta_historicos(self):
        for version in (2, 3):
            for empresa, paciente, *_resto in self.casos:
                permiso = "puede_editar_medicina_estetica" if version == 2 else "puede_crear_medicina_estetica"
                self.asignar(empresa, version, **{permiso: True})
                for app in (False, True):
                    with self.subTest(version=version, empresa=empresa.slug, app=app):
                        ruta = reverse("clinica_historial_clinico_consolidado", args=[empresa.slug, paciente.pk]) + ("?app=1" if app else "")
                        with CaptureQueriesContext(connection) as consultas:
                            pagina = self.client.get(ruta)
                        self.assertEqual(pagina.status_code, 403)
                        self.comprobar_sin_consultas_historicas(consultas)
                        self.assertNotContains(pagina, "Respuesta previa reservada", status_code=403)
                        with CaptureQueriesContext(connection) as directas:
                            request = RequestFactory().get(ruta)
                            request.user = self.usuario
                            with self.assertRaises(PermissionDenied):
                                views.historial_clinico_consolidado(request, empresa.slug, paciente.pk)
                        self.comprobar_sin_consultas_historicas(directas)
                        if not app:
                            datos = {
                                "tipo_historia": "medicina_estetica", "historia_medicina_estetica-fecha_atencion": "2026-11-04T11:00",
                                "historia_medicina_estetica-estado": "borrador", "historia_medicina_estetica-plan_tratamiento": "Intento sin lectura",
                            }
                            with CaptureQueriesContext(connection) as consultas_post:
                                denegado = self.client.post(ruta, datos)
                            self.assertEqual(denegado.status_code, 403)
                            self.comprobar_sin_consultas_historicas(consultas_post)
                            with CaptureQueriesContext(connection) as directas_post:
                                request = RequestFactory().post(ruta, datos)
                                request.user = self.usuario
                                with self.assertRaises(PermissionDenied):
                                    views.historial_clinico_consolidado(request, empresa.slug, paciente.pk)
                            self.comprobar_sin_consultas_historicas(directas_post)

    def test_lector_de_area_ve_su_nota_en_consolidado_sin_respuestas_del_formulario(self):
        for version in (2, 3):
            for empresa, paciente, *_resto, historias in self.casos:
                self.asignar(empresa, version, puede_ver_medicina_estetica=True)
                confidencial = f"Antecedente adicional reservado {empresa.slug} v{version}"
                AntecedenteAdicionalPaciente.objects.update_or_create(
                    empresa=empresa, paciente=paciente, defaults={"contenido": confidencial},
                )
                evento = ExpedienteEvento.objects.create(
                    empresa=empresa, paciente=paciente, titulo="Evento clínico reservado",
                    descripcion=f"Detalle reservado de otra sección {empresa.slug} v{version}",
                )
                Paciente.objects.filter(pk=paciente.pk).update(es_alergico=True, alergias="Alergia clínica reservada")
                for app in (False, True):
                    with self.subTest(version=version, empresa=empresa.slug, app=app):
                        ruta = reverse("clinica_historial_clinico_consolidado", args=[empresa.slug, paciente.pk]) + ("?app=1" if app else "")
                        with CaptureQueriesContext(connection) as consultas:
                            pagina = self.client.get(ruta)
                        self.assertEqual(pagina.status_code, 200)
                        self.assertContains(pagina, historias["medicina_estetica"].plan_tratamiento)
                        self.assertNotContains(pagina, historias["cirugia_plastica"].plan_tratamiento)
                        self.assertNotContains(pagina, "Respuesta previa reservada")
                        self.assertNotContains(pagina, confidencial)
                        self.assertNotContains(pagina, "Alergia clínica reservada")
                        self.assertNotContains(pagina, evento.descripcion)
                        self.assertNotContains(pagina, paciente.expediente_codigo)
                        self.assertNotContains(pagina, paciente.identidad)
                        self.assertNotContains(pagina, paciente.telefono)
                        self.assertFalse(pagina.context["preconsultas"])
                        self.assertFalse(any(f'"{PreconsultaClinica._meta.db_table}"' in consulta["sql"] for consulta in consultas))
                        self.assertFalse(pagina.context["eventos"])
                        self.assertFalse(any(f'"{ExpedienteEvento._meta.db_table}"' in consulta["sql"] for consulta in consultas))
                        preconsulta = PreconsultaClinica.objects.get(empresa=empresa, paciente=paciente)
                        self.assertFalse(views._secciones_preconsulta_usuario(preconsulta, self.usuario, empresa))

    def test_inline_v3_registra_solo_con_creacion_exacta_y_no_con_edicion(self):
        for empresa, paciente, profesional, _anterior, historias in self.casos:
            with self.subTest(empresa=empresa.slug):
                ruta = reverse("clinica_historial_clinico_consolidado", args=[empresa.slug, paciente.pk])
                datos = {
                    "tipo_historia": "medicina_estetica", "historia_medicina_estetica-fecha_atencion": "2026-11-04T11:00",
                    "historia_medicina_estetica-estado": "borrador", "historia_medicina_estetica-plan_tratamiento": "Nueva nota inline",
                }
                self.asignar(empresa, puede_ver_historia_clinica=True, puede_editar_medicina_estetica=True)
                total = HistoriaClinicaEspecialidad.objects.count()
                self.assertIn(self.client.post(ruta, datos).status_code, (302, 403))
                self.assertEqual(HistoriaClinicaEspecialidad.objects.count(), total)
                self.asignar(empresa, puede_ver_historia_clinica=True, puede_crear_medicina_estetica=True)
                pagina = self.client.get(ruta, {"seccion": "especialidad", "tipo": "medicina_estetica"})
                self.assertEqual(pagina.status_code, 200)
                self.assertContains(pagina, 'name="historia_medicina_estetica-fecha_atencion"')
                self.assertNotContains(pagina, historias["cirugia_plastica"].plan_tratamiento)
                self.assertEqual(self.client.post(ruta, datos).status_code, 302)
                nueva = HistoriaClinicaEspecialidad.objects.filter(empresa=empresa, paciente=paciente).latest("pk")
                self.assertEqual(nueva.tipo, "medicina_estetica")
                self.assertEqual(nueva.profesional_id, profesional.pk)
                self.assertEqual((nueva.creado_por_id, nueva.actualizado_por_id), (self.usuario.pk, self.usuario.pk))

    def test_botones_de_edicion_por_nota_y_permiso_generico_v3_no_sustituye_area(self):
        for empresa, paciente, *_resto, historias in self.casos:
            with self.subTest(empresa=empresa.slug):
                lecturas = {
                    "puede_ver_historia_clinica": True, "puede_ver_capilar": True,
                    "puede_ver_cirugia_clinica": True, "puede_ver_enfermeria": True,
                }
                self.asignar(empresa, puede_editar_capilar=True, **lecturas)
                lista = reverse("clinica_historias_especialidad", args=[empresa.slug, paciente.pk]) + "?app=1"
                lectura = self.client.get(lista)
                self.assertEqual(lectura.status_code, 200)
                notas = {nota["tipo"]: nota for nota in lectura.context["mobile_history_notes"]}
                self.assertIn("edit_url", notas["capilar"])
                self.assertNotIn("edit_url", notas["cirugia_plastica"])
                self.assertNotIn("edit_url", notas["enfermeria"])
                self.assertEqual(self.client.get(notas["capilar"]["edit_url"]).status_code, 200)

                self.asignar(empresa, puede_editar_historia_clinica=True, **lecturas)
                lectura = self.client.get(lista)
                self.assertTrue(all(not nota.get("edit_url") for nota in lectura.context["mobile_history_notes"]))
                with self.assertRaises(PermissionDenied):
                    request = RequestFactory().get(self.url(empresa, paciente, historia=historias["capilar"]))
                    request.user = self.usuario
                    views.editar_historia_especialidad(request, empresa.slug, paciente.pk, historias["capilar"].pk)

    def test_autorizacion_directa_mantiene_modulo_membresia_y_empresa_del_rol(self):
        empresa, paciente, *_resto = self.casos[0]
        otra = self.casos[1][0]
        self.asignar(empresa, puede_editar_medicina_estetica=True)
        request = RequestFactory().get(self.url(empresa, paciente))
        request.user = self.usuario
        self.assertTrue(permiso_historia_app(request, empresa, editar=True))
        self.assertTrue(permiso_historia_app(request, empresa, "medicina_estetica", editar=True))
        self.assertFalse(permiso_historia_app(request, empresa, "cirugia_plastica", editar=True))
        self.assertFalse(permiso_historia_app(request, otra, "medicina_estetica", editar=True))
        with self.assertRaises(PermissionDenied):
            request = RequestFactory().post(self.url(empresa, paciente, "cirugia_plastica"), self.datos())
            request.user = self.usuario
            views.crear_historia_especialidad(request, empresa.slug, paciente.pk, "cirugia_plastica")
        EmpresaModulo.objects.filter(empresa=empresa, modulo=self.modulo).update(activo=False)
        with self.assertRaises(Http404):
            autorizar_historias_app(request, empresa, "medicina_estetica", editar=True)
        EmpresaModulo.objects.filter(empresa=empresa, modulo=self.modulo).update(activo=True)
        self.usuario.empresas_acceso.remove(empresa)
        with self.assertRaises(PermissionDenied):
            autorizar_historias_app(request, empresa, "medicina_estetica", editar=True)
