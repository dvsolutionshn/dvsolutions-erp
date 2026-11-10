#!/usr/bin/env python3
"""Apply the reviewed inventory fix to a copy of the running release.

Run as root on the production host. Only the web WorkingDirectory changes.
The current release, CRM jobs, environment, database and shared files are kept.
"""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

try:
    from .inventory_overlay import FIX_COMMIT, OverlayError, apply_overlay, build_overlay
except ImportError:
    from inventory_overlay import FIX_COMMIT, OverlayError, apply_overlay, build_overlay


LIVE = Path("/var/www/dvsolutions-erp")
RELEASES = Path("/var/www/dvsolutions-releases")
BACKUPS = Path("/var/backups/dvsolutions-erp")
ENV_FILE = Path("/etc/dvsolutions-erp.env")
WEB = "dvsolutions.service"
TIMER = "dvsolutions-crm-automatizaciones.timer"


class DeploymentError(Exception):
    pass


def run(label, args, *, cwd=None):
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if result.returncode:
        # Never echo environment values, credentials or raw application output.
        raise DeploymentError(f"{label}: fallo (codigo {result.returncode}).")
    return result.stdout.strip()


def property_value(name):
    return run("Consultar servicio", ["systemctl", "show", WEB, "--property=" + name, "--value"])


def state(unit):
    result = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True)
    return result.stdout.strip() or "unknown"


def inside(path, root):
    resolved = path.resolve(strict=True)
    if resolved == root or root not in resolved.parents:
        raise DeploymentError("La ruta de la version activa no pertenece al directorio de versiones.")
    return resolved


def copy_owned(source, target):
    shutil.copy2(source, target)
    info = os.stat(source, follow_symlinks=False)
    os.chown(target, info.st_uid, info.st_gid, follow_symlinks=False)
    return target


def copy_release(active, release):
    shutil.copytree(
        active, release, symlinks=True, copy_function=copy_owned,
        ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"),
    )
    # copy2 preserves file permissions; directory owners must be retained too.
    for directory, _, _ in os.walk(release, followlinks=False):
        destination = Path(directory)
        info = (active / destination.relative_to(release)).stat()
        os.chown(destination, info.st_uid, info.st_gid, follow_symlinks=False)
    for name in (".venv", "media", "staticfiles"):
        original, copied = active / name, release / name
        if not original.is_symlink() or not copied.is_symlink() or original.resolve() != copied.resolve():
            raise DeploymentError(f"No se conservo el enlace compartido {name}.")


def write_runtime_files(release, files):
    try:
        apply_overlay(release, files)
    except OverlayError as error:
        raise DeploymentError(str(error)) from None


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def http_status(path):
    request = Request("http://127.0.0.1:8001" + path, headers={
        "Host": "dvsolutionshn.com", "X-Forwarded-Proto": "https",
    })
    try:
        with build_opener(NoRedirect).open(request, timeout=5) as response:
            return response.status
    except HTTPError as response:
        return response.code
    except (URLError, TimeoutError, OSError):
        return 0


def healthy():
    for _ in range(15):
        if state(WEB) == "active" and 200 <= http_status("/") < 400:
            return True
        time.sleep(1)
    return False


def django_check(release, user, group):
    run("Comprobar Django en la nueva version", [
        "systemd-run", "--quiet", "--wait", "--pipe", "--collect",
        "--property=WorkingDirectory=" + str(release),
        "--property=EnvironmentFile=" + str(ENV_FILE),
        "--property=User=" + user, "--property=Group=" + group,
        str(LIVE / ".venv/bin/python"), "manage.py", "check",
    ])
    code = (
        "import os;os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings');"
        "import django;django.setup();from pathlib import Path;"
        "from facturacion import views,edicion_lineas;"
        f"expected=Path({str(release)!r});"
        "assert Path(views.__file__).resolve()==expected/'facturacion/views.py';"
        "assert Path(edicion_lineas.__file__).resolve()==expected/'facturacion/edicion_lineas.py';"
        "print('INVENTORY_RUNTIME_OK')"
    )
    result = run("Verificar el origen de los modulos", [
        "systemd-run", "--quiet", "--wait", "--pipe", "--collect",
        "--property=WorkingDirectory=" + str(release),
        "--property=EnvironmentFile=" + str(ENV_FILE),
        "--property=User=" + user, "--property=Group=" + group,
        str(LIVE / ".venv/bin/python"), "-c", code,
    ])
    if "INVENTORY_RUNTIME_OK" not in result:
        raise DeploymentError("Python no cargo los modulos de la version preparada.")


def activate(release, dropin, timer_before, webhook_before, *, commit=FIX_COMMIT):
    dropin.parent.mkdir(parents=True, exist_ok=True)
    if dropin.exists() or dropin.is_symlink():
        raise DeploymentError("El override de inventario ya existe; no se sobrescribio.")
    changed = False
    try:
        # Only this new drop-in is ever removed on rollback.
        with dropin.open("x", encoding="utf-8") as stream:
            changed = True
            stream.write(f"# Commit {commit}\n[Service]\nWorkingDirectory={release}\n")
        dropin.chmod(0o644)
        run("Recargar servicio", ["systemctl", "daemon-reload"])
        if Path(property_value("WorkingDirectory")) != release:
            raise DeploymentError("Otro override impide activar la carpeta preparada.")
        run("Reiniciar servicio web", ["systemctl", "restart", WEB])
        if not healthy():
            raise DeploymentError("La nueva version no supero la comprobacion HTTP.")
        pid = int(property_value("MainPID"))
        if Path(f"/proc/{pid}/cwd").resolve(strict=True) != release:
            raise DeploymentError("Gunicorn sigue ejecutando una carpeta diferente.")
        if webhook_before and http_status("/integraciones/whatsapp/webhook/") != webhook_before:
            raise DeploymentError("Cambio inesperado en la ruta de WhatsApp.")
        if state(TIMER) != timer_before:
            raise DeploymentError("Cambio inesperado en el temporizador CRM.")
    except Exception:
        rollback_healthy = False
        if changed:
            dropin.unlink(missing_ok=True)
            subprocess.run(["systemctl", "daemon-reload"], capture_output=True)
            subprocess.run(["systemctl", "restart", WEB], capture_output=True)
            rollback_healthy = healthy()
        raise DeploymentError(f"No se activo el parche. Servicio anterior restaurado: {rollback_healthy}.") from None


def deploy():
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise DeploymentError("Este despliegue requiere Linux y usuario root.")
    if RELEASES.is_symlink() or RELEASES.resolve() != RELEASES:
        raise DeploymentError("El directorio de versiones no es una ruta segura.")
    active = inside(Path(property_value("WorkingDirectory")), RELEASES)
    if state(WEB) != "active" or not ENV_FILE.is_file():
        raise DeploymentError("El servicio o su archivo de entorno no estan disponibles.")
    pid = int(property_value("MainPID"))
    if Path(f"/proc/{pid}/cwd").resolve(strict=True) != active:
        raise DeploymentError("El directorio configurado no coincide con el proceso activo.")
    files = build_overlay(active, LIVE)
    if all((active / name).is_file() and (active / name).read_bytes() == content for name, content in files.items()):
        print(json.dumps({"status": "ALREADY_ACTIVE", "release": str(active), "inventory_commit": FIX_COMMIT}))
        return

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    release = RELEASES / f"inventario-{stamp}-{FIX_COMMIT[:8]}"
    backup = BACKUPS / f"inventario-{stamp}-{FIX_COMMIT[:8]}"
    if release.exists() or backup.exists() or BACKUPS.is_symlink():
        raise DeploymentError("La carpeta nueva o su respaldo ya existe o no es segura.")
    timer_before = state(TIMER)
    webhook_before = http_status("/integraciones/whatsapp/webhook/")
    user, group = property_value("User") or "root", property_value("Group") or "root"
    copy_release(active, release)
    write_runtime_files(release, files)
    django_check(release, user, group)
    backup.mkdir(parents=True, mode=0o700)
    dropin = Path("/etc/systemd/system/dvsolutions.service.d") / f"99-inventario-{stamp}.conf"
    manifest = {
        "previous_release": str(active), "release": str(release), "inventory_commit": FIX_COMMIT,
        "changed_files": sorted(files), "dropin": str(dropin), "timer_before": timer_before,
    }
    (backup / "deployment.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    for relative in files:
        original = active / relative
        if original.is_file():
            target = backup / relative
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.copy2(original, target)
    activate(release, dropin, timer_before, webhook_before)
    result = {**manifest, "status": "DEPLOY_OK", "web": state(WEB), "backup": str(backup)}
    (backup / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    try:
        deploy()
    except (DeploymentError, OverlayError, OSError, ValueError, subprocess.SubprocessError) as error:
        message = str(error) if isinstance(error, (DeploymentError, OverlayError)) else "Comprobacion fallida; revisar la version sin publicar credenciales."
        print("DEPLOY_STOPPED: " + message, file=sys.stderr)
        sys.exit(1)
