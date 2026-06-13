import hashlib
import json
import tempfile
import zipfile
from collections import Counter, deque
from pathlib import Path, PurePosixPath

from django.apps import apps
from django.conf import settings
from django.core import serializers
from django.db import DEFAULT_DB_ALIAS, connections, transaction
from django.db.migrations.recorder import MigrationRecorder
from django.db.models import FileField
from django.utils import timezone

from .models import Empresa, RespaldoEmpresa, TokenRespaldoEmpresa


BACKUP_FORMAT_VERSION = "1.0"
EXCLUDED_REVERSE_APPS = {"admin", "sessions"}
EXCLUDED_BACKUP_MODELS = (RespaldoEmpresa, TokenRespaldoEmpresa)


def _key(obj):
    return (obj._meta.label_lower, str(obj.pk))


def _empresa_fields(model):
    return [
        field
        for field in model._meta.concrete_fields
        if field.is_relation and field.related_model is Empresa
    ]


def _add_object(obj, objects, queue):
    if obj is None or obj.pk is None or isinstance(obj, EXCLUDED_BACKUP_MODELS):
        return False
    key = _key(obj)
    if key in objects:
        return False
    objects[key] = obj
    queue.append(obj)
    return True


def _collect_owned_objects(empresa):
    objects = {}
    queue = deque()
    _add_object(empresa, objects, queue)

    for model in apps.get_models():
        if model in EXCLUDED_BACKUP_MODELS or model._meta.proxy or not model._meta.managed:
            continue
        for field in _empresa_fields(model):
            for obj in model._default_manager.filter(**{field.name: empresa}).iterator():
                _add_object(obj, objects, queue)

    while queue:
        parent = queue.popleft()
        for relation in parent._meta.get_fields():
            if not relation.auto_created or not relation.is_relation:
                continue
            if not (relation.one_to_many or relation.one_to_one):
                continue
            related_model = relation.related_model
            if (
                related_model is None
                or related_model in EXCLUDED_BACKUP_MODELS
                or related_model._meta.app_label in EXCLUDED_REVERSE_APPS
            ):
                continue
            filters = {relation.field.name: parent}
            tenant_fields = _empresa_fields(related_model)
            if tenant_fields:
                filters[tenant_fields[0].name] = empresa
            try:
                related_qs = related_model._default_manager.filter(**filters)
            except (TypeError, ValueError):
                continue
            for child in related_qs.iterator():
                _add_object(child, objects, queue)

    return objects


def _collect_dependencies(empresa, objects):
    queue = deque(objects.values())
    checked = set()
    while queue:
        obj = queue.popleft()
        obj_key = _key(obj)
        if obj_key in checked:
            continue
        checked.add(obj_key)

        for field in obj._meta.concrete_fields:
            if not field.is_relation or not field.many_to_one and not field.one_to_one:
                continue
            related = getattr(obj, field.name, None)
            if isinstance(related, Empresa) and related.pk != empresa.pk:
                continue
            if _add_object(related, objects, queue):
                continue

        for field in obj._meta.many_to_many:
            if field.auto_created:
                continue
            try:
                related_items = getattr(obj, field.name).all()
            except (AttributeError, ValueError):
                continue
            for related in related_items.iterator():
                if isinstance(related, Empresa) and related.pk != empresa.pk:
                    continue
                _add_object(related, objects, queue)

    return objects


def _media_entries(objects):
    media_root = Path(settings.MEDIA_ROOT).resolve()
    seen = set()
    entries = []
    for obj in objects:
        for field in obj._meta.concrete_fields:
            if not isinstance(field, FileField):
                continue
            field_file = getattr(obj, field.name, None)
            name = getattr(field_file, "name", "")
            if not name or name in seen:
                continue
            candidate = (media_root / name).resolve()
            try:
                candidate.relative_to(media_root)
            except ValueError:
                continue
            if candidate.is_file():
                seen.add(name)
                entries.append((name.replace("\\", "/"), candidate))
    return sorted(entries, key=lambda item: item[0])


def _zip_safe_name(name):
    parts = [part for part in PurePosixPath(name).parts if part not in {"", ".", ".."}]
    return "/".join(parts)


def _restore_guide(empresa):
    return f"""RESPALDO PORTATIL DE DV SOLUTIONS ERP

Empresa: {empresa.nombre}
Slug: {empresa.slug}
Formato: {BACKUP_FORMAT_VERSION}

CONTENIDO
- manifest.json: identidad, conteos, migraciones y hashes.
- datos.json: fixture Django con datos de la empresa y dependencias necesarias.
- media/: documentos, imagenes y archivos encontrados para los registros respaldados.

RESTAURACION SEGURA
1. Nunca restaure directamente sobre produccion.
2. Prepare un servidor de prueba con la misma version del codigo.
3. Verifique que todas las migraciones listadas en manifest.json esten aplicadas.
4. Valide los hashes SHA-256 del manifiesto.
5. Ejecute: python manage.py loaddata datos.json
6. Copie el contenido de media/ al MEDIA_ROOT configurado.
7. Revise usuarios, permisos, consecutivos, inventarios, saldos, facturas y expedientes.
8. Solo despues de la validacion, planifique una restauracion controlada en produccion.

ADVERTENCIA
El archivo contiene informacion confidencial, financiera y potencialmente datos de salud.
Guardelo en discos cifrados, con acceso restringido y al menos una copia fuera del servidor.
"""


@transaction.atomic
def generar_respaldo_empresa(empresa):
    connection = connections[DEFAULT_DB_ALIAS]
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")

    owned = _collect_owned_objects(empresa)
    all_objects = _collect_dependencies(empresa, owned)
    ordered_objects = sorted(
        all_objects.values(),
        key=lambda obj: (obj._meta.app_label, obj._meta.model_name, str(obj.pk)),
    )
    media_entries = _media_entries(ordered_objects)
    data_json = serializers.serialize(
        "json",
        ordered_objects,
        indent=2,
        use_natural_foreign_keys=False,
        use_natural_primary_keys=False,
    ).encode("utf-8")

    model_counts = Counter(obj._meta.label_lower for obj in ordered_objects)
    migrations = list(
        MigrationRecorder(connections[DEFAULT_DB_ALIAS])
        .migration_qs.values_list("app", "name")
        .order_by("app", "name")
    )
    generated_at = timezone.now()
    filename = f"respaldo_{empresa.slug}_{generated_at:%Y%m%d_%H%M%S}.zip"

    file_hashes = {"datos.json": hashlib.sha256(data_json).hexdigest()}
    archive = tempfile.SpooledTemporaryFile(max_size=32 * 1024 * 1024, mode="w+b")
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as backup_zip:
        backup_zip.writestr("datos.json", data_json)
        backup_zip.writestr("LEEME_RESTAURACION.txt", _restore_guide(empresa))
        for media_name, media_path in media_entries:
            archive_name = f"media/{_zip_safe_name(media_name)}"
            digest = hashlib.sha256()
            with media_path.open("rb") as source, backup_zip.open(archive_name, "w") as destination:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
                    destination.write(chunk)
            file_hashes[archive_name] = digest.hexdigest()

        manifest = {
            "formato": "dvsolutions-empresa-backup",
            "version": BACKUP_FORMAT_VERSION,
            "generado_en": generated_at.isoformat(),
            "empresa": {
                "id": empresa.pk,
                "slug": empresa.slug,
                "nombre": empresa.nombre,
                "rtn": empresa.rtn,
            },
            "base_datos": connection.settings_dict["ENGINE"],
            "registros": len(ordered_objects),
            "archivos_media": len(media_entries),
            "conteos_por_modelo": dict(sorted(model_counts.items())),
            "migraciones": [{"app": app, "nombre": name} for app, name in migrations],
            "sha256_archivos": file_hashes,
        }
        backup_zip.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))

    archive.seek(0, 2)
    size = archive.tell()
    archive.seek(0)
    digest = hashlib.sha256()
    for chunk in iter(lambda: archive.read(1024 * 1024), b""):
        digest.update(chunk)
    archive.seek(0)

    return {
        "archivo": archive,
        "nombre": filename,
        "tamano_bytes": size,
        "sha256": digest.hexdigest(),
        "registros": len(ordered_objects),
        "archivos": len(media_entries),
    }
