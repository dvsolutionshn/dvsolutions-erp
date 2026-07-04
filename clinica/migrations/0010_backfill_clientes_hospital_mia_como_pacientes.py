from django.db import migrations


def backfill_clientes_hospital_mia(apps, schema_editor):
    Empresa = apps.get_model("core", "Empresa")
    Cliente = apps.get_model("facturacion", "Cliente")
    Paciente = apps.get_model("clinica", "Paciente")

    empresa = Empresa.objects.filter(slug="hospital_mia").first()
    if not empresa:
        return

    consecutivo = Paciente.objects.filter(empresa_id=empresa.id).count() + 1

    def siguiente_expediente():
        nonlocal consecutivo
        while True:
            codigo = f"MIA-{consecutivo:05d}"
            consecutivo += 1
            if not Paciente.objects.filter(empresa_id=empresa.id, expediente_codigo=codigo).exists():
                return codigo

    clientes = Cliente.objects.filter(empresa_id=empresa.id).order_by("fecha_creacion", "id")
    for cliente in clientes.iterator():
        nombre = (cliente.nombre or "").strip()
        if not nombre or nombre.casefold() == "consumidor final":
            continue

        identidad = (cliente.rtn or "").strip()
        paciente = Paciente.objects.filter(empresa_id=empresa.id, cliente_id=cliente.id).first()
        if not paciente and identidad:
            paciente = Paciente.objects.filter(
                empresa_id=empresa.id,
                identidad__iexact=identidad,
            ).first()
        if not paciente:
            paciente = Paciente.objects.filter(
                empresa_id=empresa.id,
                nombre__iexact=nombre,
            ).first()

        datos = {
            "cliente_id": cliente.id,
            "identidad": identidad,
            "nombre": nombre,
            "fecha_nacimiento": cliente.fecha_nacimiento,
            "telefono": cliente.telefono or "",
            "whatsapp": cliente.telefono_whatsapp or cliente.telefono or "",
            "correo": cliente.correo or "",
            "direccion": cliente.direccion or "",
            "municipio": cliente.ciudad or "",
            "acepta_promociones": cliente.acepta_promociones,
            "activo": cliente.activo,
        }
        if paciente:
            for campo, valor in datos.items():
                setattr(paciente, campo, valor)
            paciente.save(update_fields=list(datos))
            continue

        Paciente.objects.create(
            empresa_id=empresa.id,
            expediente_codigo=siguiente_expediente(),
            tipo_id="dni",
            **datos,
        )


class Migration(migrations.Migration):

    dependencies = [
        ("clinica", "0009_invitacionregistropaciente"),
        ("facturacion", "0057_movimientoinventario_bodega"),
    ]

    operations = [
        migrations.RunPython(backfill_clientes_hospital_mia, migrations.RunPython.noop),
    ]
