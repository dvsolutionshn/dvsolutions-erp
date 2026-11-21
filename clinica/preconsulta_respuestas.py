"""Completa la lectura de las respuestas guardadas en el formulario original."""

from .forms import PreconsultaClinicaPublicaForm


CAMPOS_GENERALES_LECTURA = (
    "nombres", "apellidos", "primer_nombre", "segundo_nombre", "primer_apellido", "segundo_apellido",
    "identidad", "fecha_nacimiento", "sexo", "estado_civil", "correo", "telefono_codigo_area", "telefono",
    "direccion", "lugar_nacimiento", "ocupacion", "lugar_trabajo", "informante", "informante_detalle",
    "contacto_emergencia_completo", "contacto_emergencia", "telefono_emergencia", "referido_por", "referido_por_detalle",
    "redes_sociales",
)
CAMPOS_MODELO_LECTURA = (
    "motivo_consulta", "funciones_organicas", "funciones_detalle", "revision_sistemas", "revision_sistemas_detalle",
    "antecedentes_hospitalarios", "antecedentes_hospitalarios_detalle", "antecedentes_personales_detalle",
    "medicamentos_habituales_detalle", "antecedentes_familiares_detalle", "dieta", "ejercicio", "habitos",
    "alergias", "consentimiento_datos",
)


def _campo_respuesta(nombre, valor, etiqueta=None):
    if valor is None or valor == "" or valor == []:
        return None
    campo = PreconsultaClinicaPublicaForm.base_fields.get(nombre)
    if isinstance(valor, bool):
        valor = "Sí" if valor else "No"
    opciones = dict(getattr(campo, "choices", ()))
    if opciones:
        seleccion = valor if isinstance(valor, (list, tuple)) else [valor]
        valor = [opciones.get(item, item) for item in seleccion]
    return {
        "label": etiqueta or (str(campo.label) if campo and campo.label else nombre.replace("_", " ").capitalize()),
        "value": valor,
        "type": "chips" if isinstance(valor, (list, tuple)) else "texto",
    }


def _agregar_campos(seccion, campos):
    for campo in campos:
        if campo and campo not in seccion["campos"]:
            seccion["campos"].append(campo)


def completar_secciones_preconsulta(preconsulta, secciones):
    """Conserva las secciones y respuestas históricas del lector existente."""
    datos = preconsulta.datos_generales if isinstance(preconsulta.datos_generales, dict) else {}
    general = datos.get("formulario_general", {}) or {}
    motivo, funciones, antecedentes, habitos, familiares, consentimiento = secciones
    motivo["clave"] = "motivo_consulta"
    funciones["clave"] = "funciones_organicas"
    antecedentes["clave"] = "antecedentes"
    habitos["clave"] = "habitos"
    familiares["clave"] = "familiares"
    consentimiento["clave"] = "consentimiento"
    motivo["campos"].insert(0, _campo_respuesta("motivo_consulta", getattr(preconsulta, "motivo_consulta", ""), "Motivo de Consulta"))
    _agregar_campos(funciones, [
        _campo_respuesta("funciones_organicas", general.get("funciones_organicas_historico"), "Estado histórico de funciones orgánicas"),
        _campo_respuesta("funciones_organicas", getattr(preconsulta, "funciones_organicas", ""), "Funciones orgánicas generales"),
        _campo_respuesta("funciones_detalle", getattr(preconsulta, "funciones_detalle", ""), "Detalle de funciones orgánicas"),
        _campo_respuesta("revision_sistemas", getattr(preconsulta, "revision_sistemas", "")),
        _campo_respuesta("revision_sistemas_detalle", getattr(preconsulta, "revision_sistemas_detalle", "")),
    ])
    tiene_consentimiento = getattr(preconsulta, "consentimiento_datos", False)
    hospitalarios = getattr(preconsulta, "antecedentes_hospitalarios", False)
    _agregar_campos(antecedentes, [
        _campo_respuesta("antecedentes_hospitalarios", hospitalarios, "Hospitalizaciones, accidentes, fracturas o cirugías previas")
        if hospitalarios or (tiene_consentimiento and not registro_inicial_sin_historia(preconsulta)) else None,
        _campo_respuesta("antecedentes_hospitalarios_detalle", getattr(preconsulta, "antecedentes_hospitalarios_detalle", ""), "Hospitalizaciones o cirugías previas"),
        _campo_respuesta("antecedentes_personales_detalle", getattr(preconsulta, "antecedentes_personales_detalle", ""), "Detalle de antecedentes"),
        _campo_respuesta("medicamentos_habituales_detalle", getattr(preconsulta, "medicamentos_habituales_detalle", ""), "Detalle medicamentos habituales"),
        _campo_respuesta("alergias", getattr(preconsulta, "alergias", ""), "Alergias declaradas"),
    ])
    _agregar_campos(habitos, [
        _campo_respuesta("dieta", getattr(preconsulta, "dieta", ""), "Dieta"),
        _campo_respuesta("ejercicio", getattr(preconsulta, "ejercicio", ""), "Ejercicio"),
        _campo_respuesta("habitos", getattr(preconsulta, "habitos", "")),
    ])
    _agregar_campos(familiares, [
        _campo_respuesta("antecedentes_familiares_detalle", getattr(preconsulta, "antecedentes_familiares_detalle", ""), "Detalle familiares"),
    ])
    _agregar_campos(consentimiento, [
        _campo_respuesta("consentimiento_datos", True, "Consentimiento para el uso de la información") if tiene_consentimiento else None,
    ])
    generales = {
        "clave": "datos_generales", "requiere_datos_generales": True,
        "titulo": "Datos generales declarados en este formulario",
        "descripcion": "Respuestas recibidas con este enlace y conservadas en su consulta original.",
        "campos": [],
    }
    componentes = {
        "primer_nombre": "nombres", "segundo_nombre": "nombres",
        "primer_apellido": "apellidos", "segundo_apellido": "apellidos",
        "contacto_emergencia": "contacto_emergencia_completo", "telefono_emergencia": "contacto_emergencia_completo",
    }
    for nombre in CAMPOS_GENERALES_LECTURA:
        if nombre in componentes and datos.get(componentes[nombre]):
            continue
        campo = _campo_respuesta(nombre, datos.get(nombre))
        if campo:
            generales["campos"].append(campo)
    secciones.append(generales)
    secciones.append({
        "clave": "examen_fisico", "titulo": "Examen físico declarado",
        "descripcion": "Mediciones recibidas con este formulario.",
        "campos": [_campo_respuesta(nombre, general.get(nombre)) for nombre in (
            "examen_peso", "examen_talla", "examen_imc", "examen_pa", "examen_fc", "examen_sato2",
        )],
    })
    return secciones


def registro_inicial_sin_historia(preconsulta):
    """Distinguir respuestas del registro básico de una historia completa."""
    datos = preconsulta.datos_generales if isinstance(preconsulta.datos_generales, dict) else {}
    if not datos.get("formulario_general_pendiente_doctor"):
        return False
    general = datos.get("formulario_general") or {}
    iniciales = {"pendiente_doctor_desde_paso", "motivo_categoria", "procedimientos_interes", "procedimientos_interes_otros"}
    if any(valor not in (None, "", []) for clave, valor in general.items() if clave not in iniciales):
        return False
    return not any(
        getattr(preconsulta, campo, None) not in (None, "", [], False)
        for campo in (*CAMPOS_MODELO_LECTURA, "antecedentes_personales", "medicamentos_habituales", "antecedentes_familiares", "antecedentes_infecciosos")
        if campo != "consentimiento_datos"
    )


def bloques_respuestas_paciente(usuario, empresa, paciente, claves=None, *, modo_app=False, tipos=None):
    """Lectura de las respuestas recibidas junto al formulario profesional.

    Usa el mismo lector y alcance del expediente, sin copiar respuestas a una
    nota nueva ni cambiar los datos generales del paciente.
    """
    from types import SimpleNamespace

    from django.core.exceptions import PermissionDenied

    from core.clinical_permissions import datos_paciente_limitados
    from .models import PreconsultaClinica
    from .preconsulta_control import puede_ver_respuestas_preconsulta
    from .views import _secciones_preconsulta_usuario

    if paciente.empresa_id != empresa.pk:
        raise PermissionDenied("El paciente no pertenece a esta empresa.")
    if not puede_ver_respuestas_preconsulta(usuario, empresa):
        return []
    registros = PreconsultaClinica.objects.filter(
        empresa=empresa, paciente=paciente, estado="completada",
    ).select_related("cita").only(
        "id", "empresa_id", "paciente_id", "tipo", "estado", "fecha_completada", "fecha_creacion",
        "cita_id", "cita__id", "cita__empresa_id", "cita__paciente_id", "cita__fecha_hora",
        "datos_generales", "antecedentes_personales", "medicamentos_habituales",
        "antecedentes_familiares", "antecedentes_infecciosos", *CAMPOS_MODELO_LECTURA,
    ).order_by("-fecha_completada", "-fecha_creacion", "-id")
    if tipos is not None:
        registros = registros.filter(tipo__in=tipos)
    bloques = []
    for registro in registros:
        cita = registro.cita if registro.cita_id else None
        if cita and (cita.empresa_id != empresa.pk or cita.paciente_id != paciente.pk):
            raise PermissionDenied("La cita del formulario no corresponde a este expediente.")
        if modo_app:
            from .mobile_preconsultas import _secciones_seguras

            secciones = _secciones_seguras(
                registro, lambda lectura: _secciones_preconsulta_usuario(lectura, usuario, empresa),
                incluir_datos_generales=not datos_paciente_limitados(usuario, empresa),
            )
            etiqueta = registro.get_tipo_display()
            preconsulta = SimpleNamespace(
                id=registro.pk, get_tipo_display=lambda valor=etiqueta: valor,
                fecha_completada=registro.fecha_completada, cita_id=registro.cita_id,
                cita=SimpleNamespace(fecha_hora=cita.fecha_hora) if cita else None,
            )
        else:
            secciones = _secciones_preconsulta_usuario(registro, usuario, empresa)
            preconsulta = registro
        if claves is not None:
            secciones = [seccion for seccion in secciones if seccion.get("clave") in claves]
        if secciones:
            bloques.append({
                "preconsulta": preconsulta, "secciones": secciones,
                "registro_inicial_pendiente": registro_inicial_sin_historia(registro),
            })
    return bloques
