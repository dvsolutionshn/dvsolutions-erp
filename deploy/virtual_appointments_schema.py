"""Only the two reviewed additive appointment fields, never broad migrate-all.

Called in an isolated service process using the candidate release and the
existing production environment. No account, role, patient or appointment rows
are created by this helper. Rollback deliberately never drops these columns.
"""
TARGETS = (
    ("clinica", "0039_citaclinica_virtual"),
    ("crm", "0027_citacliente_virtual"),
)
TABLES = {"clinica": "clinica_citaclinica", "crm": "crm_citacliente"}
MODELS = {"clinica": "citaclinica", "crm": "citacliente"}


class SchemaSafetyError(RuntimeError):
    pass


def validate_plan(plan):
    allowed = set(TARGETS)
    if any(backwards or (migration.app_label, migration.name) not in allowed for migration, backwards in plan):
        raise SchemaSafetyError("Hay migraciones pendientes o reversas fuera de las dos columnas revisadas.")


def inspect_schema():
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor
    from django.db.migrations.operations.fields import AddField

    if not connection.features.can_rollback_ddl:
        raise SchemaSafetyError("La base de datos no admite el DDL transaccional requerido.")
    executor = MigrationExecutor(connection)
    executor.loader.check_consistent_history(connection)
    if executor.loader.detect_conflicts():
        raise SchemaSafetyError("El grafo de migraciones tiene conflictos; no se modifico el esquema.")
    plan = executor.migration_plan(list(TARGETS))
    validate_plan(plan)
    for app, name in TARGETS:
        migration = executor.loader.disk_migrations.get((app, name))
        if migration is None or len(migration.operations) != 1 or migration.atomic is not True:
            raise SchemaSafetyError("La migracion propuesta no es la aditiva revisada.")
        operation = migration.operations[0]
        if not (isinstance(operation, AddField) and operation.model_name == MODELS[app]
                and operation.name == "virtual" and operation.field.get_internal_type() == "BooleanField"
                and operation.field.default is False and not operation.field.null
                and operation.preserve_default is True
                and operation.field.has_db_default() and operation.field.db_default is False
                and operation.field.db_column in (None, "virtual") and not operation.field.db_index
                and not operation.field.primary_key and not operation.field.unique):
            raise SchemaSafetyError("La migracion no coincide con el campo virtual revisado.")
        with connection.cursor() as cursor:
            fields = connection.introspection.get_table_description(cursor, TABLES[app])
        found = [field for field in fields if field.name == "virtual"]
        applied = (app, name) in executor.loader.applied_migrations
        if bool(found) != applied:
            raise SchemaSafetyError("El esquema y el historial de migraciones no coinciden; no se forzo el cambio.")
        if found and connection.introspection.get_field_type(found[0].type_code, found[0]) != "BooleanField":
            raise SchemaSafetyError("La columna virtual existente no es booleana.")
        if found and (found[0].null_ok is not False or str(found[0].default).strip().lower() not in {"false", "0", "(false)", "(0)"}):
            raise SchemaSafetyError("Falta el valor por defecto compatible con la version anterior.")
    return executor, plan


def apply_schema():
    from django.db import connection

    if connection.vendor == "postgresql":
        # These limits apply only to this short-lived migration process.
        with connection.cursor() as cursor:
            cursor.execute("SET lock_timeout = '5s'")
            cursor.execute("SET statement_timeout = '30s'")
    executor, plan = inspect_schema()
    if plan:
        executor.migrate(list(TARGETS))
    # Fresh loader checks both the schema and the recorded migration state.
    _, remaining = inspect_schema()
    if remaining:
        raise SchemaSafetyError("Las migraciones revisadas no quedaron aplicadas.")
    return [(migration.app_label, migration.name) for migration, _ in plan]
