"""Diagnóstico de lectura, limitado siempre a una empresa y sin recalcular documentos."""
from decimal import Decimal

from django.db.models import BooleanField, Case, CharField, Exists, F, OuterRef, Q, Value, When
from django.urls import reverse

from facturacion.models import Factura, NotaCredito, PagoComisionProveedor, PagoFactura
from .models import AsientoContable, LineaAsientoContable, RegularizacionMonedaFactura


ESTADOS_REVISION = {
    "pendiente": "Pendiente de revisión integral",
    "parcial": "Saldo de cliente regularizado; partidas históricas conservadas",
    "integral": "Partidas revisadas",
    "inconsistente": "Fuente inconsistente",
    "sin_asiento": "Documento sin asiento contabilizado",
}
FUENTES = {"factura": "Factura", "pago_factura": "Cobro y comisión",
           "nota_credito": "Nota de crédito", "pago_comision_proveedor": "Pago de comisión"}


def _rango(queryset, campo, filtros):
    if filtros.get("desde"):
        queryset = queryset.filter(**{campo + "__gte": filtros["desde"]})
    if filtros.get("hasta"):
        queryset = queryset.filter(**{campo + "__lte": filtros["hasta"]})
    return queryset


def _fuentes_empresa(empresa):
    # Conservar los cobros anulados como respaldo de sus asientos históricos.
    # Los pendientes operativos se filtran en consulta_documentos_sin_asiento.
    facturas = Factura.objects.filter(empresa=empresa, cliente__empresa=empresa, moneda="USD")
    pagos = PagoFactura.objects.filter(factura__empresa=empresa, factura__cliente__empresa=empresa,
                                      factura__moneda="USD").filter(
        Q(proveedor_comision__isnull=True) | Q(proveedor_comision__empresa=empresa)).filter(
        Q(cuenta_financiera__isnull=True) | Q(cuenta_financiera__empresa=empresa, cuenta_financiera__cuenta_contable__empresa=empresa))
    pagos = pagos.filter(Q(cuenta_financiera_impuesto__isnull=True) | Q(
        cuenta_financiera_impuesto__empresa=empresa, cuenta_financiera_impuesto__cuenta_contable__empresa=empresa))
    notas = NotaCredito.objects.filter(empresa=empresa, cliente__empresa=empresa).filter(
        Q(factura_origen__isnull=True, moneda="USD") | Q(
            factura_origen__empresa=empresa, factura_origen__cliente__empresa=empresa,
            cliente_id=F("factura_origen__cliente_id"), factura_origen__moneda="USD",
            moneda=F("factura_origen__moneda")))
    comisiones = PagoComisionProveedor.objects.filter(comision_origen__factura__empresa=empresa,
        comision_origen__factura__cliente__empresa=empresa, comision_origen__factura__moneda="USD",
        comision_origen__proveedor_comision__empresa=empresa).filter(
        Q(metodo="compensacion", cuenta_financiera__isnull=True)
        | Q(cuenta_financiera__empresa=empresa, cuenta_financiera__cuenta_contable__empresa=empresa))
    return {"factura": facturas, "pago_factura": pagos,
            "nota_credito": notas, "pago_comision_proveedor": comisiones}


def consulta_asientos_historicos(empresa, filtros=None, aplicar_estado=True):
    from .models import RevisionConversionAsiento
    from .estado_conversion import asientos_cobros_anulados_neutralizados

    filtros = filtros or {}
    fuentes = _fuentes_empresa(empresa)
    asientos = _rango(AsientoContable.objects.filter(empresa=empresa,
        conversion_verificada=False, estado="contabilizado"), "fecha", filtros)
    asientos = asientos.exclude(pk__in=asientos_cobros_anulados_neutralizados(empresa, filtros.get("hasta")))
    if filtros.get("fuente"):
        asientos = asientos.filter(documento_tipo=filtros["fuente"])
    integrales = RevisionConversionAsiento.objects.filter(empresa=empresa, origen_id=OuterRef("pk")).filter(
        Q(ajuste__isnull=True) | Q(ajuste__empresa=empresa, ajuste__estado="contabilizado", ajuste__fecha=F("fecha")))
    parciales = RegularizacionMonedaFactura.objects.filter(empresa=empresa, asiento_origen_id=OuterRef("pk"),
        factura__empresa=empresa, cuenta_cliente__empresa=empresa).filter(
        Q(asiento_ajuste__isnull=True) | Q(asiento_ajuste__empresa=empresa, asiento_ajuste__estado="contabilizado"))
    partidas_ajenas = LineaAsientoContable.objects.filter(asiento_id=OuterRef("pk")).exclude(cuenta__empresa=empresa)
    asientos = asientos.annotate(_integral=Exists(integrales), _parcial=Exists(parciales), _partida_ajena=Exists(partidas_ajenas),
        _fuente_valida=Case(*[
            When(documento_tipo=tipo, then=Exists(
                (queryset.exclude(metodo="compensacion") if tipo == "pago_comision_proveedor" else queryset)
                .filter(pk=OuterRef("documento_id"))))
            for tipo, queryset in fuentes.items()
        ], default=Value(False), output_field=BooleanField()))
    asientos = asientos.annotate(estado_revision=Case(
        When(Q(_fuente_valida=False) | Q(_partida_ajena=True), then=Value("inconsistente")),
        When(_integral=True, then=Value("integral")), When(_parcial=True, then=Value("parcial")),
        default=Value("pendiente"), output_field=CharField()))
    if aplicar_estado and filtros.get("estado") and filtros["estado"] != "sin_asiento":
        asientos = asientos.filter(estado_revision=filtros["estado"])
    return asientos.select_related("creado_por").prefetch_related("lineas__cuenta").order_by("fecha", "pk")


def consulta_documentos_sin_asiento(empresa, filtros=None):
    filtros = filtros or {}
    fuentes = _fuentes_empresa(empresa)
    detalles = {
        "factura": ("fecha_emision", "numero_factura", "emision", Q(estado="emitida", total__gt=0)),
        "pago_factura": ("fecha", "factura__numero_factura", "cobro", Q(monto__gt=0) | Q(retencion_isr__gt=0) | Q(retencion_isv__gt=0)
                         | Q(comision_retenida_cliente=True, monto_comision__gt=0)),
        "nota_credito": ("fecha_emision", "numero_nota", "emision", Q(estado="emitida", total__gt=0)),
        "pago_comision_proveedor": ("fecha", "comision_origen__factura__numero_factura", "egreso", Q(monto__gt=0)),
    }
    consultas = []
    for tipo, consulta in fuentes.items():
        if filtros.get("fuente") and filtros["fuente"] != tipo:
            continue
        if tipo == "pago_factura":
            consulta = consulta.filter(anulado=False)
        elif tipo == "pago_comision_proveedor":
            consulta = consulta.filter(comision_origen__anulado=False)
        campo_fecha, referencia, evento, condicion = detalles[tipo]
        publicados = AsientoContable.objects.filter(empresa=empresa, documento_tipo=tipo,
            documento_id=OuterRef("pk"), evento=evento, estado="contabilizado")
        respaldo = Exists(publicados)
        if tipo == "pago_comision_proveedor":
            cobro = AsientoContable.objects.filter(
                empresa=empresa, documento_tipo="pago_factura", documento_id=OuterRef("comision_origen_id"),
                evento="cobro", estado="contabilizado", fecha=OuterRef("fecha"),
            )
            respaldo = Case(
                When(
                    Q(metodo="compensacion", cuenta_financiera__isnull=True,
                      comision_origen__comision_retenida_cliente=True,
                      comision_origen__factura__empresa__slug="digital_planning", monto__gt=0)
                    & Q(monto=F("comision_origen__monto_comision"), fecha=F("comision_origen__fecha"),
                        tipo_cambio=F("comision_origen__tipo_cambio")),
                    then=Exists(cobro),
                ),
                When(metodo="compensacion", then=Value(False)),
                default=respaldo, output_field=BooleanField(),
            )
        consulta = _rango(consulta.filter(condicion), campo_fecha, filtros).annotate(
            _registrado=respaldo).filter(_registrado=False).order_by()
        consultas.append(consulta.annotate(_diag_fecha=F(campo_fecha), _diag_fuente=Value(tipo, output_field=CharField()),
            _diag_objeto=F("pk"), _diag_referencia=F(referencia)).values(
            "_diag_fecha", "_diag_fuente", "_diag_objeto", "_diag_referencia"))
    if not consultas:
        return Factura.objects.none().annotate(_diag_fecha=F("fecha_emision"),
            _diag_fuente=Value("factura", output_field=CharField()), _diag_objeto=F("pk"),
            _diag_referencia=F("numero_factura")).values("_diag_fecha", "_diag_fuente", "_diag_objeto", "_diag_referencia")
    return consultas[0].union(*consultas[1:]).order_by("_diag_fecha", "_diag_fuente", "_diag_objeto")


def resumen_historicos_usd(empresa, filtros=None):
    base = consulta_asientos_historicos(empresa, filtros, aplicar_estado=False)
    resumen = {estado: base.filter(estado_revision=estado).count()
               for estado in ("pendiente", "parcial", "integral", "inconsistente")}
    resumen["sin_asiento"] = consulta_documentos_sin_asiento(empresa, filtros).count()
    return resumen


def datos_fuente(empresa, tipo, documento_id):
    """Resuelve nombres y enlaces sólo después de validar toda la cadena empresarial."""
    consultas = _fuentes_empresa(empresa)
    if tipo not in consultas:
        return None
    documento = consultas[tipo].filter(pk=documento_id).first()
    if not documento:
        return None
    if tipo == "factura":
        nombre, referencia, importe, tasa = documento.cliente.nombre, documento.numero_factura, documento.total, documento.tipo_cambio
        vista, objeto = "ver_factura", documento.pk
    elif tipo == "nota_credito":
        nombre, referencia, importe, tasa = documento.cliente.nombre, documento.numero_nota, documento.total, documento.tipo_cambio
        vista, objeto = "ver_nota_credito", documento.pk
    elif tipo == "pago_factura":
        nombre, referencia, importe = documento.factura.cliente.nombre, documento.factura.numero_factura, documento.total_aplicado
        tasa = documento.tipo_cambio or documento.factura.tipo_cambio
        vista, objeto = "ver_factura", documento.factura_id
    else:
        nombre, referencia, importe = documento.proveedor.nombre, documento.comision_origen.factura.numero_factura, documento.monto
        tasa = documento.tipo_cambio or documento.comision_origen.tipo_cambio or documento.comision_origen.factura.tipo_cambio
        vista, objeto = "ver_factura", documento.comision_origen.factura_id
    moneda = documento.moneda if tipo in {"factura", "nota_credito"} else "USD"
    return {"nombre": nombre, "referencia": referencia or str(documento_id), "importe_original": importe, "moneda": moneda,
            "tipo_cambio": tasa, "url": reverse(vista, args=[empresa.slug, objeto])}


def fila_asiento(empresa, asiento):
    from .models import RevisionConversionAsiento

    fuente = datos_fuente(empresa, asiento.documento_tipo, asiento.documento_id)
    estado = asiento.estado_revision
    integral = RevisionConversionAsiento.objects.filter(empresa=empresa, origen=asiento).select_related("ajuste").first()
    parcial = RegularizacionMonedaFactura.objects.filter(empresa=empresa, asiento_origen=asiento).first()
    return {"fecha": asiento.fecha, "fuente": FUENTES.get(asiento.documento_tipo, "Fuente sin identificar"),
        "documento_tipo": asiento.documento_tipo, "documento_id": asiento.documento_id,
        "referencia": fuente["referencia"] if fuente else "Fuente no disponible en esta empresa",
        "nombre": fuente["nombre"] if fuente else "", "importe_original": fuente["importe_original"] if fuente else None,
        "moneda": fuente["moneda"] if fuente else "",
        "tasa_documento": fuente["tipo_cambio"] if fuente else None, "tasa_asiento": asiento.tipo_cambio,
        "tasa_revisada": parcial.tipo_cambio if parcial else None,
        "estado": estado, "estado_texto": ESTADOS_REVISION[estado], "asiento": asiento,
        "url_fuente": fuente["url"] if fuente else "", "revision_integral": integral,
        "revision_parcial": parcial, "puede_revisar": estado in {"pendiente", "parcial"},
        "debe_hnl": asiento.total_debe, "haber_hnl": asiento.total_haber}


def fila_sin_asiento(empresa, registro):
    tipo, documento_id = registro["_diag_fuente"], registro["_diag_objeto"]
    fuente = datos_fuente(empresa, tipo, documento_id)
    return {"fecha": registro["_diag_fecha"], "fuente": FUENTES[tipo], "documento_tipo": tipo,
        "documento_id": documento_id, "referencia": fuente["referencia"] if fuente else "Fuente no disponible",
        "nombre": fuente["nombre"] if fuente else "", "importe_original": fuente["importe_original"] if fuente else None,
        "moneda": fuente["moneda"] if fuente else "",
        "tasa_documento": fuente["tipo_cambio"] if fuente else None,
        "estado": "sin_asiento", "estado_texto": ESTADOS_REVISION["sin_asiento"],
        "url_fuente": fuente["url"] if fuente else "", "puede_revisar": False}


def iterar_diagnostico(empresa, filtros=None):
    filtros = filtros or {}
    if filtros.get("estado") != "sin_asiento":
        for asiento in consulta_asientos_historicos(empresa, filtros).iterator(chunk_size=100):
            yield fila_asiento(empresa, asiento)
    if filtros.get("estado") in {None, "", "sin_asiento"}:
        for registro in consulta_documentos_sin_asiento(empresa, filtros).iterator(chunk_size=100):
            yield fila_sin_asiento(empresa, registro)


def celda_csv_segura(valor):
    if valor is None:
        return ""
    if isinstance(valor, (Decimal, int)):
        return str(valor)
    valor = str(valor)
    return "'" + valor if valor.lstrip().startswith(("=", "+", "-", "@")) else valor


ENCABEZADOS_CSV = ["Empresa", "Fecha", "Documento", "ID documento", "Referencia", "Cliente o proveedor",
    "Moneda del documento", "Importe documento original", "Tasa documento HNL por unidad original", "Tasa registrada del asiento", "Estado de revisión",
    "Asiento", "Debe registrado HNL", "Haber registrado HNL", "Saldo CxC previo HNL",
    "Saldo CxC revisado HNL", "Diferencia CxC HNL", "Enlace documento"]


def valores_csv(empresa, fila):
    parcial = fila.get("revision_parcial")
    valores = [empresa.slug, fila["fecha"], fila["fuente"], fila["documento_id"], fila["referencia"], fila["nombre"],
        fila.get("moneda"), fila.get("importe_original"), fila.get("tasa_documento"), fila.get("tasa_asiento"), fila["estado_texto"],
        fila.get("asiento").numero if fila.get("asiento") else "", fila.get("debe_hnl"), fila.get("haber_hnl"),
        parcial.saldo_hnl_anterior if parcial else None, parcial.saldo_hnl_corregido if parcial else None,
        parcial.saldo_hnl_corregido - parcial.saldo_hnl_anterior if parcial else None, fila["url_fuente"]]
    return [celda_csv_segura(valor) for valor in valores]
