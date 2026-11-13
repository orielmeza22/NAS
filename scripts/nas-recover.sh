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
    FORCE_FLAG="${3:-}"
    if [ -z "$SNAP_NAME" ]; then
      fail "Uso: $0 rollback-local <snapshot-name-o-tag> [--force|-y] (Ej: tank/nas@k8s-auto-20261030-1400)"
    fi

    SNAP_TAG=$(echo "$SNAP_NAME" | awk -F'@' '{print $2}')
    BASE_DS=$(echo "$SNAP_NAME" | awk -F'@' '{print $1}')

    if [ -z "$SNAP_TAG" ]; then
      SNAP_TAG="$SNAP_NAME"
      BASE_DS="tank/nas"
    fi

    # Buscar todos los snapshots que compartan este tag para rollback recursivo coordinado
    MATCHING_SNAPS=$(sudo zfs list -t snapshot -o name -H 2>/dev/null | grep -E "^${BASE_DS}(/.*)?@${SNAP_TAG}\$" || true)

    if [ -z "$MATCHING_SNAPS" ]; then
      fail "No se encontraron snapshots que coincidan con tag: @${SNAP_TAG} en ${BASE_DS}"
    fi

    warn "============================================================"
    warn "  ⚠️  ADVERTENCIA DE REVERSIÓN COORDINADA OPENZFS"
    warn "============================================================"
    echo "Los siguientes snapshots y datasets asociados serán revertidos:"
    echo "$MATCHING_SNAPS" | while read -r s; do echo "  - $s"; done
    warn "Cualquier archivo o modificación posterior a este snapshot se perderá."
    warn "============================================================"

    if [ "$FORCE_FLAG" != "--force" ] && [ "$FORCE_FLAG" != "-y" ]; then
      read -r -p "¿Está seguro de ejecutar este rollback? [y/N]: " CONFIRM
      if [ "$CONFIRM" != "y" ] && [ "$CONFIRM" != "Y" ]; then
        fail "Operación de rollback cancelada por el usuario."
      fi
    fi

    echo "$MATCHING_SNAPS" | while read -r s; do
      info "Revertiendo dataset: $s..."
      sudo zfs release sentinel-immutable "$s" 2>/dev/null || true
      sudo zfs rollback -r "$s"
    done

    # Si se revirtió PostgreSQL, reiniciar deployments para sincronizar conexiones
    if kubectl get deployment nextcloud-postgres >/dev/null 2>&1; then
      info "Reiniciando pods de Nextcloud y base de datos para consistencia transaccional..."
      kubectl rollout restart deployment/nextcloud-postgres deployment/nextcloud 2>/dev/null || true
    fi

    pass "Rollback coordinado completado exitosamente. Datasets y base de datos sincronizados."
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
    REMOTE_FILE="${3:-}"
    FORCE_FLAG="${4:-}"
    info "Iniciando recuperación Bare-Metal real desde Cloudflare R2 hacia dataset: $TARGET_DS..."

    if ! kubectl -n kube-system get secret r2-backup-credentials >/dev/null 2>&1; then
      fail "Secreto r2-backup-credentials ausente en el clúster (kube-system)."
    fi

    R2_ACCOUNT_ID=$(kubectl -n kube-system get secret r2-backup-credentials -o jsonpath='{.data.account-id}' | base64 -d)
    R2_ACCESS_KEY_ID=$(kubectl -n kube-system get secret r2-backup-credentials -o jsonpath='{.data.access-key-id}' | base64 -d)
    R2_SECRET_ACCESS_KEY=$(kubectl -n kube-system get secret r2-backup-credentials -o jsonpath='{.data.secret-access-key}' | base64 -d)
    BACKUP_ENCRYPTION_KEY=$(kubectl -n kube-system get secret r2-backup-credentials -o jsonpath='{.data.encryption-key}' | base64 -d)

    export RCLONE_CONFIG_R2_TYPE="s3"
    export RCLONE_CONFIG_R2_PROVIDER="Cloudflare"
    export RCLONE_CONFIG_R2_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID"
    export RCLONE_CONFIG_R2_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY"
    export RCLONE_CONFIG_R2_ENDPOINT="https://${R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
    export RCLONE_CONFIG_R2_NO_CHECK_BUCKET="true"

    # Devuelve el objeto cifrado más reciente de un prefijo, ignorando las firmas .hmac
    pick_latest() {
      rclone lsf -R --files-only "r2:nas-backup/$1" 2>/dev/null |
        grep -E '[.]zfs[.]enc$' | sort | tail -n 1
    }

    if [ -z "$REMOTE_FILE" ]; then
      info "Buscando el backup más reciente en Cloudflare R2..."
      for prefix in weekly/ incremental/ daily/; do
        REL_NAME="$(pick_latest "$prefix")"
        if [ -n "$REL_NAME" ]; then
          REMOTE_FILE="${prefix}${REL_NAME}"
          break
        fi
      done
    fi

    if [ -z "$REMOTE_FILE" ]; then
      fail "No se encontraron copias cifradas en el bucket r2:nas-backup"
    fi

    case "$REMOTE_FILE" in
      *.zfs.enc) : ;;
      *) fail "La ruta indicada no es un objeto cifrado (.zfs.enc): ${REMOTE_FILE}" ;;
    esac

    TARGET_MOUNTED="$(sudo zfs get -H -o value mounted "$TARGET_DS" 2>/dev/null || echo no)"
    if [ "$TARGET_MOUNTED" = "yes" ] && [ "$FORCE_FLAG" != "--force" ]; then
      fail "El dataset ${TARGET_DS} está montado y en uso. Detenga los servicios o restaure en otro dataset (por ejemplo tank/dr-verify) y luego repita con --force."
    fi

    TMP_DIR="$(mktemp -d)"
    trap 'rm -rf "$TMP_DIR"' EXIT
    TMP_ENC="${TMP_DIR}/objeto.zfs.enc"
    TMP_HMAC_FILE="${TMP_DIR}/objeto.hmac"

    info "Descargando r2:nas-backup/${REMOTE_FILE} ..."
    if ! rclone copyto "r2:nas-backup/${REMOTE_FILE}" "$TMP_ENC"; then
      fail "No se pudo descargar el objeto ${REMOTE_FILE}."
    fi
    [ -s "$TMP_ENC" ] || fail "El objeto descargado está vacío."

    if rclone copyto "r2:nas-backup/${REMOTE_FILE}.hmac" "$TMP_HMAC_FILE" 2>/dev/null && [ -s "$TMP_HMAC_FILE" ]; then
      EXPECTED_HMAC=$(tr -d -c '0123456789abcdef' < "$TMP_HMAC_FILE")
      ACTUAL_HMAC=$(openssl dgst -sha256 -hmac "$BACKUP_ENCRYPTION_KEY" -r "$TMP_ENC" | awk '{print $1}')
      if [ "$EXPECTED_HMAC" != "$ACTUAL_HMAC" ]; then
        fail "La firma HMAC-SHA256 no coincide: el objeto está corrupto o fue alterado. Restauración cancelada sin escribir datos."
      fi
      info "Firma HMAC-SHA256 verificada."
    else
      warn "El objeto no tiene firma .hmac asociada: se continúa sin verificación de integridad."
    fi

    info "Descifrando y enviando a ${TARGET_DS} ..."
    export BACKUP_ENCRYPTION_KEY
    if ! openssl enc -d -aes-256-cbc -salt -pbkdf2 -iter 600000 -pass env:BACKUP_ENCRYPTION_KEY < "$TMP_ENC" | sudo zfs receive -F "$TARGET_DS"; then
      fail "La restauración sobre ${TARGET_DS} falló (descifrado o zfs receive)."
    fi

    pass "Dataset ZFS restaurado exitosamente desde la nube."
    sudo zfs list "$TARGET_DS"
    ;;

  *)
    echo "Uso: $0 {list|rollback-local <snapshot>|restore-file <snapshot> <relpath> [destino]|restore-cloud}"
    exit 1
    ;;
esac
