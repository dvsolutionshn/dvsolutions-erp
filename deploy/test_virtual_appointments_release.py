"""No systemd operations; exercise the maintenance runner with mocks."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from deploy import deploy_virtual_appointments_release as release


class VirtualReleaseTests(unittest.TestCase):
    def test_wrong_platform_stops_before_any_service_call(self):
        with patch.object(release.os, 'geteuid', return_value=1, create=True), patch.object(release, 'property_value') as service:
            with self.assertRaises(release.DeploymentError):
                release.deploy()
            service.assert_not_called()

    def test_unsafe_service_directory_stops_before_reading_service(self):
        unsafe = MagicMock()
        unsafe.is_symlink.return_value = True
        with patch.object(release.os, 'geteuid', return_value=0, create=True), \
             patch.object(release, 'LIVE', unsafe), patch.object(release, 'property_value') as service:
            with self.assertRaises(release.DeploymentError):
                release.deploy()
            service.assert_not_called()

    def test_django_check_uses_copy_and_only_template_compilation(self):
        with patch.object(release, 'run', return_value='VIRTUAL_APPOINTMENT_RUNTIME_OK') as run:
            release.django_virtual_check(Path('/safe/copy'), 'dvsolutions', 'www-data')
            self.assertEqual(run.call_count, 2)
            calls = str(run.call_args_list)
            self.assertIn('--property=WorkingDirectory=' + str(Path('/safe/copy')), run.call_args_list[0].args[1])
            self.assertNotIn('migrate', calls)
            self.assertNotIn('collectstatic', calls)
            self.assertNotIn('.save(', calls)
            self.assertNotIn('RequestFactory', calls)

    def test_successful_activation_checks_all_four_routes(self):
        routes = {path: 302 for path in release.CHECK_ROUTES}
        with patch.object(release, 'activate') as activate, patch.object(release, 'http_status', return_value=302) as status:
            self.assertEqual(release.activate_virtual(Path('/new'), Path('/old'), Path('/unused'), 'active', 405, routes), routes)
            self.assertEqual(status.call_count, 8)
            self.assertEqual(activate.call_args.kwargs['commit'], release.FIX_COMMIT)

    def test_route_regression_removes_only_own_dropin_and_restores(self):
        with tempfile.TemporaryDirectory() as temp:
            dropin = Path(temp) / 'own.conf'
            dropin.write_text(f'# Commit {release.FIX_COMMIT}\n[Service]\nWorkingDirectory={Path("/new")}\n', encoding='utf-8')
            other = Path(temp) / 'other.conf'
            other.write_text('preserve', encoding='utf-8')
            with patch.object(release, 'activate'), patch.object(release, 'http_status', return_value=500), \
                 patch.object(release, 'run') as run, patch.object(release, 'healthy', return_value=True), \
                 patch.object(release, 'property_value', return_value='/old'):
                with self.assertRaisesRegex(release.DeploymentError, 'restaurada: True'):
                    release.activate_virtual(Path('/new'), Path('/old'), dropin, 'active', 405, {p: 302 for p in release.CHECK_ROUTES})
                self.assertFalse(dropin.exists())
                self.assertEqual(other.read_text(), 'preserve')
                self.assertEqual(run.call_count, 2)

    def test_changed_dropin_never_removed(self):
        with tempfile.TemporaryDirectory() as temp:
            dropin = Path(temp) / 'own.conf'
            dropin.write_text('concurrent unrelated change', encoding='utf-8')
            with patch.object(release, 'activate'), patch.object(release, 'http_status', return_value=500), patch.object(release, 'run') as run:
                with self.assertRaisesRegex(release.DeploymentError, 'override cambio'):
                    release.activate_virtual(Path('/new'), Path('/old'), dropin, 'active', 405, {p: 302 for p in release.CHECK_ROUTES})
                self.assertTrue(dropin.exists())
                run.assert_not_called()

    def test_schema_preflight_does_not_apply(self):
        output = 'VIRTUAL_SCHEMA_STATUS {"changed":[],"pending":[["crm","0027_citacliente_virtual"]]}'
        with patch.object(release, 'run', return_value=output) as run:
            result = release.schema_stage(Path('/copy'), 'app', 'group')
            self.assertEqual(result['changed'], [])
            code = run.call_args.args[1][-1]
            self.assertIn('_,plan=inspect_schema()', code)
            self.assertNotIn('changed=apply_schema()', code)
            self.assertIn('virtual_appointments_schema.py', code)

    def test_concurrent_code_guard_ignores_private_shared_directories(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'facturacion').mkdir()
            code = root / 'facturacion/views.py'
            code.write_text('original code', encoding='utf-8')
            (root / 'media').mkdir()
            (root / 'media/never-read.py').write_text('private synthetic upload', encoding='utf-8')
            env = root / '.env'
            env.write_text('private synthetic setting', encoding='utf-8')
            before = release.code_snapshot(root)
            self.assertEqual(set(before), {str(Path('facturacion/views.py'))})
            env.write_text('changed private setting', encoding='utf-8')
            self.assertEqual(release.code_snapshot(root), before)
            code.write_text('concurrent code change', encoding='utf-8')
            self.assertNotEqual(release.code_snapshot(root), before)

    def test_schema_apply_calls_only_reviewed_helper(self):
        with patch.object(release, 'run', return_value='VIRTUAL_SCHEMA_STATUS {"changed":[],"pending":[]}') as run:
            release.schema_stage(Path('/copy'), 'app', 'group', apply=True)
            code = run.call_args.args[1][-1]
            self.assertIn('changed=apply_schema()', code)
            self.assertNotIn('manage.py migrate', code)
            self.assertNotIn('collectstatic', code)
            self.assertNotIn('DropField', code)

    def test_schema_malformed_status_fails_closed(self):
        for output in ('', 'VIRTUAL_SCHEMA_STATUS {}',
                       'VIRTUAL_SCHEMA_STATUS {"changed":"invalid","pending":[]}',
                       'VIRTUAL_SCHEMA_STATUS {}\\nVIRTUAL_SCHEMA_STATUS {}'):
            with self.subTest(output=output), patch.object(release, 'run', return_value=output):
                with self.assertRaises(release.DeploymentError):
                    release.schema_stage(Path('/copy'), 'app', 'group')


if __name__ == '__main__':
    unittest.main()


