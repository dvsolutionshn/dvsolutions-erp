#!/usr/bin/env python3
"""Instala el control por producto en una copia de la versión web activa."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from textwrap import dedent

from deploy_inventory_release import (
    ENV_FILE, LIVE, RELEASES, TIMER, WEB, DeploymentError, activate,
    copy_release, django_check, http_status, inside, property_value, run, state,
)


MIGRACION = "0084_producto_venta_lotes_vencidos"
ARCHIVOS = (
    "facturacion/models.py",
    "facturacion/views.py",
    "facturacion/templates/facturacion/crear_producto.html",
    f"facturacion/migrations/{MIGRACION}.py",
)


def manage(release, *args):
    usuario = property_value("User") or "root"
    grupo = property_value("Group") or "root"
    return run("Ejecutar Django en la versión preparada", [
        "systemd-run", "--quiet", "--wait", "--pipe", "--collect",
        f"--property=WorkingDirectory={release}", f"--property=EnvironmentFile={ENV_FILE}",
        f"--property=User={usuario}", f"--property=Group={grupo}",
        str(LIVE / ".venv/bin/python"), "manage.py", *args,
    ])


def buscar_producto(release, nombre):
    # Seleccionar solo columnas presentes también en la versión sin la migración.
    codigo = dedent(f"""
        import json
        from facturacion.models import Producto

        def normalizar(texto):
            return ''.join(texto.casefold().split())

        nombre = normalizar({nombre!r})
        candidatos = Producto.objects.filter(
            empresa__activa=True, activo=True, eliminado=False, controla_inventario=True,
        ).values('pk', 'empresa_id', 'empresa__slug', 'nombre')
        print(json.dumps([p for p in candidatos if normalizar(p['nombre']) == nombre]))
    """)
    coincidencias = json.loads(manage(release, "shell", "-c", codigo).splitlines()[-1])
    if len(coincidencias) != 1:
        raise DeploymentError(f"Se encontraron {len(coincidencias)} productos activos con ese nombre; se requiere una coincidencia única.")
    return coincidencias[0]


def aplicar_parche(release, commit):
    diff = subprocess.run([
        "git", "-C", str(LIVE), "diff", "--no-ext-diff", "--binary", f"{commit}^", commit, "--", *ARCHIVOS,
    ], capture_output=True)
    if diff.returncode or not diff.stdout:
        raise DeploymentError("El commit no contiene el parche de vencimientos.")
    for opcion in (["--check"], []):
        resultado = subprocess.run(
            ["git", "apply", *opcion], cwd=release, input=diff.stdout, capture_output=True,
        )
        if resultado.returncode:
            raise DeploymentError("El parche no coincide con el código de la versión activa.")


def deploy(revision, nombre):
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise DeploymentError("El despliegue requiere ejecutarse como root.")
    if RELEASES.is_symlink() or RELEASES.resolve() != RELEASES:
        raise DeploymentError("La carpeta de versiones no es una ruta segura.")
    activa = inside(Path(property_value("WorkingDirectory")), RELEASES)
    pid = int(property_value("MainPID"))
    if state(WEB) != "active" or Path(f"/proc/{pid}/cwd").resolve(strict=True) != activa:
        raise DeploymentError("La versión configurada no coincide con el proceso web activo.")
    commit = run("Identificar el commit", ["git", "-C", str(LIVE), "rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}"])
    run("Verificar que el commit está publicado en main", [
        "git", "-C", str(LIVE), "merge-base", "--is-ancestor", commit, "refs/remotes/origin/main",
    ])
    producto = buscar_producto(activa, nombre)
    marca = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    destino = RELEASES / f"vencimientos-{marca}-{commit[:8]}"
    dropin = Path("/etc/systemd/system/dvsolutions.service.d") / f"99-vencimientos-{marca}.conf"
    temporizador = state(TIMER)
    webhook = http_status("/integraciones/whatsapp/webhook/")

    copy_release(activa, destino)
    aplicar_parche(destino, commit)
    django_check(destino, property_value("User") or "root", property_value("Group") or "root")
    manage(destino, "migrate", "facturacion", MIGRACION, "--noinput")
    activate(destino, dropin, temporizador, webhook, commit=commit)

    codigo = dedent(f"""
        from facturacion.models import Producto

        productos = Producto.objects.filter(
            pk={producto['pk']!r}, empresa_id={producto['empresa_id']!r}, nombre={producto['nombre']!r},
            empresa__activa=True, activo=True, eliminado=False, controla_inventario=True,
        )
        if productos.update(permitir_venta_lotes_vencidos=True) == 1:
            print('PRODUCTO_HABILITADO')
    """)
    if "PRODUCTO_HABILITADO" not in manage(destino, "shell", "-c", codigo):
        raise DeploymentError("La versión está activa, pero no se confirmó el permiso del producto.")
    print(json.dumps({
        "status": "DEPLOY_OK", "release": str(destino), "commit": commit,
        "empresa": producto["empresa__slug"], "producto_id": producto["pk"], "producto": producto["nombre"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("commit")
    parser.add_argument("producto", help="Nombre exacto del único producto que se habilitará")
    opciones = parser.parse_args()
    try:
        deploy(opciones.commit, opciones.producto)
    except (DeploymentError, OSError, ValueError, IndexError) as error:
        detalle = str(error) if isinstance(error, DeploymentError) else "Falló la comprobación de la versión o del producto."
        print("DEPLOY_STOPPED: " + detalle, file=sys.stderr)
        sys.exit(1)
