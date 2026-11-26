"""Consulta móvil de citas existentes, sin crear una agenda ni permisos nuevos."""

from types import SimpleNamespace
from urllib.parse import urlencode

from django.core.paginator import Paginator
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from core.access import interfaz_clinica_activa
from core.clinical_permissions import EMPRESAS_CLINICAS_CON_ROLES, pacientes_solo_tarjetas

from .models import CitaCliente


def construir_contexto_busqueda_citas_app(
    request, empresa, empresa_agenda, *, es_clinica,
    agenda_solo_consulta, agenda_ver_detalles_clinicos,
):
    """Busca sólo nombres en el ámbito ya autorizado de la agenda de la app.

    La fecha visible del calendario no limita esta consulta: anteriores/futuras
    usa el instante actual. Los resultados son datos de presentación, nunca un
    formulario de edición ni una autorización para otra operación.
    """
    contexto = {
        "citas_busqueda_disponible": False,
        "citas_busqueda_activa": False,
        "citas_busqueda_q": "",
        "citas_busqueda_periodo": "todas",
        "citas_busqueda_page": None,
        "citas_busqueda_total": 0,
        "citas_busqueda_prev_url": "",
        "citas_busqueda_next_url": "",
    }
    usuario = getattr(request, "user", None)
    if not (
        request.method in {"GET", "HEAD"}
        and es_clinica and empresa and empresa_agenda
        and empresa.slug in EMPRESAS_CLINICAS_CON_ROLES
        and interfaz_clinica_activa(empresa) and empresa.activa
        and usuario and usuario.is_authenticated
        and usuario.puede_acceder_empresa(empresa)
        and (usuario.is_superuser or empresa.licencia_operativa)
        and (
            usuario.tiene_permiso_erp("puede_ver_calendario", empresa)
            or usuario.tiene_permiso_erp("puede_citas", empresa)
        )
    ):
        return contexto

    # No se acepta un origen proporcionado por el cliente ni un ámbito distinto
    # del que agenda_mobile ya calcula para este perfil (Recepción es local).
    from .views import _cita_pertenece_agenda_espejo, _empresa_origen_agenda_mobile, _profesional_pertenece_agenda_espejo

    origen = empresa if pacientes_solo_tarjetas(usuario, empresa) else _empresa_origen_agenda_mobile(empresa)
    if empresa_agenda.pk != origen.pk:
        return contexto

    query = " ".join((request.GET.get("citas_q") or "").split())[:100]
    periodo = request.GET.get("citas_periodo") or "todas"
    if periodo not in {"todas", "anteriores", "futuras"}:
        periodo = "todas"
    contexto.update(
        citas_busqueda_disponible=True,
        citas_busqueda_activa=bool(query),
        citas_busqueda_q=query,
        citas_busqueda_periodo=periodo,
    )
    if not query:
        return contexto

    ahora = timezone.now()
    citas = CitaCliente.objects.filter(empresa=empresa_agenda).filter(
        Q(paciente__isnull=False, paciente__empresa=empresa_agenda)
        | Q(paciente__isnull=True, cliente__empresa=empresa_agenda)
    )
    # Una relación histórica inconsistente tampoco permite revelar datos de
    # otro catálogo/empresa. No se reparan ni actualizan esos registros aquí.
    for relacion in ("profesional_salud", "servicio_clinico", "producto"):
        citas = citas.filter(Q(**{relacion + "__isnull": True}) | Q(**{relacion + "__empresa": empresa_agenda}))
    for termino in query.split():
        # Mismo nombre que display_cliente: el paciente tiene prioridad sobre
        # un cliente enlazado. Nunca se busca identidad/contacto/notas privadas.
        citas = citas.filter(
            Q(paciente__isnull=False, paciente__nombre__icontains=termino)
            | Q(paciente__isnull=True, cliente__nombre__icontains=termino)
        )
    if periodo == "anteriores":
        citas = citas.filter(fecha_hora__lt=ahora)
    elif periodo == "futuras":
        citas = citas.filter(fecha_hora__gte=ahora)
    citas = citas.select_related(
        "paciente", "cliente", "servicio_clinico", "producto", "profesional_salud",
    ).only(
        "id", "paciente_id", "cliente_id", "fecha_hora", "estado", "titulo", "virtual",
        "servicio_clinico_id", "producto_id", "profesional_salud_id", "responsable",
        "opcion_servicio", "fase_servicio", "sesion_servicio",
        "paciente__nombre", "cliente__nombre", "servicio_clinico__nombre",
        "producto__nombre", "profesional_salud__nombre",
    ).order_by(*(("fecha_hora", "pk") if periodo == "futuras" else ("-fecha_hora", "-pk")))

    if empresa_agenda.pk != empresa.pk and empresa.slug == "serviciosmedicos":
        # Convertir el predicado original a IDs/nombres elegibles antes de
        # paginar. Sólo se leen nombres del ámbito ya filtrado, no todas las
        # citas históricas. Con profesional asignado, responsable NO da acceso.
        citas = citas.filter(
            Q(profesional_salud__nombre__icontains="Luis") | Q(responsable__icontains="Luis")
        )
        from clinica.models import ProfesionalSalud

        profesionales = [
            pk for pk, nombre in ProfesionalSalud.objects.filter(empresa=empresa_agenda).values_list("pk", "nombre")
            if _profesional_pertenece_agenda_espejo(SimpleNamespace(nombre=nombre), empresa)
        ]
        responsables = [
            nombre for nombre in citas.filter(profesional_salud__isnull=True).order_by().values_list("responsable", flat=True).distinct()
            if _cita_pertenece_agenda_espejo(SimpleNamespace(profesional_salud=None, responsable=nombre), empresa)
        ]
        citas = citas.filter(
            Q(profesional_salud_id__in=profesionales)
            | Q(profesional_salud__isnull=True, responsable__in=responsables)
        )

    pagina = Paginator(citas, 30).get_page(request.GET.get("citas_pagina"))
    mostrar_detalles = bool(agenda_ver_detalles_clinicos and not agenda_solo_consulta)
    inicio = reverse("agenda_mobile", args=[empresa.slug])
    resultados = []
    for cita in pagina.object_list:
        fecha = timezone.localtime(cita.fecha_hora)
        resultados.append({
            "id": cita.pk,
            "nombre": cita.display_cliente,
            "fecha": fecha,
            "estado": cita.estado,
            "estado_label": cita.get_estado_display(),
            "virtual": cita.virtual,
            "servicio": cita.display_servicio if mostrar_detalles else "",
            "profesional": cita.display_responsable if mostrar_detalles else "",
            "es_futura": cita.fecha_hora >= ahora,
            "agenda_url": inicio + "?" + urlencode({
                "vista": "dia", "fecha": fecha.date().isoformat(), "app": "agenda",
            }) + "#agenda-app",
        })
    pagina.object_list = resultados

    def url_pagina(numero):
        # Copiar sólo filtros de presentación conocidos, nunca editar/cita_id ni
        # URLs suministradas por el cliente. Los IDs no se trasladan de empresa.
        filtros = {
            key: request.GET[key] for key in (
                "vista", "fecha", "servicio", "profesional", "estado", "paciente_historial",
            ) if request.GET.get(key)
        }
        filtros.update(app="agenda", citas_q=query, citas_periodo=periodo, citas_pagina=numero)
        return inicio + "?" + urlencode(filtros) + "#agenda-app"

    contexto.update(
        citas_busqueda_page=pagina,
        citas_busqueda_total=pagina.paginator.count,
        citas_busqueda_prev_url=url_pagina(pagina.previous_page_number()) if pagina.has_previous() else "",
        citas_busqueda_next_url=url_pagina(pagina.next_page_number()) if pagina.has_next() else "",
    )
    return contexto
