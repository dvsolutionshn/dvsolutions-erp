"""Presentación móvil de pacientes y consulta del expediente existente."""

from urllib.parse import urlencode

from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse

from core.access import gastos_adicionales_habilitados, interfaz_clinica_activa
from core.clinical_permissions import EMPRESAS_CLINICAS_CON_ROLES, rol_clinico_granular
from crm.mobile_modules import inicio_app_disponible, url_modulos_app


def es_pacientes_app(request, empresa):
    return bool(
        empresa.slug in EMPRESAS_CLINICAS_CON_ROLES
        and interfaz_clinica_activa(empresa)
        and (request.GET.get("app") == "1" or request.POST.get("app") == "1")
    )


def permiso_pacientes_app(request, empresa, granular, legado):
    nombre = granular if rol_clinico_granular(request.user, empresa) else legado
    return request.user.tiene_permiso_erp(nombre, empresa)


def autorizar_pacientes_app(request, empresa, granular, legado):
    if not es_pacientes_app(request, empresa):
        return
    if not empresa.tiene_modulo_activo("clinica_medica"):
        raise Http404("El módulo clínico no está habilitado para esta empresa.")
    if not request.user.puede_acceder_empresa(empresa) or not permiso_pacientes_app(request, empresa, granular, legado):
        raise PermissionDenied("No tiene permiso para esta acción del expediente.")


def url_paciente_app(vista, empresa, paciente_id=None, **parametros):
    args = [empresa.slug] + ([paciente_id] if paciente_id is not None else [])
    return f"{reverse(vista, args=args)}?{urlencode({'app': '1', **parametros})}"


def _secciones_formulario_paciente(form):
    grupos = (
        ("Datos generales", ("foto_perfil", "nombres", "apellidos", "nombre", "identidad", "tipo_id", "expediente_codigo", "fecha_nacimiento", "sexo", "estado_civil", "lugar_nacimiento")),
        ("Contacto", ("correo", "telefono_codigo_area", "telefono", "prefijo_telefono", "whatsapp", "celular_2", "direccion", "ocupacion", "lugar_trabajo", "contacto_emergencia_completo", "contacto_emergencia", "telefono_emergencia")),
        ("Referencia", ("informante", "informante_detalle", "referido_por", "referido_por_detalle")),
        ("Motivo de consulta", ("motivo_categoria", "procedimientos_interes", "procedimientos_interes_otros", "consentimiento_datos")),
    )
    visibles = {field.name: field for field in form.visible_fields()}
    secciones, usados = [], set()
    for titulo, nombres in grupos:
        campos = [visibles[nombre] for nombre in nombres if nombre in visibles]
        if campos:
            secciones.append({"titulo": titulo, "campos": campos})
            usados.update(field.name for field in campos)
    restantes = [field for nombre, field in visibles.items() if nombre not in usados]
    if restantes:
        secciones.append({"titulo": "Datos adicionales", "campos": restantes})
    return secciones


def contexto_pacientes_app(request, empresa, paciente=None, form=None):
    # Importación local: el helper de historias reutiliza esta base de
    # presentación, pero el catálogo de tipos no depende de ella.
    from .mobile_histories import tipos_historia_creables_app
    from .mobile_special_histories import formularios_historia_especiales_app
    from .mobile_controls import controles_disponibles_app
    from .mobile_antecedents import antecedentes_disponibles_app, url_antecedentes_app
    from .mobile_plans import planes_disponibles_app
    from .mobile_followups import seguimientos_disponibles_app
    from .mobile_general_history import historia_general_disponible_app, url_historia_general_app

    granular = bool(rol_clinico_granular(request.user, empresa))
    permiso = lambda nuevo, legado: request.user.tiene_permiso_erp(nuevo if granular else legado, empresa)
    # El menú legado del expediente y el middleware de estas rutas exigen
    # ambos permisos. La presentación nueva no aprovecha el mapper más amplio.
    anexos_legados = granular or request.user.tiene_permiso_erp("puede_pacientes", empresa)
    puede_ver_anexos = anexos_legados and permiso("puede_ver_anexos_clinicos", "puede_expediente_clinico")
    puede_subir_anexos = anexos_legados and permiso("puede_subir_anexos_clinicos", "puede_expediente_clinico")
    inicio = reverse("agenda_mobile", args=[empresa.slug])
    contexto = {
        "modo_app": True,
        "app_base_url": inicio,
        "app_invoices_url": f"{inicio}#facturas-app",
        "app_modules_url": url_modulos_app(request, empresa),
        "app_home_disponible": inicio_app_disponible(request, empresa),
        "app_patients_url": url_paciente_app("clinica_pacientes", empresa),
        "app_patient_create_url": url_paciente_app("clinica_crear_paciente", empresa),
        "app_patient_form_url": url_paciente_app("clinica_editar_paciente", empresa, paciente.pk) if paciente else url_paciente_app("clinica_crear_paciente", empresa),
        "app_patient_event_form_url": url_paciente_app("clinica_crear_evento_paciente", empresa, paciente.pk) if paciente else url_paciente_app("clinica_crear_evento", empresa),
        "can_view_patients": permiso("puede_ver_pacientes", "puede_pacientes"),
        "can_view_patient": permiso("puede_ver_datos_generales_paciente", "puede_pacientes"),
        "can_create_patient": permiso("puede_crear_pacientes", "puede_pacientes"),
        "can_edit_patient": permiso("puede_editar_pacientes", "puede_pacientes"),
        "can_view_history": permiso("puede_ver_historia_clinica", "puede_expediente_clinico"),
        "can_create_history": permiso("puede_crear_historia_clinica", "puede_expediente_clinico"),
        "can_edit_history": permiso("puede_editar_historia_clinica", "puede_expediente_clinico"),
        "can_complete_general_history": historia_general_disponible_app(request, empresa),
        "mobile_history_create_types": tipos_historia_creables_app(request, empresa, paciente),
        "mobile_special_history_forms": formularios_historia_especiales_app(request, empresa, paciente),
        "mobile_patient_control_types": controles_disponibles_app(request, empresa, paciente),
        "can_manage_antecedents": antecedentes_disponibles_app(request, empresa, paciente),
        "can_view_annexes": puede_ver_anexos,
        "can_view_attachments": puede_ver_anexos,
        "can_upload_annexes": puede_subir_anexos,
        "can_view_recipes": permiso("puede_ver_recetas", "puede_expediente_clinico"),
        "can_view_plans": permiso("puede_ver_planes_tratamiento", "puede_tratamientos_clinicos"),
        "can_view_recordatorios": permiso("puede_crear_recordatorios_paciente", "puede_pacientes"),
        "can_view_followups": seguimientos_disponibles_app(request, empresa),
        "can_view_gastos": gastos_adicionales_habilitados(empresa) and request.user.tiene_permiso_erp("puede_ver_gastos_adicionales", empresa),
    }
    destino_paciente = request.GET.get("destino", "")
    planes = planes_disponibles_app(request, empresa)
    contexto["mobile_patient_plans"] = planes
    if request.method == "GET" and es_pacientes_app(request, empresa):
        if (destino_paciente == "planes" and planes["ver"]) or (destino_paciente == "crear_plan" and planes["crear"]):
            contexto["mobile_patient_plan_mode"] = destino_paciente
            if not empresa.tiene_modulo_activo("agenda_citas") or (not granular and not request.user.tiene_permiso_erp("puede_citas", empresa)):
                contexto["app_home_disponible"] = False
                contexto["app_modules_url"] = reverse("agenda_mobile_modules_screen", args=[empresa.slug])
    if request.method == "GET" and es_pacientes_app(request, empresa) and (
        (destino_paciente == "anexos" and puede_ver_anexos)
        or (destino_paciente == "crear_anexo" and puede_subir_anexos)
    ):
        contexto["mobile_patient_annex_mode"] = destino_paciente
    if request.method == "GET" and es_pacientes_app(request, empresa):
        if destino_paciente == "antecedentes" and contexto["can_manage_antecedents"]:
            contexto["mobile_patient_antecedents_mode"] = True
            if not granular and not request.user.tiene_permiso_erp("puede_citas", empresa):
                contexto["app_home_disponible"] = False
                contexto["app_modules_url"] = reverse("agenda_mobile_modules_screen", args=[empresa.slug])
        elif destino_paciente == "control_clinico":
            controles = contexto["mobile_patient_control_types"]
            tipo_solicitado = request.GET.get("tipo", "")
            tipo_elegido = next((item for item in controles if item["codigo"] == tipo_solicitado), None)
            if not tipo_solicitado and controles:
                tipo_elegido = controles[0]
            if tipo_elegido:
                contexto["mobile_patient_control_mode"] = True
                contexto["mobile_patient_control_type"] = tipo_elegido
                if not granular and not request.user.tiene_permiso_erp("puede_citas", empresa):
                    contexto["app_home_disponible"] = False
                    contexto["app_modules_url"] = reverse("agenda_mobile_modules_screen", args=[empresa.slug])
        elif destino_paciente == "evaluacion_clinica":
            formularios = contexto["mobile_special_history_forms"]
            formulario_solicitado = request.GET.get("formulario", "")
            formulario_elegido = next((item for item in formularios if item["codigo"] == formulario_solicitado), None)
            if not formulario_solicitado and formularios:
                formulario_elegido = formularios[0]
            if formulario_elegido:
                contexto["mobile_patient_special_history_mode"] = True
                contexto["mobile_patient_special_history_form"] = formulario_elegido
        elif destino_paciente == "historias" and contexto["can_view_history"]:
            contexto["mobile_patient_history_mode"] = "historias"
        elif destino_paciente == "crear_historia":
            tipos_creables = contexto["mobile_history_create_types"]
            tipo_solicitado = request.GET.get("tipo", "")
            tipo_elegido = next((tipo for tipo in tipos_creables if tipo["codigo"] == tipo_solicitado), None)
            if not tipo_solicitado and tipos_creables:
                tipo_elegido = tipos_creables[0]
            if tipo_elegido:
                contexto["mobile_patient_history_mode"] = "crear_historia"
                contexto["mobile_patient_history_create_type"] = tipo_elegido
    if paciente:
        contexto["app_patient_general_history_url"] = url_historia_general_app(empresa, paciente) if contexto["can_complete_general_history"] else ""
        contexto["app_patient_followups_url"] = url_paciente_app("clinica_seguimientos_paciente", empresa, paciente.pk)
        contexto["app_patient_plans_url"] = url_paciente_app("clinica_planes_tratamiento_paciente", empresa, paciente.pk)
        contexto["app_patient_plan_create_url"] = url_paciente_app("clinica_plan_tratamiento_crear_app", empresa, paciente.pk)
        if contexto["can_manage_antecedents"]:
            contexto["app_patient_antecedents_url"] = url_antecedentes_app(empresa, paciente)
        for nombre, vista in {
            "detail": "clinica_paciente_detalle", "edit": "clinica_editar_paciente",
            "histories": "clinica_historias_especialidad", "history": "clinica_historial_clinico_consolidado",
            "event": "clinica_crear_evento_paciente",
            "annexes": "clinica_examenes_paciente", "annex_upload": "clinica_subir_examen_paciente",
        }.items():
            contexto[f"app_patient_{nombre}_url"] = url_paciente_app(vista, empresa, paciente.pk)
        contexto["app_patient_gastos_url"] = f"{reverse('clinica_gastos_adicionales', args=[empresa.slug])}?{urlencode({'app': '1', 'paciente_id': paciente.pk})}"
    if form:
        contexto["mobile_patient_form_sections"] = _secciones_formulario_paciente(form)
        contexto["mobile_patient_form_groups"] = [{"titulo": item["titulo"], "fields": item["campos"]} for item in contexto["mobile_patient_form_sections"]]
    return contexto


def notas_historia_app(historias):
    campos = (
        ("motivo_consulta", "Motivo de consulta"), ("antecedentes", "Antecedentes"),
        ("historia_enfermedad_actual", "Enfermedad actual"), ("signos_vitales", "Signos vitales"),
        ("examen_fisico", "Examen físico"), ("evaluacion_clinica", "Evaluación clínica"),
        ("diagnostico", "Diagnóstico"), ("analisis_clinico", "Análisis clínico"),
        ("procedimiento", "Procedimiento"), ("conducta", "Conducta"),
        ("plan_tratamiento", "Plan de tratamiento"), ("indicaciones", "Indicaciones"),
        ("observaciones", "Observaciones"),
    )
    notas = []
    for historia in historias:
        contenido = [{"label": label, "value": getattr(historia, nombre)} for nombre, label in campos if getattr(historia, nombre)]
        # Se reutilizan los datos ya interpretados por la vista web, sin otro parser clínico.
        funciones_generales = getattr(historia, "funciones_generales_registradas", "")
        if funciones_generales:
            contenido.append({"label": "Funciones orgánicas generales", "value": funciones_generales})
        for item in getattr(historia, "funciones_registradas", []):
            valor = " · ".join(str(item[clave]) for clave in ("estado", "detalle") if item.get(clave))
            if valor:
                contenido.append({"label": f"Función orgánica · {item['label']}", "value": valor})
        for item in getattr(historia, "examen_registrado", []):
            contenido.append({"label": f"Examen físico · {item['label']}", "value": item["value"]})
        contenido.extend(getattr(historia, "datos_tricopigmentacion", []))
        notas.append({
            "id": historia.pk,
            "titulo": historia.get_tipo_display(), "tipo": historia.tipo,
            "fecha": historia.fecha_atencion, "profesional": historia.profesional,
            "estado": historia.get_estado_display(), "bloqueada": historia.bloqueada,
            "creado_por": historia.creado_por,
            "actualizado_por": historia.actualizado_por,
            "fecha_actualizacion": historia.fecha_actualizacion,
            "campos": contenido,
        })
    return notas


def respuesta_pacientes_app(request, empresa, response):
    if es_pacientes_app(request, empresa):
        response["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response["Pragma"] = "no-cache"
        response["Expires"] = "0"
    return response


def render_pacientes_app(request, empresa, web_template, mobile_template, contexto):
    if es_pacientes_app(request, empresa):
        contexto.update(contexto_pacientes_app(request, empresa, contexto.get("paciente"), contexto.get("form")))
        from .mobile_histories import TIPOS_HISTORIA_EDITABLES_APP

        paciente = contexto.get("paciente")
        if paciente and contexto["can_edit_history"]:
            for nota in contexto.get("mobile_history_notes", []):
                if nota["tipo"] in TIPOS_HISTORIA_EDITABLES_APP and not nota["bloqueada"]:
                    nota["edit_url"] = f"{reverse('clinica_editar_historia_especialidad', args=[empresa.slug, paciente.pk, nota['id']])}?app=1"
                    if nota["tipo"] in {"evaluacion_integral", "tricopigmentacion"}:
                        nota["edit_label"] = "Editar textos y fecha"
        template_name = mobile_template
    else:
        template_name = web_template
    return respuesta_pacientes_app(request, empresa, render(request, template_name, contexto))


def volver_paciente_app(request, empresa, paciente):
    if not es_pacientes_app(request, empresa):
        return redirect("clinica_paciente_detalle", empresa_slug=empresa.slug, paciente_id=paciente.pk)
    contexto = contexto_pacientes_app(request, empresa, paciente)
    for flag, destino in (
        ("can_view_patient", "app_patient_detail_url"),
        ("can_view_history", "app_patient_history_url"),
        ("can_view_patients", "app_patients_url"),
        ("can_edit_patient", "app_patient_edit_url"),
        ("can_create_patient", "app_patient_create_url"),
    ):
        if contexto[flag]:
            return redirect(contexto[destino])
    return redirect(contexto["app_modules_url"])
