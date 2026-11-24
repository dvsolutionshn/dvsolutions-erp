"""Pure deployment orchestration tests; no SSH, Django, DB or real systemctl."""

from contextlib import nullcontext
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from deploy import deploy_app_search_release as deployment


class AppSearchReleaseTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="app-search-release-test-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.live = self.root / "source"
        self.releases = self.root / "releases"
        self.active = self.releases / "previous-active"
        self.backups = self.root / "backups"
        self.dropins = self.root / "service.d"
        for path in (self.live, self.active, self.backups, self.dropins):
            path.mkdir(parents=True)
        self.env = self.root / "runtime.env"
        self.env.write_text("SYNTHETIC_PLACEHOLDER=not-a-credential\n", encoding="utf-8")
        self.old_dropin = self.dropins / "99-previous-clinical.conf"
        self.old_dropin.write_bytes(b"PREVIOUS_OVERRIDE_PRESERVED\n")
        self.files = {name: ("synthetic target " + name + "\n").encode() for name in deployment.RUNTIME_FILES}
        self.write(self.active / "crm/views.py", b"SYNTHETIC_OLD_VIEWS\n")
        self.write(self.active / "crm/templates/crm/agenda_mobile.html", b"SYNTHETIC_OLD_TEMPLATE\n")
        self.write(self.active / "facturacion/inventory-overlay.txt", b"KEEP_INVENTORY\n")
        self.write(self.active / "crm/whatsapp-overlay.txt", b"KEEP_WHATSAPP\n")
        self.values = {"WorkingDirectory": str(self.active), "MainPID": "406005", "User": "dvsolutions", "Group": "www-data", "DropInPaths": self.old_dropin.as_posix()}
        self.output = io.StringIO()
        self.patch("LIVE", self.live)
        self.patch("RELEASES", self.releases)
        self.patch("BACKUPS", self.backups)
        self.patch("DROPINS", self.dropins)
        self.patch("ENV_FILE", self.env)
        self.patch("deployment_lock", return_value=nullcontext())
        self.patch("property_value", side_effect=lambda name: self.values[name])
        self.patch("state", return_value="active")
        self.patch("process_cwd", return_value=self.active)
        self.patch("healthy", return_value=True)
        self.build = self.patch("build_overlay", return_value=self.files)
        self.static = self.patch("verify_shared_static")
        self.copy = self.patch("copy_release", side_effect=lambda source, destination: shutil.copytree(source, destination))
        self.apply = self.patch("apply_overlay", side_effect=self.apply_inactive)
        self.check = self.patch("django_app_check")
        self.activate_app = self.patch("activate_app", return_value=dict.fromkeys(deployment.APP_ROUTES, 302))
        self.patch("http_status", side_effect=lambda path: 403 if "whatsapp" in path else 302)
        self.run = self.patch("run")
        uid = patch.object(deployment.os, "geteuid", return_value=0, create=True)
        uid.start()
        self.addCleanup(uid.stop)
        stdout = patch("sys.stdout", self.output)
        stdout.start()
        self.addCleanup(stdout.stop)

    def patch(self, name, *args, **kwargs):
        context = patch.object(deployment, name, *args, **kwargs)
        mocked = context.start()
        self.addCleanup(context.stop)
        return mocked

    @staticmethod
    def write(path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    @staticmethod
    def snapshot(path):
        return {file.relative_to(path).as_posix(): file.read_bytes() for file in path.rglob("*") if file.is_file()}

    def apply_inactive(self, destination, files):
        self.assertNotEqual(destination, self.active)
        for name, content in files.items():
            self.write(destination / name, content)

    def assert_original_preserved(self, snapshot):
        self.assertEqual(self.snapshot(self.active), snapshot)
        self.assertEqual(self.old_dropin.read_bytes(), b"PREVIOUS_OVERRIDE_PRESERVED\n")

    def test_non_root_refuses_before_any_discovery_or_copy(self):
        with patch.object(deployment.os, "geteuid", return_value=1000):
            with self.assertRaises(deployment.DeploymentError):
                deployment.deploy()
        self.build.assert_not_called()
        self.copy.assert_not_called()
        self.activate_app.assert_not_called()

    def test_wrong_live_process_directory_refuses_without_mutation(self):
        before = self.snapshot(self.active)
        with patch.object(deployment, "process_cwd", return_value=self.live):
            with self.assertRaisesRegex(deployment.DeploymentError, "no coincide"):
                deployment.deploy()
        self.build.assert_not_called()
        self.copy.assert_not_called()
        self.assert_original_preserved(before)

    def test_active_directory_outside_releases_is_rejected(self):
        self.values["WorkingDirectory"] = str(self.live)
        with self.assertRaises(deployment.DeploymentError):
            deployment.deploy()
        self.build.assert_not_called()
        self.copy.assert_not_called()

    def test_unhealthy_existing_service_is_not_changed(self):
        before = self.snapshot(self.active)
        with patch.object(deployment, "healthy", return_value=False):
            with self.assertRaisesRegex(deployment.DeploymentError, "no se modifico"):
                deployment.deploy()
        self.copy.assert_not_called()
        self.assert_original_preserved(before)

    def test_unreviewed_overlay_fails_before_copy_or_backups(self):
        before = self.snapshot(self.active)
        self.build.side_effect = deployment.OverlayError("unreviewed synthetic source")
        with self.assertRaises(deployment.OverlayError):
            deployment.deploy()
        self.static.assert_not_called()
        self.copy.assert_not_called()
        self.assertEqual(list(self.backups.iterdir()), [])
        self.assert_original_preserved(before)

    def test_wrong_static_assets_fail_without_writing_them_or_copying_runtime(self):
        before = self.snapshot(self.active)
        self.static.side_effect = deployment.DeploymentError("wrong synthetic asset")
        with self.assertRaises(deployment.DeploymentError):
            deployment.deploy()
        self.copy.assert_not_called()
        self.activate_app.assert_not_called()
        self.assert_original_preserved(before)

    def test_already_active_still_checks_static_and_does_not_restart_or_copy(self):
        for name, content in self.files.items():
            self.write(self.active / name, content)
        before = self.snapshot(self.active)
        deployment.deploy()
        self.assertEqual(json.loads(self.output.getvalue())["status"], "ALREADY_ACTIVE")
        self.static.assert_called_once_with(self.active, self.files)
        self.copy.assert_not_called()
        self.check.assert_not_called()
        self.activate_app.assert_not_called()
        self.assertEqual(list(self.backups.iterdir()), [])
        self.assert_original_preserved(before)

    def test_success_prepares_only_new_release_backups_and_web_activation_contract(self):
        before = self.snapshot(self.active)
        deployment.deploy()
        result = json.loads(self.output.getvalue())
        self.assertEqual(result["status"], "DEPLOY_OK")
        self.assertEqual(result["app_search_commit"], deployment.FIX_COMMIT)
        self.assertEqual(result["changed_files"], sorted(self.files))
        navigation = "crm/templates/crm/mobile_agenda_navigation.html"
        self.assertEqual(len(result["changed_files"]), 8)
        self.assertIn(navigation, result["changed_files"])
        self.assertFalse(result["migrations"])
        self.assertFalse(result["shared_static_changed"])
        release, backup, dropin = Path(result["release"]), Path(result["backup"]), Path(result["dropin"])
        self.assertEqual(release.parent, self.releases)
        self.assertEqual(backup.parent, self.backups)
        self.assertEqual(dropin.parent, self.dropins)
        self.assertTrue(dropin.name.startswith("z-app-search-"))
        self.assertGreater(dropin.name, self.old_dropin.name)
        self.copy.assert_called_once_with(self.active, release)
        self.apply.assert_called_once_with(release, self.files)
        self.check.assert_called_once_with(release, "dvsolutions", "www-data")
        self.activate_app.assert_called_once_with(release, self.active, dropin, "active", 403, dict.fromkeys(deployment.APP_ROUTES, 302))
        self.assertEqual(self.static.call_count, 2)
        for name, content in self.files.items():
            self.assertEqual((release / name).read_bytes(), content)
        for name, content in before.items():
            if name not in self.files:
                self.assertEqual((release / name).read_bytes(), content)
        self.assertEqual((backup / "crm/views.py").read_bytes(), before["crm/views.py"])
        self.assertEqual((backup / "crm/templates/crm/agenda_mobile.html").read_bytes(), before["crm/templates/crm/agenda_mobile.html"])
        self.assertFalse((backup / "facturacion/inventory-overlay.txt").exists())
        self.assertFalse((self.active / navigation).exists())
        self.assertFalse((backup / navigation).exists())
        self.assert_original_preserved(before)
        self.run.assert_not_called()

    def test_failed_django_preflight_does_not_activate_or_alter_original(self):
        before = self.snapshot(self.active)
        self.check.side_effect = deployment.DeploymentError("synthetic preflight failed")
        with self.assertRaises(deployment.DeploymentError):
            deployment.deploy()
        self.activate_app.assert_not_called()
        self.assertEqual(list(self.backups.iterdir()), [])
        self.assert_original_preserved(before)

    def test_concurrent_activation_is_not_overwritten(self):
        before = self.snapshot(self.active)
        self.check.side_effect = lambda *args: self.values.update(WorkingDirectory=str(self.live))
        with self.assertRaisesRegex(deployment.DeploymentError, "cambio durante"):
            deployment.deploy()
        self.activate_app.assert_not_called()
        self.assertEqual(list(self.backups.iterdir()), [])
        self.assert_original_preserved(before)

    def test_concurrent_reviewed_file_change_is_preserved_and_activation_stops(self):
        self.check.side_effect = lambda *args: (self.active / "crm/views.py").write_bytes(b"OPERATOR_CHANGE\n")
        with self.assertRaisesRegex(deployment.DeploymentError, "cambio durante"):
            deployment.deploy()
        self.activate_app.assert_not_called()
        self.assertEqual((self.active / "crm/views.py").read_bytes(), b"OPERATOR_CHANGE\n")

    def test_asset_change_during_preparation_stops_before_activation(self):
        self.static.side_effect = [None, deployment.DeploymentError("asset changed")]
        with self.assertRaises(deployment.DeploymentError):
            deployment.deploy()
        self.activate_app.assert_not_called()
        self.assertEqual(list(self.backups.iterdir()), [])

    def test_existing_candidate_override_is_not_overwritten(self):
        before = self.old_dropin.read_bytes()
        with patch.object(deployment, "choose_dropin", return_value=self.old_dropin):
            with self.assertRaisesRegex(deployment.DeploymentError, "no se sobrescribio"):
                deployment.deploy()
        self.copy.assert_not_called()
        self.activate_app.assert_not_called()
        self.assertEqual(self.old_dropin.read_bytes(), before)


class AppSearchDropinOrderingTests(unittest.TestCase):
    STAMP = "20261123-120000-123456"

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="app-search-dropin-test-")
        self.addCleanup(temporary.cleanup)
        self.dropins = Path(temporary.name).resolve()
        override = patch.object(deployment, "DROPINS", self.dropins)
        override.start()
        self.addCleanup(override.stop)

    def choose(self, basenames):
        metadata = " ".join("/etc/systemd/system/dvsolutions.service.d/" + name for name in basenames)
        with patch.object(deployment, "property_value", return_value=metadata) as value:
            result = deployment.choose_dropin(self.STAMP)
        value.assert_called_once_with("DropInPaths")
        self.assertEqual(result.parent, deployment.DROPINS)
        self.assertLessEqual(len(result.name), 240)
        for name in basenames:
            self.assertGreater(result.name, name)
        return result

    def test_many_existing_99_internal_z_names_sort_before_the_new_prefix(self):
        names = ["99-" + "z" * length + "-respuestas-app-248c10ed.conf" for length in range(1, 16)]
        result = self.choose(names)
        self.assertEqual(result.name, f"z-app-search-{self.STAMP}.conf")

    def test_previous_z_prefixes_are_outsorted_without_changing_them(self):
        names = ["z-app-search-old.conf", "zz-clinica.conf", "zzzzzzzz-whatsapp.conf", "99-existing.conf"]
        result = self.choose(names)
        self.assertEqual(result.name, f"{'z' * 9}-app-search-{self.STAMP}.conf")

    def test_empty_metadata_uses_safe_simple_name(self):
        result = self.choose([])
        self.assertEqual(result.name, f"z-app-search-{self.STAMP}.conf")

    def test_unloaded_override_on_disk_also_determines_safe_ordering(self):
        unloaded = self.dropins / "zzzz-not-yet-loaded.conf"
        unloaded.write_bytes(b"PRESERVE_UNLOADED\n")
        result = self.choose([])
        self.assertEqual(result.name, f"zzzzz-app-search-{self.STAMP}.conf")
        self.assertEqual(unloaded.read_bytes(), b"PRESERVE_UNLOADED\n")

    def test_unknown_non_ascii_or_unsafe_filenames_are_rejected(self):
        for name in ("zz~late.conf", "zz;command.conf", "zz-résumé.conf", "zz-no-extension", "zz-other.config", "zz\\escaped.conf"):
            with self.subTest(name=name), patch.object(deployment, "property_value", return_value="/etc/systemd/system/dvsolutions.service.d/" + name):
                with self.assertRaises(deployment.DeploymentError):
                    deployment.choose_dropin(self.STAMP)

    def test_prefix_that_would_exceed_safe_filename_length_is_rejected(self):
        with patch.object(deployment, "property_value", return_value="/etc/systemd/system/dvsolutions.service.d/" + "z" * 230 + ".conf"):
            with self.assertRaises(deployment.DeploymentError):
                deployment.choose_dropin(self.STAMP)


class AppSearchActivationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="app-search-activation-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.previous = self.root / "previous"
        self.release = self.root / "candidate"
        self.dropin = self.root / "99-new.conf"
        self.other = self.root / "99-previous.conf"
        self.other.write_text("DO NOT TOUCH", encoding="utf-8")
        self.before = dict.fromkeys(deployment.APP_ROUTES, 302)

    def create_override(self, release, dropin, *args, **kwargs):
        dropin.write_text(f"# Commit {deployment.FIX_COMMIT}\n[Service]\nWorkingDirectory={release}\n", encoding="utf-8")

    def test_success_passes_pinned_commit_and_keeps_previous_overrides(self):
        with patch.object(deployment, "activate", side_effect=self.create_override) as activate, patch.object(deployment, "http_status", return_value=302), patch.object(deployment, "run") as run:
            result = deployment.activate_app(self.release, self.previous, self.dropin, "active", 403, self.before)
        self.assertEqual(result, self.before)
        activate.assert_called_once_with(self.release, self.dropin, "active", 403, commit=deployment.FIX_COMMIT)
        self.assertTrue(self.dropin.exists())
        self.assertEqual(self.other.read_text(), "DO NOT TOUCH")
        run.assert_not_called()

    def test_regressed_app_route_rolls_back_only_its_own_override(self):
        with patch.object(deployment, "activate", side_effect=self.create_override), patch.object(deployment, "http_status", return_value=500), patch.object(deployment, "healthy", return_value=True), patch.object(deployment, "property_value", return_value=str(self.previous)), patch.object(deployment, "run") as run:
            with self.assertRaisesRegex(deployment.DeploymentError, "restaurada: True"):
                deployment.activate_app(self.release, self.previous, self.dropin, "active", 403, self.before)
        self.assertFalse(self.dropin.exists())
        self.assertEqual(self.other.read_text(), "DO NOT TOUCH")
        self.assertEqual([call.args[1] for call in run.call_args_list], [["systemctl", "daemon-reload"], ["systemctl", "restart", deployment.WEB]])

    def test_changed_override_is_never_deleted_on_failed_route(self):
        def changed(release, dropin, *args, **kwargs):
            dropin.write_text("OPERATOR NEW CONFIG", encoding="utf-8")
        with patch.object(deployment, "activate", side_effect=changed), patch.object(deployment, "http_status", return_value=500), patch.object(deployment, "run") as run:
            with self.assertRaisesRegex(deployment.DeploymentError, "override tambien"):
                deployment.activate_app(self.release, self.previous, self.dropin, "active", 403, self.before)
        self.assertEqual(self.dropin.read_text(), "OPERATOR NEW CONFIG")
        self.assertEqual(self.other.read_text(), "DO NOT TOUCH")
        run.assert_not_called()

    def test_failed_base_activation_is_propagated_without_deleting_other_files(self):
        with patch.object(deployment, "activate", side_effect=deployment.DeploymentError("base stopped")), patch.object(deployment, "http_status") as status:
            with self.assertRaisesRegex(deployment.DeploymentError, "base stopped"):
                deployment.activate_app(self.release, self.previous, self.dropin, "active", 403, self.before)
        status.assert_not_called()
        self.assertEqual(self.other.read_text(), "DO NOT TOUCH")


class AppSearchPreflightTests(unittest.TestCase):
    def test_django_check_uses_existing_environment_and_service_identity_without_migrations(self):
        with patch.object(deployment, "run", side_effect=["", "APP_SEARCH_RUNTIME_OK"]) as run:
            deployment.django_app_check(Path("/synthetic/release"), "dvsolutions", "www-data")
        first, second = (call.args[1] for call in run.call_args_list)
        self.assertEqual(first[-2:], ["manage.py", "check"])
        self.assertIn("--property=User=dvsolutions", first)
        self.assertIn("--property=Group=www-data", first)
        self.assertIn("--property=EnvironmentFile=" + str(deployment.ENV_FILE), first)
        self.assertEqual(second[-2], "-c")
        self.assertIn("crm/mobile_appointment_search.py", second[-1])
        self.assertIn("get_template", second[-1])
        self.assertIn("mobile_agenda_navigation.html", second[-1])
        for forbidden in ("migrate", "collectstatic", "procesar_recordatorios", "create_user", "enviar_"):
            self.assertNotIn(forbidden, " ".join(first + second))

    def test_wrong_runtime_origin_marker_is_rejected(self):
        with patch.object(deployment, "run", return_value="WRONG_RUNTIME"):
            with self.assertRaisesRegex(deployment.DeploymentError, "no cargo"):
                deployment.django_app_check(Path("/synthetic/release"), "dvsolutions", "www-data")


class AppSearchStaticVerificationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="app-search-static-test-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.live = self.root / "source"
        self.active = self.root / "active"
        self.shared = self.live / "staticfiles"
        self.link = self.active / "staticfiles"
        self.shared.mkdir(parents=True)
        self.link.mkdir(parents=True)
        self.files = {}
        for source, relative in deployment.STATIC_FILES.items():
            content = ("synthetic published asset " + source + "\n").encode()
            self.files[source] = content
            path = self.shared / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        self.original_resolve = Path.resolve
        self.original_is_symlink = Path.is_symlink
        live = patch.object(deployment, "LIVE", self.live)
        live.start()
        self.addCleanup(live.stop)
        # Model a checked shared link without requiring Windows symlink rights.
        def resolved(path, *args, **kwargs):
            if path == self.link:
                return self.shared
            return self.original_resolve(path, *args, **kwargs)
        def is_link(path):
            return path == self.link or self.original_is_symlink(path)
        resolve = patch.object(Path, "resolve", resolved)
        resolve.start()
        self.addCleanup(resolve.stop)
        links = patch.object(Path, "is_symlink", is_link)
        links.start()
        self.addCleanup(links.stop)

    def test_matching_shared_assets_are_read_without_modification(self):
        before = AppSearchReleaseTests.snapshot(self.shared)
        deployment.verify_shared_static(self.active, self.files)
        self.assertEqual(AppSearchReleaseTests.snapshot(self.shared), before)

    def test_wrong_and_missing_shared_assets_refuse_instead_of_collectstatic(self):
        asset = self.shared / next(iter(deployment.STATIC_FILES.values()))
        asset.write_bytes(b"OTHER_PUBLISHED_VERSION\n")
        with self.assertRaisesRegex(deployment.DeploymentError, "collectstatic"):
            deployment.verify_shared_static(self.active, self.files)
        self.assertEqual(asset.read_bytes(), b"OTHER_PUBLISHED_VERSION\n")
        asset.unlink()
        with self.assertRaisesRegex(deployment.DeploymentError, "collectstatic"):
            deployment.verify_shared_static(self.active, self.files)
        self.assertFalse(asset.exists())

    def test_unexpected_asset_resolution_does_not_read_or_write_outside_shared_root(self):
        asset = self.shared / next(iter(deployment.STATIC_FILES.values()))
        outside = self.root / "private-outside.txt"
        outside.write_bytes(b"KEEP_OUTSIDE\n")
        def resolved(path, *args, **kwargs):
            if path == self.link:
                return self.shared
            if path == asset:
                return outside
            return self.original_resolve(path, *args, **kwargs)
        with patch.object(Path, "resolve", resolved):
            with self.assertRaisesRegex(deployment.DeploymentError, "ruta inesperada"):
                deployment.verify_shared_static(self.active, self.files)
        self.assertEqual(outside.read_bytes(), b"KEEP_OUTSIDE\n")

    def test_non_shared_static_root_is_refused(self):
        with patch.object(Path, "is_symlink", return_value=False):
            with self.assertRaisesRegex(deployment.DeploymentError, "compartidos"):
                deployment.verify_shared_static(self.active, self.files)


if __name__ == "__main__":
    unittest.main()
