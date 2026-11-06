"""Overlay mínimo y verificable de inventario sobre la versión activa."""

import ast
import hashlib
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import stat
import subprocess
import tempfile


FIX_COMMIT = "4afc40e241e6b57cb05d9163bfe22a89a5819249"
FUNCTIONS = (
    "_registrar_movimiento_lote_bodega",
    "_registrar_salida_fefo_factura",
    "_registrar_salida_vitrina_factura",
    "_revertir_salida_factura",
    "editar_factura",
)
RUNTIME_FILES = (
    "facturacion/views.py",
    "facturacion/inventario_edicion.py",
    "facturacion/edicion_lineas.py",
)

# SHA256 de las cinco funciones (incluidos sus decoradores) de 140a1a5f.
# Se normalizan únicamente CRLF y el salto de línea final del bloque.
BASELINE_FUNCTION_HASHES = {
    "_registrar_movimiento_lote_bodega": "dce7310f0e632f273d6f973122477fff55aaeb26aaec1043c03f75126362f73b",
    "_registrar_salida_fefo_factura": "6a60da3874d783a116517852208993969c10be8b9240c9203ec5bbc34ede1df6",
    "_registrar_salida_vitrina_factura": "09b22b3e4a81b02de6229b1c65c490d27d668a71fc6800e84d3003ec1968d132",
    "_revertir_salida_factura": "d04adfe402f87a484482dd366d07872d11d91f4b6798cdf46608f5b4d3b9ef3d",
    "editar_factura": "b55294789dd2587b51d1b8fc512f0da4fddf6b5ae4d18fa23d70926df7dd7f38",
}
IMPORTS = (
    ("inventario_edicion", "ajustar_inventario_factura_editada"),
    ("edicion_lineas", "preparar_lineas_factura_emitida"),
)


class OverlayError(RuntimeError):
    """La versión activa no permite aplicar el overlay revisado con seguridad."""


def _parse(source):
    try:
        return ast.parse(source)
    except (SyntaxError, ValueError):
        raise OverlayError("Un archivo Python del overlay no es valido.") from None


def _function_blocks(source):
    lines = source.splitlines(keepends=True)
    blocks = {}
    for node in _parse(source).body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name not in FUNCTIONS:
            continue
        if node.name in blocks:
            raise OverlayError(f"La funcion {node.name} esta definida mas de una vez.")
        first = min([node.lineno] + [decorator.lineno for decorator in node.decorator_list]) - 1
        blocks[node.name] = (first, node.end_lineno, "".join(lines[first:node.end_lineno]))
    missing = set(FUNCTIONS) - set(blocks)
    if missing:
        raise OverlayError("Faltan funciones requeridas: " + ", ".join(sorted(missing)))
    return blocks


def _digest(block):
    canonical = block.replace("\r\n", "\n").rstrip("\n") + "\n"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def overlay_views(active_text: str, desired_text: str) -> str:
    """Sustituye cinco funciones aprobadas y conserva el resto del módulo."""
    active = _function_blocks(active_text)
    desired = _function_blocks(desired_text)
    for name in FUNCTIONS:
        if _digest(active[name][2]) not in {
            BASELINE_FUNCTION_HASHES[name], _digest(desired[name][2]),
        }:
            raise OverlayError(f"La funcion activa {name} contiene cambios no revisados.")

    lines = active_text.splitlines(keepends=True)
    for name in sorted(FUNCTIONS, key=lambda item: active[item][0], reverse=True):
        first, last, _ = active[name]
        block = desired[name][2]
        if block and not block.endswith("\n"):
            block += "\n"
        lines[first:last] = [block]
    result = "".join(lines)
    tree = _parse(result)
    additions = []
    for module, name in IMPORTS:
        present = any(
            isinstance(node, ast.ImportFrom) and node.level == 1 and node.module == module
            and any(alias.name == name and alias.asname in (None, name) for alias in node.names)
            for node in tree.body
        )
        if not present:
            additions.append(f"from .{module} import {name}\n")
    if additions:
        imports = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]
        anchors = [node for node in imports if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module == "lotes"]
        if anchors or imports:
            position = (anchors or imports)[-1].end_lineno
        elif tree.body and isinstance(tree.body[0], ast.Expr) and isinstance(tree.body[0].value, ast.Constant) and isinstance(tree.body[0].value.value, str):
            position = tree.body[0].end_lineno
        else:
            position = 0
        lines = result.splitlines(keepends=True)
        if position and not lines[position - 1].endswith("\n"):
            lines[position - 1] += "\n"
        lines[position:position] = additions
        result = "".join(lines)
    _parse(result)
    return result


def _checked_path(root: Path, relative: str, *, required=False) -> Path:
    posix = PurePosixPath(relative)
    windows = PureWindowsPath(relative)
    if posix.is_absolute() or windows.drive or ".." in posix.parts or "\\" in relative:
        raise OverlayError("Una ruta del overlay no es relativa y segura.")
    path = root
    for part in posix.parts:
        path = path / part
        if path.is_symlink():
            raise OverlayError("Las rutas de codigo del overlay no pueden ser enlaces.")
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        raise OverlayError("Una ruta del overlay sale del directorio de la version.") from None
    if path.exists() and not path.is_file():
        raise OverlayError("Una ruta del overlay no es un archivo regular.")
    if required and not path.is_file():
        raise OverlayError(f"Falta un archivo requerido: {relative}.")
    return path


def _git_file(repo: Path, commit: str, relative: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repo), "show", f"{commit}:{relative}"], capture_output=True,
    )
    if result.returncode or not result.stdout:
        raise OverlayError(f"No se pudo leer el archivo revisado {relative}.")
    return result.stdout


def build_overlay(active: Path, repo: Path, commit=FIX_COMMIT) -> dict[str, bytes]:
    """Construye tres archivos sin escribir en la versión activa ni en Git."""
    if commit != FIX_COMMIT:
        raise OverlayError("El commit solicitado no es la correccion revisada.")
    active, repo = Path(active), Path(repo)
    active_view = _checked_path(active, RUNTIME_FILES[0], required=True)
    desired = {name: _git_file(repo, commit, name) for name in RUNTIME_FILES}
    try:
        result = {
            RUNTIME_FILES[0]: overlay_views(
                active_view.read_text(encoding="utf-8-sig"), desired[RUNTIME_FILES[0]].decode("utf-8-sig"),
            ).encode("utf-8"),
        }
        for name in RUNTIME_FILES[1:]:
            _parse(desired[name].decode("utf-8-sig"))
            current = _checked_path(active, name)
            if current.exists() and current.read_bytes() != desired[name]:
                raise OverlayError(f"El modulo activo {name} contiene cambios no revisados.")
            result[name] = desired[name]
    except UnicodeError:
        raise OverlayError("El codigo del overlay no usa UTF-8 valido.") from None
    return result


def apply_overlay(destination: Path, files: dict[str, bytes]) -> None:
    """Escribe el overlay en una copia inactiva, sin seguir enlaces de código."""
    if set(files) != set(RUNTIME_FILES) or any(not isinstance(value, bytes) for value in files.values()):
        raise OverlayError("El overlay debe contener exactamente los tres archivos de codigo revisados.")
    destination = Path(destination)
    if not destination.is_dir() or destination.is_symlink():
        raise OverlayError("El destino debe ser un directorio de version regular existente.")
    paths = {name: _checked_path(destination, name, required=name == RUNTIME_FILES[0]) for name in RUNTIME_FILES}
    try:
        for content in files.values():
            _parse(content.decode("utf-8-sig"))
        planned_view = files[RUNTIME_FILES[0]].decode("utf-8-sig")
        if overlay_views(paths[RUNTIME_FILES[0]].read_text(encoding="utf-8-sig"), planned_view) != planned_view:
            raise OverlayError("La copia de la version cambio despues de construir el overlay.")
    except UnicodeError:
        raise OverlayError("El codigo del overlay no usa UTF-8 valido.") from None
    for name in RUNTIME_FILES[1:]:
        if paths[name].exists() and paths[name].read_bytes() != files[name]:
            raise OverlayError(f"El modulo destino {name} contiene cambios no revisados.")
    # Los módulos llegan antes que la vista que los importa. Todo ocurre en una
    # copia todavía inactiva; la activación corresponde al orquestador.
    for name in (*RUNTIME_FILES[1:], RUNTIME_FILES[0]):
        path = _checked_path(destination, name, required=name == RUNTIME_FILES[0])
        info = path.stat() if path.exists() else None
        descriptor, temporary = tempfile.mkstemp(prefix=".inventario-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as target:
                target.write(files[name])
                target.flush()
                os.fsync(target.fileno())
                if info is not None and hasattr(os, "fchown"):
                    os.fchown(target.fileno(), info.st_uid, info.st_gid)
            os.chmod(temporary, stat.S_IMODE(info.st_mode) if info is not None else 0o644)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
