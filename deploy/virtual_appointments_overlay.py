"""Reviewed virtual-appointment blocks over a copy of the active release."""
import ast
import os
from pathlib import Path
import stat
import tempfile

try:
    from .inventory_overlay import OverlayError, _checked_path, _git_file
    from .app_startup_overlay import digest, decode, checked_root
except ImportError:
    from inventory_overlay import OverlayError, _checked_path, _git_file
    from app_startup_overlay import digest, decode, checked_root

BASE_COMMIT = "e3874fdf29e09ad4b32e5811dd1af080a37dabf9"
FIX_COMMIT = "a6937a1e2227d0092cb1d9cdaa17b29619fbcf13"
BLOCKS = {
    "crm/models.py": ("CitaCliente",),
    "crm/forms.py": ("CitaClienteForm",),
    "crm/views.py": ("_guardar_cita_formulario", "_sincronizar_cita_clinica"),
    "clinica/models.py": ("CitaClinica",),
    "clinica/forms.py": ("CitaClinicaForm",),
    "clinica/views.py": ("_sincronizar_agenda_desde_cita_clinica",),
}
FILES = (
    "crm/mobile_appointment_search.py",
    "crm/templates/crm/agenda_mobile.html",
    "crm/templates/crm/citas.html",
    "crm/templates/crm/mobile_appointment_search.html",
)
NEW_FILES = (
    "crm/templates/crm/includes/appointment_virtual_badge.html",
    "crm/migrations/0027_citacliente_virtual.py",
    "clinica/migrations/0039_citaclinica_virtual.py",
    "deploy/virtual_appointments_schema.py",
    "deploy/migration_templates/merge_virtual_webhooks.py",
)
RUNTIME_FILES = (*BLOCKS, *FILES, *NEW_FILES)
OPTIONAL_MERGE = "crm/migrations/0028_merge_citas_virtuales_webhooks.py"
MERGE_TEMPLATE = "deploy/migration_templates/merge_virtual_webhooks.py"
COMPAT_BRANCH = "crm/migrations/0027_whatsapp_webhooks.py"
COMPAT_BRANCH_HASH = "0e1044498d83888ef6e28e90db91ccdc387e36501c873ae1de8017ef58e8c060"
WATCH_FILES = (*RUNTIME_FILES, OPTIONAL_MERGE, COMPAT_BRANCH)
TARGET_HASHES = {
    "crm/models.py::CitaCliente": "3ba9d69fd7d8ae17036e8ce51045e5b62886c46fcc83a323c5ee66412e15c1eb",
    "crm/forms.py::CitaClienteForm": "e8414fc89b8e5dda70fa579af9c529a094ee4235d2779ee93a743006ec5edd11",
    "crm/views.py::_guardar_cita_formulario": "7652876a1b2fc436d0efd5c9c367460218e237a4b13f4db1acf765b372e3aa69",
    "crm/views.py::_sincronizar_cita_clinica": "8e020d8bb191e55c6738ab468275d8cb0c08fca2fa8cd741e912b33b71ef2b46",
    "clinica/models.py::CitaClinica": "c61a1f35d1ab7991b68396d1d8ab9573ebd19ac8499de0e8ae8d567aebbe7e98",
    "clinica/forms.py::CitaClinicaForm": "f4ea0877e3a4451e29c49b70956e08a8ca66cb3a10a6813184c910971e17ebc9",
    "clinica/views.py::_sincronizar_agenda_desde_cita_clinica": "91f7fa9fd4f8eab66250aef22431797fa01cc82b562f9d4154585c909d7257fa",
    "crm/mobile_appointment_search.py": "f8c7fa343675e7c2e3c16614769fbcc8f5e2b6d8fe4dffd3d6e33d1a4a7df04a",
    "crm/templates/crm/agenda_mobile.html": "56d6c952689146b31ac2b48e81ceb610aab101c192c3f238a0cf9f098beec091",
    "crm/templates/crm/citas.html": "b637be4cddc6d15e8ddc9e6284a2bc407cb4aea7e8193e790ed7109318d27db8",
    "crm/templates/crm/mobile_appointment_search.html": "0a598550f7c0fcc638cf04a3526e753a10bfee5fbb43521820bfae79d8a5066a",
    "crm/templates/crm/includes/appointment_virtual_badge.html": "09c4145f2e46aedbd39c0346619b5200f5ee1ba707cd626e4b07334e84237eca",
    "crm/migrations/0027_citacliente_virtual.py": "1286e3de7f600f9304dea4c4a59590ba2846f03c461875068ec36aff313de749",
    "clinica/migrations/0039_citaclinica_virtual.py": "aa2057a763cab9bd61087f40ac67288bc41de361c9c09c651d6cd24423e17e8b",
    "deploy/virtual_appointments_schema.py": "2d8ac9540c8c0af84baa0d3f3afe380e95f32956d5932724a6ba3290237c3c97",
    "deploy/migration_templates/merge_virtual_webhooks.py": "f9b761706bde955737b46e77e8e109a229832a12a2a671087ec5f412a6e504c7",
    "crm/migrations/0028_merge_citas_virtuales_webhooks.py": "f9b761706bde955737b46e77e8e109a229832a12a2a671087ec5f412a6e504c7"
}
ADDED_IMPORT = "from core.clinical_permissions import EMPRESAS_CLINICAS_CON_ROLES\n"
IMPORT_FILES = {"crm/forms.py", "clinica/forms.py"}


def blocks(source, names):
    try:
        tree = ast.parse(source.lstrip("\ufeff"))
        compile(tree, "<virtual-appointment-overlay>", "exec")
    except (SyntaxError, ValueError):
        raise OverlayError("Un modulo de citas no es Python valido.") from None
    lines = source.splitlines(keepends=True)
    result = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.ClassDef)) or node.name not in names:
            continue
        if node.name in result:
            raise OverlayError("Un bloque de citas esta duplicado.")
        start = min([node.lineno] + [item.lineno for item in node.decorator_list]) - 1
        result[node.name] = start, node.end_lineno, "".join(lines[start:node.end_lineno])
    if set(result) != set(names):
        raise OverlayError("Falta un bloque de citas requerido.")
    return result


def block_digest(text):
    return digest((text.replace("\r\n", "\n").rstrip("\n") + "\n").encode())


def outside(source, names):
    lines = source.splitlines(keepends=True)
    for start, end, text in sorted(blocks(source, names).values(), reverse=True):
        lines[start:end] = ["# REVIEWED APPOINTMENT BLOCK\n"]
    return "".join(lines).replace("\r\n", "\n").replace(ADDED_IMPORT, "")


def ensure_import(source):
    tree = ast.parse(source.lstrip("\ufeff"))
    for node in tree.body:
        if (isinstance(node, ast.ImportFrom) and node.module == "core.clinical_permissions" and not node.level
                and any(alias.name == "EMPRESAS_CLINICAS_CON_ROLES" and alias.asname in (None, alias.name) for alias in node.names)):
            return source
    imports = [node for node in tree.body if isinstance(node, (ast.ImportFrom, ast.Import))]
    if not imports:
        raise OverlayError("Faltan imports esperados en el formulario activo.")
    lines = source.splitlines(keepends=True)
    position = imports[-1].end_lineno
    lines[position:position] = [ADDED_IMPORT]
    return "".join(lines)


def validate(files):
    if set(files) not in (set(RUNTIME_FILES), {*RUNTIME_FILES, OPTIONAL_MERGE}) or any(not isinstance(data, bytes) or not data for data in files.values()):
        raise OverlayError("El overlay de citas no contiene exactamente los archivos revisados.")
    expected = {*FILES, *NEW_FILES, OPTIONAL_MERGE, *(path + "::" + name for path, names in BLOCKS.items() for name in names)}
    if set(TARGET_HASHES) != expected:
        raise OverlayError("Falta el manifiesto revisado de citas virtuales.")
    for path, data in files.items():
        source = decode(data)
        if path in IMPORT_FILES and ensure_import(source) != source:
            raise OverlayError("Falta el import revisado del alcance clinico.")
        if path in BLOCKS:
            for name, (_, _, text) in blocks(source, BLOCKS[path]).items():
                if block_digest(text) != TARGET_HASHES[path + "::" + name]:
                    raise OverlayError("Un bloque no coincide con las citas virtuales revisadas.")
        elif digest(data) != TARGET_HASHES[path]:
            raise OverlayError("Un archivo no coincide con las citas virtuales revisadas: " + path)
        if path.endswith(".py"):
            try:
                compile(source.lstrip("\ufeff"), path, "exec")
            except (SyntaxError, ValueError):
                raise OverlayError("Un modulo revisado no es Python valido.") from None


def build_overlay(active, repo):
    active = checked_root(active)
    desired = {path: _git_file(Path(repo), FIX_COMMIT, path) for path in RUNTIME_FILES}
    branch = _checked_path(active, COMPAT_BRANCH)
    merge = _checked_path(active, OPTIONAL_MERGE)
    if branch.exists():
        if digest(branch.read_bytes()) != COMPAT_BRANCH_HASH:
            raise OverlayError("La rama previa de WhatsApp no coincide con la version revisada; no se modifico.")
        # A standard empty merge belongs only in releases which actually have
        # this already-installed branch. The clean Git tree never imports it.
        desired[OPTIONAL_MERGE] = desired[MERGE_TEMPLATE]
    elif merge.exists():
        raise OverlayError("Existe una union de migraciones sin su rama previa; no se forzo el cambio.")
    validate(desired)
    for path in desired:
        target = _checked_path(active, path, required=path not in (*NEW_FILES, OPTIONAL_MERGE))
        if path in BLOCKS:
            baseline = decode(_git_file(Path(repo), BASE_COMMIT, path))
            proposed = decode(desired[path])
            if outside(baseline, BLOCKS[path]) != outside(proposed, BLOCKS[path]):
                raise OverlayError("El commit incluye cambios fuera de los bloques de citas revisados.")
            current = decode(target.read_bytes())
            before, after, original = blocks(baseline, BLOCKS[path]), blocks(proposed, BLOCKS[path]), blocks(current, BLOCKS[path])
            lines = current.splitlines(keepends=True)
            for name, (start, end, text) in sorted(original.items(), key=lambda item: item[1][0], reverse=True):
                if block_digest(text) not in {block_digest(before[name][2]), block_digest(after[name][2])}:
                    raise OverlayError("Un bloque activo de citas contiene cambios no revisados: " + path + "::" + name)
                lines[start:end] = [after[name][2].rstrip("\n") + "\n"]
            result = "".join(lines)
            desired[path] = (ensure_import(result) if path in IMPORT_FILES else result).encode()
        elif target.exists():
            allowed = {digest(desired[path])}
            if path not in NEW_FILES and path != OPTIONAL_MERGE:
                allowed.add(digest(_git_file(Path(repo), BASE_COMMIT, path)))
            if digest(target.read_bytes()) not in allowed:
                raise OverlayError("Un archivo activo de citas contiene cambios no revisados: " + path)
    validate(desired)
    return desired


def apply_overlay(destination, files, repo):
    validate(files)
    destination = checked_root(destination)
    if build_overlay(destination, repo) != files:
        raise OverlayError("La copia cambio durante la preparacion de citas virtuales.")
    paths = {path: _checked_path(destination, path, required=path not in (*NEW_FILES, OPTIONAL_MERGE)) for path in files}
    # Only these two reviewed code directories may be added to the copy.
    for path in paths.values():
        if not path.parent.is_dir():
            if path.parent.relative_to(destination).as_posix() not in {"crm/templates/crm/includes", "deploy/migration_templates"} or not path.parent.parent.is_dir():
                raise OverlayError("Falta un directorio requerido de citas virtuales.")
            path.parent.mkdir(mode=0o755)
    for name in (*NEW_FILES, *((OPTIONAL_MERGE,) if OPTIONAL_MERGE in files else ()), *FILES, *BLOCKS):
        path = _checked_path(destination, name)
        info = path.stat() if path.exists() else None
        descriptor, temporary = tempfile.mkstemp(prefix=".virtual-appointments-", dir=path.parent)
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
