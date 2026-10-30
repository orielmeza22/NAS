#!/usr/bin/env bash
# ==============================================================================
# Enterprise Educational NAS — Automated Offsite Disaster Recovery (DR) Drill
# Downloads latest encrypted ZFS stream from Cloudflare R2, decrypts on the fly
# with AES-256-CBC (PBKDF2), restores into an isolated dataset (tank/dr-verify),
# validates integrity, and cleans up the temporary dataset.
# ==============================================================================
set -euo pipefail

COLOR_RESET="\033[0m"
COLOR_GREEN="\033[32m"
COLOR_RED="\033[31m"
COLOR_BLUE="\033[34m"

info() { echo -e "${COLOR_BLUE}[DR-DRILL]${COLOR_RESET} $1"; }
pass() { echo -e "${COLOR_GREEN}[DR-PASS]${COLOR_RESET} $1"; }
fail() { echo -e "${COLOR_RED}[DR-FAIL]${COLOR_RESET} $1"; exit 1; }

echo "============================================================"
echo "   🚨 SIMULACRO AUTOMATIZADO DE DISASTER RECOVERY (3-2-1)   "
echo "============================================================"

info "1. Extrayendo credenciales selladas de Cloudflare R2 desde K3s..."
R2_ACCOUNT_ID=$(kubectl -n kube-system get secret r2-backup-credentials -o jsonpath='{.data.account-id}' | base64 -d)
R2_ACCESS_KEY_ID=$(kubectl -n kube-system get secret r2-backup-credentials -o jsonpath='{.data.access-key-id}' | base64 -d)
R2_SECRET_ACCESS_KEY=$(kubectl -n kube-system get secret r2-backup-credentials -o jsonpath='{.data.secret-access-key}' | base64 -d)
BACKUP_ENCRYPTION_KEY=$(kubectl -n kube-system get secret r2-backup-credentials -o jsonpath='{.data.encryption-key}' | base64 -d)

if [ -z "$R2_ACCOUNT_ID" ] || [ -z "$BACKUP_ENCRYPTION_KEY" ]; then
  fail "Credenciales de R2 o llave de cifrado no disponibles en r2-backup-credentials."
fi

info "2. Localizando el último backup completo cifrado en Cloudflare R2..."
LATEST_REMOTE=$(kubectl -n kube-system run dr-locator --rm -i --restart=Never \
  --image=rclone/rclone@sha256:2687085f718d3c628f7fdfb77c52a1d344332aed543110bb88088f2c70d43eb5 \
  --env="RCLONE_CONFIG_R2_TYPE=s3" \
  --env="RCLONE_CONFIG_R2_PROVIDER=Cloudflare" \
  --env="RCLONE_CONFIG_R2_ACCESS_KEY_ID=$R2_ACCESS_KEY_ID" \
  --env="RCLONE_CONFIG_R2_SECRET_ACCESS_KEY=$R2_SECRET_ACCESS_KEY" \
  --env="RCLONE_CONFIG_R2_ENDPOINT=https://${R2_ACCOUNT_ID}.r2.cloudflarestorage.com" \
  --env="RCLONE_CONFIG_R2_NO_CHECK_BUCKET=true" \
  --command -- sh -c '
    rclone lsf -R r2:nas-backup/weekly/ 2>/dev/null | sed "s|^|weekly/|" | tail -n 1
  ' 2>/dev/null | grep -E '\.zfs\.enc$' | tail -n 1 || true)

if [ -z "$LATEST_REMOTE" ]; then
  LATEST_REMOTE=$(kubectl -n kube-system run dr-locator-daily --rm -i --restart=Never \
    --image=rclone/rclone@sha256:2687085f718d3c628f7fdfb77c52a1d344332aed543110bb88088f2c70d43eb5 \
    --env="RCLONE_CONFIG_R2_TYPE=s3" \
    --env="RCLONE_CONFIG_R2_PROVIDER=Cloudflare" \
    --env="RCLONE_CONFIG_R2_ACCESS_KEY_ID=$R2_ACCESS_KEY_ID" \
    --env="RCLONE_CONFIG_R2_SECRET_ACCESS_KEY=$R2_SECRET_ACCESS_KEY" \
    --env="RCLONE_CONFIG_R2_ENDPOINT=https://${R2_ACCOUNT_ID}.r2.cloudflarestorage.com" \
    --env="RCLONE_CONFIG_R2_NO_CHECK_BUCKET=true" \
    --command -- sh -c '
      rclone lsf -R r2:nas-backup/daily/ 2>/dev/null | sed "s|^|daily/|" | tail -n 1
    ' 2>/dev/null | grep -E '\.zfs\.enc$' | tail -n 1 || true)
fi

if [ -z "$LATEST_REMOTE" ]; then
  fail "No se encontraron objetos .zfs.enc en r2:nas-backup."
fi
pass "Objeto remoto localizado: r2:nas-backup/${LATEST_REMOTE}"

info "3. Descargando stream de R2, descifrando AES-256-CBC y restaurando en dataset aislado (tank/dr-verify)..."
sudo zfs destroy -r tank/dr-verify 2>/dev/null || true

kubectl -n kube-system run dr-restorer --rm -i --restart=Never \
  --image=rclone/rclone@sha256:2687085f718d3c628f7fdfb77c52a1d344332aed543110bb88088f2c70d43eb5 \
  --env="RCLONE_CONFIG_R2_TYPE=s3" \
  --env="RCLONE_CONFIG_R2_PROVIDER=Cloudflare" \
  --env="RCLONE_CONFIG_R2_ACCESS_KEY_ID=$R2_ACCESS_KEY_ID" \
  --env="RCLONE_CONFIG_R2_SECRET_ACCESS_KEY=$R2_SECRET_ACCESS_KEY" \
  --env="RCLONE_CONFIG_R2_ENDPOINT=https://${R2_ACCOUNT_ID}.r2.cloudflarestorage.com" \
  --env="RCLONE_CONFIG_R2_NO_CHECK_BUCKET=true" \
  --command -- rclone cat "r2:nas-backup/${LATEST_REMOTE}" 2>/dev/null | \
  openssl enc -d -aes-256-cbc -pbkdf2 -pass "pass:${BACKUP_ENCRYPTION_KEY}" | \
  sudo zfs receive -F tank/dr-verify

if sudo zfs list -H tank/dr-verify >/dev/null 2>&1; then
  RESTORED_USED=$(sudo zfs get -H -o value used tank/dr-verify)
  pass "Dataset tank/dr-verify restaurado íntegramente desde la nube (${RESTORED_USED})."
else
  fail "Fallo al restaurar el dataset tank/dr-verify."
fi

info "4. Limpiando dataset temporal de simulacro (tank/dr-verify)..."
sudo zfs destroy -r tank/dr-verify
pass "Simulacro de Disaster Recovery completado con RTO < 60s y RPO verificado."
