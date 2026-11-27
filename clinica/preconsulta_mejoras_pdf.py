"""Preguntas del enlace clínico v2, sin reinterpretar respuestas heredadas."""

from copy import deepcopy

from django import forms


VERSION_FORMULARIO_PUBLICO = "2"
CONDICIONES_DIAGNOSTICADAS_CHOICES = [
    ("hipertension", "Hipertensión arterial"),
    ("diabetes", "Diabetes mellitus tipo 1 o 2"),
    ("resistencia_insulina", "Resistencia a la insulina"),
    ("hipotiroidismo", "Hipotiroidismo"),
    ("hipertiroidismo", "Hipertiroidismo"),
    ("obesidad", "Obesidad"),
    ("cardiacas", "Enfermedades cardíacas"),
    ("asma", "Asma"),
    ("pulmonares", "Enfermedades pulmonares"),
    ("anemia", "Anemia"),
    ("coagulacion", "Trastornos de la coagulación"),
    ("trombosis_venosa_profunda", "Trombosis venosa profunda"),
    ("embolia_pulmonar", "Embolia pulmonar"),
    ("insuficiencia_venosa", "Insuficiencia venosa"),
    ("renal", "Enfermedad renal"),
    ("hepaticas", "Enfermedades hepáticas"),
    ("epilepsia", "Epilepsia"),
    ("fibromialgia", "Fibromialgia"),
    ("artritis_reumatoide", "Artritis reumatoide"),
    ("lupus", "Lupus"),
    ("ovario_poliquistico", "Síndrome de ovario poliquístico"),
    ("cancer", "Cáncer"),
    ("ansiedad", "Ansiedad"),
    ("depresion", "Depresión"),
    ("trastornos_psiquiatricos", "Trastornos psiquiátricos"),
    ("vih", "VIH"),
    ("hepatitis", "Hepatitis B o C"),
    ("cicatrizacion", "Antecedentes de cicatrización"),
    ("otras", "Otras condiciones médicas"),
    ("ninguna_anteriores", "Ninguna de las anteriores"),
]

# Estos controles desaparecen del enlace, pero sus respuestas siguen siendo
# antecedentes consultables y no aceptan modificaciones desde la petición.
CAMPOS_RETIRADOS = frozenset({
    "alergias_respuesta", "alergias_detalle", "gine_gestas",
    "evaluacion_psicologica", "evaluacion_psicologica_detalle", "decision_cirugia_otros",
})
CAMPOS_NUEVOS = (
    "condiciones_diagnosticadas", "funciones_otras_alteraciones",
    "alergias_farmacos_respuesta", "alergias_farmacos_detalle",
    "alergias_alimentos_respuesta", "alergias_alimentos_detalle",
    "antecedentes_infecciosos_respuesta", "gine_gestas_respuesta",
)
NEGATIVAS_NUEVAS = {
    "funciones_otras_alteraciones": {"valor": "no", "texto": "Niego otras alteraciones"},
    "alergias_farmacos_respuesta": {"valor": "no", "texto": "Niego tener alergias a medicamentos"},
    "alergias_alimentos_respuesta": {"valor": "no", "texto": "Niego tener alergias a alimentos"},
    "antecedentes_infecciosos_respuesta": {"valor": "no", "texto": "Niego antecedentes de enfermedades infecciosas, incluido COVID-19"},
    "gine_gestas_respuesta": {"valor": "no", "texto": "Niego embarazos previos"},
}
DETALLES_NUEVOS = {
    "funciones_otras_alteraciones": "funciones_detalle",
    "alergias_farmacos_respuesta": "alergias_farmacos_detalle",
    "alergias_alimentos_respuesta": "alergias_alimentos_detalle",
    "antecedentes_infecciosos_respuesta": "antecedentes_infecciosos",
}
TEXTOS_NEGATIVOS_RETIRADOS = frozenset({
    "antecedentes_infecciosos", "riesgo_tromboembolico_otros", "decision_cirugia_otros", "gine_gestas",
})


def configurar_mejoras_pdf(form):
    si_no = [("si", "Sí"), ("no", "No")]
    preguntas = {
        "funciones_otras_alteraciones": "¿Tiene alguna otra alteración?",
        "alergias_farmacos_respuesta": "¿Tiene alergias a medicamentos? (antibióticos, AINEs, penicilina, diclofenaco u otros)",
        "alergias_alimentos_respuesta": "¿Tiene alergias a alimentos? (mariscos, nueces, maní, piña, almendras u otros)",
        "antecedentes_infecciosos_respuesta": "¿Ha tenido enfermedades infecciosas? (VIH, malaria, chikungunya, herpes, COVID-19 u otras)",
        "gine_gestas_respuesta": "¿Ha estado embarazada alguna vez? (Gestas)",
    }
    for nombre, etiqueta in preguntas.items():
        form.fields[nombre] = forms.ChoiceField(choices=si_no, widget=forms.RadioSelect, label=etiqueta)
    for nombre, etiqueta in (
        ("alergias_farmacos_detalle", "¿A cuáles medicamentos es alérgico/a?"),
        ("alergias_alimentos_detalle", "¿A cuáles alimentos es alérgico/a?"),
    ):
        form.fields[nombre] = forms.CharField(required=False, label=etiqueta, widget=forms.Textarea(attrs={"rows": 3}))
    form.fields["condiciones_diagnosticadas"] = forms.MultipleChoiceField(
        required=False, choices=CONDICIONES_DIAGNOSTICADAS_CHOICES,
        widget=forms.CheckboxSelectMultiple(attrs={
            "class": "animated-check-list", "data-negativa-value": "ninguna_anteriores",
            "data-respuesta-explicita": "true",
        }),
        label="Seleccione las condiciones diagnosticadas por un médico",
    )
    for nombre in CAMPOS_RETIRADOS:
        form.fields.pop(nombre, None)
    form.fields["motivo_consulta"].label = "Motivo principal de consulta (razón por la que viene a consulta)"
    form.fields["diagnostico_medico"].label = (
        "Condiciones diagnosticadas por un médico (hipertensión, cáncer, diabetes, trastornos tiroideos, "
        "epilepsia, ansiedad, depresión, trastornos psiquiátricos, etc.)"
    )
    form.fields["diagnostico_medico_detalle"].label = "Otras condiciones médicas: especifique"
    form.fields["funciones_detalle"].label = "Describa la otra alteración"
    form.fields["consumo_riesgo"].label = (
        "Hábitos de riesgo o consumo actual (cigarrillos, alcohol, marihuana, cocaína, bebidas energéticas "
        "u otras sustancias recreativas)"
    )
    form.fields["antecedentes_familiares"].choices = [
        (valor, "Otros" if valor == "otra" else etiqueta)
        for valor, etiqueta in form.fields["antecedentes_familiares"].choices
    ]
    form.fields["antecedentes_familiares_detalle"].label = "Otros antecedentes familiares: indique familiar y condición"
    riesgo = form.fields["riesgo_tromboembolico"]
    riesgo.choices = [(valor, "Ninguno" if valor == "ninguno" else etiqueta)
                      for valor, etiqueta in riesgo.choices if valor != "no_aplica"] + [("otros", "Otros")]
    form.fields["gine_embarazada"].label = "¿Está embarazada actualmente?"
    form.fields["gine_lactancia"].label = "¿Está dando lactancia en este momento?"


def validar_mejoras_pdf(form, datos, exigir_texto, error):
    for respuesta, detalle in DETALLES_NUEVOS.items():
        if datos.get(respuesta) == "si":
            exigir_texto(detalle)
        elif (datos.get(detalle) or "").strip():
            error(detalle, "El detalle contradice la respuesta seleccionada. Revise esta pregunta.")

    condiciones = datos.get("condiciones_diagnosticadas") or []
    if datos.get("diagnostico_medico") == "si":
        if not condiciones:
            error("condiciones_diagnosticadas", "Seleccione las condiciones diagnosticadas o indique una condición no listada.")
        if "ninguna_anteriores" in condiciones and len(condiciones) > 1:
            error("condiciones_diagnosticadas", "Ninguna de las anteriores no puede combinarse con otras condiciones.")
        necesita_detalle = bool({"otras", "ninguna_anteriores"}.intersection(condiciones))
        if necesita_detalle:
            exigir_texto("diagnostico_medico_detalle")
        elif (datos.get("diagnostico_medico_detalle") or "").strip():
            error("diagnostico_medico_detalle", "Seleccione Otras condiciones médicas para registrar ese detalle.")
    elif condiciones or (datos.get("diagnostico_medico_detalle") or "").strip():
        error("condiciones_diagnosticadas", "Las condiciones seleccionadas contradicen la respuesta sobre diagnóstico médico.")

    for campo, opcion, detalle in (
        ("antecedentes_familiares", "otra", "antecedentes_familiares_detalle"),
        ("riesgo_tromboembolico", "otros", "riesgo_tromboembolico_otros"),
    ):
        if opcion in (datos.get(campo) or []):
            exigir_texto(detalle)
        elif (datos.get(detalle) or "").strip():
            error(detalle, "Seleccione Otros para registrar ese detalle.")

    if form.mostrar_ginecologia is not False:
        for nombre in ("gine_partos", "gine_cesareas", "gine_abortos"):
            if datos.get("gine_gestas_respuesta") == "si":
                exigir_texto(nombre, True)
            elif (datos.get(nombre) or "").strip():
                error(nombre, "La respuesta contradice la declaración sobre embarazos previos.")


def conservar_respuestas_anteriores(form, datos):
    previo = form.instance.datos_generales if isinstance(form.instance.datos_generales, dict) else {}
    general_previo = previo.get("formulario_general") or {}
    general = datos["formulario_general"]
    if isinstance(general_previo, dict):
        for nombre, valor in general_previo.items():
            if nombre not in form.fields:
                general[nombre] = deepcopy(valor)
        for nombre in (
            "diagnostico_medico_detalle", "antecedentes_familiares_detalle", "riesgo_tromboembolico_otros",
            "gine_partos", "gine_cesareas", "gine_abortos",
        ):
            valor = general_previo.get(nombre)
            if valor in (None, "", []):
                valor = form._valores_modelo_historicos.get(nombre)
            if valor not in (None, "", []) and valor != general.get(nombre):
                general.setdefault(f"{nombre}_historico", deepcopy(valor))
    valor_infecciosos = form._valores_modelo_historicos.get("antecedentes_infecciosos")
    if valor_infecciosos and valor_infecciosos != form.cleaned_data.get("antecedentes_infecciosos"):
        general.setdefault("antecedentes_infecciosos_historico", valor_infecciosos)
    datos["version_formulario_publico"] = int(VERSION_FORMULARIO_PUBLICO)
    return datos
