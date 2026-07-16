import logging
import threading

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from core.models import Empresa

from .models import Cliente


logger = logging.getLogger(__name__)

EMPRESAS_CLIENTES_COMPARTIDOS = frozenset({
    "luque_aestetic",
    "hospital_mia",
    "medical_spa",
    "serviciosmedicos",
})
EMPRESAS_IDENTIDAD_CLIENTE_OBLIGATORIA = frozenset({"hospital_mia", "medical_spa"})

CAMPOS_GENERALES_COMPARTIDOS = (
    "nombre",
    "rtn",
    "correo",
    "telefono",
    "telefono_whatsapp",
    "fecha_nacimiento",
    "acepta_promociones",
    "canal_preferido",
    "direccion",
    "ciudad",
    "activo",
)

_estado = threading.local()


def empresa_comparte_clientes(empresa):
    return bool(empresa and empresa.slug in EMPRESAS_CLIENTES_COMPARTIDOS)


def _datos_generales(cliente):
    return {campo: getattr(cliente, campo) for campo in CAMPOS_GENERALES_COMPARTIDOS}


def sincronizar_cliente_compartido(cliente):
    if (
        getattr(_estado, "sincronizando", False)
        or not empresa_comparte_clientes(cliente.empresa)
        or (cliente.nombre or "").strip().casefold() == "consumidor final"
    ):
        return {"creados": 0, "actualizados": 0, "conflictos": []}

    resultado = {"creados": 0, "actualizados": 0, "conflictos": []}
    datos = _datos_generales(cliente)
    rtn = (cliente.rtn or "").strip()
    _estado.sincronizando = True
    try:
        with transaction.atomic():
            empresas = Empresa.objects.filter(
                slug__in=EMPRESAS_CLIENTES_COMPARTIDOS
            ).exclude(pk=cliente.empresa_id)
            for empresa in empresas:
                if empresa.slug in EMPRESAS_IDENTIDAD_CLIENTE_OBLIGATORIA and not rtn:
                    resultado["conflictos"].append({
                        "empresa": empresa.slug,
                        "cliente_id": None,
                        "motivo": "No se puede crear el cliente compartido sin identidad/RTN.",
                    })
                    continue

                destino = Cliente.objects.filter(
                    empresa=empresa,
                    perfil_compartido_id=cliente.perfil_compartido_id,
                ).first()

                if not destino and rtn:
                    coincidencias = list(
                        Cliente.objects.filter(empresa=empresa, rtn__iexact=rtn)[:2]
                    )
                    if len(coincidencias) == 1:
                        destino = coincidencias[0]
                        destino.perfil_compartido_id = cliente.perfil_compartido_id

                if not destino:
                    colision_nombre = Cliente.objects.filter(
                        empresa=empresa,
                        nombre__iexact=cliente.nombre.strip(),
                    ).first()
                    if colision_nombre:
                        resultado["conflictos"].append({
                            "empresa": empresa.slug,
                            "cliente_id": colision_nombre.id,
                            "motivo": "Ya existe otro cliente con el mismo nombre y no coincide por identidad/RTN.",
                        })
                        continue
                    destino = Cliente(
                        empresa=empresa,
                        perfil_compartido_id=cliente.perfil_compartido_id,
                        **datos,
                    )
                    destino.save()
                    resultado["creados"] += 1
                    continue

                cambios = []
                if destino.perfil_compartido_id != cliente.perfil_compartido_id:
                    destino.perfil_compartido_id = cliente.perfil_compartido_id
                    cambios.append("perfil_compartido_id")
                for campo, valor in datos.items():
                    if getattr(destino, campo) != valor:
                        setattr(destino, campo, valor)
                        cambios.append(campo)
                if cambios:
                    destino.save(update_fields=cambios)
                    resultado["actualizados"] += 1
    except (ValidationError, IntegrityError) as exc:
        logger.warning(
            "No se pudo sincronizar el cliente compartido %s: %s",
            cliente.pk,
            exc,
        )
        resultado["conflictos"].append({
            "empresa": cliente.empresa.slug,
            "cliente_id": cliente.id,
            "motivo": str(exc),
        })
    finally:
        _estado.sincronizando = False

    return resultado
