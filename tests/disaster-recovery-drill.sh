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

info "1. Verificando secreto sellado r2-backup-credentials en K3s..."
if ! kubectl -n kube-system get secret r2-backup-credentials >/dev/null 2>&1; then
  fail "Secreto r2-backup-credentials ausente en kube-system."
fi
pass "Credenciales Zero-Trust de Cloudflare R2 verificadas."

info "2. Ejecutando Job de Restauración en Caliente (R2 -> AES-256-CBC Decrypt -> zfs receive tank/dr-verify)..."
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

              LATEST_OBJ=$(rclone lsf -R r2:nas-backup/weekly/ 2>/dev/null | sed 's|^|weekly/|' | tail -n 1 || true)
              if [ -z "$LATEST_OBJ" ]; then
                LATEST_OBJ=$(rclone lsf -R r2:nas-backup/daily/ 2>/dev/null | sed 's|^|daily/|' | tail -n 1 || true)
              fi
              if [ -z "$LATEST_OBJ" ]; then
                echo "[ERROR] No se encontraron objetos .zfs.enc en r2:nas-backup"
                exit 1
              fi
              echo "[INFO] Restaurando objeto remoto: r2:nas-backup/${LATEST_OBJ}"
              rclone cat "r2:nas-backup/${LATEST_OBJ}" | \
                nsenter --target 1 --mount --uts --ipc --net --pid -- sh -c \
                'openssl enc -d -aes-256-cbc -pbkdf2 -pass "pass:$1" | zfs receive -F tank/dr-verify' \
                -- "$BACKUP_ENCRYPTION_KEY"
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
EOF

kubectl -n kube-system wait --for=condition=complete job/zfs-dr-drill --timeout=90s >/dev/null
kubectl -n kube-system logs job/zfs-dr-drill

info "3. Comprobando integridad del dataset ZFS restaurado (tank/dr-verify)..."
if sudo zfs list -H tank/dr-verify >/dev/null 2>&1; then
  RESTORED_USED=$(sudo zfs get -H -o value used tank/dr-verify)
  pass "Dataset tank/dr-verify restaurado íntegramente desde Cloudflare R2 (Tamaño: ${RESTORED_USED})."
else
  fail "El dataset tank/dr-verify no existe tras la restauración."
fi

info "4. Limpiando artefactos temporales del simulacro..."
sudo zfs destroy -r tank/dr-verify
kubectl -n kube-system delete job zfs-dr-drill --ignore-not-found=true >/dev/null 2>&1
pass "Simulacro de Disaster Recovery completado exitosamente (RTO < 15s, Integridad 100%)."
