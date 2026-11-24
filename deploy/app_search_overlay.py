"""Pinned, APP-only search overlay for a copy of the active web release.

This module never discovers or activates a service, runs Django, migrates a
database or contacts application endpoints. The deployment orchestrator must
pass an inactive copy to apply_overlay and activate it only after its checks.
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


BASE_COMMIT = "248c10edfc35d1211abe6742bd261b2d2111bb4e"
FIX_COMMIT = "bd49c1b89c25507ac434aeeafb98679b7bacfdce"
VIEWS = "crm/views.py"
TEMPLATE = "crm/templates/crm/agenda_mobile.html"
NEW_FILES = (
    "crm/mobile_appointment_search.py",
    "crm/static/crm/mobile_appointment_search.css",
    "crm/static/crm/mobile_invoice_search.js",
    "crm/templates/crm/mobile_appointment_search.html",
    "crm/templates/crm/mobile_invoice_search.html",
    "crm/templates/crm/mobile_agenda_navigation.html",
)
RUNTIME_FILES = (VIEWS, TEMPLATE, *NEW_FILES)
BASE_FUNCTION_HASH = "614f872b17010cfaee5ad32e8a9e5431ee40ebb5ae2c5f16c17cde96a6ee1856"
TARGET_FUNCTION_HASH = "8aa596a8c844bfdf0ce90472c80ab0c49f00616845d8799c8a271fe35ae20aa2"
BASE_TEMPLATE_HASH = "00b4b848723801a72bad1dc1d2f30fa835e4b9af7dc6985cadbd5aaf597dd532"
# Observed live template: BASE_COMMIT with ONLY the reviewed APP navigation
# extraction (e5642bff) and date-form fix (f9124670) absent. Independently
# reconstructed and hash-matched; later reception/clinical restrictions remain.
COMPATIBLE_TEMPLATE_HASH = "d9ca0818966df43cfd612bb587fba2a91c09da9b691f66f4082144e1306ecb10"
TARGET_FILE_HASHES = {
    TEMPLATE: "762c68957202314d456f3a3dd2fc19df5d1e6ec3c2e865aaadb0cdcf11ab7c40",
    NEW_FILES[0]: "bc2d95a7f8ee2613d5c0d7d5945524db86830bc59f5da5c0a915d83ace08b200",
    NEW_FILES[1]: "94b18c55cc4dbb9d131f99771606157f57a1c203cf5069fe6b625bc1dbfb0279",
    NEW_FILES[2]: "42336532333f8165acc3c2922619325843c60f1318644ee65c1b5232cd914189",
    NEW_FILES[3]: "0fb610e84ea324199d7507e56943076744e8663600efbba892f9915e2b9134f8",
    NEW_FILES[4]: "c4ed14eb6c5656f28758d89bec35b133683f976302e40b01833a079d4de4b608",
    NEW_FILES[5]: "044313e58d70554f5753d5c3092c83a261d096bc2c2b2b61c70bd8fbe5276c27",
}


def _decode(content):
    try:
        return content.decode("utf-8")
    except UnicodeError:
        raise OverlayError("El codigo de buscadores no usa UTF-8 valido.") from None


def _tree(source):
    try:
        tree = ast.parse(source[1:] if source.startswith("\ufeff") else source)
        compile(tree, "<app-search-overlay>", "exec")
        return tree
    except (SyntaxError, ValueError):
        raise OverlayError("Un archivo Python de buscadores no es valido.") from None


def _file_digest(content):
    return hashlib.sha256(content.replace(b"\r\n", b"\n")).hexdigest()


def _function_block(source):
    matches = [node for node in _tree(source).body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "agenda_mobile"]
    if len(matches) != 1:
        raise OverlayError("Debe existir exactamente una funcion agenda_mobile.")
    node = matches[0]
    first = min([node.lineno] + [decorator.lineno for decorator in node.decorator_list]) - 1
    lines = source.splitlines(keepends=True)
    return first, node.end_lineno, "".join(lines[first:node.end_lineno])


def _function_digest(block):
    canonical = block.replace("\r\n", "\n").rstrip("\n") + "\n"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def overlay_views(active_source, desired_source):
    """Replace the sole reviewed function, preserving all other raw lines."""
    first, last, current = _function_block(active_source)
    _, _, desired = _function_block(desired_source)
    if _function_digest(desired) != TARGET_FUNCTION_HASH:
        raise OverlayError("La funcion propuesta no coincide con el commit revisado.")
    if _function_digest(current) not in {BASE_FUNCTION_HASH, TARGET_FUNCTION_HASH}:
        raise OverlayError("agenda_mobile contiene cambios activos no revisados.")
    # No translation-mode read_text: CRLF/BOM and unrelated custom code remain.
    lines = active_source.splitlines(keepends=True)
    lines[first:last] = [desired.rstrip("\n") + "\n"]
    result = "".join(lines)
    _tree(result)
    return result


def _root(path):
    path = Path(path)
    if not path.is_dir() or path.is_symlink():
        raise OverlayError("La version debe ser un directorio regular existente.")
    return path


def _paths(root):
    paths = {
        name: _checked_path(root, name, required=name in {VIEWS, TEMPLATE})
        for name in RUNTIME_FILES
    }
    if any(not path.parent.is_dir() for path in paths.values()):
        raise OverlayError("Falta un directorio de codigo requerido en la version.")
    return paths


def _validate_target(files):
    if set(files) != set(RUNTIME_FILES) or any(not isinstance(content, bytes) or not content for content in files.values()):
        raise OverlayError("El overlay debe contener exactamente ocho archivos revisados.")
    _, _, function = _function_block(_decode(files[VIEWS]))
    if _function_digest(function) != TARGET_FUNCTION_HASH:
        raise OverlayError("La funcion propuesta no coincide con el commit revisado.")
    for name in (TEMPLATE, *NEW_FILES):
        if _file_digest(files[name]) != TARGET_FILE_HASHES[name]:
            raise OverlayError(f"El archivo propuesto {name} no coincide con el commit revisado.")
        _decode(files[name])
    _tree(_decode(files[NEW_FILES[0]]))


def _validate_active(paths):
    _, _, function = _function_block(_decode(paths[VIEWS].read_bytes()))
    if _function_digest(function) not in {BASE_FUNCTION_HASH, TARGET_FUNCTION_HASH}:
        raise OverlayError("agenda_mobile contiene cambios activos no revisados.")
    if _file_digest(paths[TEMPLATE].read_bytes()) not in {BASE_TEMPLATE_HASH, COMPATIBLE_TEMPLATE_HASH, TARGET_FILE_HASHES[TEMPLATE]}:
        raise OverlayError("La plantilla movil activa contiene cambios no revisados.")
    # Individually reviewed baseline/target files allow safe partial deployment
    # completion. Unknown existing content is never silently overwritten.
    for name in NEW_FILES:
        if paths[name].exists() and _file_digest(paths[name].read_bytes()) != TARGET_FILE_HASHES[name]:
            raise OverlayError(f"El archivo activo {name} contiene cambios no revisados.")


def build_overlay(active, repo, commit=FIX_COMMIT):
    """Read the pinned Git target and active release without changing either."""
    if commit != FIX_COMMIT:
        raise OverlayError("El commit solicitado no es el de buscadores revisado.")
    active = _root(active)
    paths = _paths(active)
    desired = {name: _git_file(Path(repo), FIX_COMMIT, name) for name in RUNTIME_FILES}
    _validate_target(desired)
    _validate_active(paths)
    desired[VIEWS] = overlay_views(
        _decode(paths[VIEWS].read_bytes()), _decode(desired[VIEWS]),
    ).encode("utf-8")
    _validate_target(desired)
    return desired


def apply_overlay(destination, files):
    """Write only the reviewed eight files to an inactive release copy.

    All paths and contents are checked before the first write. Activation and
    rollback belong to the orchestrator; this utility never restarts a service.
    """
    _validate_target(files)
    paths = _paths(_root(destination))
    _validate_active(paths)
    current = _decode(paths[VIEWS].read_bytes())
    proposed = _decode(files[VIEWS])
    if overlay_views(current, proposed) != proposed:
        raise OverlayError("La copia cambio despues de construir el overlay.")
    # Dependencies arrive before the view; a partial write is never activated.
    for name in (*NEW_FILES, TEMPLATE, VIEWS):
        path = _checked_path(Path(destination), name, required=name in {VIEWS, TEMPLATE})
        info = path.stat() if path.exists() else None
        descriptor, temporary = tempfile.mkstemp(prefix=".app-buscadores-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(files[name])
                stream.flush()
                os.fsync(stream.fileno())
                if info is not None and hasattr(os, "fchown"):
                    os.fchown(stream.fileno(), info.st_uid, info.st_gid)
            os.chmod(temporary, stat.S_IMODE(info.st_mode) if info is not None else 0o644)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
