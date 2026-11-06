"""Pruebas puras del overlay; no necesitan Django ni una base de datos."""

import ast
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from deploy import inventory_overlay as overlay


def fixture(version):
    header = '"""Version con WhatsApp preservado."""\nfrom __future__ import annotations\nfrom .lotes import existente\n\n'
    functions = "\n".join(
        f'@transaction.atomic\ndef {name}():\n    return "{version}-{name}"\n'
        for name in overlay.FUNCTIONS
    )
    untouched = '\n# Integracion particular de la version activa.\ndef whatsapp_untouched():\n    return "WHATSAPP-KEEP"\n'
    return header + functions + untouched


class InventoryOverlayTests(unittest.TestCase):
    def setUp(self):
        self.active = fixture("active")
        self.desired = fixture("desired").replace('return "WHATSAPP-KEEP"', 'return "DO-NOT-COPY"')
        blocks = overlay._function_blocks(self.active)
        approved = {name: overlay._digest(blocks[name][2]) for name in overlay.FUNCTIONS}
        self.approved = patch.dict(overlay.BASELINE_FUNCTION_HASHES, approved, clear=True)
        self.approved.start()
        self.addCleanup(self.approved.stop)
        self.directory = tempfile.TemporaryDirectory(prefix="inventory-overlay-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / "facturacion").mkdir()
        self.view = self.root / "facturacion/views.py"
        self.view.write_text(self.active, encoding="utf-8", newline="\n")
        self.blobs = {
            overlay.RUNTIME_FILES[0]: self.desired.encode(),
            overlay.RUNTIME_FILES[1]: b"COSTO = 1\n",
            overlay.RUNTIME_FILES[2]: b"NORMALIZAR = True\n",
        }

    def build(self):
        with patch.object(overlay, "_git_file", side_effect=lambda repo, commit, name: self.blobs[name]):
            return overlay.build_overlay(self.root, self.root)

    def test_replaces_only_five_functions_preserving_whatsapp_and_module_header(self):
        result = overlay.overlay_views(self.active, self.desired)
        self.assertIn('return "WHATSAPP-KEEP"', result)
        self.assertNotIn("DO-NOT-COPY", result)
        self.assertTrue(result.startswith(self.active.split("\n\n", 1)[0]))
        for name in overlay.FUNCTIONS:
            self.assertIn(f'desired-{name}', result)
            self.assertNotIn(f'active-{name}', result)
        self.assertEqual(result.count("from .inventario_edicion import ajustar_inventario_factura_editada"), 1)
        self.assertEqual(result.count("from .edicion_lineas import preparar_lineas_factura_emitida"), 1)
        compile(result, "overlay.py", "exec")

    def test_overlay_is_idempotent_and_accepts_already_desired_functions(self):
        first = overlay.overlay_views(self.active, self.desired)
        self.assertEqual(overlay.overlay_views(first, self.desired), first)

    def test_rejects_unreviewed_change_including_function_decorator(self):
        mutated = self.active.replace("@transaction.atomic", "@custom.decorator", 1)
        with self.assertRaisesRegex(overlay.OverlayError, "cambios no revisados"):
            overlay.overlay_views(mutated, self.desired)

    def test_missing_and_duplicate_functions_are_rejected(self):
        block = overlay._function_blocks(self.active)[overlay.FUNCTIONS[0]][2]
        for source, target in ((self.active.replace(block, ""), self.desired), (self.active, self.desired.replace(overlay._function_blocks(self.desired)[overlay.FUNCTIONS[0]][2], ""))):
            with self.subTest(source=source[:30]):
                with self.assertRaisesRegex(overlay.OverlayError, "Faltan funciones"):
                    overlay.overlay_views(source, target)
        with self.assertRaisesRegex(overlay.OverlayError, "mas de una vez"):
            overlay.overlay_views(self.active + "\n" + block, self.desired)

    def test_crlf_baseline_is_accepted_without_changing_other_function(self):
        result = overlay.overlay_views(self.active.replace("\n", "\r\n"), self.desired)
        self.assertIn('def whatsapp_untouched():\r\n    return "WHATSAPP-KEEP"\r\n', result)
        compile(result, "overlay.py", "exec")

    def test_import_insertion_without_anchor_preserves_future_import(self):
        active = self.active.replace("from .lotes import existente\n", "")
        result = overlay.overlay_views(active, self.desired)
        compile(result, "overlay.py", "exec")
        imports = [node for node in ast.parse(result).body if isinstance(node, ast.ImportFrom)]
        self.assertEqual(imports[0].module, "__future__")

    def test_build_returns_exactly_three_files_without_writing_active_version(self):
        files = self.build()
        self.assertEqual(set(files), set(overlay.RUNTIME_FILES))
        self.assertEqual(self.view.read_text(), self.active)
        self.assertFalse((self.root / overlay.RUNTIME_FILES[1]).exists())
        self.assertIn(b"WHATSAPP-KEEP", files[overlay.RUNTIME_FILES[0]])

    def test_build_rejects_missing_views_unreviewed_commit_and_existing_custom_module(self):
        with self.assertRaisesRegex(overlay.OverlayError, "commit solicitado"):
            overlay.build_overlay(self.root, self.root, commit="0" * 40)
        self.view.unlink()
        with self.assertRaisesRegex(overlay.OverlayError, "archivo requerido"):
            self.build()
        self.view.write_text(self.active)
        (self.root / overlay.RUNTIME_FILES[1]).write_text("CUSTOM = True\n")
        with self.assertRaisesRegex(overlay.OverlayError, "modulo activo"):
            self.build()

    def test_apply_writes_runtime_files_preserves_unrelated_files_and_repeats_safely(self):
        unrelated = self.root / "whatsapp-existing.txt"
        unrelated.write_bytes(b"PRESERVE")
        files = self.build()
        before_mode = self.view.stat().st_mode & 0o777
        overlay.apply_overlay(self.root, files)
        overlay.apply_overlay(self.root, files)
        for name, content in files.items():
            self.assertEqual((self.root / name).read_bytes(), content)
        self.assertEqual(unrelated.read_bytes(), b"PRESERVE")
        self.assertEqual(self.view.stat().st_mode & 0o777, before_mode)

    def test_apply_rejects_extra_missing_absolute_and_traversal_paths_before_writes(self):
        files = self.build()
        for invalid in ("../outside.py", "/outside.py", "C:/outside.py", "facturacion\\outside.py"):
            with self.subTest(path=invalid):
                wrong = {**files, invalid: b"INVALID = True\n"}
                with self.assertRaises(overlay.OverlayError):
                    overlay.apply_overlay(self.root, wrong)
                self.assertEqual(self.view.read_text(), self.active)
        incomplete = files.copy()
        incomplete.pop(overlay.RUNTIME_FILES[1])
        with self.assertRaises(overlay.OverlayError):
            overlay.apply_overlay(self.root, incomplete)

    def test_apply_rejects_custom_modules_changed_copy_and_invalid_source(self):
        files = self.build()
        module = self.root / overlay.RUNTIME_FILES[1]
        module.write_bytes(b"CUSTOM = True\n")
        with self.assertRaisesRegex(overlay.OverlayError, "modulo destino"):
            overlay.apply_overlay(self.root, files)
        self.assertEqual(self.view.read_text(), self.active)
        module.unlink()
        self.view.write_text(self.active + "\nNEW_UNREVIEWED_GLOBAL = True\n")
        with self.assertRaisesRegex(overlay.OverlayError, "cambio despues"):
            overlay.apply_overlay(self.root, files)
        self.view.write_text(self.active)
        invalid = {**files, overlay.RUNTIME_FILES[1]: b"def invalid(\n"}
        with self.assertRaisesRegex(overlay.OverlayError, "Python"):
            overlay.apply_overlay(self.root, invalid)
        self.assertEqual(self.view.read_text(), self.active)

    def test_apply_rejects_symlinked_package_without_touching_its_target(self):
        destination = self.root / "destination"
        destination.mkdir()
        link = destination / "facturacion"
        try:
            link.symlink_to(self.root / "facturacion", target_is_directory=True)
        except OSError:
            self.skipTest("La cuenta local no permite crear enlaces simbolicos.")
        with self.assertRaisesRegex(overlay.OverlayError, "enlaces"):
            overlay.apply_overlay(destination, self.build())
        self.assertEqual(self.view.read_text(), self.active)


if __name__ == "__main__":
    unittest.main()
