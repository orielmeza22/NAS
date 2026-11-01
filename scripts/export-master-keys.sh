#!/usr/bin/env bash
# ==============================================================================
# Enterprise Educational NAS — Master Encryption & Secrets Disaster Recovery Kit
# Exports all cluster master keys (Bitnami Sealed Secrets private key, R2 offsite
# encryption key, and application credentials) for offline cold storage (KeePass,
# encrypted USB drive, or physical vault).
# ==============================================================================
set -euo pipefail

COLOR_RESET="\033[0m"
COLOR_GREEN="\033[32m"
COLOR_RED="\033[31m"
COLOR_BLUE="\033[34m"
COLOR_YELLOW="\033[33m"

info() { echo -e "${COLOR_BLUE}[MASTER-KEYS]${COLOR_RESET} $1"; }
pass() { echo -e "${COLOR_GREEN}[SUCCESS]${COLOR_RESET} $1"; }
fail() { echo -e "${COLOR_RED}[ERROR]${COLOR_RESET} $1"; exit 1; }
warn() { echo -e "${COLOR_YELLOW}[WARN]${COLOR_RESET} $1"; }

OUTPUT_DIR="${1:-/tmp/nas-emergency-kit}"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
TARGET_BUNDLE="${OUTPUT_DIR}/emergency-kit-${TIMESTAMP}"

mkdir -p "$TARGET_BUNDLE"
chmod 700 "$TARGET_BUNDLE"

info "1. Localizando y exportando clave privada activa de Bitnami Sealed Secrets..."
SEALED_KEY_SECRET=$(kubectl -n kube-system get secret -l sealedsecrets.bitnami.com/sealed-secrets-key -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)

if [ -z "$SEALED_KEY_SECRET" ]; then
  # Fallback a búsqueda por prefijo
  SEALED_KEY_SECRET=$(kubectl -n kube-system get secrets -o jsonpath='{.items[?(@.metadata.name=~"^sealed-secrets-key")].metadata.name}' 2>/dev/null | awk '{print $1}')
fi

if [ -n "$SEALED_KEY_SECRET" ]; then
  kubectl -n kube-system get secret "$SEALED_KEY_SECRET" -o yaml > "${TARGET_BUNDLE}/sealed-secrets-master-key.yaml"
  pass "Clave maestra de Sealed Secrets exportada: ${SEALED_KEY_SECRET}"
else
  warn "No se encontró el secreto de clave de Sealed Secrets en kube-system. Asegúrese de que el controlador esté instalado."
fi

info "2. Exportando credenciales y clave de cifrado de Cloudflare R2..."
if kubectl -n kube-system get secret r2-backup-credentials >/dev/null 2>&1; then
  kubectl -n kube-system get secret r2-backup-credentials -o yaml > "${TARGET_BUNDLE}/r2-backup-credentials.yaml"
  pass "Credenciales y clave AES-256 de Cloudflare R2 exportadas."
else
  warn "Secreto r2-backup-credentials ausente en kube-system."
fi

info "3. Exportando credenciales maestras de aplicaciones locales..."
if kubectl -n default get secret nextcloud-admin-credentials >/dev/null 2>&1; then
  kubectl -n default get secret nextcloud-admin-credentials -o yaml > "${TARGET_BUNDLE}/nextcloud-admin-credentials.yaml"
fi
if kubectl -n default get secret samba-credentials >/dev/null 2>&1; then
  kubectl -n default get secret samba-credentials -o yaml > "${TARGET_BUNDLE}/samba-credentials.yaml"
fi

cat <<EOF > "${TARGET_BUNDLE}/README_DISASTER_RECOVERY.txt"
================================================================================
KIT DE EMERGENCIA Y DESASTRE BARE-METAL (COLD STORAGE)
Generado: $(date -Iseconds)
Host: $(hostname)
================================================================================
ADVERTENCIA DE SEGURIDAD ESTRICTA:
Este kit contiene las claves privadas maestras que descifran todos los respaldos
de Cloudflare R2 y los SealedSecrets de este NAS.

SI EL SERVIDOR SUFRE DESTRUCCIÓN FÍSICA O ROBO:
1. Este directorio es el ÚNICO medio para recuperar el clúster en hardware nuevo.
2. Debe ser guardado en una bóveda digital cifrada (Bitwarden, KeePass) o en un
   pendrive cifrado (LUKS / BitLocker) en la caja fuerte de la institución.
3. Para restaurar en un servidor nuevo, ejecute:
   bash scripts/import-master-keys.sh ${TARGET_BUNDLE}
================================================================================
EOF

ARCHIVE_PATH="${OUTPUT_DIR}/nas-emergency-kit-${TIMESTAMP}.tar.gz"
tar -czf "$ARCHIVE_PATH" -C "$OUTPUT_DIR" "emergency-kit-${TIMESTAMP}"
rm -rf "$TARGET_BUNDLE"
chmod 400 "$ARCHIVE_PATH"

pass "================================================================================"
pass "  EMERGENCY KIT GENERADO EXITOSAMENTE: ${ARCHIVE_PATH}"
pass "  Copie este archivo fuera del servidor INMEDIATAMENTE y guárdelo en lugar seguro."
pass "================================================================================"
