"""No systemd operations; exercise the maintenance runner with mocks."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from deploy import deploy_app_startup_release as release


class StartupReleaseTests(unittest.TestCase):
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
        with patch.object(release, 'run', return_value='APP_STARTUP_RUNTIME_OK') as run:
            release.django_startup_check(Path('/safe/copy'), 'dvsolutions', 'www-data')
            self.assertEqual(run.call_count, 2)
            calls = str(run.call_args_list)
            self.assertIn('--property=WorkingDirectory=' + str(Path('/safe/copy')), run.call_args_list[0].args[1])
            self.assertNotIn('migrate', calls)
            self.assertNotIn('collectstatic', calls)
            self.assertNotIn('.save(', calls)
            self.assertNotIn('RequestFactory', calls)

    def test_successful_activation_checks_all_four_routes(self):
        routes = {path: 302 for path in release.APP_ROUTES}
        with patch.object(release, 'activate') as activate, patch.object(release, 'http_status', return_value=302) as status:
            self.assertEqual(release.activate_startup(Path('/new'), Path('/old'), Path('/unused'), 'active', 405, routes), routes)
            self.assertEqual(status.call_count, 4)
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
                    release.activate_startup(Path('/new'), Path('/old'), dropin, 'active', 405, {p: 302 for p in release.APP_ROUTES})
                self.assertFalse(dropin.exists())
                self.assertEqual(other.read_text(), 'preserve')
                self.assertEqual(run.call_count, 2)

    def test_changed_dropin_never_removed(self):
        with tempfile.TemporaryDirectory() as temp:
            dropin = Path(temp) / 'own.conf'
            dropin.write_text('concurrent unrelated change', encoding='utf-8')
            with patch.object(release, 'activate'), patch.object(release, 'http_status', return_value=500), patch.object(release, 'run') as run:
                with self.assertRaisesRegex(release.DeploymentError, 'override cambio'):
                    release.activate_startup(Path('/new'), Path('/old'), dropin, 'active', 405, {p: 302 for p in release.APP_ROUTES})
                self.assertTrue(dropin.exists())
                run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
