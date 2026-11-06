"""Filesystem bounds and rollback for the inventory release activation."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from deploy import deploy_inventory_release as deployment


class InventoryReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="inventory-release-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.release = self.root / "release"
        (self.release / "facturacion").mkdir(parents=True)
        self.dropin = self.root / "service.d" / "99-inventario-test.conf"

    def test_runtime_file_cannot_escape_the_new_release(self):
        with self.assertRaises(deployment.DeploymentError):
            deployment.write_runtime_files(self.release, {"../escape.py": b"unsafe"})
        self.assertFalse((self.root / "escape.py").exists())

    def test_existing_override_is_never_overwritten(self):
        self.dropin.parent.mkdir()
        self.dropin.write_text("existing configuration", encoding="utf-8")
        with patch.object(deployment, "run") as run:
            with self.assertRaises(deployment.DeploymentError):
                deployment.activate(self.release, self.dropin, "active", 403)
        self.assertEqual(self.dropin.read_text(encoding="utf-8"), "existing configuration")
        run.assert_not_called()

    def test_failed_health_check_restores_the_previous_override_chain(self):
        with patch.object(deployment, "run") as run, patch.object(
            deployment, "property_value", return_value=str(self.release),
        ), patch.object(deployment, "healthy", side_effect=[False, True]), patch.object(
            deployment.subprocess, "run",
        ) as rollback:
            with self.assertRaisesRegex(deployment.DeploymentError, "restaurado: True"):
                deployment.activate(self.release, self.dropin, "active", 403)
        self.assertFalse(self.dropin.exists())
        self.assertEqual(run.call_count, 2)
        self.assertEqual([call.args[0] for call in rollback.call_args_list], [
            ["systemctl", "daemon-reload"],
            ["systemctl", "restart", deployment.WEB],
        ])

    def test_success_changes_only_web_working_directory(self):
        def value(name):
            return str(self.release) if name == "WorkingDirectory" else "123"

        with patch.object(deployment, "run") as run, patch.object(
            deployment, "property_value", side_effect=value,
        ), patch.object(deployment, "healthy", return_value=True), patch.object(
            deployment, "state", return_value="active",
        ), patch.object(deployment, "http_status", return_value=403), patch.object(
            Path, "resolve", return_value=self.release,
        ):
            deployment.activate(self.release, self.dropin, "active", 403)
        content = self.dropin.read_text(encoding="utf-8")
        self.assertIn(f"WorkingDirectory={self.release}", content)
        self.assertNotIn("ExecStart", content)
        self.assertNotIn("Environment", content)
        self.assertEqual([call.args[1] for call in run.call_args_list], [
            ["systemctl", "daemon-reload"],
            ["systemctl", "restart", deployment.WEB],
        ])

    def test_changed_whatsapp_route_rolls_back_web_activation(self):
        def value(name):
            return str(self.release) if name == "WorkingDirectory" else "123"

        with patch.object(deployment, "run"), patch.object(
            deployment, "property_value", side_effect=value,
        ), patch.object(deployment, "healthy", return_value=True), patch.object(
            deployment, "http_status", return_value=404,
        ), patch.object(Path, "resolve", return_value=self.release), patch.object(
            deployment.subprocess, "run",
        ):
            with self.assertRaises(deployment.DeploymentError):
                deployment.activate(self.release, self.dropin, "active", 403)
        self.assertFalse(self.dropin.exists())


if __name__ == "__main__":
    unittest.main()
