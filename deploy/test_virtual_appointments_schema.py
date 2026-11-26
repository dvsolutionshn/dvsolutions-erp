"""Closed migration safety tests: no configured Django database or transport."""

import importlib
import hashlib
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from django.db import models
from django.db.migrations import Migration
from django.db.migrations.operations.fields import AddField, RemoveField

from deploy import virtual_appointments_schema as schema


def reviewed_migration(app, name):
    migration = Migration(name, app)
    migration.operations = [AddField(
        model_name=schema.MODELS[app], name="virtual",
        field=models.BooleanField("Cita virtual", default=False, db_default=False),
    )]
    return migration


class VirtualAppointmentSchemaSafetyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="virtual-schema-review-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.enterContext(patch.object(schema, "_candidate_root", return_value=self.root))
        self.disk = {target: reviewed_migration(*target) for target in schema.TARGETS}
        self.applied = set()
        self.present = set()
        self.connection = MagicMock()
        self.connection.vendor = "sqlite"
        self.connection.features.can_rollback_ddl = True
        self.cursor = MagicMock()
        self.connection.cursor.return_value.__enter__.return_value = self.cursor
        self.connection.introspection.get_table_description.side_effect = self.description
        self.connection.introspection.get_field_type.return_value = "BooleanField"
        self.executor = MagicMock()
        self.executor.loader.disk_migrations = self.disk
        self.executor.loader.applied_migrations = self.applied
        self.executor.loader.detect_conflicts.return_value = {}
        self.executor.migration_plan.side_effect = self.plan
        self.executor.migrate.side_effect = self.migrate
        self.factory = self.enterContext(patch("django.db.migrations.executor.MigrationExecutor", return_value=self.executor))
        self.enterContext(patch("django.db.connection", self.connection))

    def description(self, cursor, table):
        app = next(app for app, value in schema.TABLES.items() if value == table)
        return [SimpleNamespace(name="virtual", type_code="boolean", null_ok=False, default="false")] if app in self.present else []

    def plan(self, targets):
        with_merge = schema.MERGE_TARGET in self.disk
        expected = [schema.TARGETS[0], schema.MERGE_TARGET] if with_merge else list(schema.TARGETS)
        self.assertEqual(targets, expected)
        pending = (*schema.TARGETS, schema.MERGE_TARGET) if with_merge else schema.TARGETS
        return [(self.disk[target], False) for target in pending if target not in self.applied]

    def migrate(self, targets):
        expected = [schema.TARGETS[0], schema.MERGE_TARGET] if schema.MERGE_TARGET in self.disk else list(schema.TARGETS)
        self.assertEqual(targets, expected)
        self.applied.update(schema.TARGETS)
        if schema.MERGE_TARGET in self.disk:
            self.applied.add(schema.MERGE_TARGET)
        self.present.update(schema.TABLES)

    def known_merge(self):
        directory = self.root / "crm" / "migrations"
        directory.mkdir(parents=True)
        legacy_file = directory / (schema.LEGACY_WEBHOOKS[1] + ".py")
        merge_file = directory / (schema.MERGE_TARGET[1] + ".py")
        legacy_body = b"# Closed synthetic witness; never import or run this branch.\n"
        legacy_file.write_bytes(legacy_body)
        merge_body = ("from django.db import migrations\n\n\nclass Migration(migrations.Migration):\n"
            "    atomic = True\n    dependencies = " + repr(list(schema.MERGE_DEPENDENCIES)) + "\n    operations = []\n")
        merge_file.write_text(merge_body, encoding="utf-8")
        self.enterContext(patch.object(schema, "LEGACY_WEBHOOKS_HASH", hashlib.sha256(legacy_body).hexdigest()))
        self.enterContext(patch.object(schema, "_assert_migration_path"))
        legacy = Migration(schema.LEGACY_WEBHOOKS[1], schema.LEGACY_WEBHOOKS[0])
        legacy.dependencies = [("crm", "0026_bloqueo_disponibilidad_fecha_fin")]
        merge = Migration(schema.MERGE_TARGET[1], schema.MERGE_TARGET[0])
        merge.dependencies = list(schema.MERGE_DEPENDENCIES)
        merge.operations = []
        self.disk[schema.LEGACY_WEBHOOKS] = legacy
        self.disk[schema.MERGE_TARGET] = merge
        self.applied.add(schema.LEGACY_WEBHOOKS)
        return legacy_file, merge_file

    def assert_refused(self):
        with self.assertRaises(schema.SchemaSafetyError):
            schema.inspect_schema()
        self.executor.migrate.assert_not_called()

    def test_plan_accepts_only_forward_reviewed_targets_and_idempotent_empty(self):
        schema.validate_plan(self.plan(list(schema.TARGETS)))
        schema.validate_plan([])

    def test_plan_rejects_reverse_unrelated_and_unreviewed_same_app_migrations(self):
        for app, name, backwards in (
            (*schema.TARGETS[0], True), ("auth", "0012_alter_user_first_name_max_length", False),
            ("crm", "9999_unreviewed", False),
        ):
            with self.subTest(app=app, name=name, backwards=backwards):
                migration = Migration(name, app)
                with self.assertRaises(schema.SchemaSafetyError):
                    schema.validate_plan([(migration, backwards)])

    def test_nontransactional_database_is_refused_before_executor(self):
        self.connection.features.can_rollback_ddl = False
        self.assert_refused()
        self.factory.assert_not_called()

    def test_inconsistent_history_stops_before_schema_introspection(self):
        self.executor.loader.check_consistent_history.side_effect = RuntimeError("inconsistent synthetic history")
        with self.assertRaisesRegex(RuntimeError, "inconsistent"):
            schema.inspect_schema()
        self.connection.introspection.get_table_description.assert_not_called()
        self.executor.migrate.assert_not_called()

    def test_conflicting_graph_is_refused_before_schema_introspection(self):
        self.executor.loader.detect_conflicts.return_value = {"crm": ["0027_a", "0027_b"]}
        self.assert_refused()
        self.connection.introspection.get_table_description.assert_not_called()

    def test_clean_missing_columns_are_the_only_pending_forward_plan(self):
        executor, plan = schema.inspect_schema()
        self.assertIs(executor, self.executor)
        self.assertEqual([(m.app_label, m.name, backwards) for m, backwards in plan],
            [(app, name, False) for app, name in schema.TARGETS])
        self.executor.loader.check_consistent_history.assert_called_once_with(self.connection)
        self.executor.migrate.assert_not_called()

    def test_history_and_column_presence_must_match_in_both_directions(self):
        for target in schema.TARGETS:
            for exists_without_history in (True, False):
                with self.subTest(target=target, exists_without_history=exists_without_history):
                    self.applied.clear(); self.present.clear()
                    if exists_without_history:
                        self.present.add(target[0])
                    else:
                        self.applied.add(target)
                    self.assert_refused()

    def test_existing_applied_column_must_be_boolean(self):
        self.applied.update(schema.TARGETS)
        self.present.update(schema.TABLES)
        self.connection.introspection.get_field_type.return_value = "CharField"
        self.assert_refused()

    def test_existing_column_must_be_nonnullable_with_false_database_default(self):
        self.applied.update(schema.TARGETS)
        self.present.update(schema.TABLES)
        for null_ok, default in ((True, "false"), (None, "false"), (False, None), (False, "true"), (False, "1")):
            with self.subTest(null_ok=null_ok, default=default):
                self.connection.introspection.get_table_description.side_effect = None
                self.connection.introspection.get_table_description.return_value = [
                    SimpleNamespace(name="virtual", type_code="boolean", null_ok=null_ok, default=default),
                ]
                self.assert_refused()

    def test_supported_postgresql_and_sqlite_false_defaults_are_accepted(self):
        self.applied.update(schema.TARGETS)
        self.present.update(schema.TABLES)
        for default in ("false", "0", "(false)", "(0)"):
            with self.subTest(default=default):
                self.connection.introspection.get_table_description.side_effect = None
                self.connection.introspection.get_table_description.return_value = [
                    SimpleNamespace(name="virtual", type_code="boolean", null_ok=False, default=default),
                ]
                self.assertEqual(schema.inspect_schema()[1], [])

    def test_missing_or_extra_migration_operations_are_refused(self):
        target = schema.TARGETS[0]
        for replacement in (None, [], [self.disk[target].operations[0], self.disk[target].operations[0]]):
            with self.subTest(replacement=replacement):
                original = self.disk[target]
                if replacement is None:
                    self.disk.pop(target)
                    self.executor.migration_plan.side_effect = lambda targets: []
                else:
                    self.disk[target] = reviewed_migration(*target)
                    self.disk[target].operations = replacement
                self.assert_refused()
                self.disk[target] = original
                self.executor.migration_plan.side_effect = self.plan

    def test_non_addfield_wrong_model_name_type_or_defaults_are_refused(self):
        target = schema.TARGETS[0]
        cases = [RemoveField(model_name=schema.MODELS[target[0]], name="virtual")]
        cases.extend(AddField(model_name=model, name=name, field=field) for model, name, field in (
            ("other_model", "virtual", models.BooleanField(default=False)),
            (schema.MODELS[target[0]], "other_column", models.BooleanField(default=False)),
            (schema.MODELS[target[0]], "virtual", models.CharField(max_length=10, default=False)),
            (schema.MODELS[target[0]], "virtual", models.BooleanField(default=True)),
            (schema.MODELS[target[0]], "virtual", models.BooleanField(default=False, null=True)),
            (schema.MODELS[target[0]], "virtual", models.BooleanField(default=False, primary_key=True)),
            (schema.MODELS[target[0]], "virtual", models.BooleanField(default=False, unique=True)),
        ))
        for operation in cases:
            with self.subTest(operation=operation):
                self.disk[target].operations = [operation]
                self.assert_refused()

    def test_published_migrations_are_exactly_two_additive_false_fields(self):
        for app, name in schema.TARGETS:
            module = importlib.import_module(f"{app}.migrations.{name}")
            migration = module.Migration(name, app)
            self.assertTrue(migration.atomic)
            self.assertEqual(len(migration.operations), 1)
            operation = migration.operations[0]
            self.assertIs(type(operation), AddField)
            self.assertEqual((operation.model_name, operation.name), (schema.MODELS[app], "virtual"))
            self.assertIs(type(operation.field), models.BooleanField)
            self.assertIs(operation.field.default, False)
            self.assertIs(operation.field.db_default, False)
            self.assertTrue(operation.field.has_db_default())
            self.assertFalse(operation.field.null)
            self.assertFalse(operation.field.db_index)
            self.assertIsNone(operation.field.db_column)

    def test_nonatomic_default_removal_or_other_columns_indexes_are_refused(self):
        target = schema.TARGETS[0]
        invalid = [
            ("atomic", False), ("preserve_default", False),
            ("db_column", "another_column"), ("db_index", True),
            ("db_default", True), ("db_default", models.NOT_PROVIDED),
        ]
        for attribute, value in invalid:
            with self.subTest(attribute=attribute, value=value):
                migration = reviewed_migration(*target)
                self.disk[target] = migration
                if attribute == "atomic":
                    migration.atomic = value
                elif attribute == "preserve_default":
                    migration.operations[0].preserve_default = value
                else:
                    setattr(migration.operations[0].field, attribute, value)
                self.assert_refused()

    def test_apply_uses_only_exact_targets_and_fresh_postvalidation(self):
        self.assertEqual(schema.apply_schema(), list(schema.TARGETS))
        self.executor.migrate.assert_called_once_with(list(schema.TARGETS))
        self.assertEqual(self.factory.call_count, 2)
        self.assertEqual(self.executor.loader.check_consistent_history.call_count, 2)
        self.cursor.execute.assert_not_called()

    def test_already_applied_is_readonly_and_repeated_application_idempotent(self):
        self.assertEqual(schema.apply_schema(), list(schema.TARGETS))
        self.assertEqual(schema.apply_schema(), [])
        self.executor.migrate.assert_called_once_with(list(schema.TARGETS))

    def test_postgresql_timeouts_are_bounded_and_no_other_sql_is_issued(self):
        self.connection.vendor = "postgresql"
        schema.apply_schema()
        self.assertEqual([call.args for call in self.cursor.execute.call_args_list], [
            ("SET lock_timeout = '5s'",), ("SET statement_timeout = '30s'",),
        ])
        self.executor.migrate.assert_called_once_with(list(schema.TARGETS))

    def test_unfinished_application_is_detected_without_retry_or_reverse(self):
        self.executor.migrate.side_effect = None
        with self.assertRaisesRegex(schema.SchemaSafetyError, "no quedaron aplicadas"):
            schema.apply_schema()
        self.executor.migrate.assert_called_once_with(list(schema.TARGETS))

    def test_migration_failure_is_not_retried_reversed_or_faked(self):
        self.executor.migrate.side_effect = RuntimeError("synthetic DDL failure")
        with self.assertRaisesRegex(RuntimeError, "synthetic DDL"):
            schema.apply_schema()
        self.executor.migrate.assert_called_once_with(list(schema.TARGETS))
        self.assertEqual(self.factory.call_count, 1)

    def test_unreviewed_dependency_blocks_apply_before_migrate(self):
        unreviewed = Migration("0001_not_authorized", "other")
        self.executor.migration_plan.side_effect = lambda targets: [(unreviewed, False)]
        with self.assertRaises(schema.SchemaSafetyError):
            schema.apply_schema()
        self.executor.migrate.assert_not_called()

    def test_clean_source_has_no_optional_merge_witness(self):
        self.assertFalse(schema._merge_witness())
        _, plan = schema.inspect_schema()
        self.assertEqual(len(plan), 2)
        self.assertEqual(self.executor._virtual_effective_targets, schema.TARGETS)

    def test_known_already_applied_branch_allows_only_two_fields_plus_empty_merge(self):
        self.known_merge()
        self.assertTrue(schema._merge_witness())
        expected = [*schema.TARGETS, schema.MERGE_TARGET]
        self.assertEqual(schema.apply_schema(), expected)
        self.executor.migrate.assert_called_once_with([schema.TARGETS[0], schema.MERGE_TARGET])
        self.assertEqual(schema.apply_schema(), [])
        self.assertEqual(self.executor.migrate.call_count, 1)
        self.assertIn(schema.LEGACY_WEBHOOKS, self.applied)

    def test_already_added_columns_can_record_only_empty_merge_without_reapplying_fields(self):
        self.known_merge()
        self.applied.update(schema.TARGETS)
        self.present.update(schema.TABLES)
        self.assertEqual(schema.apply_schema(), [schema.MERGE_TARGET])
        self.executor.migrate.assert_called_once_with([schema.TARGETS[0], schema.MERGE_TARGET])

    def test_known_branch_must_already_be_recorded_not_scheduled_or_faked(self):
        self.known_merge()
        self.applied.remove(schema.LEGACY_WEBHOOKS)
        self.assert_refused()
        self.executor.migration_plan.assert_not_called()

    def test_changed_legacy_witness_is_rejected_before_executor_or_sql(self):
        legacy, _ = self.known_merge()
        legacy.write_bytes(legacy.read_bytes() + b"# unknown byte\n")
        self.assert_refused()
        self.factory.assert_not_called()
        self.cursor.execute.assert_not_called()

    def test_missing_legacy_or_merge_file_is_rejected_before_executor(self):
        legacy, merge = self.known_merge()
        legacy_body, merge_body = legacy.read_bytes(), merge.read_bytes()
        for missing in (legacy, merge):
            with self.subTest(missing=missing.name):
                missing.unlink()
                self.assert_refused()
                self.factory.assert_not_called()
                legacy.write_bytes(legacy_body)
                merge.write_bytes(merge_body)

    def test_histories_or_loaded_branches_without_files_cannot_look_like_clean_source(self):
        for key in (schema.LEGACY_WEBHOOKS, schema.MERGE_TARGET):
            with self.subTest(key=key):
                self.applied.add(key)
                self.assert_refused()
                self.applied.clear()

    def test_known_files_with_missing_loaded_branch_or_merge_are_rejected(self):
        self.known_merge()
        for key in (schema.LEGACY_WEBHOOKS, schema.MERGE_TARGET):
            with self.subTest(key=key):
                original = self.disk.pop(key)
                self.assert_refused()
                self.disk[key] = original

    def test_merge_file_requires_exact_declarative_dependencies_empty_ops_and_atomicity(self):
        _, merge = self.known_merge()
        original = merge.read_text(encoding="utf-8")
        invalid = [
            original.replace("atomic = True", "atomic = False"),
            original.replace("operations = []", "operations = [1]"),
            original.replace(repr(list(schema.MERGE_DEPENDENCIES)), "[]"),
            original + "\nraise RuntimeError('must never run')\n",
            original.replace("    operations = []", "    operations = []\n    replaces = []"),
            original.replace("    operations = []", "    operations = []\n    def apply(self): pass"),
            original.replace("    operations = []", "    operations = list()"),
        ]
        for text in invalid:
            with self.subTest(text=text):
                merge.write_text(text, encoding="utf-8")
                self.assert_refused()
                self.factory.assert_not_called()

    def test_loaded_merge_metadata_cannot_add_operations_dependencies_or_replacements(self):
        self.known_merge()
        for attribute, value in (
            ("dependencies", []), ("operations", [RemoveField(model_name="citacliente", name="virtual")]),
            ("atomic", False), ("replaces", [schema.LEGACY_WEBHOOKS]), ("run_before", [("auth", "0001_initial")]),
        ):
            with self.subTest(attribute=attribute):
                merge = self.disk[schema.MERGE_TARGET]
                original = getattr(merge, attribute)
                setattr(merge, attribute, value)
                self.assert_refused()
                setattr(merge, attribute, original)

    def test_old_branch_never_allowed_in_forward_or_backward_plan_even_with_witness(self):
        self.known_merge()
        for backwards in (False, True):
            with self.subTest(backwards=backwards):
                self.executor.migration_plan.side_effect = lambda targets: [(self.disk[schema.LEGACY_WEBHOOKS], backwards)]
                with self.assertRaises(schema.SchemaSafetyError):
                    schema.apply_schema()
                self.executor.migrate.assert_not_called()

    def test_merge_plan_without_witness_and_reverse_merge_are_always_refused(self):
        merge = Migration(schema.MERGE_TARGET[1], schema.MERGE_TARGET[0])
        with self.assertRaises(schema.SchemaSafetyError):
            schema.validate_plan([(merge, False)])
        with self.assertRaises(schema.SchemaSafetyError):
            schema.validate_plan([(merge, True)], allow_merge=True)
        self.executor.migration_plan.side_effect = lambda targets: [(merge, False)]
        self.assert_refused()

    def test_other_conflicts_still_block_known_merge(self):
        self.known_merge()
        self.executor.loader.detect_conflicts.return_value = {"crm": [schema.MERGE_TARGET[1], "9999_unreviewed"]}
        self.assert_refused()
        self.executor.migration_plan.assert_not_called()

    def test_witness_accepts_only_line_ending_normalization_not_content_changes(self):
        legacy, merge = self.known_merge()
        legacy.write_bytes(legacy.read_bytes().replace(b"\n", b"\r\n"))
        merge.write_bytes(merge.read_bytes().replace(b"\n", b"\r\n"))
        self.assertTrue(schema._merge_witness())

    def test_loaded_migration_module_must_come_from_exact_candidate_file(self):
        module_name = "closed_virtual_migration_test_module"
        expected = self.root / "expected.py"
        expected.write_text("# no imports or transports\n", encoding="utf-8")
        migration_class = type("Migration", (Migration,), {"__module__": module_name})
        migration = migration_class("synthetic", "crm")
        with patch.dict(sys.modules, {module_name: SimpleNamespace(__file__=str(expected))}):
            schema._assert_migration_path(migration, expected)
        for module in (SimpleNamespace(__file__=str(self.root / "elsewhere.py")), SimpleNamespace()):
            with self.subTest(module=module), patch.dict(sys.modules, {module_name: module}):
                with self.assertRaises(schema.SchemaSafetyError):
                    schema._assert_migration_path(migration, expected)


if __name__ == "__main__":
    unittest.main()
