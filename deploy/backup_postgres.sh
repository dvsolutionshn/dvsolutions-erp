#!/usr/bin/env bash
set -euo pipefail

BACKUP_DIR="/opt/dvsolutions/backups"
DB_NAME="${POSTGRES_DB:-dvsolutions}"
DATE_TAG="$(date +%Y%m%d_%H%M%S)"

mkdir -p "$BACKUP_DIR"
pg_dump "$DB_NAME" | gzip > "$BACKUP_DIR/${DB_NAME}_${DATE_TAG}.sql.gz"

# Conserva 14 dias de backups locales.
find "$BACKUP_DIR" -type f -name "${DB_NAME}_*.sql.gz" -mtime +14 -delete
