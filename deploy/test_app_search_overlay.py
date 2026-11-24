"""Pure temporary-directory tests; no Django, database or service operations."""
import ast
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from deploy import app_search_overlay as overlay


def source(version):
    return (
        '"""Runtime con integracion particular preservada."""\n'
        'from __future__ import annotations\n\n'
        'WHATSAPP_RUNTIME = "KEEP-ORIGINAL"\n\n'
        'def agenda_mobile(request=None, empresa_slug=None):\n'
        f'    return "{version}"\n\n'
        'def custom_whatsapp_handler():\n'
        '    return "KEEP-ORIGINAL"\n'
    )


class AppSearchOverlayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="app-search-overlay-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base_source = source("baseline")
        self.target_source = source("target").replace('WHATSAPP_RUNTIME = "KEEP-ORIGINAL"', 'WHATSAPP_RUNTIME = "DO-NOT-COPY"')
        self.base_template = b'<section id="agenda">Original</section>\n'
        self.target_template = b'<section id="agenda">Search</section>\n'
        self.blobs = {
            overlay.VIEWS: self.target_source.encode(),
            overlay.TEMPLATE: self.target_template,
            overlay.NEW_FILES[0]: b'def appointment_search():\n    return "names-only"\n',
            overlay.NEW_FILES[1]: b'.appointment-search { display: grid; }\n',
            overlay.NEW_FILES[2]: b'(() => { "use strict"; })();\n',
            overlay.NEW_FILES[3]: b'<section>Buscar citas</section>\n',
            overlay.NEW_FILES[4]: b'<section>Buscar facturas</section>\n',
            overlay.NEW_FILES[5]: b'<script>window.mobileAgendaNavigation = {};</script>\n',
        }
        updates = (
            patch.object(overlay, "BASE_FUNCTION_HASH", overlay._function_digest(overlay._function_block(self.base_source)[2])),
            patch.object(overlay, "TARGET_FUNCTION_HASH", overlay._function_digest(overlay._function_block(self.target_source)[2])),
            patch.object(overlay, "BASE_TEMPLATE_HASH", overlay._file_digest(self.base_template)),
            patch.dict(overlay.TARGET_FILE_HASHES, {name: overlay._file_digest(content) for name, content in self.blobs.items() if name != overlay.VIEWS}, clear=True),
            patch.object(overlay, "_git_file", side_effect=lambda repo, commit, name: self.blobs[name]),
        )
        for update in updates:
            update.start()
            self.addCleanup(update.stop)
        self.active = self.make_release("active")

    def make_release(self, name, *, view=None, template=None):
        directory = self.root / name
        directory.mkdir()
        for relative in overlay.RUNTIME_FILES:
            (directory / relative).parent.mkdir(parents=True, exist_ok=True)
        (directory / overlay.VIEWS).write_bytes((view if view is not None else self.base_source).encode())
        (directory / overlay.TEMPLATE).write_bytes(template if template is not None else self.base_template)
        (directory / "whatsapp-preserve.txt").write_bytes(b"LOCAL-RUNTIME")
        return directory

    def snapshot(self, root):
        return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file() and not path.is_symlink()}

    def build(self, root=None):
        return overlay.build_overlay(root or self.active, self.root)

    def test_build_is_read_only_exact_eight_files_and_preserves_other_functions(self):
        before = self.snapshot(self.active)
        result = self.build()
        self.assertEqual(set(result), set(overlay.RUNTIME_FILES))
        self.assertEqual(self.snapshot(self.active), before)
        views = result[overlay.VIEWS].decode()
        self.assertIn('return "target"', views)
        self.assertNotIn("DO-NOT-COPY", views)
        self.assertIn('WHATSAPP_RUNTIME = "KEEP-ORIGINAL"', views)
        self.assertIn('def custom_whatsapp_handler():\n    return "KEEP-ORIGINAL"', views)
        ast.parse(views)

    def test_apply_to_copy_preserves_active_source_and_unrelated_copy_files(self):
        before = self.snapshot(self.active)
        destination = self.make_release("copy")
        (destination / overlay.VIEWS).chmod(0o640)
        mode = stat.S_IMODE((destination / overlay.VIEWS).stat().st_mode)
        result = self.build()
        overlay.apply_overlay(destination, result)
        self.assertEqual(self.snapshot(self.active), before)
        self.assertEqual((destination / "whatsapp-preserve.txt").read_bytes(), b"LOCAL-RUNTIME")
        self.assertEqual(stat.S_IMODE((destination / overlay.VIEWS).stat().st_mode), mode)
        for relative, content in result.items():
            self.assertEqual((destination / relative).read_bytes(), content)

    def test_target_is_idempotent(self):
        destination = self.make_release("target")
        result = self.build()
        overlay.apply_overlay(destination, result)
        overlay.apply_overlay(destination, result)
        self.assertEqual(overlay.build_overlay(destination, self.root), result)

    def test_mixed_reviewed_pairs_complete_without_overwriting_unrelated_code(self):
        for index, (view, template) in enumerate(((self.base_source, self.target_template), (source("target"), self.base_template))):
            destination = self.make_release(f"mixed-{index}", view=view, template=template)
            new_file = destination / overlay.NEW_FILES[2]
            new_file.write_bytes(self.blobs[overlay.NEW_FILES[2]].replace(b"\n", b"\r\n"))
            result = self.build(destination)
            overlay.apply_overlay(destination, result)
            self.assertEqual((destination / overlay.TEMPLATE).read_bytes(), self.target_template)
            self.assertIn('return "target"', (destination / overlay.VIEWS).read_text())
            self.assertIn("KEEP-ORIGINAL", (destination / overlay.VIEWS).read_text())

    def test_crlf_and_bom_preserve_raw_unrelated_view_bytes(self):
        active = "\ufeff" + self.base_source.replace("\n", "\r\n")
        result = overlay.overlay_views(active, self.target_source)
        _, _, old = overlay._function_block(active)
        _, _, new = overlay._function_block(result)
        self.assertEqual(result.replace(new, old, 1), active)
        self.assertTrue(result.startswith("\ufeff"))
        destination = self.make_release("crlf", view=active, template=self.base_template.replace(b"\n", b"\r\n"))
        result_files = self.build(destination)
        overlay.apply_overlay(destination, result_files)
        self.assertEqual((destination / overlay.VIEWS).read_bytes(), result.encode())

    def test_unknown_active_function_is_rejected_before_writes(self):
        for index, value in enumerate((self.base_source.replace('return "baseline"', 'return "custom"'), self.base_source.replace("def agenda_mobile", "@custom\ndef agenda_mobile"))):
            destination = self.make_release(f"unknown-view-{index}", view=value)
            before = self.snapshot(destination)
            with self.assertRaisesRegex(overlay.OverlayError, "no revisados"):
                self.build(destination)
            self.assertEqual(self.snapshot(destination), before)

    def test_unknown_active_template_is_rejected_before_writes(self):
        destination = self.make_release("unknown-template", template=b"CUSTOM\n")
        with self.assertRaisesRegex(overlay.OverlayError, "plantilla movil"):
            self.build(destination)

    def test_existing_custom_search_file_is_rejected_before_writes(self):
        (self.active / overlay.NEW_FILES[0]).write_bytes(b"CUSTOM = True\n")
        before = self.snapshot(self.active)
        with self.assertRaisesRegex(overlay.OverlayError, "no revisados"):
            self.build()
        self.assertEqual(self.snapshot(self.active), before)

    def test_unknown_commit_and_tampered_git_target_are_rejected(self):
        with self.assertRaisesRegex(overlay.OverlayError, "commit solicitado"):
            overlay.build_overlay(self.active, self.root, commit="0" * 40)
        for relative in overlay.RUNTIME_FILES:
            with self.subTest(relative=relative):
                original = self.blobs[relative]
                self.blobs[relative] = b"CUSTOM\n"
                with self.assertRaises(overlay.OverlayError):
                    self.build()
                self.blobs[relative] = original

    def test_missing_duplicate_or_invalid_python_function_is_rejected(self):
        for active in (self.base_source.replace("def agenda_mobile", "def other_mobile"), self.base_source + "\n" + self.base_source, "def agenda_mobile(\n", self.base_source + "\nreturn 'invalid-module'\n"):
            with self.subTest(active=active[:20]):
                with self.assertRaises(overlay.OverlayError):
                    overlay.overlay_views(active, self.target_source)

    def test_apply_rejects_extra_missing_malformed_paths_and_contents_before_writes(self):
        result = self.build()
        destination = self.make_release("invalid-copy")
        before = self.snapshot(destination)
        invalid = []
        for relative in ("../outside.py", "/outside.py", "C:/outside.py", "crm\\outside.py"):
            invalid.append({**result, relative: b"INVALID\n"})
        invalid.extend((
            {name: content for name, content in result.items() if name != overlay.NEW_FILES[0]},
            {**result, overlay.NEW_FILES[0]: b"def broken(\n"},
            {**result, overlay.TEMPLATE: b"CUSTOM\n"},
            {**result, overlay.NEW_FILES[1]: b""},
            {**result, overlay.NEW_FILES[1]: "NOT BYTES"},
        ))
        for files in invalid:
            with self.subTest(keys=list(files)):
                with self.assertRaises(overlay.OverlayError):
                    overlay.apply_overlay(destination, files)
                self.assertEqual(self.snapshot(destination), before)

    def test_apply_rejects_unrelated_changes_after_build(self):
        destination = self.make_release("changed-copy")
        result = self.build()
        view = destination / overlay.VIEWS
        view.write_bytes(view.read_bytes() + b"\nNEW_LOCAL_CHANGE = True\n")
        before = self.snapshot(destination)
        with self.assertRaisesRegex(overlay.OverlayError, "cambio despues"):
            overlay.apply_overlay(destination, result)
        self.assertEqual(self.snapshot(destination), before)

    def test_safe_path_rejects_parent_absolute_and_windows_paths(self):
        for relative in ("../outside.py", "/outside.py", "C:/outside.py", "crm\\outside.py"):
            with self.subTest(relative=relative):
                with self.assertRaises(overlay.OverlayError):
                    overlay._checked_path(self.active, relative)

    def test_symlink_root_and_code_component_guards_without_os_link_privileges(self):
        before = self.snapshot(self.active)
        with patch.object(Path, "is_symlink", autospec=True, side_effect=lambda path: path == self.active):
            with self.assertRaisesRegex(overlay.OverlayError, "directorio regular"):
                self.build()
        with patch.object(Path, "is_symlink", autospec=True, side_effect=lambda path: path == self.active / "crm"):
            with self.assertRaisesRegex(overlay.OverlayError, "enlaces"):
                self.build()
        self.assertEqual(self.snapshot(self.active), before)

    def test_real_os_symlink_components_do_not_touch_external_targets(self):
        external = self.root / "external"
        external.mkdir()
        (external / "views.py").write_bytes(self.base_source.encode())
        destination = self.root / "linked-copy"
        destination.mkdir()
        try:
            (destination / "crm").symlink_to(external, target_is_directory=True)
        except OSError:
            self.skipTest("La cuenta local no permite crear enlaces simbolicos.")
        before = (external / "views.py").read_bytes()
        with self.assertRaisesRegex(overlay.OverlayError, "enlaces"):
            self.build(destination)
        self.assertEqual((external / "views.py").read_bytes(), before)
        linked_root = self.root / "linked-root"
        linked_root.symlink_to(self.active, target_is_directory=True)
        with self.assertRaisesRegex(overlay.OverlayError, "directorio regular"):
            self.build(linked_root)


class PublishedAppSearchCompatibilityTests(unittest.TestCase):
    """Reproduce the observed variant solely from immutable published commits."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="app-search-published-")
        self.addCleanup(self.temp.cleanup)
        self.active = Path(self.temp.name)
        self.repo = Path(__file__).resolve().parent.parent
        for name in overlay.RUNTIME_FILES:
            (self.active / name).parent.mkdir(parents=True, exist_ok=True)
        self.base_views = overlay._git_file(self.repo, overlay.BASE_COMMIT, overlay.VIEWS)
        self.base_template = overlay._git_file(self.repo, overlay.BASE_COMMIT, overlay.TEMPLATE)
        (self.active / overlay.VIEWS).write_bytes(self.base_views)
        (self.active / overlay.TEMPLATE).write_bytes(self.base_template)

    def reconstruct_live_template(self):
        # Reverse the date fix using its original full-context patch, in the
        # disposable fixture only. Never run a fuzzy patch against a release.
        date_patch = subprocess.check_output([
            "git", "-C", str(self.repo), "diff", "--unified=3",
            "f912467001bdf1a208da754fb188540435d9c209^",
            "f912467001bdf1a208da754fb188540435d9c209", "--", overlay.TEMPLATE,
        ])
        result = subprocess.run(
            ["git", "-c", "core.autocrlf=false", "apply", "--reverse"],
            cwd=self.active, input=date_patch, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, "Pinned date patch did not match the disposable fixture.")
        content = (self.active / overlay.TEMPLATE).read_bytes().decode().replace("\r\n", "\n")
        navigation_patch = subprocess.check_output([
            "git", "-C", str(self.repo), "diff", "--unified=3",
            "e5642bff02fbd1f5ba1c14bfa59878d425880828^",
            "e5642bff02fbd1f5ba1c14bfa59878d425880828", "--", overlay.TEMPLATE,
        ]).decode().replace("\r\n", "\n")
        # Later reception changes affect context adjacent to the include, not
        # the extraction itself. Reverse only each exact changed-line group,
        # anchoring deleted groups to a unique retained subsequent context.
        hunks, current = [], None
        for line in navigation_patch.splitlines(keepends=True):
            if line.startswith("@@"):
                if current is not None:
                    hunks.append(current)
                current = []
            elif current is not None and line[:1] in (" ", "+", "-"):
                current.append(line)
        if current is not None:
            hunks.append(current)
        self.assertTrue(hunks)
        for hunk in hunks:
            cursor = 0
            while cursor < len(hunk):
                if hunk[cursor][:1] == " ":
                    cursor += 1
                    continue
                old, new = [], []
                while cursor < len(hunk) and hunk[cursor][:1] in ("+", "-"):
                    (new if hunk[cursor][0] == "+" else old).append(hunk[cursor][1:])
                    cursor += 1
                old, new = "".join(old), "".join(new)
                if new:
                    self.assertEqual(content.count(new), 1, "Changed navigation lines must be unique.")
                    content = content.replace(new, old, 1)
                else:
                    context = ""
                    for retained in hunk[cursor:]:
                        if retained[0] != " ":
                            break
                        context += retained[1:]
                        if content.count(context) == 1:
                            break
                    self.assertTrue(context)
                    self.assertEqual(content.count(context), 1, "Retained navigation context must be unique.")
                    content = content.replace(context, old + context, 1)
        return content.encode()

    def test_exact_observed_variant_installs_navigation_date_and_search_target(self):
        variant = self.reconstruct_live_template()
        self.assertEqual(overlay._file_digest(variant), overlay.COMPATIBLE_TEMPLATE_HASH)
        self.assertEqual(overlay._file_digest(variant), "d9ca0818966df43cfd612bb587fba2a91c09da9b691f66f4082144e1306ecb10")
        (self.active / overlay.TEMPLATE).write_bytes(variant.replace(b"\n", b"\r\n"))
        self.assertFalse((self.active / overlay.NEW_FILES[-1]).exists())
        files = overlay.build_overlay(self.active, self.repo)
        self.assertEqual(len(files), 8)
        self.assertEqual((self.active / overlay.VIEWS).read_bytes(), self.base_views)
        overlay.apply_overlay(self.active, files)  # This is an inactive temporary fixture.
        for name in (overlay.TEMPLATE, *overlay.NEW_FILES):
            self.assertEqual((self.active / name).read_bytes(), overlay._git_file(self.repo, overlay.FIX_COMMIT, name))
        _, _, base_function = overlay._function_block(self.base_views.decode())
        view = (self.active / overlay.VIEWS).read_bytes().decode()
        _, _, new_function = overlay._function_block(view)
        self.assertEqual(view.replace(new_function, base_function, 1), self.base_views.decode())
        self.assertIn('name="fecha"', (self.active / overlay.TEMPLATE).read_text(encoding="utf-8"))
        self.assertIn('window.mobileAgendaNavigation', (self.active / overlay.NEW_FILES[-1]).read_text(encoding="utf-8"))
        self.assertEqual(overlay.build_overlay(self.active, self.repo), files)

    def test_single_unreviewed_byte_in_observed_variant_is_rejected(self):
        variant = self.reconstruct_live_template() + b"\n"
        template = self.active / overlay.TEMPLATE
        template.write_bytes(variant)
        with self.assertRaisesRegex(overlay.OverlayError, "plantilla movil"):
            overlay.build_overlay(self.active, self.repo)
        self.assertEqual(template.read_bytes(), variant)
        self.assertEqual((self.active / overlay.VIEWS).read_bytes(), self.base_views)
        self.assertTrue(all(not (self.active / name).exists() for name in overlay.NEW_FILES))


if __name__ == "__main__":
    unittest.main()
