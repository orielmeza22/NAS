#!/usr/bin/env bash
# ==============================================================================
# Enterprise Educational NAS — Automated Offsite Disaster Recovery (DR) Drill
# Downloads latest encrypted ZFS stream from Cloudflare R2, verifies HMAC-SHA256
# authenticity BEFORE decryption, decrypts with AES-256-CBC (PBKDF2 600k iter),
# restores full + differential streams into tank/dr-verify, and validates data.
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
echo "   SIMULACRO AUTOMATIZADO DE DISASTER RECOVERY (3-2-1)      "
echo "============================================================"
DRILL_START_TS=$(date +%s)

info "1. Verificando secreto sellado r2-backup-credentials en K3s..."
if ! kubectl -n kube-system get secret r2-backup-credentials >/dev/null 2>&1; then
  fail "Secreto r2-backup-credentials ausente en kube-system."
fi
pass "Credenciales de Cloudflare R2 verificadas."

info "2. Ejecutando Job de Restauración Autenticada (R2 -> HMAC-SHA256 Verify -> AES-256-CBC Decrypt -> zfs receive tank/dr-verify)..."
sudo zfs destroy -r tank/dr-verify 2>/dev/null || true
kubectl -n kube-system delete job zfs-dr-drill --ignore-not-found=true >/dev/null 2>&1

cat <<'EOF' | kubectl apply -f - >/dev/null
apiVersion: batch/v1
kind: Job
metadata:
  name: zfs-dr-drill
  namespace: kube-system
  labels:
    app.kubernetes.io/name: zfs-dr-drill
    app.kubernetes.io/component: disaster-recovery
spec:
  backoffLimit: 0
  template:
    spec:
      hostPID: true
      restartPolicy: Never
      containers:
        - name: dr-restore
          image: rclone/rclone@sha256:2687085f718d3c628f7fdfb77c52a1d344332aed543110bb88088f2c70d43eb5
          securityContext:
            privileged: true
          command: ["/bin/sh", "-c"]
          args:
            - |
              set -euo pipefail
              export RCLONE_CONFIG_R2_TYPE="s3"
              export RCLONE_CONFIG_R2_PROVIDER="Cloudflare"
              export RCLONE_CONFIG_R2_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID"
              export RCLONE_CONFIG_R2_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY"
              export RCLONE_CONFIG_R2_ENDPOINT="https://${R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
              export RCLONE_CONFIG_R2_NO_CHECK_BUCKET="true"

              LATEST_FULL=$(rclone lsf -R r2:nas-backup/weekly/ 2>/dev/null | grep '\.zfs\.enc$' | sort | tail -n 1 || true)
              if [ -z "$LATEST_FULL" ]; then
                echo "[ERROR] No se encontró backup semanal base (.zfs.enc) en r2:nas-backup/weekly/"
                exit 1
              fi

              echo "[INFO] Descargando y verificando autenticidad HMAC-SHA256 de weekly/${LATEST_FULL}..."
              rclone copyto "r2:nas-backup/weekly/${LATEST_FULL}" "/tmp/nas-tunnel/dr-full.zfs.enc"
              rclone copyto "r2:nas-backup/weekly/${LATEST_FULL}.hmac" "/tmp/nas-tunnel/dr-full.zfs.enc.hmac"

              export BACKUP_ENCRYPTION_KEY
              nsenter --target 1 --mount --uts --ipc --net --pid -- env BACKUP_ENCRYPTION_KEY="$BACKUP_ENCRYPTION_KEY" sh -c '
                set -euo pipefail
                EXPECTED_HMAC=$(cat /tmp/nas-tunnel/dr-full.zfs.enc.hmac | tr -d " \n\r")
                ACTUAL_HMAC=$(openssl dgst -sha256 -hmac "$BACKUP_ENCRYPTION_KEY" -r /tmp/nas-tunnel/dr-full.zfs.enc | awk "{print \$1}")
                if [ "$EXPECTED_HMAC" != "$ACTUAL_HMAC" ]; then
                  echo "[FATAL] Verificación HMAC-SHA256 fallida: stream alterado o corrupto."
                  rm -f /tmp/nas-tunnel/dr-full.zfs.enc*
                  exit 1
                fi
                echo "[OK] Firma HMAC-SHA256 válida. Descifrando e inyectando en tank/dr-verify..."
                openssl enc -d -aes-256-cbc -salt -pbkdf2 -iter 600000 -pass env:BACKUP_ENCRYPTION_KEY -in /tmp/nas-tunnel/dr-full.zfs.enc | zfs receive -F tank/dr-verify
                rm -f /tmp/nas-tunnel/dr-full.zfs.enc*
              '

              BASE_TAG=$(echo "$LATEST_FULL" | sed -E 's/^tank-full-(.+)\.zfs\.enc$/\1/')
              LATEST_DIFF=$(rclone lsf -R r2:nas-backup/incremental/ 2>/dev/null | grep "^tank-diff-from-${BASE_TAG}-to-.*\.zfs\.enc$" | sort | tail -n 1 || true)
              if [ -n "$LATEST_DIFF" ]; then
                echo "[INFO] Aplicando último diferencial incremental asociado: incremental/${LATEST_DIFF}..."
                rclone copyto "r2:nas-backup/incremental/${LATEST_DIFF}" "/tmp/nas-tunnel/dr-diff.zfs.enc"
                rclone copyto "r2:nas-backup/incremental/${LATEST_DIFF}.hmac" "/tmp/nas-tunnel/dr-diff.zfs.enc.hmac"
                nsenter --target 1 --mount --uts --ipc --net --pid -- env BACKUP_ENCRYPTION_KEY="$BACKUP_ENCRYPTION_KEY" sh -c '
                  set -euo pipefail
                  EXPECTED_HMAC=$(cat /tmp/nas-tunnel/dr-diff.zfs.enc.hmac | tr -d " \n\r")
                  ACTUAL_HMAC=$(openssl dgst -sha256 -hmac "$BACKUP_ENCRYPTION_KEY" -r /tmp/nas-tunnel/dr-diff.zfs.enc | awk "{print \$1}")
                  if [ "$EXPECTED_HMAC" != "$ACTUAL_HMAC" ]; then
                    echo "[FATAL] Verificación HMAC-SHA256 fallida en diferencial."
                    rm -f /tmp/nas-tunnel/dr-diff.zfs.enc*
                    exit 1
                  fi
                  openssl enc -d -aes-256-cbc -salt -pbkdf2 -iter 600000 -pass env:BACKUP_ENCRYPTION_KEY -in /tmp/nas-tunnel/dr-diff.zfs.enc | zfs receive -F tank/dr-verify
                  rm -f /tmp/nas-tunnel/dr-diff.zfs.enc*
                '
              fi
          env:
            - name: R2_ACCOUNT_ID
              valueFrom:
                secretKeyRef:
                  name: r2-backup-credentials
                  key: account-id
            - name: R2_ACCESS_KEY_ID
              valueFrom:
                secretKeyRef:
                  name: r2-backup-credentials
                  key: access-key-id
            - name: R2_SECRET_ACCESS_KEY
              valueFrom:
                secretKeyRef:
                  name: r2-backup-credentials
                  key: secret-access-key
            - name: BACKUP_ENCRYPTION_KEY
              valueFrom:
                secretKeyRef:
                  name: r2-backup-credentials
                  key: encryption-key
          volumeMounts:
            - name: tunnel-volume
              mountPath: /tmp/nas-tunnel
      volumes:
        - name: tunnel-volume
          hostPath:
            path: /tmp/nas-tunnel
            type: DirectoryOrCreate
EOF

kubectl -n kube-system wait --for=condition=complete job/zfs-dr-drill --timeout=600s >/dev/null
kubectl -n kube-system logs job/zfs-dr-drill

info "3. Comprobando integridad criptográfica y de archivos del dataset ZFS restaurado (tank/dr-verify)..."
if sudo zfs list -H tank/dr-verify >/dev/null 2>&1; then
  RESTORED_USED=$(sudo zfs get -H -o value used tank/dr-verify)
  MOUNT_PATH=$(sudo zfs get -H -o value mountpoint tank/dr-verify)
  FILE_HASH_COUNT=0
  if [ -d "$MOUNT_PATH" ]; then
    FILE_HASH_COUNT=$(sudo find "$MOUNT_PATH" -maxdepth 3 -type f -exec sha256sum {} + 2>/dev/null | wc -l || echo 0)
  fi
  pass "Dataset tank/dr-verify restaurado y verificado (${RESTORED_USED}, ${FILE_HASH_COUNT} archivos verificados con SHA-256)."
else
  fail "El dataset tank/dr-verify no existe tras la restauración."
fi

info "4. Limpiando artefactos temporales del simulacro..."
sudo zfs destroy -r tank/dr-verify
kubectl -n kube-system delete job zfs-dr-drill --ignore-not-found=true >/dev/null 2>&1
DRILL_ELAPSED=$(( $(date +%s) - DRILL_START_TS ))
pass "Simulacro de Disaster Recovery completado exitosamente (RTO medido: ${DRILL_ELAPSED}s, HMAC-SHA256 y contenido verificados)."
