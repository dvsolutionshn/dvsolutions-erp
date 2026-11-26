"""Pure temporary-file overlay tests; no Django, database or external transport."""
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from deploy import virtual_appointments_overlay as overlay


def module_source(names, version):
    text = '\ufeff# Preserve the active release\nfrom datetime import date\n\nFISCAL_POLICY = "unchanged"\n\n'
    for name in names:
        if name.startswith("Cita"):
            text += f'class {name}:\n    value = "{version}"\n\n'
        else:
            text += f'def {name}(value=None):\n    return "{version}"\n\n'
    return text + 'def fiscal_control():\n    return "canonical-fiscal-control"\n'


class VirtualAppointmentOverlayTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="virtual-appointments-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.base = {}
        self.target = {}
        for name, blocks in overlay.BLOCKS.items():
            self.base[name] = module_source(blocks, "base").encode()
            target = module_source(blocks, "target")
            self.target[name] = (overlay.ensure_import(target) if name in overlay.IMPORT_FILES else target).encode()
        for name in overlay.FILES:
            self.base[name] = b'PAYLOAD = "base"\n' if name.endswith('.py') else b'baseline template\n'
            self.target[name] = b'PAYLOAD = "target"\n' if name.endswith('.py') else b'reviewed virtual template\n'
        for name in overlay.NEW_FILES:
            self.target[name] = b'ONLY_ADD_FIELD = True\n' if name.endswith('.py') else b'reviewed virtual badge\n'
        hashes = {}
        for name, data in self.target.items():
            if name in overlay.BLOCKS:
                hashes.update({name + "::" + block: overlay.block_digest(text)
                               for block, (_, _, text) in overlay.blocks(overlay.decode(data), overlay.BLOCKS[name]).items()})
            else:
                hashes[name] = overlay.digest(data)
        hashes[overlay.OPTIONAL_MERGE] = overlay.digest(self.target[overlay.MERGE_TEMPLATE])
        for update in (
            patch.dict(overlay.TARGET_HASHES, hashes, clear=True),
            patch.object(overlay, "_git_file", side_effect=self.git_file),
        ):
            update.start()
            self.addCleanup(update.stop)
        self.active = self.release("active")

    def git_file(self, repo, commit, name):
        self.assertIn(commit, (overlay.BASE_COMMIT, overlay.FIX_COMMIT))
        return (self.base if commit == overlay.BASE_COMMIT else self.target)[name]

    def release(self, name, *, custom=True):
        release = self.root / name
        for relative, data in self.base.items():
            path = release / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            if custom and relative in overlay.BLOCKS:
                data += b'\nACTIVE_ONLY_FISCAL_GUARD = "keep-exactly"\n'
            path.write_bytes(data)
        for relative in overlay.NEW_FILES:
            if '/includes/' not in relative:
                (release / relative).parent.mkdir(parents=True, exist_ok=True)
        (release / 'unrelated.txt').write_bytes(b'original unrelated active content')
        return release

    @staticmethod
    def snapshot(release):
        return {path.relative_to(release).as_posix(): path.read_bytes()
                for path in release.rglob('*') if path.is_file()}

    def install_file(self, release, name, data):
        path = release / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def test_build_is_read_only_and_replaces_exactly_seven_reviewed_blocks(self):
        self.assertEqual(sum(map(len, overlay.BLOCKS.values())), 7)
        before = self.snapshot(self.active)
        files = overlay.build_overlay(self.active, self.root)
        self.assertEqual(before, self.snapshot(self.active))
        self.assertEqual(set(files), set(overlay.RUNTIME_FILES))
        for name, names in overlay.BLOCKS.items():
            with self.subTest(name=name):
                original, changed = overlay.decode(before[name]), overlay.decode(files[name])
                self.assertEqual(overlay.outside(original, names), overlay.outside(changed, names))
                self.assertIn('ACTIVE_ONLY_FISCAL_GUARD = "keep-exactly"', changed)
                self.assertIn('return "canonical-fiscal-control"', changed)
                self.assertTrue(changed.startswith('\ufeff'))
                for _, _, text in overlay.blocks(changed, names).values():
                    self.assertIn('"target"', text)
                if name in overlay.IMPORT_FILES:
                    self.assertEqual(changed.count(overlay.ADDED_IMPORT), 1)

    def test_known_installed_branch_gets_empty_merge_without_replacing_branch(self):
        branch = b'# Existing integration code is not part of this change\n'
        self.install_file(self.active, overlay.COMPAT_BRANCH, branch)
        before = self.snapshot(self.active)
        with patch.object(overlay, 'COMPAT_BRANCH_HASH', overlay.digest(branch)):
            files = overlay.build_overlay(self.active, self.root)
            self.assertEqual(files[overlay.OPTIONAL_MERGE], self.target[overlay.MERGE_TEMPLATE])
            self.assertNotIn(overlay.COMPAT_BRANCH, files)
            copy = self.root / 'known-branch-copy'
            shutil.copytree(self.active, copy)
            overlay.apply_overlay(copy, files, self.root)
            self.assertEqual((copy / overlay.COMPAT_BRANCH).read_bytes(), branch)
            self.assertEqual(overlay.build_overlay(copy, self.root), files)
        self.assertEqual(self.snapshot(self.active), before)

    def test_unknown_old_branch_or_orphan_merge_fails_before_writes(self):
        self.install_file(self.active, overlay.COMPAT_BRANCH, b'unknown integration code')
        before = self.snapshot(self.active)
        with self.assertRaises(overlay.OverlayError):
            overlay.build_overlay(self.active, self.root)
        self.assertEqual(self.snapshot(self.active), before)
        other = self.release('orphan-merge')
        self.install_file(other, overlay.OPTIONAL_MERGE, self.target[overlay.MERGE_TEMPLATE])
        with self.assertRaises(overlay.OverlayError):
            overlay.build_overlay(other, self.root)

    def test_existing_custom_merge_is_not_overwritten(self):
        branch = b'# Existing reviewed integration\n'
        self.install_file(self.active, overlay.COMPAT_BRANCH, branch)
        self.install_file(self.active, overlay.OPTIONAL_MERGE, b'custom merge code')
        before = self.snapshot(self.active)
        with patch.object(overlay, 'COMPAT_BRANCH_HASH', overlay.digest(branch)), self.assertRaises(overlay.OverlayError):
            overlay.build_overlay(self.active, self.root)
        self.assertEqual(self.snapshot(self.active), before)

    def test_apply_only_inactive_copy_and_repeat_is_idempotent(self):
        files = overlay.build_overlay(self.active, self.root)
        before = self.snapshot(self.active)
        inactive = self.root / 'inactive'
        shutil.copytree(self.active, inactive)
        overlay.apply_overlay(inactive, files, self.root)
        first = self.snapshot(inactive)
        overlay.apply_overlay(inactive, files, self.root)
        self.assertEqual(first, self.snapshot(inactive))
        self.assertEqual(files, overlay.build_overlay(inactive, self.root))
        self.assertEqual(before, self.snapshot(self.active))
        self.assertEqual((inactive / 'unrelated.txt').read_bytes(), b'original unrelated active content')

    def test_mixed_baseline_and_target_blocks_and_files_complete_safely(self):
        for index, (name, names) in enumerate(overlay.BLOCKS.items()):
            current = overlay.decode((self.active / name).read_bytes())
            baseline = overlay.blocks(current, names)
            desired = overlay.blocks(overlay.decode(self.target[name]), names)
            chosen = names[index % len(names)]
            current = current.replace(baseline[chosen][2], desired[chosen][2], 1)
            (self.active / name).write_bytes(current.encode())
        for name in (*overlay.NEW_FILES, overlay.FILES[0]):
            self.install_file(self.active, name, self.target[name].replace(b'\n', b'\r\n'))
        files = overlay.build_overlay(self.active, self.root)
        overlay.apply_overlay(self.active, files, self.root)
        self.assertEqual(files, overlay.build_overlay(self.active, self.root))

    def test_each_unknown_active_block_is_rejected_before_writes(self):
        index = 0
        for name, names in overlay.BLOCKS.items():
            for block in names:
                with self.subTest(name=name, block=block):
                    release = self.release(f'unknown-block-{index}')
                    index += 1
                    current = overlay.decode((release / name).read_bytes())
                    text = overlay.blocks(current, names)[block][2]
                    (release / name).write_bytes(current.replace(text, text.replace('"base"', '"unknown"'), 1).encode())
                    before = self.snapshot(release)
                    with self.assertRaises(overlay.OverlayError):
                        overlay.build_overlay(release, self.root)
                    self.assertEqual(before, self.snapshot(release))

    def test_unknown_whole_files_or_existing_new_files_are_rejected(self):
        for index, name in enumerate((*overlay.FILES, *overlay.NEW_FILES)):
            with self.subTest(name=name):
                release = self.release(f'unknown-file-{index}')
                self.install_file(release, name, b'UNKNOWN = True\n')
                before = self.snapshot(release)
                with self.assertRaises(overlay.OverlayError):
                    overlay.build_overlay(release, self.root)
                self.assertEqual(before, self.snapshot(release))

    def test_target_cannot_change_unreviewed_code_outside_blocks(self):
        name = next(iter(overlay.BLOCKS))
        self.target[name] += b'\nCHANGED_FISCAL_CODE = True\n'
        before = self.snapshot(self.active)
        with self.assertRaises(overlay.OverlayError):
            overlay.build_overlay(self.active, self.root)
        self.assertEqual(before, self.snapshot(self.active))

    def test_absent_new_include_directory_is_created_only_in_inactive_copy(self):
        name = next(name for name in overlay.NEW_FILES if '/includes/' in name)
        self.assertFalse((self.active / name).parent.exists())
        files = overlay.build_overlay(self.active, self.root)
        inactive = self.root / 'new-includes'
        shutil.copytree(self.active, inactive)
        overlay.apply_overlay(inactive, files, self.root)
        self.assertEqual((inactive / name).read_bytes(), self.target[name])
        self.assertFalse((self.active / name).parent.exists())

    def test_extra_missing_tampered_and_invalid_payloads_cannot_apply(self):
        files = overlay.build_overlay(self.active, self.root)
        first = next(iter(overlay.BLOCKS))
        invalid = [
            {**files, '../escape': b'bad'},
            {name: data for name, data in files.items() if name != overlay.NEW_FILES[0]},
            {**files, overlay.NEW_FILES[0]: b'tampered'},
            {**files, first: b'def invalid(\n'},
            {**files, first: files[first].replace(b'"target"', b'"tampered"', 1)},
        ]
        for payload in invalid:
            with self.subTest(paths=tuple(payload)):
                before = self.snapshot(self.active)
                with self.assertRaises(overlay.OverlayError):
                    overlay.apply_overlay(self.active, payload, self.root)
                self.assertEqual(before, self.snapshot(self.active))

    def test_concurrent_unrelated_edit_is_not_overwritten_during_apply(self):
        files = overlay.build_overlay(self.active, self.root)
        name = next(iter(overlay.BLOCKS))
        with (self.active / name).open('ab') as stream:
            stream.write(b'\nLATEST_UNRELATED_GUARD = True\n')
        before = self.snapshot(self.active)
        with self.assertRaises(overlay.OverlayError):
            overlay.apply_overlay(self.active, files, self.root)
        self.assertEqual(before, self.snapshot(self.active))

    def test_crlf_and_bom_outside_reviewed_blocks_are_preserved_byte_for_byte(self):
        for name in overlay.BLOCKS:
            data = (self.active / name).read_bytes().replace(b'\n', b'\r\n')
            (self.active / name).write_bytes(data)
        files = overlay.build_overlay(self.active, self.root)
        for name, names in overlay.BLOCKS.items():
            original = overlay.decode((self.active / name).read_bytes())
            changed = overlay.decode(files[name])
            before = overlay.blocks(original, names)
            after = overlay.blocks(changed, names)
            restored = changed
            for block in names:
                restored = restored.replace(after[block][2], before[block][2], 1)
            if name in overlay.IMPORT_FILES:
                restored = restored.replace(overlay.ADDED_IMPORT, '', 1)
            self.assertEqual(restored, original)

    def test_missing_duplicate_and_invalid_python_blocks_fail_closed(self):
        for source in ('def another():\n    pass\n',
                       'class CitaCliente:\n    pass\nclass CitaCliente:\n    pass\n',
                       'class CitaCliente(\n'):
            with self.subTest(source=source):
                with self.assertRaises(overlay.OverlayError):
                    overlay.blocks(source, ('CitaCliente',))

    def test_missing_or_corrupt_reviewed_manifest_fails_closed(self):
        for hashes in ({}, {**overlay.TARGET_HASHES, overlay.NEW_FILES[0]: '0' * 64}):
            with patch.dict(overlay.TARGET_HASHES, hashes, clear=True):
                with self.assertRaises(overlay.OverlayError):
                    overlay.build_overlay(self.active, self.root)

    def test_expected_import_is_idempotent_and_incorrect_alias_is_not_accepted(self):
        source = 'from core.clinical_permissions import EMPRESAS_CLINICAS_CON_ROLES as EMPRESAS_CLINICAS_CON_ROLES\n'
        self.assertEqual(overlay.ensure_import(source), source)
        source = 'from core.clinical_permissions import EMPRESAS_CLINICAS_CON_ROLES as OTHER_NAME\n'
        changed = overlay.ensure_import(source)
        self.assertTrue(changed.endswith(overlay.ADDED_IMPORT))
        self.assertEqual(overlay.ensure_import(changed), changed)
        with self.assertRaises(overlay.OverlayError):
            overlay.ensure_import('ONLY_CONSTANT = True\n')

    def test_unsafe_paths_are_rejected(self):
        for name in ('../escape', '/escape', 'C:/escape', 'crm\\escape'):
            with self.subTest(name=name), self.assertRaises(overlay.OverlayError):
                overlay._checked_path(self.active, name)

    def test_code_symlinks_are_rejected_without_following_them(self):
        original = Path.is_symlink
        candidates = [self.active, self.active / next(iter(overlay.BLOCKS)),
                      self.active / next(name for name in overlay.NEW_FILES if '/includes/' in name).rsplit('/', 1)[0]]
        for suspicious in candidates:
            with self.subTest(path=str(suspicious)), patch.object(
                Path, 'is_symlink', autospec=True,
                side_effect=lambda path, suspicious=suspicious: path == suspicious or original(path),
            ):
                with self.assertRaises(overlay.OverlayError):
                    overlay.build_overlay(self.active, self.root)


class PinnedVirtualAppointmentsRehearsalTests(unittest.TestCase):
    def test_actual_reviewed_blobs_preserve_other_runtime_customizations(self):
        repo = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory(prefix="virtual-pinned-rehearsal-") as temp:
            active = Path(temp) / "active"
            for name in (*overlay.BLOCKS, *overlay.FILES):
                path = active / name
                path.parent.mkdir(parents=True, exist_ok=True)
                content = overlay._git_file(repo, overlay.BASE_COMMIT, name)
                if name in overlay.BLOCKS:
                    content += b'\nACTIVE_CUSTOM_OUTSIDE = "KEEP"\n'
                path.write_bytes(content)
            (active / "deploy").mkdir()
            for app in ("crm", "clinica"):
                (active / app / "migrations").mkdir()
            (active / "deploy/keep.py").write_bytes(b"KEEP_OLDER_DEPLOY_TOOLS = True\n")
            (active / "facturacion").mkdir()
            (active / "facturacion/views.py").write_bytes(b"KEEP_FISCAL_OVERLAY = True\n")
            before = {name: (active / name).read_bytes() for name in (*overlay.BLOCKS, *overlay.FILES)}
            files = overlay.build_overlay(active, repo)
            copy = Path(temp) / "inactive-copy"
            shutil.copytree(active, copy)
            overlay.apply_overlay(copy, files, repo)
            for name, names in overlay.BLOCKS.items():
                self.assertEqual(overlay.outside(overlay.decode((copy / name).read_bytes()), names),
                                 overlay.outside(overlay.decode(before[name]), names))
            self.assertEqual((copy / "facturacion/views.py").read_bytes(), b"KEEP_FISCAL_OVERLAY = True\n")
            self.assertEqual((copy / "deploy/keep.py").read_bytes(), b"KEEP_OLDER_DEPLOY_TOOLS = True\n")
            self.assertEqual(overlay.build_overlay(copy, repo), files)
            overlay.apply_overlay(copy, files, repo)
            self.assertEqual(before, {name: (active / name).read_bytes() for name in before})


if __name__ == '__main__':
    unittest.main()
