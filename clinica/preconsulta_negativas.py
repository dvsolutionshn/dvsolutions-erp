"""Declaraciones del paciente: nunca inferir una negativa de una omisión."""

from copy import deepcopy

from django import forms


NEGATIVAS_POR_PREGUNTA = {
    "diagnostico_medico": {"valor": "no", "texto": "Niego tener condiciones diagnosticadas por un médico"},
    "alergias_respuesta": {"valor": "no", "texto": "Niego tener alergias"},
    "medicamentos_respuesta": {"valor": "no", "texto": "Niego tomar medicamentos de uso habitual"},
    "antecedentes_hospitalarios": {"valor": "no", "texto": "Niego hospitalizaciones, accidentes, fracturas o cirugías previas"},
    "quirurgicos_operado": {"valor": "no", "texto": "Niego cirugías previas"},
    "consumo_riesgo": {"valor": "no", "texto": "Niego hábitos de riesgo o consumo actual"},
    "dieta": {"valor": "no", "texto": "No realizo dieta"},
    "ejercicio": {"valor": "no", "texto": "No realizo ejercicio o actividad física"},
    "antecedentes_familiares": {"valor": "no_aplica", "texto": "Niego antecedentes familiares de estas enfermedades"},
    "riesgo_tromboembolico": {"valor": "ninguno", "texto": "Niego los antecedentes o factores de riesgo enumerados"},
    "evaluacion_psicologica": {"valor": "ninguna", "texto": "Niego ansiedad, depresión u otra condición psicológica o emocional"},
    "gine_embarazada": {"valor": "no", "texto": "Niego estar embarazada"},
    "gine_lactancia": {"valor": "no", "texto": "No me encuentro en período de lactancia"},
    "gine_mamografia": {"valor": "no", "texto": "Niego mamografías o ultrasonidos mamarios previos"},
    "decision_cirugia": {"valor": "no_decidido", "texto": "Aún no he decidido operarme"},
}

NEGATIVAS_TEXTO = {
    "antecedentes_infecciosos": "Niego antecedentes de enfermedades infecciosas, incluido COVID-19",
    "riesgo_tromboembolico_otros": "Niego otros factores de riesgo tromboembólico",
    "decision_cirugia_otros": "No hay otras personas involucradas en mi decisión",
    "gine_menarca": "No he tenido mi primera menstruación",
    "gine_gestas": "Niego embarazos previos",
    "gine_partos": "Niego partos vaginales previos",
    "gine_cesareas": "Niego cesáreas previas",
    "gine_abortos": "Niego pérdidas o interrupciones del embarazo",
    "gine_ultima_menstruacion": "No he presentado menstruación",
}

DETALLES_AFIRMATIVOS = {
    "diagnostico_medico": "diagnostico_medico_detalle",
    "alergias_respuesta": "alergias_detalle",
    "medicamentos_respuesta": "medicamentos_detalle",
    "antecedentes_hospitalarios": "antecedentes_hospitalarios_detalle",
    "quirurgicos_operado": "quirurgicos_detalle",
    "consumo_riesgo": "consumo_riesgo_detalle",
    "antecedentes_familiares": "antecedentes_familiares_detalle",
    "evaluacion_psicologica": "evaluacion_psicologica_detalle",
}


def valores_enviados(form, nombre):
    clave = form.add_prefix(nombre)
    if hasattr(form.data, "getlist"):
        return form.data.getlist(clave)
    valor = form.data.get(clave)
    return list(valor) if isinstance(valor, (list, tuple)) else ([] if valor is None else [valor])


def configurar_negativas(form):
    sexo = getattr(form.paciente, "sexo_normalizado", None)
    form.mostrar_ginecologia = True if sexo == "femenino" else False if sexo == "masculino" else None
    form.fields["version_declaracion"] = forms.CharField(
        required=True, initial="1", widget=forms.HiddenInput, label="Formulario actualizado",
        error_messages={"required": "Recargue el formulario para confirmar las respuestas explícitas del paciente."},
    )
    for nombre, campo in form.fields.items():
        for atributo in ("data-no-aplica-name", "data-no-aplica-selected"):
            campo.widget.attrs.pop(atributo, None)
        if nombre.startswith("gine_") and form.mostrar_ginecologia is False:
            campo.required = False
            campo.disabled = True
            # Se conserva el JSON original al guardar, sin validar en secreto
            # controles que el paciente no puede ver ni corregir.
            form.initial.pop(nombre, None)
            campo.initial = None

    for nombre, negativa in NEGATIVAS_POR_PREGUNTA.items():
        campo = form.fields[nombre]
        opciones = list(campo.choices)
        if nombre == "riesgo_tromboembolico":
            opciones = [(valor, texto) for valor, texto in opciones if valor != "no_aplica"]
        if nombre == "decision_cirugia":
            opciones.append((negativa["valor"], negativa["texto"]))
        campo.choices = [(valor, negativa["texto"] if valor == negativa["valor"] else texto) for valor, texto in opciones]
        campo.widget.attrs["data-negativa-value"] = negativa["valor"]
        campo.required = not nombre.startswith("gine_") or form.mostrar_ginecologia is True
        if campo.required:
            campo.widget.attrs["data-respuesta-explicita"] = "true"
        if not form.is_bound:
            # Ni los valores predeterminados ni una respuesta antigua prueban
            # que el paciente haya confirmado esta nueva declaración.
            form.initial.pop(nombre, None)
            campo.initial = None

    for nombre, _etiqueta in form.FUNCIONES_ORGANICAS_CAMPOS:
        form.fields[nombre].required = True
        form.fields[nombre].widget.attrs["data-respuesta-explicita"] = "true"
        if not form.is_bound:
            form.initial.pop(nombre, None)
            form.fields[nombre].initial = None
    for nombre in ("motivo_consulta", "funciones_detalle", *DETALLES_AFIRMATIVOS.values(), *NEGATIVAS_TEXTO):
        campo = form.fields[nombre]
        if not campo.disabled and (not nombre.startswith("gine_") or form.mostrar_ginecologia is True):
            campo.widget.attrs["data-respuesta-explicita"] = "true"
    for nombre, etiqueta in NEGATIVAS_TEXTO.items():
        campo = form.fields[nombre]
        if campo.disabled:
            continue
        marcador = form.add_prefix(f"no_aplica_{nombre}")
        campo.widget.attrs.update({"data-no-aplica-name": marcador, "data-no-aplica-label": etiqueta})
        if (form.is_bound and valores_enviados(form, f"no_aplica_{nombre}") == ["1"]
                and not any(str(valor).strip() for valor in valores_enviados(form, nombre))):
            campo.widget.attrs["data-no-aplica-selected"] = "true"
    form._negativas_declaradas = {}


def validar_negativas(form, datos):
    if valores_enviados(form, "version_declaracion") != ["1"]:
        form.add_error("version_declaracion", "Recargue el formulario y confirme las respuestas antes de enviarlo.")

    def error(nombre, mensaje):
        if nombre not in form.errors:
            form.add_error(nombre, mensaje)

    def valores(nombre):
        valor = datos.get(nombre)
        return valor if isinstance(valor, list) else ([] if valor in (None, "") else [valor])

    for nombre, _etiqueta in form.FUNCIONES_ORGANICAS_CAMPOS:
        if len(valores_enviados(form, nombre)) > 1:
            error(nombre, "Seleccione una sola respuesta para esta función.")

    marcados = set()
    for clave in form.data:
        prefijo = form.add_prefix("no_aplica_")
        if not clave.startswith(prefijo):
            continue
        nombre = clave[len(prefijo):]
        if nombre not in NEGATIVAS_TEXTO or nombre not in form.fields or form.fields[nombre].disabled:
            error(nombre if nombre in form.fields else None, "La negativa no corresponde a esta pregunta; revise su respuesta.")
            continue
        if valores_enviados(form, f"no_aplica_{nombre}") != ["1"]:
            error(nombre, "Confirme una sola respuesta válida para esta pregunta.")
            continue
        if (datos.get(nombre) or "").strip():
            error(nombre, "Seleccione una negativa o escriba su respuesta, no ambas.")
            continue
        marcados.add(nombre)
        datos[nombre] = NEGATIVAS_TEXTO[nombre]
        form._negativas_declaradas[nombre] = {"valor": "niega", "texto": NEGATIVAS_TEXTO[nombre]}

    for nombre, negativa in NEGATIVAS_POR_PREGUNTA.items():
        if form.fields[nombre].disabled:
            continue
        seleccion = valores(nombre)
        recibidos = valores_enviados(form, nombre)
        if len(recibidos) != len(set(recibidos)) or (not isinstance(form.fields[nombre], forms.MultipleChoiceField) and len(recibidos) > 1):
            error(nombre, "Confirme una sola vez la respuesta de esta pregunta.")
        if negativa["valor"] in seleccion:
            if len(seleccion) != 1:
                error(nombre, "La negativa no puede combinarse con otras respuestas de la misma pregunta.")
            else:
                form._negativas_declaradas[nombre] = dict(negativa)

    def exigir_texto(nombre, permitir_negativa=False):
        valor = (datos.get(nombre) or "").strip()
        if not valor or valor.casefold() == "no aplica":
            error(nombre, "Responda esta pregunta" + (" o marque su declaración explícita." if permitir_negativa else "."))
        if not permitir_negativa and nombre in marcados:
            error(nombre, "Esta respuesta requiere un detalle; no puede omitirse con una negativa.")

    exigir_texto("motivo_consulta")
    exigir_texto("antecedentes_infecciosos", True)
    for respuesta, detalle in DETALLES_AFIRMATIVOS.items():
        seleccion = valores(respuesta)
        if respuesta == "evaluacion_psicologica":
            afirmativa = "otros" in seleccion
        elif respuesta == "antecedentes_familiares":
            afirmativa = bool(seleccion) and "no_aplica" not in seleccion
        else:
            afirmativa = "si" in seleccion
        if afirmativa:
            exigir_texto(detalle)
        elif (datos.get(detalle) or "").strip():
            error(detalle, "El detalle contradice la respuesta seleccionada. Revise esta pregunta.")
    if any(datos.get(nombre) == "alterada" for nombre, _ in form.FUNCIONES_ORGANICAS_CAMPOS):
        exigir_texto("funciones_detalle")
    elif (datos.get("funciones_detalle") or "").strip():
        error("funciones_detalle", "Indique una función alterada para registrar su detalle.")
    for respuesta, detalle, negativa in (
        ("riesgo_tromboembolico", "riesgo_tromboembolico_otros", "ninguno"),
        ("decision_cirugia", "decision_cirugia_otros", "no_decidido"),
    ):
        seleccion = valores(respuesta)
        if seleccion and negativa not in seleccion:
            exigir_texto(detalle, True)
        elif (datos.get(detalle) or "").strip():
            error(detalle, "El detalle contradice la respuesta seleccionada. Revise esta pregunta.")
    if form.mostrar_ginecologia is True:
        for nombre in NEGATIVAS_TEXTO:
            if nombre.startswith("gine_"):
                exigir_texto(nombre, True)
    if form.mostrar_ginecologia is not False and "no" in valores("gine_mamografia") and datos.get("gine_mamografia_fecha"):
        error("gine_mamografia_fecha", "La fecha contradice la respuesta sobre estudios mamarios previos.")


def guardar_negativas(form, datos):
    datos["negativas_explicitas"] = {"version": 1, "respuestas": deepcopy(form._negativas_declaradas)}
    # Las preguntas ginecológicas no mostradas no borran respuestas heredadas.
    if form.mostrar_ginecologia is False and isinstance(form.instance.datos_generales, dict):
        previo = form.instance.datos_generales.get("formulario_general") or {}
        if isinstance(previo, dict):
            datos["formulario_general"].update({nombre: deepcopy(valor) for nombre, valor in previo.items() if nombre.startswith("gine_")})
    return datos
