#!/usr/bin/env python3
"""Deploy reviewed APP startup maintenance over the actual active release.

Linux/root only. No migrations, collectstatic, data, secrets or CRM timer changes.
Never activates the older source checkout directly. Stops on unknown code.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

try:
    from .app_startup_overlay import FIX_COMMIT, OverlayError, RUNTIME_FILES, apply_overlay, build_overlay
    from .deploy_app_search_release import APP_ROUTES, DROPINS, choose_dropin, deployment_lock, process_cwd
    from .deploy_inventory_release import (
        BACKUPS, ENV_FILE, LIVE, RELEASES, WEB, TIMER, DeploymentError,
        activate, copy_release, healthy, http_status, inside, property_value, run, state,
    )
except ImportError:
    from app_startup_overlay import FIX_COMMIT, OverlayError, RUNTIME_FILES, apply_overlay, build_overlay
    from deploy_app_search_release import APP_ROUTES, DROPINS, choose_dropin, deployment_lock, process_cwd
    from deploy_inventory_release import (
        BACKUPS, ENV_FILE, LIVE, RELEASES, WEB, TIMER, DeploymentError,
        activate, copy_release, healthy, http_status, inside, property_value, run, state,
    )


def snapshot(root):
    return {name: (root / name).read_bytes() if (root / name).is_file() else None for name in RUNTIME_FILES}


def django_startup_check(release, user, group):
    service = [
        "systemd-run", "--quiet", "--wait", "--pipe", "--collect",
        "--property=WorkingDirectory=" + str(release),
        "--property=EnvironmentFile=" + str(ENV_FILE),
        "--property=User=" + user, "--property=Group=" + group,
        str(LIVE / ".venv/bin/python"),
    ]
    run("Comprobar Django en la copia", service + ["manage.py", "check"])
    code = (
        "import os;os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings');"
        "import django;django.setup();from pathlib import Path;from crm import views;"
        "from django.template.loader import get_template,render_to_string;"
        f"expected=Path({str(release)!r});"
        "assert Path(views.__file__).resolve()==expected/'crm/views.py';"
        "templates=['agenda_mobile.html','mobile_agenda_navigation.html','mobile_app_recovery.html','mobile_app_service_worker.js'];"
        "assert all(Path(get_template('crm/'+name).origin.name).resolve()==expected/'crm/templates/crm'/name for name in templates);"
        "worker=render_to_string('crm/mobile_app_service_worker.js',{'app_home':'/hospital_mia/dashboard/citas/app/'});"
        "assert 'function loadAppEntry' in worker and 'APP_START_TIMEOUT = 20000' in worker;"
        "print('APP_STARTUP_RUNTIME_OK')"
    )
    if "APP_STARTUP_RUNTIME_OK" not in run("Verificar origen y templates de inicio", service + ["-c", code]):
        raise DeploymentError("La copia no cargo los templates de inicio revisados.")


def activate_startup(release, active, dropin, timer_before, webhook_before, routes_before):
    activate(release, dropin, timer_before, webhook_before, commit=FIX_COMMIT)
    routes_after = {path: http_status(path) for path in APP_ROUTES}
    if routes_after == routes_before:
        return routes_after
    expected = f"# Commit {FIX_COMMIT}\n[Service]\nWorkingDirectory={release}\n"
    if dropin.is_symlink() or dropin.read_text(encoding="utf-8") != expected:
        raise DeploymentError("Una ruta fallo y el override cambio; requiere revision del servicio.")
    dropin.unlink()
    run("Restaurar configuracion anterior", ["systemctl", "daemon-reload"])
    run("Restaurar proceso anterior", ["systemctl", "restart", WEB])
    restored = healthy() and Path(property_value("WorkingDirectory")) == active
    raise DeploymentError(f"Una ruta de app fallo. Version anterior restaurada: {restored}.")


def deploy():
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise DeploymentError("Este actualizador requiere Linux y usuario root.")
    for root in (LIVE, RELEASES, BACKUPS, DROPINS):
        if root.is_symlink() or root.resolve() != root:
            raise DeploymentError("Un directorio de despliegue no es una ruta segura.")
    with deployment_lock():
        active = inside(Path(property_value("WorkingDirectory")), RELEASES)
        pid = int(property_value("MainPID"))
        if state(WEB) != "active" or not ENV_FILE.is_file() or pid <= 0 or process_cwd(pid) != active or not healthy():
            raise DeploymentError("El proceso activo no supero las comprobaciones previas; no se modifico.")
        files = build_overlay(active, LIVE)
        original = snapshot(active)
        if all(original[name] == content for name, content in files.items()):
            print(json.dumps({"status": "ALREADY_ACTIVE", "release": str(active), "app_startup_commit": FIX_COMMIT}))
            return
        timer_before = state(TIMER)
        webhook_before = http_status("/integraciones/whatsapp/webhook/")
        routes_before = {path: http_status(path) for path in APP_ROUTES}
        if any(not 200 <= status < 400 for status in routes_before.values()):
            raise DeploymentError("Una ruta de app ya falla; no se modifico el servicio.")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        release = RELEASES / f"app-startup-{stamp}-{FIX_COMMIT[:8]}"
        backup = BACKUPS / f"app-startup-{stamp}-{FIX_COMMIT[:8]}"
        chosen = choose_dropin(stamp)
        dropin = chosen.with_name(chosen.name.replace("-app-search-", "-app-startup-"))
        if release.exists() or backup.exists() or dropin.exists() or dropin.is_symlink():
            raise DeploymentError("La version, respaldo u override ya existen; no se sobrescribio.")
        user, group = property_value("User") or "root", property_value("Group") or "root"
        copy_release(active, release)
        apply_overlay(release, files, LIVE)
        django_startup_check(release, user, group)
        # Concurrent edits or activation invalidate this exact reviewed snapshot.
        if (Path(property_value("WorkingDirectory")) != active or int(property_value("MainPID")) != pid
                or process_cwd(pid) != active or snapshot(active) != original
                or build_overlay(active, LIVE) != files):
            raise DeploymentError("La version activa cambio; no se activo la copia.")
        backup.mkdir(parents=True, mode=0o700)
        manifest = {
            "previous_release": str(active), "release": str(release), "app_startup_commit": FIX_COMMIT,
            "changed_files": sorted(files), "dropin": str(dropin), "timer_before": timer_before,
            "migrations": False, "shared_static_changed": False,
        }
        for name in files:
            old = active / name
            if old.is_file():
                saved = backup / name
                saved.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                shutil.copy2(old, saved)
        (backup / "deployment.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        routes_after = activate_startup(release, active, dropin, timer_before, webhook_before, routes_before)
        result = {**manifest, "status": "DEPLOY_OK", "backup": str(backup), "web": state(WEB),
                  "app_routes": routes_after, "app_routes_unchanged": routes_after == routes_before}
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
