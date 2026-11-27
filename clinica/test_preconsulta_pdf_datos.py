"""Datos de prueba del enlace actual sin alterar el contrato del formulario interno."""

from copy import deepcopy


def respuestas_pdf(valores):
    datos = deepcopy(valores)
    datos.update(
        version_declaracion="2",
        condiciones_diagnosticadas=["otras"] if datos.get("diagnostico_medico") == "si" else [],
        funciones_otras_alteraciones="si" if datos.get("funciones_detalle") else "no",
        alergias_farmacos_respuesta=datos.pop("alergias_respuesta", "no"),
        alergias_farmacos_detalle=datos.pop("alergias_detalle", ""),
        alergias_alimentos_respuesta="no", alergias_alimentos_detalle="",
        antecedentes_infecciosos_respuesta="si" if datos.get("antecedentes_infecciosos") else "no",
        gine_gestas_respuesta="si" if datos.get("gine_gestas") not in (None, "", "0") else "no",
        riesgo_tromboembolico=["ninguno"],
    )
    if datos.get("antecedentes_familiares_detalle"):
        datos["antecedentes_familiares"] = [*datos.get("antecedentes_familiares", []), "otra"]
    if datos.pop("no_aplica_antecedentes_infecciosos", None):
        datos.update(antecedentes_infecciosos_respuesta="no", antecedentes_infecciosos="")
    if datos["gine_gestas_respuesta"] == "no":
        for campo in ("gine_partos", "gine_cesareas", "gine_abortos"):
            datos.pop(campo, None)
    for campo in ("gine_gestas", "evaluacion_psicologica", "evaluacion_psicologica_detalle", "decision_cirugia_otros"):
        datos.pop(campo, None)
    return datos
