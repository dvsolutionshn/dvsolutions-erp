from django.db import transaction

from .models import Paciente


EMPRESAS_PACIENTES_COMPARTIDOS = frozenset({
    "hospital_mia",
    "medical_spa",
    "luque_aestetic",
    "serviciosmedicos",
})


def _codigo_expediente_disponible(empresa):
    identificador = f"{empresa.slug or ''} {empresa.nombre or ''}".lower()
    if "mia" in identificador:
        prefijo = "MIA"
    elif "medical" in identificador or "spa" in identificador:
        prefijo = "MMS"
    elif "luque" in identificador:
        prefijo = "LQ"
    elif "serviciosmedicos" in identificador or "servicios medicos" in identificador:
        prefijo = "SM"
    else:
        prefijo = "EXP"
    consecutivo = Paciente.objects.filter(empresa=empresa).count() + 1
    while True:
        codigo = f"{prefijo}-{consecutivo:05d}"
        if not Paciente.objects.filter(empresa=empresa, expediente_codigo=codigo).exists():
            return codigo
        consecutivo += 1


def asegurar_paciente_desde_cliente(cliente):
    if (
        cliente.empresa.slug not in EMPRESAS_PACIENTES_COMPARTIDOS
        or (cliente.nombre or "").strip().casefold() == "consumidor final"
    ):
        return None, False

    identidad = (cliente.rtn or "").strip()
    with transaction.atomic():
        paciente = Paciente.objects.filter(empresa=cliente.empresa, cliente=cliente).first()
        if not paciente and identidad:
            paciente = Paciente.objects.filter(
                empresa=cliente.empresa,
                identidad__iexact=identidad,
            ).first()
        if not paciente and cliente.nombre:
            paciente = Paciente.objects.filter(
                empresa=cliente.empresa,
                nombre__iexact=cliente.nombre.strip(),
            ).first()

        if not cliente.activo:
            if paciente and paciente.activo:
                paciente.activo = False
                if not paciente.cliente_id:
                    paciente.cliente = cliente
                    paciente.save(update_fields=["activo", "cliente", "fecha_actualizacion"])
                else:
                    paciente.save(update_fields=["activo", "fecha_actualizacion"])
            return paciente, False

        datos = {
            "cliente": cliente,
            "identidad": identidad,
            "nombre": cliente.nombre,
            "fecha_nacimiento": cliente.fecha_nacimiento,
            "telefono": cliente.telefono or "",
            "whatsapp": cliente.telefono_whatsapp or cliente.telefono or "",
            "correo": cliente.correo or "",
            "direccion": cliente.direccion or "",
            "municipio": cliente.ciudad or "",
            "acepta_promociones": cliente.acepta_promociones,
            "activo": cliente.activo,
        }
        if not paciente:
            paciente = Paciente.objects.create(
                empresa=cliente.empresa,
                expediente_codigo=_codigo_expediente_disponible(cliente.empresa),
                tipo_id="dni",
                **datos,
            )
            return paciente, True

        cambios = []
        for campo, valor in datos.items():
            if getattr(paciente, campo) != valor:
                setattr(paciente, campo, valor)
                cambios.append(campo)
        if cambios:
            paciente.save(update_fields=cambios + ["fecha_actualizacion"])
        return paciente, False
