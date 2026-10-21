"""Consulta de sólo lectura del diario, siempre delimitada por empresa."""

from datetime import datetime
from decimal import Decimal

from django.core.paginator import Paginator
from django.db.models import Count, DecimalField, Exists, OuterRef, Prefetch, Q, Sum, Value
from django.db.models.functions import Coalesce

from .models import AsientoContable, CuentaContable, LineaAsientoContable


ORDENES = {
    "recientes": ("-fecha", "-id"),
    "antiguos": ("fecha", "id"),
    "importe_desc": ("-importe_orden", "-fecha", "-id"),
    "importe_asc": ("importe_orden", "-fecha", "-id"),
}


def contexto_listado_asientos(empresa, parametros):
    """Valida filtros y carga únicamente las partidas de la página solicitada.

    Exists evita que dos líneas que coinciden multipliquen filas o importes.
    Las cuentas inactivas se conservan como filtro para consultar el histórico.
    """
    filtros = {
        nombre: parametros.get(nombre, "").strip()
        for nombre in ("q", "estado", "fecha_inicio", "fecha_fin", "cuenta", "orden", "por_pagina")
    }
    errores = []
    invalido = False
    if filtros["orden"] not in ORDENES:
        filtros["orden"] = "recientes"
    if filtros["por_pagina"] not in {"25", "50", "100"}:
        filtros["por_pagina"] = "25"

    base = AsientoContable.objects.filter(empresa=empresa)
    resumen = base.aggregate(
        total=Count("id"),
        contabilizados=Count("id", filter=Q(estado="contabilizado")),
        borradores=Count("id", filter=Q(estado="borrador")),
        anulados=Count("id", filter=Q(estado="anulado")),
    )
    cuentas = list(CuentaContable.objects.filter(empresa=empresa).order_by("codigo", "id"))
    cuentas_ids = {str(cuenta.pk) for cuenta in cuentas}
    estados = dict(AsientoContable.ESTADO_CHOICES)
    if filtros["estado"] and filtros["estado"] not in estados:
        errores.append("El estado seleccionado no es válido. Elige un estado de la lista.")
        invalido = True
    if filtros["cuenta"] and filtros["cuenta"] not in cuentas_ids:
        errores.append("La cuenta seleccionada no está disponible en esta empresa.")
        invalido = True

    fechas = {}
    for nombre, etiqueta in (("fecha_inicio", "inicial"), ("fecha_fin", "final")):
        valor = filtros[nombre]
        if not valor:
            continue
        try:
            fecha = datetime.strptime(valor, "%Y-%m-%d").date()
            if fecha.isoformat() != valor:
                raise ValueError
            fechas[nombre] = fecha
        except (ValueError, OverflowError):
            errores.append(f"La fecha {etiqueta} no es válida. Usa el formato año-mes-día.")
            invalido = True
    if len(fechas) == 2 and fechas["fecha_inicio"] > fechas["fecha_fin"]:
        errores.append("La fecha inicial no puede ser posterior a la fecha final.")
        invalido = True

    asientos = base
    if invalido:
        asientos = asientos.none()
    else:
        if filtros["q"]:
            q = filtros["q"]
            lineas_coincidentes = LineaAsientoContable.objects.filter(
                asiento_id=OuterRef("pk"),
            ).filter(
                Q(detalle__icontains=q)
                | (Q(cuenta__empresa=empresa) & (Q(cuenta__codigo__icontains=q) | Q(cuenta__nombre__icontains=q)))
            )
            asientos = asientos.alias(_coincide_linea=Exists(lineas_coincidentes)).filter(
                Q(numero__icontains=q) | Q(descripcion__icontains=q)
                | Q(referencia__icontains=q) | Q(origen_modulo__icontains=q)
                | Q(_coincide_linea=True)
            )
        if filtros["estado"]:
            asientos = asientos.filter(estado=filtros["estado"])
        if filtros["cuenta"]:
            lineas_cuenta = LineaAsientoContable.objects.filter(
                asiento_id=OuterRef("pk"), cuenta_id=filtros["cuenta"], cuenta__empresa=empresa,
            )
            asientos = asientos.alias(_coincide_cuenta=Exists(lineas_cuenta)).filter(_coincide_cuenta=True)
        if "fecha_inicio" in fechas:
            asientos = asientos.filter(fecha__gte=fechas["fecha_inicio"])
        if "fecha_fin" in fechas:
            asientos = asientos.filter(fecha__lte=fechas["fecha_fin"])

    if filtros["orden"].startswith("importe_"):
        importe = DecimalField(max_digits=26, decimal_places=2)
        asientos = asientos.annotate(
            importe_orden=Coalesce(Sum("lineas__debe"), Value(Decimal("0.00")), output_field=importe),
        )
    asientos = asientos.order_by(*ORDENES[filtros["orden"]])
    paginator = Paginator(asientos, int(filtros["por_pagina"]))
    page = paginator.get_page(parametros.get("page"))
    page.object_list = list(page.object_list.prefetch_related(Prefetch(
        "lineas", queryset=LineaAsientoContable.objects.select_related("cuenta").order_by("id"),
        to_attr="lineas_revision",
    )))
    for asiento in page.object_list:
        # Se suman los Decimal originales: ni redondeos binarios de SQLite ni
        # consultas individuales para revisar las partidas de esta página.
        asiento.importe_debe = sum((linea.debe for linea in asiento.lineas_revision), Decimal("0.00"))
        asiento.importe_haber = sum((linea.haber for linea in asiento.lineas_revision), Decimal("0.00"))
        asiento.numero_lineas = len(asiento.lineas_revision)

    def enlace(**cambios):
        consulta = parametros.copy()
        # Sólo conservamos los filtros admitidos; cada enlace parte de sus
        # valores normalizados y no arrastra parámetros arbitrarios.
        for clave in list(consulta):
            if clave not in filtros:
                del consulta[clave]
        for clave, valor in filtros.items():
            if valor:
                consulta[clave] = valor
            elif clave in consulta:
                del consulta[clave]
        for clave, valor in cambios.items():
            if valor is not None and valor != "":
                consulta[clave] = str(valor)
            elif clave in consulta:
                del consulta[clave]
        return "?" + consulta.urlencode()

    paginas = []
    for numero in paginator.get_elided_page_range(page.number, on_each_side=1, on_ends=1):
        if numero == paginator.ELLIPSIS:
            paginas.append({"numero": "…", "url": "", "actual": False})
        else:
            paginas.append({"numero": numero, "url": enlace(page=numero), "actual": numero == page.number})
    claves_resumen = {"": "total", "contabilizado": "contabilizados", "borrador": "borradores", "anulado": "anulados"}
    enlaces_estado = [
        {
            "nombre": nombre, "valor": valor, "url": enlace(estado=valor),
            "activo": filtros["estado"] == valor, "total": resumen[claves_resumen[valor]],
        }
        for valor, nombre in (("", "Todos"), ("contabilizado", "Contabilizados"), ("borrador", "Borradores"), ("anulado", "Anulados"))
    ]
    return {
        "empresa": empresa, "asientos": page.object_list, "resumen_lista": resumen,
        "filtros": filtros, "cuentas_filtro": cuentas, "page_obj": page, "paginator": paginator,
        "paginacion": {
            "anterior_url": enlace(page=page.previous_page_number()) if page.has_previous() else "",
            "siguiente_url": enlace(page=page.next_page_number()) if page.has_next() else "",
            "paginas": paginas,
        },
        "enlaces_estado": enlaces_estado, "errores_filtros": errores,
        "hay_filtros": any(filtros[nombre] for nombre in ("q", "estado", "fecha_inicio", "fecha_fin", "cuenta")),
        "total_resultados": paginator.count,
    }
