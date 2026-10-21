"""Lectura privada de preconsultas existentes, sin formularios ni enlaces públicos."""

from types import SimpleNamespace

from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, render
from django.urls import reverse

from core.access import interfaz_clinica_activa
from core.clinical_permissions import EMPRESAS_CLINICAS_CON_ROLES, rol_clinico_granular
from .models import PreconsultaClinica


def es_preconsultas_app(request, empresa):
    """Fuera de las cuatro clínicas se conserva la vista web original."""
    return bool(
        empresa.slug in EMPRESAS_CLINICAS_CON_ROLES
        and ("app" in request.GET or "app" in request.POST)
    )


def preconsultas_disponibles_app(request, empresa, method="GET"):
    """Conservar el permiso original de cada método, incluido HEAD."""
    usuario = request.user
    if not (
        method in {"GET", "HEAD"}
        and empresa and empresa.slug in EMPRESAS_CLINICAS_CON_ROLES
        and empresa.activa and interfaz_clinica_activa(empresa)
        and getattr(usuario, "is_authenticated", False)
        and usuario.puede_acceder_empresa(empresa)
        and (usuario.is_superuser or empresa.licencia_operativa)
        and empresa.tiene_modulo_activo("clinica_medica")
    ):
        return False
    permiso = "puede_expediente_clinico"
    if rol_clinico_granular(usuario, empresa):
        permiso = "puede_ver_historia_clinica" if method == "GET" else "puede_editar_historia_clinica"
    return usuario.tiene_permiso_erp(permiso, empresa)


def _identificador(valor):
    texto = str(valor)
    if not texto or not texto.isascii() or not texto.isdecimal() or len(texto) > 19:
        raise PermissionDenied("Identificador de preconsulta no válido.")
    numero = int(texto)
    if not 0 < numero <= 9223372036854775807 or str(numero) != texto:
        raise PermissionDenied("Identificador de preconsulta no válido.")
    return numero


def autorizar_preconsulta_app(request, empresa, paciente_id, preconsulta_id=None):
    # Historias por área conserva su permiso de lectura para ambos métodos;
    # sólo la ruta de detalle tiene el mapper histórico HEAD -> editar.
    metodo_permiso = request.method if preconsulta_id is not None else "GET"
    if request.method not in {"GET", "HEAD"} or not preconsultas_disponibles_app(request, empresa, metodo_permiso):
        raise PermissionDenied("No tiene permiso para consultar estas preconsultas.")
    if request.POST or request.FILES or request.body:
        raise PermissionDenied("La consulta de preconsultas no admite un cuerpo de datos.")
    # La lista de historias permite enfocar un área conservando su lectura
    # original. El detalle de preconsulta mantiene app=1 solamente.
    permitidos = {"app", "tipo"} if preconsulta_id is None else {"app"}
    if (set(request.GET) - permitidos or "app" not in request.GET
            or request.GET.getlist("app") != ["1"]):
        raise PermissionDenied("Parámetros de preconsulta móvil no válidos.")
    if preconsulta_id is None and "tipo" in request.GET:
        from .mobile_patients import tipo_historia_paciente_app

        tipo_historia_paciente_app(request)
    paciente_numero = _identificador(paciente_id)
    preconsulta_numero = _identificador(preconsulta_id) if preconsulta_id is not None else None
    partes = request.path.strip("/").split("/")
    if "pacientes" in partes:
        indice = partes.index("pacientes") + 1
        if indice < len(partes) and _identificador(partes[indice]) != paciente_numero:
            raise PermissionDenied("Identificador de paciente no válido.")
    if preconsulta_numero is not None and "preconsulta" in partes:
        indice = partes.index("preconsulta") + 1
        if indice < len(partes) and _identificador(partes[indice]) != preconsulta_numero:
            raise PermissionDenied("Identificador de preconsulta no válido.")


def url_preconsulta_app(empresa, paciente, preconsulta_id):
    return f"{reverse('clinica_preconsulta_detalle', args=[empresa.slug, paciente.pk, preconsulta_id])}?app=1"


def enlaces_preconsultas_app(request, empresa, paciente, preconsultas, tipo=None):
    """Validar el conjunto original completo antes de sus counts/JOINs/límite."""
    base = preconsultas.select_related(None)
    if base.exclude(empresa_id=empresa.pk, paciente_id=paciente.pk).exists():
        raise PermissionDenied("El expediente contiene una preconsulta de otra empresa.")
    if tipo:
        base = base.filter(tipo=tipo)
    permitido = preconsultas_disponibles_app(request, empresa)
    etiquetas = dict(PreconsultaClinica.TIPO_CHOICES)
    return [{
        "id": fila["id"], "tipo_label": etiquetas.get(fila["tipo"], fila["tipo"]),
        "fecha_completada": fila["fecha_completada"], "motivo_consulta": fila["motivo_consulta"],
        "url": url_preconsulta_app(empresa, paciente, fila["id"]) if permitido else "",
    } for fila in base.values("id", "tipo", "fecha_completada", "motivo_consulta")[:30]]


def _valor_plano(valor):
    """Sólo estructura de presentación; no validar códigos ni contenido clínico."""
    if valor is None or isinstance(valor, (str, bool, int, float)):
        return True
    return isinstance(valor, (list, tuple)) and all(
        item is None or isinstance(item, (str, bool, int, float)) for item in valor
    )


class _LecturaGeneral(dict):
    def get(self, clave, default=None):
        valor = super().get(clave, default)
        if not _valor_plano(valor):
            raise PermissionDenied("La preconsulta contiene una respuesta con estructura no compatible.")
        return valor


def _selecciones_originales(valor):
    # El organizador original acepta texto o una lista plana. Sus valores vacíos
    # son ausencia; un escalar truthy no iterable falla en _etiquetas_seleccion.
    if not valor:
        return valor
    if not isinstance(valor, (str, list, tuple)) or not _valor_plano(valor):
        raise PermissionDenied("La preconsulta contiene una selección con estructura no compatible.")
    return valor


def _secciones_seguras(preconsulta, secciones_callback):
    datos = preconsulta.datos_generales
    general = datos.get("formulario_general", {}) or {} if isinstance(datos, dict) else {}
    if not isinstance(general, dict):
        raise PermissionDenied("La preconsulta contiene un formulario con estructura no compatible.")
    # El callback original es la única autoridad de campos, etiquetas, choices,
    # grupos y orden. El lector valida exclusivamente las claves que consulta.
    lectura = SimpleNamespace(
        datos_generales={"formulario_general": _LecturaGeneral(general)},
        antecedentes_personales=_selecciones_originales(preconsulta.antecedentes_personales),
        medicamentos_habituales=_selecciones_originales(preconsulta.medicamentos_habituales),
        antecedentes_familiares=_selecciones_originales(preconsulta.antecedentes_familiares),
        antecedentes_infecciosos=preconsulta.antecedentes_infecciosos,
    )
    return secciones_callback(lectura)


def render_preconsulta_app(request, empresa, paciente, preconsulta_id, secciones_callback):
    metadatos = ("id", "empresa_id", "paciente_id", "tipo", "estado", "fecha_completada")
    base = PreconsultaClinica.objects.filter(pk=preconsulta_id, empresa=empresa, paciente=paciente)
    preconsulta = get_object_or_404(base.only(*metadatos))
    secciones = []
    if preconsulta.estado == "completada":
        respuestas = base.filter(estado="completada").only(
            *metadatos, "datos_generales", "antecedentes_personales", "medicamentos_habituales",
            "antecedentes_familiares", "antecedentes_infecciosos",
        ).first()
        if respuestas is None:
            # Una revocación/borrado entre lecturas no abre respuestas ni crea
            # registros. Se vuelve a consultar sólo el estado/identidad propios.
            preconsulta = get_object_or_404(base.only(*metadatos))
        else:
            preconsulta = respuestas
            secciones = _secciones_seguras(preconsulta, secciones_callback)
    modulos = reverse("agenda_mobile_modules_screen", args=[empresa.slug])
    puede_historia = preconsultas_disponibles_app(request, empresa)
    historias = f"{reverse('clinica_historias_especialidad', args=[empresa.slug, paciente.pk])}?app=1" if puede_historia else ""
    contexto = {
        "modo_app": True, "empresa": SimpleNamespace(nombre=empresa.nombre, slug=empresa.slug),
        "paciente": SimpleNamespace(id=paciente.pk, pk=paciente.pk, nombre=paciente.nombre,
                                    expediente_codigo=paciente.expediente_codigo),
        "preconsulta": SimpleNamespace(id=preconsulta.pk, tipo=preconsulta.tipo,
                                       tipo_label=preconsulta.get_tipo_display(), estado=preconsulta.estado,
                                       estado_label=preconsulta.get_estado_display(),
                                       fecha_completada=preconsulta.fecha_completada),
        "secciones_preconsulta": secciones,
        "can_view_history": puede_historia, "can_view_app_modules": True,
        "app_home_disponible": False, "app_base_url": "",
        "app_hide_agenda_navigation": True, "app_hide_patient_navigation": True,
        "app_modules_url": modulos, "app_patient_histories_url": historias,
        "app_preconsulta_url": url_preconsulta_app(empresa, paciente, preconsulta.pk),
        "app_preconsulta_back_url": historias or modulos,
    }
    response = render(request, "clinica/mobile_preconsulta_detail.html", contexto)
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    return response
