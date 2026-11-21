"""Completa la lectura de las respuestas guardadas en el formulario original."""

from .forms import PreconsultaClinicaPublicaForm


CAMPOS_GENERALES_LECTURA = (
    "nombres", "apellidos", "primer_nombre", "segundo_nombre", "primer_apellido", "segundo_apellido",
    "identidad", "fecha_nacimiento", "sexo", "estado_civil", "correo", "telefono_codigo_area", "telefono",
    "direccion", "lugar_nacimiento", "ocupacion", "lugar_trabajo", "informante", "informante_detalle",
    "contacto_emergencia_completo", "contacto_emergencia", "telefono_emergencia", "referido_por", "referido_por_detalle",
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
        _campo_respuesta("funciones_organicas", getattr(preconsulta, "funciones_organicas", ""), "Funciones orgánicas generales"),
        _campo_respuesta("funciones_detalle", getattr(preconsulta, "funciones_detalle", ""), "Detalle de funciones orgánicas"),
        _campo_respuesta("revision_sistemas", getattr(preconsulta, "revision_sistemas", "")),
        _campo_respuesta("revision_sistemas_detalle", getattr(preconsulta, "revision_sistemas_detalle", "")),
    ])
    tiene_consentimiento = getattr(preconsulta, "consentimiento_datos", False)
    hospitalarios = getattr(preconsulta, "antecedentes_hospitalarios", False)
    _agregar_campos(antecedentes, [
        _campo_respuesta("antecedentes_hospitalarios", hospitalarios, "Hospitalizaciones, accidentes, fracturas o cirugías previas")
        if hospitalarios or tiene_consentimiento else None,
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
