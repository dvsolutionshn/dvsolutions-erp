"""Pinned, APP-only startup maintenance over a copy of the active release.

Never replaces a complete views module or deploys a checkout over live overlays.
The orchestrator owns service activation and rollback; this module does neither.
"""
import ast
import hashlib
import os
from pathlib import Path
import stat
import tempfile

try:
    from .inventory_overlay import OverlayError, _checked_path, _git_file
except ImportError:
    from inventory_overlay import OverlayError, _checked_path, _git_file

BASE_COMMIT = "90704ee74140c23ec97659991892a1ae24f33613"
FIX_COMMIT = "dd927d018f7b7a386a5b2e471c81fddec9e448a3"
VIEWS = "crm/views.py"
TEMPLATE = "crm/templates/crm/agenda_mobile.html"
NAVIGATION = "crm/templates/crm/mobile_agenda_navigation.html"
NEW_FILES = (
    "crm/templates/crm/mobile_app_recovery.html",
    "crm/templates/crm/mobile_app_service_worker.js",
)
RUNTIME_FILES = (VIEWS, TEMPLATE, NAVIGATION, *NEW_FILES)
FUNCTION = "agenda_mobile_service_worker"
# Hashes of the reviewed Git blobs; the view hash covers only the worker view.
TARGET_HASHES = {
    "crm/views.py": "43d823c5a21f8c4a4ece87c78962d42121a25a702b3295075c414ff8c6969708",
    "crm/templates/crm/agenda_mobile.html": "53e72ab80bc0fca6ff21d1611759486dc9a3d24a1fbe89321ebbd1b6422ccae1",
    "crm/templates/crm/mobile_agenda_navigation.html": "32452171e5d17d2acdd255a401613d74da9cdce83903a80af568195d19533043",
    "crm/templates/crm/mobile_app_recovery.html": "0e5a3dddc802d09244ba12910964a8dc6d8684d3babce466ec6448bb448cf86e",
    "crm/templates/crm/mobile_app_service_worker.js": "ee6d4ab1e9a2b9ea1b0d63b6f1c57df413d513112600b316662f3b4b21b54a94"
}


def digest(content):
    return hashlib.sha256(content.replace(b"\r\n", b"\n")).hexdigest()


def decode(content):
    try:
        return content.decode("utf-8")
    except UnicodeError:
        raise OverlayError("El codigo de inicio no es UTF-8 valido.") from None


def block(source):
    try:
        tree = ast.parse(source.lstrip("\ufeff"))
        compile(tree, "<app-startup-overlay>", "exec")
    except (SyntaxError, ValueError):
        raise OverlayError("El modulo de inicio no es Python valido.") from None
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == FUNCTION]
    if len(nodes) != 1:
        raise OverlayError("Debe existir exactamente una funcion del service worker.")
    node = nodes[0]
    start = min([node.lineno] + [item.lineno for item in node.decorator_list]) - 1
    return start, node.end_lineno, "".join(source.splitlines(keepends=True)[start:node.end_lineno])


def function_digest(source):
    return digest((block(source)[2].replace("\r\n", "\n").rstrip("\n") + "\n").encode())


def checked_root(path):
    root = Path(path)
    if root.is_symlink() or not root.is_dir():
        raise OverlayError("La version debe ser un directorio regular existente.")
    return root


def target_files(repo):
    files = {name: _git_file(Path(repo), FIX_COMMIT, name) for name in RUNTIME_FILES}
    validate_target(files)
    return files


def validate_target(files):
    if set(files) != set(RUNTIME_FILES) or any(not isinstance(value, bytes) or not value for value in files.values()):
        raise OverlayError("El mantenimiento requiere exactamente cinco archivos revisados.")
    if set(TARGET_HASHES) != set(RUNTIME_FILES):
        raise OverlayError("Falta el manifiesto del mantenimiento revisado.")
    for name, content in files.items():
        actual = function_digest(decode(content)) if name == VIEWS else digest(content)
        if actual != TARGET_HASHES[name]:
            raise OverlayError("Un archivo no coincide con el mantenimiento revisado: " + name)
        decode(content)


def overlay_view(active, desired, baseline):
    start, end, _ = block(active)
    if function_digest(active) not in {function_digest(baseline), function_digest(desired)}:
        raise OverlayError("El service worker activo contiene cambios no revisados.")
    lines = active.splitlines(keepends=True)
    lines[start:end] = [block(desired)[2].rstrip("\n") + "\n"]
    result = "".join(lines)
    block(result)
    return result


def build_overlay(active, repo):
    active = checked_root(active)
    desired = target_files(repo)
    paths = {name: _checked_path(active, name, required=name not in NEW_FILES) for name in RUNTIME_FILES}
    baseline = {name: _git_file(Path(repo), BASE_COMMIT, name) for name in (VIEWS, TEMPLATE, NAVIGATION)}
    for name in (TEMPLATE, NAVIGATION, *NEW_FILES):
        if paths[name].exists() and digest(paths[name].read_bytes()) not in {
            digest(desired[name]), *([digest(baseline[name])] if name in baseline else []),
        }:
            raise OverlayError("Un archivo activo contiene cambios no revisados: " + name)
    desired[VIEWS] = overlay_view(decode(paths[VIEWS].read_bytes()), decode(desired[VIEWS]), decode(baseline[VIEWS])).encode()
    validate_target(desired)
    return desired


def apply_overlay(destination, files, repo):
    validate_target(files)
    destination = checked_root(destination)
    expected = build_overlay(destination, repo)
    if expected != files:
        raise OverlayError("La copia cambio durante la preparacion; no se activo.")
    paths = {name: _checked_path(destination, name, required=name not in NEW_FILES) for name in RUNTIME_FILES}
    if any(not path.parent.is_dir() for path in paths.values()):
        raise OverlayError("Falta un directorio de codigo del mantenimiento.")
    for name in (*NEW_FILES, NAVIGATION, TEMPLATE, VIEWS):
        path = paths[name]
        info = path.stat() if path.exists() else None
        descriptor, temporary = tempfile.mkstemp(prefix=".app-startup-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(files[name])
                stream.flush()
                os.fsync(stream.fileno())
                if info is not None and hasattr(os, "fchown"):
                    os.fchown(stream.fileno(), info.st_uid, info.st_gid)
            os.chmod(temporary, stat.S_IMODE(info.st_mode) if info else 0o644)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
