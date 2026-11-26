#!/usr/bin/env python3
"""Deploy virtual appointments over the actual active release.

Linux/root only. Only two reviewed additive columns; no broad migrate-all, collectstatic, account or CRM timer changes.
Never activates the older source checkout directly. Stops on unknown code.
"""
import argparse
from datetime import datetime, timezone
import json
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys

try:
    from .virtual_appointments_overlay import FIX_COMMIT, OverlayError, RUNTIME_FILES, apply_overlay, build_overlay
    from .deploy_app_search_release import APP_ROUTES, DROPINS, choose_dropin, deployment_lock, process_cwd
    from .deploy_inventory_release import (
        BACKUPS, ENV_FILE, LIVE, RELEASES, WEB, TIMER, DeploymentError,
        activate, copy_release, healthy, http_status, inside, property_value, run, state,
    )
except ImportError:
    from virtual_appointments_overlay import FIX_COMMIT, OverlayError, RUNTIME_FILES, apply_overlay, build_overlay
    from deploy_app_search_release import APP_ROUTES, DROPINS, choose_dropin, deployment_lock, process_cwd
    from deploy_inventory_release import (
        BACKUPS, ENV_FILE, LIVE, RELEASES, WEB, TIMER, DeploymentError,
        activate, copy_release, healthy, http_status, inside, property_value, run, state,
    )


CHECK_ROUTES = (*APP_ROUTES, *(path.replace("/citas/app/", "/citas/") for path in APP_ROUTES))
CODE_ROOTS = ("crm", "clinica", "core", "facturacion", "contabilidad", "rrhh", "config", "deploy", "integraciones")


def code_snapshot(root):
    # Detect concurrent edits to other application code without opening shared
    # media, virtualenvs, environment files, patient uploads or backup contents.
    result = {}
    for name in CODE_ROOTS:
        directory = root / name
        if directory.is_symlink():
            raise DeploymentError("Una carpeta de codigo es un enlace inesperado.")
        if not directory.is_dir():
            continue
        for path in directory.rglob("*"):
            if path.suffix not in {".py", ".html", ".js", ".css"}:
                continue
            if path.is_symlink():
                raise DeploymentError("Un archivo de codigo es un enlace inesperado.")
            if path.is_file():
                result[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def snapshot(root):
    return {name: (root / name).read_bytes() if (root / name).is_file() else None for name in RUNTIME_FILES}


def django_virtual_check(release, user, group):
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
        "from crm.models import CitaCliente;from clinica.models import CitaClinica;"
        "from django.template.loader import get_template;"
        f"expected=Path({str(release)!r});"
        "assert Path(views.__file__).resolve()==expected/'crm/views.py';"
        "templates=['agenda_mobile.html','citas.html','mobile_appointment_search.html','includes/appointment_virtual_badge.html'];"
        "assert all(Path(get_template('crm/'+name).origin.name).resolve()==expected/'crm/templates/crm'/name for name in templates);"
        "assert all(model._meta.get_field('virtual').default is False and model._meta.get_field('virtual').db_default is False for model in (CitaCliente,CitaClinica));"
        "print('VIRTUAL_APPOINTMENT_RUNTIME_OK')"
    )
    if "VIRTUAL_APPOINTMENT_RUNTIME_OK" not in run("Verificar modelos y templates de citas", service + ["-c", code]):
        raise DeploymentError("La copia no cargo los modelos y templates revisados.")


def schema_stage(release, user, group, *, apply=False):
    service = [
        "systemd-run", "--quiet", "--wait", "--pipe", "--collect",
        "--property=WorkingDirectory=" + str(release),
        "--property=EnvironmentFile=" + str(ENV_FILE),
        "--property=User=" + user, "--property=Group=" + group,
        str(LIVE / ".venv/bin/python"),
    ]
    operation = "changed=apply_schema(); pending=[]" if apply else "_,plan=inspect_schema(); changed=[]; pending=[(m.app_label,m.name) for m,_ in plan]"
    code = (
        "import os,json;os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings');"
        "import django;django.setup();from pathlib import Path;"
        "from deploy import virtual_appointments_schema as schema;"
        "from deploy.virtual_appointments_schema import inspect_schema,apply_schema;"
        f"assert Path(schema.__file__).resolve()==Path({str(release)!r})/'deploy/virtual_appointments_schema.py';"
        + operation + ";print('VIRTUAL_SCHEMA_STATUS '+json.dumps({'changed':changed,'pending':pending}))"
    )
    result = run("Aplicar solo los dos campos virtuales" if apply else "Revisar migraciones aditivas", service + ["-c", code])
    lines = [line for line in result.splitlines() if line.startswith("VIRTUAL_SCHEMA_STATUS ")]
    if len(lines) != 1:
        raise DeploymentError("La comprobacion de esquema no devolvio el estado esperado.")
    try:
        summary = json.loads(lines[0].split(" ", 1)[1])
    except ValueError:
        raise DeploymentError("La comprobacion de esquema no devolvio JSON valido.") from None
    if not isinstance(summary, dict) or set(summary) != {"changed", "pending"} or not all(isinstance(summary[key], list) for key in summary):
        raise DeploymentError("La comprobacion de esquema no tiene el formato esperado.")
    return summary



def activate_virtual(release, active, dropin, timer_before, webhook_before, routes_before):
    activate(release, dropin, timer_before, webhook_before, commit=FIX_COMMIT)
    routes_after = {path: http_status(path) for path in CHECK_ROUTES}
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
        original_code = code_snapshot(active)
        user, group = property_value("User") or "root", property_value("Group") or "root"
        if all(original[name] == content for name, content in files.items()):
            if schema_stage(active, user, group)["pending"]:
                raise DeploymentError("El codigo ya esta activo pero falta el esquema; requiere revision antes de continuar.")
            print(json.dumps({"status": "ALREADY_ACTIVE", "release": str(active), "app_virtual_commit": FIX_COMMIT}))
            return
        timer_before = state(TIMER)
        webhook_before = http_status("/integraciones/whatsapp/webhook/")
        routes_before = {path: http_status(path) for path in CHECK_ROUTES}
        if any(not 200 <= status < 400 for status in routes_before.values()):
            raise DeploymentError("Una ruta de app ya falla; no se modifico el servicio.")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        release = RELEASES / f"app-virtual-{stamp}-{FIX_COMMIT[:8]}"
        backup = BACKUPS / f"app-virtual-{stamp}-{FIX_COMMIT[:8]}"
        chosen = choose_dropin(stamp)
        dropin = chosen.with_name(chosen.name.replace("-app-search-", "-app-virtual-"))
        if release.exists() or backup.exists() or dropin.exists() or dropin.is_symlink():
            raise DeploymentError("La version, respaldo u override ya existen; no se sobrescribio.")
        copy_release(active, release)
        if code_snapshot(release) != original_code or code_snapshot(active) != original_code:
            raise DeploymentError("El codigo activo cambio durante la copia; no se activo la version.")
        apply_overlay(release, files, LIVE)
        django_virtual_check(release, user, group)
        schema_before = schema_stage(release, user, group)
        # Concurrent edits or activation invalidate this exact reviewed snapshot.
        if (Path(property_value("WorkingDirectory")) != active or int(property_value("MainPID")) != pid
                or process_cwd(pid) != active or snapshot(active) != original
                or build_overlay(active, LIVE) != files or code_snapshot(active) != original_code):
            raise DeploymentError("La version activa cambio; no se activo la copia.")
        backup.mkdir(parents=True, mode=0o700)
        manifest = {
            "previous_release": str(active), "release": str(release), "app_virtual_commit": FIX_COMMIT,
            "changed_files": sorted(files), "dropin": str(dropin), "timer_before": timer_before,
            "schema_before": schema_before, "shared_static_changed": False,
        }
        for name in files:
            old = active / name
            if old.is_file():
                saved = backup / name
                saved.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                shutil.copy2(old, saved)
        (backup / "deployment.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        schema_after = schema_stage(release, user, group, apply=True)
        if (Path(property_value("WorkingDirectory")) != active or int(property_value("MainPID")) != pid
                or process_cwd(pid) != active or snapshot(active) != original or code_snapshot(active) != original_code):
            raise DeploymentError("La version activa cambio durante la migracion; no se activo la copia. Los campos aditivos se conservan.")
        routes_after = activate_virtual(release, active, dropin, timer_before, webhook_before, routes_before)
        result = {**manifest, "status": "DEPLOY_OK", "backup": str(backup), "web": state(WEB),
                  "app_routes": routes_after, "app_routes_unchanged": routes_after == routes_before, "schema_after": schema_after}
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


