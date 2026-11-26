"""Closed migration safety tests: no configured Django database or transport."""

import importlib
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
        self.assertEqual(targets, list(schema.TARGETS))
        return [(self.disk[target], False) for target in schema.TARGETS if target not in self.applied]

    def migrate(self, targets):
        self.assertEqual(targets, list(schema.TARGETS))
        self.applied.update(schema.TARGETS)
        self.present.update(schema.TABLES)

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


if __name__ == "__main__":
    unittest.main()
