"""Alertas del expediente basadas en declaraciones, sin modificar al paciente."""

from django.db.models import F

from core.clinical_permissions import rol_clinico_granular, rol_clinico_v2

from .models import PreconsultaClinica


def alerta_condiciones_paciente(usuario, empresa, paciente):
    """Usar la última declaración diagnóstica; una negativa posterior la retira.

    La información permanece en el formulario original, con su fecha e historial.
    El permiso se comprueba antes de consultar diagnósticos sensibles.
    """
    if paciente.empresa_id != empresa.pk or not usuario.puede_acceder_empresa(empresa):
        return None
    permiso = (
        "puede_ver_antecedentes_clinicos" if rol_clinico_v2(usuario, empresa)
        else "puede_ver_historia_clinica" if rol_clinico_granular(usuario, empresa)
        else "puede_expediente_clinico"
    )
    if not usuario.tiene_permiso_erp(permiso, empresa):
        return None

    from .preconsulta_mejoras_pdf import CONDICIONES_DIAGNOSTICADAS_CHOICES

    etiquetas = dict(CONDICIONES_DIAGNOSTICADAS_CHOICES)
    registros = PreconsultaClinica.objects.filter(
        empresa=empresa, paciente=paciente, estado="completada",
    ).only("id", "datos_generales", "fecha_completada", "fecha_creacion").order_by(
        F("fecha_completada").desc(nulls_last=True), "-fecha_creacion", "-id",
    )
    for registro in registros.iterator():
        datos = registro.datos_generales
        general = datos.get("formulario_general") if isinstance(datos, dict) else None
        if not isinstance(general, dict):
            continue
        respuesta = general.get("diagnostico_medico")
        if respuesta == "no":
            return None
        if respuesta != "si":
            continue
        # No atribuir una lista nueva a respuestas históricas de texto libre.
        seleccion = general.get("condiciones_diagnosticadas")
        if not isinstance(seleccion, list) or any(not isinstance(valor, str) for valor in seleccion):
            return None
        condiciones = [
            etiquetas[valor] for valor in dict.fromkeys(seleccion)
            if valor in etiquetas and valor not in {"otras", "ninguna_anteriores"}
        ]
        detalle = general.get("diagnostico_medico_detalle", "")
        detalle = detalle.strip() if isinstance(detalle, str) and set(seleccion) & {"otras", "ninguna_anteriores"} else ""
        if not condiciones and not detalle:
            return None
        return {
            "condiciones": condiciones, "detalle": detalle,
            "fecha": registro.fecha_completada, "preconsulta_id": registro.pk,
        }
    return None
