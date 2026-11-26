"""Only the two reviewed additive appointment fields, never broad migrate-all.

Called in an isolated service process using the candidate release and the
existing production environment. No account, role, patient or appointment rows
are created by this helper. Rollback deliberately never drops these columns.
An optional empty merge records only the union with a verified, already-applied
legacy migration; that legacy branch is never executed or faked here.
"""
import ast
import hashlib
from pathlib import Path
import sys


TARGETS = (
    ("clinica", "0039_citaclinica_virtual"),
    ("crm", "0027_citacliente_virtual"),
)
TABLES = {"clinica": "clinica_citaclinica", "crm": "crm_citacliente"}
MODELS = {"clinica": "citaclinica", "crm": "citacliente"}
LEGACY_WEBHOOKS = ("crm", "0027_whatsapp_webhooks")
MERGE_TARGET = ("crm", "0028_merge_citas_virtuales_webhooks")
LEGACY_WEBHOOKS_HASH = "0e1044498d83888ef6e28e90db91ccdc387e36501c873ae1de8017ef58e8c060"
MERGE_DEPENDENCIES = (TARGETS[1], LEGACY_WEBHOOKS)


class SchemaSafetyError(RuntimeError):
    pass


def _candidate_root():
    return Path(__file__).parent.parent


def _migration_path(root, key):
    path = root
    for part in (key[0], "migrations", key[1] + ".py"):
        path = path / part
        if path.is_symlink():
            raise SchemaSafetyError("Las migraciones revisadas no pueden ser enlaces.")
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        raise SchemaSafetyError("La migracion no pertenece a la copia candidata.") from None
    if path.exists() and not path.is_file():
        raise SchemaSafetyError("La migracion debe ser un archivo regular.")
    return path


def _merge_witness():
    """Validate optional migration files before Django imports their modules."""
    root = _candidate_root()
    legacy = _migration_path(root, LEGACY_WEBHOOKS)
    merge = _migration_path(root, MERGE_TARGET)
    if not legacy.exists():
        if merge.exists():
            raise SchemaSafetyError("Existe un merge sin la rama aplicada que debe conservar.")
        return False
    if hashlib.sha256(legacy.read_bytes().replace(b"\r\n", b"\n")).hexdigest() != LEGACY_WEBHOOKS_HASH:
        raise SchemaSafetyError("La rama previa no coincide con la migracion ya revisada.")
    if not merge.is_file():
        raise SchemaSafetyError("Falta el merge vacio de las dos ramas de citas.")
    try:
        tree = ast.parse(merge.read_text(encoding="utf-8-sig"))
        statements = list(tree.body)
        if statements and isinstance(statements[0], ast.Expr) and isinstance(statements[0].value, ast.Constant) and isinstance(statements[0].value.value, str):
            statements.pop(0)
        if len(statements) != 2:
            raise ValueError
        imported, definition = statements
        if not (isinstance(imported, ast.ImportFrom) and imported.level == 0 and imported.module == "django.db"
                and len(imported.names) == 1 and imported.names[0].name == "migrations" and imported.names[0].asname is None
                and isinstance(definition, ast.ClassDef) and definition.name == "Migration"
                and not definition.decorator_list and not definition.keywords and not getattr(definition, "type_params", [])
                and len(definition.bases) == 1
                and isinstance(definition.bases[0], ast.Attribute) and definition.bases[0].attr == "Migration"
                and isinstance(definition.bases[0].value, ast.Name) and definition.bases[0].value.id == "migrations"):
            raise ValueError
        values = {}
        for statement in definition.body:
            if not (isinstance(statement, ast.Assign) and len(statement.targets) == 1 and isinstance(statement.targets[0], ast.Name)):
                raise ValueError
            name = statement.targets[0].id
            if name in values:
                raise ValueError
            values[name] = ast.literal_eval(statement.value)
        if not (set(values) == {"atomic", "dependencies", "operations"} and values["atomic"] is True
                and values["dependencies"] == list(MERGE_DEPENDENCIES) and values["operations"] == []):
            raise ValueError
    except (SyntaxError, UnicodeError, ValueError, TypeError):
        raise SchemaSafetyError("El merge no es la union vacia y declarativa revisada.") from None
    return True


def _assert_migration_path(migration, path):
    module = sys.modules.get(type(migration).__module__)
    loaded = getattr(module, "__file__", None)
    if not loaded or Path(loaded).resolve() != path.resolve():
        raise SchemaSafetyError("Django no cargo la migracion desde la copia candidata.")


def validate_plan(plan, *, allow_merge=False):
    allowed = set(TARGETS)
    if allow_merge:
        allowed.add(MERGE_TARGET)
    if any(backwards or (migration.app_label, migration.name) not in allowed for migration, backwards in plan):
        raise SchemaSafetyError("Hay migraciones pendientes o reversas fuera de las dos columnas revisadas.")


def inspect_schema():
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor
    from django.db.migrations.operations.fields import AddField

    if not connection.features.can_rollback_ddl:
        raise SchemaSafetyError("La base de datos no admite el DDL transaccional requerido.")
    allow_merge = _merge_witness()
    executor = MigrationExecutor(connection)
    executor.loader.check_consistent_history(connection)
    loader = executor.loader
    if allow_merge:
        legacy = loader.disk_migrations.get(LEGACY_WEBHOOKS)
        merge = loader.disk_migrations.get(MERGE_TARGET)
        if legacy is None or LEGACY_WEBHOOKS not in loader.applied_migrations or merge is None:
            raise SchemaSafetyError("La rama previa no esta aplicada o falta su merge; no se ejecutara.")
        root = _candidate_root()
        _assert_migration_path(legacy, _migration_path(root, LEGACY_WEBHOOKS))
        _assert_migration_path(merge, _migration_path(root, MERGE_TARGET))
        if (merge.dependencies != list(MERGE_DEPENDENCIES) or merge.operations != [] or merge.atomic is not True
                or merge.replaces != [] or merge.run_before != []):
            raise SchemaSafetyError("La migracion de union no es el merge vacio revisado.")
    elif any(key in loader.disk_migrations or key in loader.applied_migrations for key in (LEGACY_WEBHOOKS, MERGE_TARGET)):
        raise SchemaSafetyError("El historial contiene una rama sin sus archivos revisados.")
    if executor.loader.detect_conflicts():
        raise SchemaSafetyError("El grafo de migraciones tiene conflictos; no se modifico el esquema.")
    effective_targets = (TARGETS[0], MERGE_TARGET) if allow_merge else TARGETS
    executor._virtual_effective_targets = effective_targets
    plan = executor.migration_plan(list(effective_targets))
    validate_plan(plan, allow_merge=allow_merge)
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
        executor.migrate(list(executor._virtual_effective_targets))
    # Fresh loader checks both the schema and the recorded migration state.
    _, remaining = inspect_schema()
    if remaining:
        raise SchemaSafetyError("Las migraciones revisadas no quedaron aplicadas.")
    return [(migration.app_label, migration.name) for migration, _ in plan]
