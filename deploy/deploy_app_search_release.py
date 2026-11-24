#!/usr/bin/env python3
"""Deploy the reviewed APP searches over a copy of the actual active release.

Run as root on the production host after pulling this script. No migrations,
accounts, business data, credentials, CRM timer or unrelated static assets change.
"""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

try:
    from .app_search_overlay import FIX_COMMIT, OverlayError, RUNTIME_FILES, apply_overlay, build_overlay
    from .deploy_inventory_release import (
        BACKUPS, ENV_FILE, LIVE, RELEASES, TIMER, WEB, DeploymentError,
        activate, copy_release, healthy, http_status, inside, property_value, run, state,
    )
except ImportError:
    from app_search_overlay import FIX_COMMIT, OverlayError, RUNTIME_FILES, apply_overlay, build_overlay
    from deploy_inventory_release import (
        BACKUPS, ENV_FILE, LIVE, RELEASES, TIMER, WEB, DeploymentError,
        activate, copy_release, healthy, http_status, inside, property_value, run, state,
    )


DROPINS = Path("/etc/systemd/system/dvsolutions.service.d")
LOCK_FILE = Path("/run/lock/dvsolutions-app-search-deploy.lock")
STATIC_FILES = {
    "crm/static/crm/mobile_invoice_search.js": "crm/mobile_invoice_search.js",
    "crm/static/crm/mobile_appointment_search.css": "crm/mobile_appointment_search.css",
}
APP_ROUTES = tuple(
    f"/{slug}/dashboard/citas/app/"
    for slug in ("hospital_mia", "medical_spa", "luque_aestetic", "serviciosmedicos")
)


def same_code(left, right):
    return left.decode("utf-8-sig").replace("\r\n", "\n") == right.decode("utf-8-sig").replace("\r\n", "\n")


def process_cwd(pid):
    return Path(f"/proc/{pid}/cwd").resolve(strict=True)


@contextmanager
def deployment_lock():
    import fcntl

    if LOCK_FILE.is_symlink():
        raise DeploymentError("El bloqueo de despliegue no es un archivo seguro.")
    with LOCK_FILE.open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise DeploymentError("Ya hay un despliegue de buscadores en curso.") from None
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def runtime_snapshot(active):
    # Payload paths have already been validated by build_overlay.
    return {name: (active / name).read_bytes() if (active / name).is_file() else None for name in RUNTIME_FILES}


def verify_shared_static(active, files):
    root = active / "staticfiles"
    source_root = LIVE / "staticfiles"
    if not root.is_symlink() or root.resolve(strict=True) != source_root.resolve(strict=True):
        raise DeploymentError("La version activa no conserva los archivos estaticos compartidos.")
    shared = root.resolve(strict=True)
    for source, relative in STATIC_FILES.items():
        asset = shared / relative
        if not asset.is_file():
            raise DeploymentError(
                "Faltan archivos del buscador. Ejecuta collectstatic desde /var/www/dvsolutions-erp y reintenta."
            )
        if asset.is_symlink() or asset.resolve(strict=True) != shared / relative:
            raise DeploymentError("Un archivo estatico tiene una ruta inesperada.")
        if not same_code(asset.read_bytes(), files[source]):
            raise DeploymentError(
                "Los archivos del buscador no coinciden. Ejecuta collectstatic desde /var/www/dvsolutions-erp y reintenta."
            )


def django_app_check(release, user, group):
    service = [
        "systemd-run", "--quiet", "--wait", "--pipe", "--collect",
        "--property=WorkingDirectory=" + str(release),
        "--property=EnvironmentFile=" + str(ENV_FILE),
        "--property=User=" + user, "--property=Group=" + group,
        str(LIVE / ".venv/bin/python"),
    ]
    run("Comprobar Django en la copia preparada", service + ["manage.py", "check"])
    code = (
        "import os;os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings');"
        "import django;django.setup();from pathlib import Path;"
        "from django.conf import settings;from django.template.loader import get_template;"
        "from crm import views,mobile_appointment_search;"
        "from crm.views import _cita_pertenece_agenda_espejo,_empresa_origen_agenda_mobile,_profesional_pertenece_agenda_espejo;"
        f"expected=Path({str(release)!r});"
        "assert Path(views.__file__).resolve()==expected/'crm/views.py';"
        "assert Path(mobile_appointment_search.__file__).resolve()==expected/'crm/mobile_appointment_search.py';"
        "templates=['agenda_mobile.html','mobile_invoice_search.html','mobile_appointment_search.html','mobile_agenda_navigation.html'];"
        "assert all(Path(get_template('crm/'+name).origin.name).resolve()==expected/'crm/templates/crm'/name for name in templates);"
        "assert Path(settings.STATIC_ROOT).resolve()==(expected/'staticfiles').resolve();"
        "print('APP_SEARCH_RUNTIME_OK')"
    )
    result = run("Verificar origen real de modulos y templates", service + ["-c", code])
    if "APP_SEARCH_RUNTIME_OK" not in result:
        raise DeploymentError("La copia no cargo los modulos y templates revisados.")


def activate_app(release, active, dropin, timer_before, webhook_before, routes_before):
    activate(release, dropin, timer_before, webhook_before, commit=FIX_COMMIT)
    routes_after = {path: http_status(path) for path in APP_ROUTES}
    if routes_after == routes_before:
        return routes_after
    # Existing activate covers the web/webhook/timer. Restore its own newly
    # created override as well if any of the four APP entrypoints regress.
    expected = f"# Commit {FIX_COMMIT}\n[Service]\nWorkingDirectory={release}\n"
    if dropin.is_symlink() or dropin.read_text(encoding="utf-8") != expected:
        raise DeploymentError("Una ruta de app cambio y el override tambien; revisar servicio antes de continuar.")
    dropin.unlink()
    run("Restaurar configuracion anterior del servicio", ["systemctl", "daemon-reload"])
    run("Restaurar proceso anterior", ["systemctl", "restart", WEB])
    restored = healthy() and Path(property_value("WorkingDirectory")) == active
    raise DeploymentError(f"Una ruta de la app fallo. Version anterior restaurada: {restored}.")


def deploy():
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise DeploymentError("Este actualizador requiere Linux y usuario root.")
    if LIVE.is_symlink() or LIVE.resolve(strict=True) != LIVE:
        raise DeploymentError("El repositorio de despliegue no es una ruta segura.")
    if RELEASES.is_symlink() or RELEASES.resolve(strict=True) != RELEASES:
        raise DeploymentError("El directorio de versiones no es una ruta segura.")
    if BACKUPS.is_symlink() or BACKUPS.resolve() != BACKUPS:
        raise DeploymentError("El directorio de respaldos no es una ruta segura.")
    if DROPINS.is_symlink() or DROPINS.resolve() != DROPINS:
        raise DeploymentError("El directorio del servicio no es una ruta segura.")

    with deployment_lock():
        active = inside(Path(property_value("WorkingDirectory")), RELEASES)
        if state(WEB) != "active" or not ENV_FILE.is_file():
            raise DeploymentError("El servicio o su archivo de entorno no estan disponibles.")
        pid = int(property_value("MainPID"))
        if pid <= 0 or process_cwd(pid) != active:
            raise DeploymentError("El proceso activo no coincide con la carpeta configurada.")
        if not healthy():
            raise DeploymentError("El servicio actual no supero el control HTTP; no se modifico.")

        files = build_overlay(active, LIVE)
        verify_shared_static(active, files)
        original = runtime_snapshot(active)
        if all(original[name] is not None and same_code(original[name], content) for name, content in files.items()):
            print(json.dumps({"status": "ALREADY_ACTIVE", "release": str(active), "app_search_commit": FIX_COMMIT}))
            return

        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        release = RELEASES / f"app-search-{stamp}-{FIX_COMMIT[:8]}"
        backup = BACKUPS / f"app-search-{stamp}-{FIX_COMMIT[:8]}"
        dropin = DROPINS / f"99-zz-app-search-{stamp}.conf"
        if release.exists() or backup.exists() or dropin.exists() or dropin.is_symlink():
            raise DeploymentError("La copia, respaldo u override ya existe; no se sobrescribio.")
        timer_before = state(TIMER)
        webhook_before = http_status("/integraciones/whatsapp/webhook/")
        routes_before = {path: http_status(path) for path in APP_ROUTES}
        if any(not 200 <= status < 400 for status in routes_before.values()):
            raise DeploymentError("Una ruta de la app ya falla; no se modifico el servicio.")
        user, group = property_value("User") or "root", property_value("Group") or "root"

        copy_release(active, release)
        apply_overlay(release, files)
        django_app_check(release, user, group)
        # Refuse a concurrent release activation or changes to the files reviewed.
        if (
            Path(property_value("WorkingDirectory")) != active
            or int(property_value("MainPID")) != pid
            or process_cwd(pid) != active
            or runtime_snapshot(active) != original
        ):
            raise DeploymentError("La version activa cambio durante la preparacion; no se activo la copia.")
        verify_shared_static(active, files)

        backup.mkdir(parents=True, mode=0o700)
        manifest = {
            "previous_release": str(active), "release": str(release),
            "app_search_commit": FIX_COMMIT, "changed_files": sorted(files),
            "dropin": str(dropin), "timer_before": timer_before,
            "migrations": False, "shared_static_changed": False,
        }
        for relative in files:
            old = active / relative
            if old.is_file():
                saved = backup / relative
                saved.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                shutil.copy2(old, saved)
        (backup / "deployment.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        # These unauthenticated GETs check routing, not a user's medical data.
        routes_after = activate_app(release, active, dropin, timer_before, webhook_before, routes_before)
        result = {
            **manifest, "status": "DEPLOY_OK", "web": state(WEB),
            "backup": str(backup), "app_routes": routes_after,
            "app_routes_unchanged": routes_after == routes_before,
        }
        (backup / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result))


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    try:
        deploy()
    except (DeploymentError, OverlayError, OSError, ValueError, subprocess.SubprocessError) as error:
        message = str(error) if isinstance(error, (DeploymentError, OverlayError)) else "Comprobacion fallida; revisar rutas y servicio sin publicar credenciales."
        print("DEPLOY_STOPPED: " + message, file=sys.stderr)
        sys.exit(1)
