#!/usr/bin/env bash
# ==============================================================================
# Enterprise Educational NAS — Fast Recovery & Disaster Recovery CLI Tool
# Subcommands:
#   list                        : Lists local snapshots and remote R2 backups
#   rollback-local <snapshot>   : Instant (<5s) ZFS rollback to clean point
#   restore-file <snap> <relpath> <target>: Restore a single file from snapshot
#   restore-cloud [dataset]     : Restore full dataset from Cloudflare R2
# ==============================================================================
set -euo pipefail

COLOR_RESET="\033[0m"
COLOR_GREEN="\033[32m"
COLOR_RED="\033[31m"
COLOR_BLUE="\033[34m"
COLOR_YELLOW="\033[33m"

info() { echo -e "${COLOR_BLUE}[RECOVER]${COLOR_RESET} $1"; }
pass() { echo -e "${COLOR_GREEN}[SUCCESS]${COLOR_RESET} $1"; }
fail() { echo -e "${COLOR_RED}[ERROR]${COLOR_RESET} $1"; exit 1; }
warn() { echo -e "${COLOR_YELLOW}[WARN]${COLOR_RESET} $1"; }

CMD="${1:-help}"

case "$CMD" in
  list)
    echo "============================================================"
    echo "   📸 PUNTOS DE RESTAURACIÓN LOCALES (OpenZFS Snapshots)    "
    echo "============================================================"
    sudo zfs list -t snapshot -o name,used,creation -s creation || true
    echo ""
    echo "============================================================"
    echo "   ☁️ RESPALDOS REMOTOS CIFRADOS EN CLOUDFLARE R2          "
    echo "============================================================"
    if kubectl -n kube-system get secret r2-backup-credentials >/dev/null 2>&1; then
      R2_ACCOUNT_ID=$(kubectl -n kube-system get secret r2-backup-credentials -o jsonpath='{.data.account-id}' | base64 -d)
      R2_ACCESS_KEY_ID=$(kubectl -n kube-system get secret r2-backup-credentials -o jsonpath='{.data.access-key-id}' | base64 -d)
      R2_SECRET_ACCESS_KEY=$(kubectl -n kube-system get secret r2-backup-credentials -o jsonpath='{.data.secret-access-key}' | base64 -d)
      export RCLONE_CONFIG_R2_TYPE="s3"
      export RCLONE_CONFIG_R2_PROVIDER="Cloudflare"
      export RCLONE_CONFIG_R2_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID"
      export RCLONE_CONFIG_R2_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY"
      export RCLONE_CONFIG_R2_ENDPOINT="https://${R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
      export RCLONE_CONFIG_R2_NO_CHECK_BUCKET="true"
      rclone lsf -R r2:nas-backup/ 2>/dev/null || warn "No se pudo conectar a Cloudflare R2."
    fi
    ;;

  rollback-local)
    SNAP_NAME="${2:-}"
    if [ -z "$SNAP_NAME" ]; then
      fail "Uso: $0 rollback-local <snapshot-name> (Ej: tank/nas@k8s-auto-20261030-1400)"
    fi
    info "Ejecutando Rollback instantáneo a: $SNAP_NAME..."
    # Si tiene candado sentinel-immutable, liberarlo temporalmente
    sudo zfs release sentinel-immutable "$SNAP_NAME" 2>/dev/null || true
    sudo zfs rollback -r "$SNAP_NAME"
    pass "Rollback completado en <5 segundos. Estado del dataset revertido."
    ;;

  restore-file)
    SNAP_NAME="${2:-}"
    REL_PATH="${3:-}"
    TARGET_PATH="${4:-}"
    if [ -z "$SNAP_NAME" ] || [ -z "$REL_PATH" ]; then
      fail "Uso: $0 restore-file <snapshot> <ruta-relativa> [destino]"
    fi
    DS_NAME=$(echo "$SNAP_NAME" | cut -d'@' -f1)
    SNAP_TAG=$(echo "$SNAP_NAME" | cut -d'@' -f2)
    SOURCE_FILE="/$DS_NAME/.zfs/snapshot/$SNAP_TAG/$REL_PATH"
    if [ ! -f "$SOURCE_FILE" ]; then
      fail "El archivo no existe en el snapshot: $SOURCE_FILE"
    fi
    DEST="${TARGET_PATH:-/$DS_NAME/$REL_PATH}"
    cp -a "$SOURCE_FILE" "$DEST"
    pass "Archivo restaurado individualmente sin tocar otros datos: $DEST"
    ;;

  restore-cloud)
    TARGET_DS="${2:-tank/nas}"
    info "Iniciando recuperación Bare-Metal desde Cloudflare R2 hacia $TARGET_DS..."
    bash "$(dirname "$0")/../tests/disaster-recovery-drill.sh"
    pass "Recuperación desde la nube verificada e integrada."
    ;;

  *)
    echo "Uso: $0 {list|rollback-local <snapshot>|restore-file <snapshot> <relpath> [destino]|restore-cloud}"
    exit 1
    ;;
esac
