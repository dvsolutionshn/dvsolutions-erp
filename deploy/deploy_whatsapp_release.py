#!/usr/bin/env python3
"""Deploy an already-reviewed WhatsApp archive without altering the live checkout.

Run as root on the named production host. No dependencies are installed and no
timer is enabled, disabled or started. Database rollback is deliberately absent:
the WhatsApp migration only adds fields/table and remains compatible with old code.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, unquote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


LIVE = Path("/var/www/dvsolutions-erp")
RELEASES = Path("/var/www/dvsolutions-releases")
BACKUPS = Path("/var/backups/dvsolutions-erp")
ENV_FILE = Path("/etc/dvsolutions-erp.env")
WEB = "dvsolutions.service"
JOB = "dvsolutions-crm-automatizaciones.service"
TIMER = "dvsolutions-crm-automatizaciones.timer"
DROPIN_NAME = "90-crm-whatsapp-release.conf"
MIGRATION = "0027_whatsapp_webhooks"


class DeploymentError(Exception):
    pass


def run(label, args, *, cwd=None, env=None):
    result = subprocess.run(args, cwd=cwd, env=env, text=True, capture_output=True)
    if result.returncode:
        # Output from Django, database clients or URLs can contain credentials.
        raise DeploymentError(f"{label}: fallo (codigo {result.returncode}).")
    return result.stdout.strip()


def state(unit):
    result = subprocess.run(["systemctl", "is-active", unit], text=True, capture_output=True)
    return result.stdout.strip() or "unknown"


def read_environment():
    original = ENV_FILE.read_text(encoding="utf-8")
    values = {}
    for raw in original.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:]
        # EnvironmentFile treats a line beginning with # as a comment; a #
        # inside an unquoted password is part of its value, not an inline comment.
        tokens = shlex.split(line, comments=False, posix=True)
        if len(tokens) != 1 or "=" not in tokens[0]:
            raise DeploymentError("El formato del archivo de entorno requiere revision manual.")
        key, value = tokens[0].split("=", 1)
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise DeploymentError("El archivo de entorno contiene una clave no valida.")
        values[key] = value
    return original, values


def postgres_environment(values):
    env = os.environ.copy()
    if values.get("DATABASE_URL"):
        parsed = urlsplit(values["DATABASE_URL"])
        if parsed.scheme not in {"postgres", "postgresql"} or not parsed.path.lstrip("/"):
            raise DeploymentError("DATABASE_URL no identifica una base PostgreSQL valida.")
        env.update(PGDATABASE=unquote(parsed.path.lstrip("/")), PGUSER=unquote(parsed.username or ""),
                   PGPASSWORD=unquote(parsed.password or ""), PGHOST=parsed.hostname or "localhost",
                   PGPORT=str(parsed.port or 5432))
        params = parse_qs(parsed.query)
        for query_key, env_key in (("sslmode", "PGSSLMODE"), ("sslrootcert", "PGSSLROOTCERT")):
            if query_key in params:
                env[env_key] = params[query_key][-1]
    elif values.get("POSTGRES_DB"):
        env.update(PGDATABASE=values["POSTGRES_DB"], PGUSER=values.get("POSTGRES_USER", ""),
                   PGPASSWORD=values.get("POSTGRES_PASSWORD", ""), PGHOST=values.get("POSTGRES_HOST", "localhost"),
                   PGPORT=values.get("POSTGRES_PORT", "5432"))
    else:
        raise DeploymentError("No se encontro la configuracion PostgreSQL del servicio.")
    env["PGCONNECT_TIMEOUT"] = "10"
    return env


def atomic_write(path, content, *, mode, uid, gid):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    descriptor, temporary = tempfile.mkstemp(prefix=".whatsapp-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as target:
            target.write(content)
            target.flush()
            os.fsync(target.fileno())
            os.fchmod(target.fileno(), mode)
            os.fchown(target.fileno(), uid, gid)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def snapshot(path, backup, name):
    if path.is_symlink():
        raise DeploymentError("Una ruta de configuracion es un enlace inesperado.")
    exists = path.exists()
    info = {"path": str(path), "exists": exists}
    if exists:
        stat = path.stat()
        saved = backup / name
        shutil.copy2(path, saved)
        saved.chmod(0o600)
        info.update(backup=str(saved), mode=stat.st_mode & 0o777, uid=stat.st_uid, gid=stat.st_gid)
    return info


def restore(info):
    path = Path(info["path"])
    if info["exists"]:
        atomic_write(path, Path(info["backup"]).read_bytes(), mode=info["mode"], uid=info["uid"], gid=info["gid"])
    elif path.exists():
        # Only the exact new drop-in created by this script can reach this branch.
        path.unlink()


def extract_archive(archive, release):
    with tarfile.open(archive, "r:*") as bundle:
        for item in bundle.getmembers():
            name = PurePosixPath(item.name)
            if name.is_absolute() or ".." in name.parts or ".git" in name.parts:
                raise DeploymentError("El archivo contiene una ruta no permitida.")
            if not (item.isfile() or item.isdir()):
                raise DeploymentError("El archivo contiene enlaces o tipos especiales no permitidos.")
        bundle.extractall(release, filter="data")
    for name in (".venv", "media", "staticfiles"):
        destination, source = release / name, LIVE / name
        if destination.exists() or destination.is_symlink() or not source.exists():
            raise DeploymentError(f"No se pudo preservar la ruta compartida {name}.")
        destination.symlink_to(source, target_is_directory=True)
    if not (release / "manage.py").is_file() or not (release / "crm/migrations" / f"{MIGRATION}.py").is_file():
        raise DeploymentError("La version no contiene los archivos esperados de WhatsApp.")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def http(path, *, data=None):
    request = Request("http://127.0.0.1:8001" + path, data=data,
                      headers={"Host": "dvsolutionshn.com", "X-Forwarded-Proto": "https", "Content-Type": "application/json"})
    try:
        with build_opener(NoRedirect).open(request, timeout=5) as response:
            return response.status, response.read(4096)
    except HTTPError as response:
        return response.code, response.read(4096)
    except (URLError, TimeoutError, OSError):
        return 0, b""


def healthy(token=None):
    for _ in range(12):
        if state(WEB) == "active":
            if token is None:
                code, _ = http("/")
                if 200 <= code < 400:
                    return True
            else:
                challenge = secrets.token_hex(12)
                query = urlencode({"hub.mode": "subscribe", "hub.verify_token": token, "hub.challenge": challenge})
                code, content = http("/integraciones/whatsapp/webhook/?" + query)
                refused, _ = http("/integraciones/whatsapp/webhook/", data=b"{}")
                if code == 200 and content == challenge.encode() and refused == 403:
                    return True
        time.sleep(1)
    return False


def pilot_active(python, environment, value=None):
    """Read or pause only the named pilot; no credentials/contacts are selected."""
    code = (
        "import os,json;os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings');"
        "import django;django.setup();from crm.models import ConfiguracionCRM;"
        "q=ConfiguracionCRM.objects.only('pk','whatsapp_activo').get(empresa__slug='hospital_mia');"
    )
    if value is None:
        code += "print(json.dumps(q.whatsapp_activo))"
        result = json.loads(run("Leer estado del piloto", [python, "-c", code], cwd=LIVE, env=environment))
        if not isinstance(result, bool):
            raise DeploymentError("El estado del piloto no es valido.")
        return result
    code += f"ConfiguracionCRM.objects.filter(pk=q.pk).update(whatsapp_activo={bool(value)!r})"
    run("Actualizar pausa del piloto", [python, "-c", code], cwd=LIVE, env=environment)


def deploy(args):
    if os.geteuid() != 0:
        raise DeploymentError("Este despliegue requiere root.")
    os.umask(0o077)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", args.release_id) or not re.fullmatch(r"[a-fA-F0-9]{7,40}", args.base_commit):
        raise DeploymentError("Identificador de version o commit no valido.")
    if not re.fullmatch(r"[a-fA-F0-9]{64}", args.sha256):
        raise DeploymentError("SHA256 no valido.")
    archive = Path(args.archive).resolve(strict=True)
    with archive.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    if digest != args.sha256.lower():
        raise DeploymentError("El SHA256 del archivo no coincide.")
    head = run("Comprobar commit", ["git", "rev-parse", "HEAD"], cwd=LIVE)
    if not head.startswith(args.base_commit.lower()) or run("Comprobar checkout", ["git", "status", "--porcelain", "--untracked-files=no"], cwd=LIVE):
        raise DeploymentError("El checkout productivo cambio; se necesita revisar la version nuevamente.")
    if state(WEB) != "active":
        raise DeploymentError("El servicio web no esta activo antes del despliegue.")
    timer_before = state(TIMER)
    for root in (RELEASES, BACKUPS):
        if root.is_symlink() or root.resolve() != root:
            raise DeploymentError("La raiz de versiones o respaldos no es una ruta segura.")
    release = RELEASES / ("whatsapp-" + args.release_id)
    backup = BACKUPS / ("whatsapp-" + args.release_id)
    if release.exists() or release.is_symlink() or backup.exists() or backup.is_symlink():
        raise DeploymentError("La version o el respaldo ya existe; no se sobrescribira.")
    original_env, values = read_environment()
    if not values.get("META_WHATSAPP_APP_SECRET", "").strip():
        raise DeploymentError("Falta el secreto de la app Meta en el entorno del servicio.")
    releases_existed = RELEASES.exists()
    RELEASES.mkdir(parents=True, exist_ok=True, mode=0o755)
    if not releases_existed:
        RELEASES.chmod(0o755)
    release.mkdir(mode=0o755)
    release.chmod(0o755)
    backup.mkdir(parents=True, mode=0o700)
    snapshots = [snapshot(ENV_FILE, backup, "environment.original")]
    for unit in (WEB, JOB):
        dropin = Path("/etc/systemd/system") / (unit + ".d") / DROPIN_NAME
        snapshots.append(snapshot(dropin, backup, unit + ".dropin.original"))
    manifest = {"release": str(release), "backup": str(backup), "base_commit": head,
                "archive_sha256": digest, "timer_before": timer_before, "snapshots": snapshots}
    (backup / "deployment.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    pg_env = postgres_environment(values)
    database_backup = backup / "database.dump"
    run("Respaldo PostgreSQL", ["pg_dump", "--format=custom", "--file", str(database_backup)], env=pg_env)
    database_backup.chmod(0o600)
    run("Verificar respaldo PostgreSQL", ["pg_restore", "--list", str(database_backup)], env=pg_env)
    extract_archive(archive, release)
    generated_token = not values.get("META_WHATSAPP_VERIFY_TOKEN", "").strip()
    if generated_token:
        values["META_WHATSAPP_VERIFY_TOKEN"] = secrets.token_urlsafe(36)
    environment = os.environ.copy()
    environment.update(values)
    python = str(LIVE / ".venv/bin/python")
    run("Comprobacion Django", ["runuser", "-u", "dvsolutions", "--", python, "manage.py", "check", "--deploy"],
        cwd=release, env=environment)
    plan = run("Plan de migracion CRM", [python, "manage.py", "migrate", "crm", MIGRATION, "--plan"], cwd=release, env=environment)
    planned = re.findall(r"^([A-Za-z_][A-Za-z0-9_]*\.\d\S*)$", plan, re.MULTILINE)
    if any(item != "crm." + MIGRATION for item in planned) or (not planned and "No planned migration operations" not in plan):
        raise DeploymentError("El plan incluye migraciones inesperadas; no se aplico.")
    (backup / "migration-plan.txt").write_text(plan, encoding="utf-8")
    run("Migracion aditiva CRM", [python, "manage.py", "migrate", "crm", MIGRATION, "--noinput"], cwd=release, env=environment)
    pilot_before = pilot_active(python, environment)
    manifest["pilot_whatsapp_active_before"] = pilot_before
    (backup / "deployment.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    changed = False
    try:
        changed = True
        # The pilot has pending notifications: keep its credential flag paused
        # while preserving every other company's automation and the timer state.
        pilot_active(python, environment, False)
        if generated_token:
            lines = [line for line in original_env.splitlines()
                     if not re.match(r"^\s*(?:export\s+)?META_WHATSAPP_VERIFY_TOKEN\s*=", line)]
            updated = "\n".join(lines) + "\nMETA_WHATSAPP_VERIFY_TOKEN=" + values["META_WHATSAPP_VERIFY_TOKEN"] + "\n"
            info = snapshots[0]
            atomic_write(ENV_FILE, updated.encode(), mode=info["mode"], uid=info["uid"], gid=info["gid"])
        for info in snapshots[1:]:
            atomic_write(Path(info["path"]), f"[Service]\nWorkingDirectory={release}\n".encode(), mode=0o644, uid=0, gid=0)
        run("Recargar unidades", ["systemctl", "daemon-reload"])
        run("Reiniciar web", ["systemctl", "restart", WEB])
        if not healthy(values["META_WHATSAPP_VERIFY_TOKEN"]):
            raise DeploymentError("El webhook no supero las comprobaciones de salud.")
        if state(TIMER) != timer_before:
            raise DeploymentError("Cambio inesperado en el estado del timer.")
        if pilot_active(python, environment) is not False:
            raise DeploymentError("El piloto no permanecio en pausa.")
    except Exception:
        rollback_ok = True
        if changed:
            for info in reversed(snapshots):
                try:
                    restore(info)
                except Exception:
                    rollback_ok = False
            try:
                pilot_active(python, environment, pilot_before)
            except Exception:
                rollback_ok = False
            try:
                run("Restaurar unidades", ["systemctl", "daemon-reload"])
                run("Restaurar servicio web", ["systemctl", "restart", WEB])
                rollback_ok = healthy() and rollback_ok
            except Exception:
                rollback_ok = False
        print(json.dumps({"status": "DEPLOY_FAILED", "rollback_healthy": rollback_ok,
                          "backup": str(backup), "database_migration_reverted": False}))
        raise DeploymentError("Despliegue cancelado; revisar el estado de recuperacion informado.") from None
    result = {"status": "DEPLOY_OK", "release": str(release), "backup": str(backup),
              "base_commit": head, "archive_sha256": digest, "web": state(WEB),
              "timer": state(TIMER), "webhook_verified": True, "verify_token_generated": generated_token,
              "pilot_whatsapp_active": False}
    (backup / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive")
    parser.add_argument("sha256")
    parser.add_argument("release_id")
    parser.add_argument("base_commit")
    options = parser.parse_args()
    try:
        deploy(options)
    except DeploymentError as error:
        # DeploymentError messages are authored above and contain no raw output.
        print("DEPLOY_STOPPED: " + str(error), file=sys.stderr)
        sys.exit(1)
    except (OSError, ValueError, tarfile.TarError, subprocess.SubprocessError):
        # No exception repr/traceback: environment URLs and tokens must stay private.
        print("DEPLOY_STOPPED: comprobacion fallida; no se publican detalles privados.", file=sys.stderr)
        sys.exit(1)
