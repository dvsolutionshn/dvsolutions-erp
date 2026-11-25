"""Closed temporary-files tests; no service, network, account or business data."""
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from deploy import app_startup_overlay as overlay


def source(value):
    return ('\ufeff# Preserve active overlay\nACTIVE_ONLY = "keep"\n'
            'def agenda_mobile_service_worker(request, empresa_slug):\n'
            f'    return "{value}"\n\n'
            'def another_view():\n    return "keep"\n')


class StartupOverlayTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="app-startup-test-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.base = {overlay.VIEWS: source("base").encode(), overlay.TEMPLATE: b'old-template\n', overlay.NAVIGATION: b'old-nav\n'}
        self.target = {overlay.VIEWS: source("target").replace('ACTIVE_ONLY = "keep"', 'ACTIVE_ONLY = "must-not-copy"').encode(),
                       overlay.TEMPLATE: b'new-template\n', overlay.NAVIGATION: b'new-nav\n',
                       **{name: b'new include\n' for name in overlay.NEW_FILES}}
        hashes = {name: overlay.function_digest(overlay.decode(data)) if name == overlay.VIEWS else overlay.digest(data)
                  for name, data in self.target.items()}
        updates = [patch.dict(overlay.TARGET_HASHES, hashes, clear=True),
                   patch.object(overlay, "_git_file", side_effect=lambda repo, commit, name: (self.base if commit == overlay.BASE_COMMIT else self.target)[name])]
        for update in updates:
            update.start()
            self.addCleanup(update.stop)
        self.active = self.release("active")

    def release(self, name):
        root = self.root / name
        for relative, data in self.base.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        (root / 'keep.txt').write_bytes(b'unrelated active deployment')
        return root

    def snapshot(self, root):
        return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob('*') if path.is_file()}

    def test_build_read_only_and_replaces_only_worker_function(self):
        before = self.snapshot(self.active)
        files = overlay.build_overlay(self.active, self.root)
        self.assertEqual(self.snapshot(self.active), before)
        self.assertEqual(set(files), set(overlay.RUNTIME_FILES))
        view = files[overlay.VIEWS].decode()
        self.assertIn('return "target"', view)
        self.assertIn('ACTIVE_ONLY = "keep"', view)
        self.assertNotIn('must-not-copy', view)
        self.assertTrue(view.startswith('\ufeff'))

    def test_apply_inactive_copy_and_idempotence(self):
        files = overlay.build_overlay(self.active, self.root)
        before = self.snapshot(self.active)
        copy = self.release("copy")
        overlay.apply_overlay(copy, files, self.root)
        overlay.apply_overlay(copy, files, self.root)
        self.assertEqual(overlay.build_overlay(copy, self.root), files)
        self.assertEqual(self.snapshot(self.active), before)
        self.assertEqual((copy / 'keep.txt').read_bytes(), b'unrelated active deployment')

    def test_unknown_current_files_rejected_before_writes(self):
        for index, relative in enumerate(overlay.RUNTIME_FILES):
            with self.subTest(relative=relative):
                root = self.release(f"unknown-{index}")
                (root / relative).write_bytes(source("custom").encode() if relative == overlay.VIEWS else b'custom')
                before = self.snapshot(root)
                with self.assertRaises(overlay.OverlayError):
                    overlay.build_overlay(root, self.root)
                self.assertEqual(self.snapshot(root), before)

    def test_partial_reviewed_target_is_accepted(self):
        for name in (*overlay.NEW_FILES, overlay.NAVIGATION):
            (self.active / name).write_bytes(self.target[name].replace(b'\n', b'\r\n'))
        overlay.build_overlay(self.active, self.root)

    def test_extra_missing_tampered_payloads_rejected(self):
        files = overlay.build_overlay(self.active, self.root)
        for invalid in ({**files, '../outside': b'bad'}, {name: data for name, data in files.items() if name != overlay.NEW_FILES[0]},
                        {**files, overlay.NEW_FILES[0]: b'tampered'}, {**files, overlay.VIEWS: b'not python'}):
            with self.subTest(names=list(invalid)):
                before = self.snapshot(self.active)
                with self.assertRaises(overlay.OverlayError):
                    overlay.apply_overlay(self.active, invalid, self.root)
                self.assertEqual(self.snapshot(self.active), before)

    def test_concurrent_unrelated_module_changes_not_silently_discarded(self):
        files = overlay.build_overlay(self.active, self.root)
        with (self.active / overlay.VIEWS).open('ab') as stream:
            stream.write(b'\nNEW_RUNTIME = True\n')
        with self.assertRaises(overlay.OverlayError):
            overlay.apply_overlay(self.active, files, self.root)

    def test_missing_duplicate_and_invalid_worker_function_rejected(self):
        for text in ('def another():\n    pass\n', source('base') + '\ndef agenda_mobile_service_worker():\n    pass\n', 'def bad(\n'):
            with self.subTest(text=text[:30]):
                with self.assertRaises(overlay.OverlayError):
                    overlay.overlay_view(text, source('target'), source('base'))

    def test_crlf_unrelated_bytes_preserved(self):
        old = source('base').replace('\n', '\r\n')
        result = overlay.overlay_view(old, source('target'), source('base'))
        old_block, new_block = overlay.block(old)[2], overlay.block(result)[2]
        self.assertEqual(result.replace(new_block, old_block, 1), old)

    def test_code_symlink_rejected(self):
        path = self.active / overlay.NEW_FILES[0]
        try:
            path.symlink_to(self.active / overlay.TEMPLATE)
        except OSError:
            original = Path.is_symlink
            with patch.object(Path, 'is_symlink', autospec=True, side_effect=lambda item: item == path or original(item)):
                with self.assertRaises(overlay.OverlayError):
                    overlay.build_overlay(self.active, self.root)
            return
        with self.assertRaises(overlay.OverlayError):
            overlay.build_overlay(self.active, self.root)

    def test_unsafe_paths_rejected(self):
        for value in ('../escape', '/escape', 'C:/escape', 'crm\\escape'):
            with self.assertRaises(overlay.OverlayError):
                overlay._checked_path(self.active, value)


class PinnedStartupRehearsalTests(unittest.TestCase):
    def test_real_git_blobs_preserve_active_customizations_and_repeat_safely(self):
        repo = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory(prefix='app-startup-pinned-') as temp:
            active = Path(temp) / 'active'
            for name in (overlay.VIEWS, overlay.TEMPLATE, overlay.NAVIGATION):
                target = active / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(overlay._git_file(repo, overlay.BASE_COMMIT, name))
            view = active / overlay.VIEWS
            with view.open('ab') as stream:
                stream.write(b'\nACTIVE_OVERLAY_REHEARSAL = True\n')
            original = view.read_bytes()
            files = overlay.build_overlay(active, repo)
            self.assertEqual(view.read_bytes(), original)
            copy = Path(temp) / 'inactive-copy'
            shutil.copytree(active, copy)
            overlay.apply_overlay(copy, files, repo)
            actual = (copy / overlay.VIEWS).read_bytes()
            self.assertIn(b'ACTIVE_OVERLAY_REHEARSAL = True', actual)
            before, after = overlay.decode(original), overlay.decode(actual)
            self.assertEqual(after.replace(overlay.block(after)[2], overlay.block(before)[2], 1), before)
            self.assertEqual(overlay.build_overlay(copy, repo), files)
            overlay.apply_overlay(copy, files, repo)
            self.assertEqual(view.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
