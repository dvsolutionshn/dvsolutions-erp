"""Sustitución revisable del catálogo accidental, exclusiva de RUMI."""

import hashlib
import json
from copy import deepcopy
from zipfile import BadZipFile

from django.core.exceptions import ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from .alcance_catalogo import es_empresa_rumi
from .importadores_rumi import (
    _detectar_catalogo, _extraer_catalogo_rumi, _validar_propuesta,
    aplicar_catalogo_cuentas,
)
from .models import (
    ClasificacionMovimientoBanco, ConfiguracionContableEmpresa, CuentaContable,
    CuentaFinanciera, bloquear_empresas_contables,
)
from .services import (
    CATALOGO_BASE_HONDURAS, CLASIFICACIONES_BANCARIAS_BASE_HONDURAS,
    CUENTAS_FINANCIERAS_BASE_HONDURAS,
)

DESCRIPCION_BASE = "Catalogo base Honduras generado por DV Solutions ERP."


def _exigir_rumi(empresa):
    if not es_empresa_rumi(empresa):
        raise ValidationError("La sustitución del catálogo está disponible únicamente en RUMI.")


def _json_seguro(valor):
    return json.loads(json.dumps(valor, cls=DjangoJSONEncoder, sort_keys=True))


def _referencias(objeto, excluir=()):
    referencias = []
    for relacion in objeto._meta.related_objects:
        if not relacion.one_to_many and not relacion.one_to_one:
            continue
        campo = relacion.field
        clave = (relacion.related_model._meta.label_lower, campo.name)
        if clave in excluir:
            continue
        ids = list(relacion.related_model._default_manager.filter(
            **{campo.attname: objeto.pk},
        ).order_by("pk").values_list("pk", flat=True))
        if ids:
            referencias.append({"modelo": clave[0], "campo": clave[1], "ids": ids})
    return referencias


def _estado(empresa):
    cuentas = list(CuentaContable.objects.filter(empresa=empresa).select_related("cuenta_padre").order_by("pk"))
    configuracion = ConfiguracionContableEmpresa.objects.filter(empresa=empresa).first()
    financieras = list(CuentaFinanciera.objects.filter(empresa=empresa).select_related("cuenta_contable").order_by("pk"))
    clasificaciones = list(ClasificacionMovimientoBanco.objects.filter(empresa=empresa).select_related("cuenta_contable").order_by("pk"))
    estado = {
        "cuentas": list(CuentaContable.objects.filter(empresa=empresa).order_by("pk").values()),
        "referencias": {str(c.pk): _referencias(c) for c in cuentas},
        "configuracion": list(ConfiguracionContableEmpresa.objects.filter(empresa=empresa).values()),
        "financieras": list(CuentaFinanciera.objects.filter(empresa=empresa).order_by("pk").values()),
        "referencias_financieras": {str(c.pk): _referencias(c) for c in financieras},
        "clasificaciones": list(ClasificacionMovimientoBanco.objects.filter(empresa=empresa).order_by("pk").values()),
        "referencias_clasificaciones": {str(c.pk): _referencias(c) for c in clasificaciones},
    }
    estado = _json_seguro(estado)
    firma = hashlib.sha256(json.dumps(estado, sort_keys=True).encode()).hexdigest()
    return cuentas, configuracion, financieras, clasificaciones, estado, firma


def leer_catalogo_para_sustitucion(empresa, archivo):
    """Reutiliza el lector RUMI sin confundir las raíces del catálogo accidental."""
    _exigir_rumi(empresa)
    try:
        if hasattr(archivo, "seek"):
            archivo.seek(0)
        libro = load_workbook(archivo, data_only=False)
    except (BadZipFile, InvalidFileException, OSError, ValueError, KeyError) as error:
        raise ValidationError("Suba el catálogo RUMI en un Excel válido .xlsx o .xlsm.") from error
    try:
        hoja, encabezado, formato, indices = _detectar_catalogo(libro)
        if formato != "rumi":
            raise ValidationError("Esta sustitución requiere el formato del catálogo RUMI con Código RUMI y Activación.")
        datos, errores, estadisticas = _extraer_catalogo_rumi(hoja, encabezado, indices, {}, True)
        if errores:
            raise ValidationError(errores)
        _validar_propuesta(empresa, datos, {}, set(), True, validar_unicidad=False)
        return {"datos": datos, "hoja": hoja.title, "fila_encabezado": encabezado, **estadisticas}
    finally:
        libro.close()


def preparar_sustitucion(empresa, datos):
    _exigir_rumi(empresa)
    from .catalogo_rumi_roles import ROLES

    cuentas, configuracion, financieras, clasificaciones, estado, firma = _estado(empresa)
    if configuracion and configuracion.catalogo_rumi_instalado:
        raise ValidationError("RUMI ya utiliza su catálogo. Use la importación normal para agregar cuentas y la configuración para ajustar enlaces.")
    _validar_propuesta(empresa, datos, {}, set(), True, validar_unicidad=False)
    definiciones = {c[0]: c for c in CATALOGO_BASE_HONDURAS}
    base = {
        c.pk: c for c in cuentas
        if c.descripcion == DESCRIPCION_BASE and c.codigo in definiciones
        and (c.nombre, c.tipo) == definiciones[c.codigo][1:3]
        and c.acepta_movimientos == definiciones[c.codigo][4]
        and c.activa and c.actividad_flujo == "auto"
        and (c.cuenta_padre.codigo if c.cuenta_padre_id else "") == definiciones[c.codigo][3]
    }
    financieras_eliminables = []
    for financiera in financieras:
        for nombre, tipo, codigo, institucion in CUENTAS_FINANCIERAS_BASE_HONDURAS:
            if (
                financiera.cuenta_contable_id in base
                and (financiera.nombre, financiera.tipo, financiera.cuenta_contable.codigo, financiera.institucion)
                == (nombre, tipo, codigo, institucion)
                and not financiera.numero and not _referencias(financiera)
            ):
                financieras_eliminables.append(financiera.pk)
    clasificaciones_eliminables = []
    for clasificacion in clasificaciones:
        if clasificacion.cuenta_contable_id in base and (
            clasificacion.nombre, clasificacion.tipo, clasificacion.cuenta_contable.codigo,
        ) in CLASIFICACIONES_BANCARIAS_BASE_HONDURAS and not _referencias(clasificacion):
            clasificaciones_eliminables.append(clasificacion.pk)

    conservar = set()
    for cuenta in base.values():
        for referencia in estado["referencias"][str(cuenta.pk)]:
            modelo = referencia["modelo"]
            if modelo == "contabilidad.configuracioncontableempresa":
                # Sólo se desvincula la configuración de esta empresa.
                if referencia["ids"] == ([configuracion.pk] if configuracion else []):
                    continue
            if modelo == "contabilidad.cuentacontable" and referencia["campo"] == "cuenta_padre":
                if set(referencia["ids"]) <= set(base):
                    continue
            if modelo == "contabilidad.cuentafinanciera" and set(referencia["ids"]) <= set(financieras_eliminables):
                continue
            if modelo == "contabilidad.clasificacionmovimientobanco" and set(referencia["ids"]) <= set(clasificaciones_eliminables):
                continue
            conservar.add(cuenta.pk)
    # Una cuenta propia o con historial conserva también todos sus ancestros.
    por_id = {c.pk: c for c in cuentas}
    for identificador in list(conservar):
        actual, visitadas = por_id[identificador], set()
        while actual.cuenta_padre_id in base and actual.cuenta_padre_id not in visitadas:
            visitadas.add(actual.cuenta_padre_id)
            conservar.add(actual.cuenta_padre_id)
            actual = por_id[actual.cuenta_padre_id]
    eliminar = set(base) - conservar
    codigos = {c.codigo.casefold() for c in cuentas}
    archivadas = {pk: f"HN-HIST-{pk}" for pk in conservar}
    if any(codigo.casefold() in codigos for codigo in archivadas.values()):
        raise ValidationError("Existe un código reservado HN-HIST. Revise las cuentas históricas antes de sustituir el catálogo.")
    existentes = {}
    for cuenta in cuentas:
        if cuenta.pk in eliminar:
            continue
        copia = deepcopy(cuenta)
        if cuenta.pk in archivadas:
            copia.codigo = archivadas[cuenta.pk]
        existentes[copia.codigo] = copia
    copias_por_id = {cuenta.pk: cuenta for cuenta in existentes.values()}
    for copia in existentes.values():
        if copia.cuenta_padre_id in copias_por_id:
            copia.cuenta_padre = copias_por_id[copia.cuenta_padre_id]
    if {item["codigo"].casefold() for item in datos} & {codigo.casefold() for codigo in archivadas.values()}:
        raise ValidationError("El catálogo nuevo ocupa un código reservado para conservar históricos. Revise el archivo.")
    colisiones = sorted({item["codigo"].casefold() for item in datos} & {
        c.codigo.casefold() for c in cuentas if c.pk not in base
    })
    if colisiones:
        raise ValidationError("Hay cuentas propias que ocupan códigos del catálogo RUMI: " + ", ".join(colisiones)
                              + ". Se conservan; revise esas cuentas antes de sustituir.")
    # El archivo debe traer toda su jerarquía; los históricos no se mezclan con ella.
    _validar_propuesta(empresa, datos, existentes, set(), True, validar_unicidad=False)
    por_codigo = {item["codigo"]: item for item in datos}
    roles = []
    for clave, definicion in ROLES.items():
        candidata = por_codigo.get(definicion.get("codigo_sugerido"))
        enlazable = bool(candidata and candidata["activa"] and candidata["acepta_movimientos"]
                         and candidata["tipo"] in definicion["tipos"])
        roles.append({
            "clave": clave, "etiqueta": definicion["etiqueta"],
            "codigo": candidata["codigo"] if candidata else "",
            "nombre": candidata["nombre"] if candidata else "",
            "estado": "Enlazar" if enlazable else "Condicionada: activar y revisar" if candidata else "Pendiente de definir",
            "enlazable": enlazable,
        })
    return {
        "firma": firma, "eliminar_ids": sorted(eliminar), "archivar_codigos": archivadas,
        "financieras_eliminar_ids": financieras_eliminables,
        "clasificaciones_eliminar_ids": clasificaciones_eliminables,
        "eliminadas": len(eliminar), "conservadas": len(conservar), "importables": len(datos),
        "cuentas_base": [{"codigo": c.codigo, "nombre": c.nombre,
                         "accion": "Conservar historial" if c.pk in conservar else "Retirar sin uso"}
                        for c in base.values()],
        "roles": roles, "enlaces": sum(r["enlazable"] for r in roles),
        "pendientes": sum(not r["enlazable"] for r in roles), "respaldo": estado,
    }


def aplicar_sustitucion(empresa, datos, *, firma, usuario=None):
    _exigir_rumi(empresa)
    from .catalogo_rumi_roles import CAMPOS_CONFIG
    from .models import RegistroSustitucionCatalogoRumi, VinculoCuentaOperativaRumi

    with transaction.atomic():
        bloquear_empresas_contables([empresa.pk])
        list(CuentaContable.objects.select_for_update().filter(empresa=empresa).order_by("pk"))
        list(ConfiguracionContableEmpresa.objects.select_for_update().filter(empresa=empresa))
        plan = preparar_sustitucion(empresa, datos)
        if firma != plan["firma"]:
            raise ValidationError("El catálogo o sus enlaces cambiaron desde la revisión. Vuelva a revisar el archivo.")
        base_ids = set(plan["eliminar_ids"]) | set(plan["archivar_codigos"])
        configuracion, _ = ConfiguracionContableEmpresa.objects.get_or_create(empresa=empresa)
        for campo in configuracion._meta.fields:
            if campo.related_model is CuentaContable and getattr(configuracion, campo.attname) in base_ids:
                setattr(configuracion, campo.name, None)
        configuracion.save()
        CuentaFinanciera.objects.filter(empresa=empresa, pk__in=plan["financieras_eliminar_ids"]).delete()
        ClasificacionMovimientoBanco.objects.filter(empresa=empresa, pk__in=plan["clasificaciones_eliminar_ids"]).delete()
        for pk, nuevo_codigo in plan["archivar_codigos"].items():
            cuenta = CuentaContable.objects.get(empresa=empresa, pk=pk)
            codigo_original = cuenta.codigo
            cuenta.codigo = nuevo_codigo
            cuenta.descripcion = (cuenta.descripcion or "") + f"\nHistórico Honduras: código original {codigo_original}. Conserva sus partidas y enlaces."
            cuenta.save(update_fields=["codigo", "descripcion"])
        CuentaContable.objects.filter(empresa=empresa, pk__in=plan["eliminar_ids"]).delete()
        resultado = aplicar_catalogo_cuentas(empresa, datos, actualizar_existentes=False)
        for rol in plan["roles"]:
            if not rol["enlazable"]:
                continue
            cuenta = CuentaContable.objects.get(empresa=empresa, codigo=rol["codigo"])
            campo = CAMPOS_CONFIG.get(rol["clave"])
            if campo:
                # Una configuración propia existente tiene prioridad y se conserva.
                if not getattr(configuracion, campo + "_id"):
                    setattr(configuracion, campo, cuenta)
            else:
                VinculoCuentaOperativaRumi.objects.update_or_create(
                    empresa=empresa, clave=rol["clave"], defaults={"cuenta": cuenta},
                )
        configuracion.catalogo_rumi_instalado = True
        configuracion.save()
        resumen = {
            "importadas": resultado["creadas"], "eliminadas": plan["eliminadas"],
            "historicas_conservadas": plan["conservadas"], "enlaces": plan["enlaces"],
            "pendientes": plan["pendientes"], "roles": plan["roles"],
        }
        registro = RegistroSustitucionCatalogoRumi.objects.create(
            empresa=empresa, usuario=usuario, respaldo=plan["respaldo"], resumen=resumen,
        )
        return {**resumen, "registro_id": registro.pk}
