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
        "can_view_annexes": puede_ver_anexos,
        "can_view_attachments": puede_ver_anexos,
        "can_upload_annexes": puede_subir_anexos,
        "can_view_recipes": permiso("puede_ver_recetas", "puede_expediente_clinico"),
        "can_view_plans": permiso("puede_ver_planes_tratamiento", "puede_tratamientos_clinicos"),
        "can_view_recordatorios": permiso("puede_crear_recordatorios_paciente", "puede_pacientes"),
        "can_view_gastos": gastos_adicionales_habilitados(empresa) and request.user.tiene_permiso_erp("puede_ver_gastos_adicionales", empresa),
    }
    destino_anexos = request.GET.get("destino", "")
    if request.method == "GET" and es_pacientes_app(request, empresa) and (
        (destino_anexos == "anexos" and puede_ver_anexos)
        or (destino_anexos == "crear_anexo" and puede_subir_anexos)
    ):
        contexto["mobile_patient_annex_mode"] = destino_anexos
    if paciente:
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
            "titulo": historia.get_tipo_display(), "tipo": historia.tipo,
            "fecha": historia.fecha_atencion, "profesional": historia.profesional,
            "estado": historia.get_estado_display(), "bloqueada": historia.bloqueada,
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
