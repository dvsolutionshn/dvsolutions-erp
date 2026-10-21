#!/usr/bin/env bash
# No permitir que bash -x ni allexport registren o exporten la credencial.
set +x
set +a
set -Eeuo pipefail

if [[ "$#" -ne 1 ]]; then
  echo "Uso: bash deploy/configurar_smtp_google.sh /ruta/real/al/archivo.env" >&2
  exit 1
fi

ENV_FILE="$1"
TEMP_FILE=""
LOCK_DIR=""
unset SMTP_SECRET SMTP_VALUES || true
SMTP_SECRET=""

cleanup() {
  unset SMTP_SECRET SMTP_VALUES || true
  if [[ -n "${TEMP_FILE}" && -f "${TEMP_FILE}" ]]; then
    rm -f -- "${TEMP_FILE}"
  fi
  if [[ -n "${LOCK_DIR}" && -d "${LOCK_DIR}" ]]; then
    rmdir -- "${LOCK_DIR}" || true
  fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if [[ ! -f "${ENV_FILE}" || -L "${ENV_FILE}" ]]; then
  echo "Indica un archivo de entorno existente, regular y sin enlace simbolico. No se modifico nada." >&2
  exit 1
fi
ENV_FILE="$(realpath -- "${ENV_FILE}")"
if [[ ! -r "${ENV_FILE}" || ! -w "${ENV_FILE}" || ! -w "$(dirname -- "${ENV_FILE}")" ]]; then
  echo "No hay permisos para actualizar ese archivo. Ejecuta como su propietario o con sudo." >&2
  exit 1
fi

if ! mkdir -- "${ENV_FILE}.smtp.lock" 2>/dev/null; then
  echo "Ya existe un bloqueo de configuracion SMTP. No se modifico nada." >&2
  exit 1
fi
LOCK_DIR="${ENV_FILE}.smtp.lock"

echo "Cuenta de autenticacion: daniel.varela@dvsolutionshn.com"
echo "Remitente visible: DV Solutions <oasis@dvsolutionshn.com>"
echo "Este comando no envia correos ni reinicia servicios."
if ! read -r -s -p "Pegue la contrasena de aplicacion de Daniel (no se mostrara): " SMTP_SECRET; then
  printf '\n' >&2
  echo "No se recibio la credencial. No se modifico nada." >&2
  exit 1
fi
printf '\n' >&2
SMTP_SECRET="${SMTP_SECRET//[[:space:]]/}"
if [[ ! "${SMTP_SECRET}" =~ ^[a-zA-Z]{16}$ ]]; then
  echo "Se esperaba una contrasena de aplicacion de Google de 16 letras. No se modifico nada." >&2
  exit 1
fi

declare -A SMTP_VALUES=(
  [EMAIL_BACKEND]="django.core.mail.backends.smtp.EmailBackend"
  [EMAIL_HOST]="smtp.gmail.com"
  [EMAIL_PORT]="587"
  [EMAIL_USE_TLS]="true"
  [EMAIL_USE_SSL]="false"
  [EMAIL_TIMEOUT]="20"
  [EMAIL_HOST_USER]="daniel.varela@dvsolutionshn.com"
  [EMAIL_HOST_PASSWORD]="${SMTP_SECRET}"
  [DEFAULT_FROM_EMAIL]="'DV Solutions <oasis@dvsolutionshn.com>'"
  [ACCESS_PUBLIC_BASE_URL]="https://dvsolutionshn.com"
)
SMTP_KEYS=(EMAIL_BACKEND EMAIL_HOST EMAIL_PORT EMAIL_USE_TLS EMAIL_USE_SSL EMAIL_TIMEOUT EMAIL_HOST_USER EMAIL_HOST_PASSWORD DEFAULT_FROM_EMAIL ACCESS_PUBLIC_BASE_URL)
declare -A SEEN=()

umask 077
TEMP_FILE="$(mktemp "${ENV_FILE}.smtp.tmp.XXXXXXXX")"
BACKUP_FILE="$(mktemp "${ENV_FILE}.pre-smtp.XXXXXXXX")"
cat -- "${ENV_FILE}" > "${BACKUP_FILE}"
chown --reference="${ENV_FILE}" "${BACKUP_FILE}" "${TEMP_FILE}"
chmod 0600 -- "${BACKUP_FILE}" "${TEMP_FILE}"

while IFS= read -r line || [[ -n "${line}" ]]; do
  if [[ "${line}" =~ ^[[:space:]]*(export[[:space:]]+)?(EMAIL_BACKEND|EMAIL_HOST|EMAIL_PORT|EMAIL_USE_TLS|EMAIL_USE_SSL|EMAIL_TIMEOUT|EMAIL_HOST_USER|EMAIL_HOST_PASSWORD|DEFAULT_FROM_EMAIL|ACCESS_PUBLIC_BASE_URL)[[:space:]]*= ]]; then
    key="${BASH_REMATCH[2]}"
    if [[ -z "${SEEN[${key}]:-}" ]]; then
      printf '%s=%s\n' "${key}" "${SMTP_VALUES[${key}]}" >> "${TEMP_FILE}"
      SEEN["${key}"]=1
    fi
  else
    printf '%s\n' "${line}" >> "${TEMP_FILE}"
  fi
done < "${ENV_FILE}"

for key in "${SMTP_KEYS[@]}"; do
  if [[ -z "${SEEN[${key}]:-}" ]]; then
    printf '%s=%s\n' "${key}" "${SMTP_VALUES[${key}]}" >> "${TEMP_FILE}"
  fi
done

unset SMTP_SECRET SMTP_VALUES
mv -f -- "${TEMP_FILE}" "${ENV_FILE}"
TEMP_FILE=""
echo "SMTP_GOOGLE_CONFIGURADO"
echo "Respaldo privado: ${BACKUP_FILE}"
echo "Carga el entorno y ejecuta: python manage.py comprobar_smtp"
echo "La prueba autorizada es: python manage.py comprobar_smtp --to daniel.varela@dvsolutionshn.com"
