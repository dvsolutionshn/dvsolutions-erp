#!/usr/bin/env bash
set -Eeuo pipefail

ENV_FILE="${1:-/etc/dvsolutions-erp.env}"
TEMP_FILE=""

cleanup() {
  if [[ -n "${TEMP_FILE}" && -f "${TEMP_FILE}" ]]; then
    rm -f -- "${TEMP_FILE}"
  fi
  unset META_SECRET || true
}
trap cleanup EXIT

if [[ "${EUID}" -ne 0 ]]; then
  echo "Este configurador debe ejecutarse como root." >&2
  exit 1
fi

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "No existe ${ENV_FILE}. No se modifico nada." >&2
  exit 1
fi

read -r -s -p "Pegue META_WHATSAPP_APP_SECRET (no se mostrara): " META_SECRET
echo

if [[ ! "${META_SECRET}" =~ ^[[:xdigit:]]{32}$ ]]; then
  echo "El secreto no tiene el formato esperado de Meta. No se modifico nada." >&2
  unset META_SECRET
  exit 1
fi

umask 077
TEMP_FILE="$(mktemp "${ENV_FILE}.tmp.XXXXXX")"
BACKUP_FILE="${ENV_FILE}.pre-meta-$(date +%Y%m%d-%H%M%S)"
cp --preserve=mode,ownership "${ENV_FILE}" "${BACKUP_FILE}"

FOUND=0
while IFS= read -r line || [[ -n "${line}" ]]; do
  case "${line}" in
    META_WHATSAPP_APP_SECRET=*)
      printf 'META_WHATSAPP_APP_SECRET=%s\n' "${META_SECRET}" >> "${TEMP_FILE}"
      FOUND=1
      ;;
    *)
      printf '%s\n' "${line}" >> "${TEMP_FILE}"
      ;;
  esac
done < "${ENV_FILE}"

if [[ "${FOUND}" -eq 0 ]]; then
  printf '\nMETA_WHATSAPP_APP_SECRET=%s\n' "${META_SECRET}" >> "${TEMP_FILE}"
fi

chown --reference="${ENV_FILE}" "${TEMP_FILE}"
chmod --reference="${ENV_FILE}" "${TEMP_FILE}"
mv "${TEMP_FILE}" "${ENV_FILE}"
TEMP_FILE=""
unset META_SECRET

echo "META_SECRET_CONFIGURADO"
echo "Se creo un respaldo privado en ${BACKUP_FILE}."
