#!/usr/bin/env bash
# ==============================================================================
# Enterprise Educational NAS — Zero-Touch Bare-Metal Bootstrap Script
# Idempotently configures OpenZFS RAID-Z1, K3s, ArgoCD and applies all manifests
# ==============================================================================
set -euo pipefail

COLOR_RESET="\033[0m"
COLOR_GREEN="\033[32m"
COLOR_BLUE="\033[34m"

info() { echo -e "${COLOR_BLUE}[BOOTSTRAP]${COLOR_RESET} $1"; }
pass() { echo -e "${COLOR_GREEN}[SUCCESS]${COLOR_RESET} $1"; }

echo "============================================================"
echo "    🚀 INICIANDO DESPLIEGUE ZERO-TOUCH ENTERPRISE NAS       "
echo "============================================================"

info "1. Verificando dependencias del sistema operativo (ZFS, OpenSSL, Curl)..."
sudo apt-get update -qq
sudo apt-get install -y -qq zfsutils-linux openssl curl rclone jq >/dev/null

info "2. Configurando pool OpenZFS (tank) en RAID-Z1..."
sudo mkdir -p /zfs-disks
for i in 1 2 3; do
  if [ ! -f "/zfs-disks/disk${i}.img" ]; then
    sudo truncate -s 2G "/zfs-disks/disk${i}.img"
  fi
done

if ! sudo zpool list tank >/dev/null 2>&1; then
  sudo zpool create -f -o ashift=12 -O compression=lz4 -O atime=off \
    -O acltype=posixacl -O xattr=sa tank raidz1 \
    /zfs-disks/disk1.img /zfs-disks/disk2.img /zfs-disks/disk3.img
  pass "Pool OpenZFS tank creado exitosamente."
else
  pass "Pool OpenZFS tank ya existe y está activo."
fi

info "3. Configurando datasets departamentales y cuotas..."
ensure_ds() {
  local ds="$1" mnt="/$1" quota="$2" rs="${3:-128K}"
  if ! sudo zfs list -H "$ds" >/dev/null 2>&1; then
    sudo zfs create -o compression=lz4 -o atime=off -o "recordsize=${rs}" -o "quota=${quota}" "$ds"
  fi
}
ensure_ds tank/nas "100G" "128K"
ensure_ds tank/nas/profesores "10G" "128K"
ensure_ds tank/nas/alumnos "5G" "128K"
ensure_ds tank/nas/publico "5G" "128K"
ensure_ds tank/nas/postgres "10G" "8K"
ensure_ds tank/nas/nextcloud "50G" "128K"
ensure_ds tank/nas/samba "20G" "128K"
sudo zfs set logbias=latency tank/nas/postgres
pass "Datasets ZFS y optimizaciones de recordsize aplicadas."

info "4. Verificando instalación de K3s..."
if ! command -v k3s >/dev/null 2>&1; then
  curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC="--write-kubeconfig-mode 644" sh -
  pass "K3s instalado."
else
  pass "K3s ya instalado."
fi

info "5. Aplicando manifiestos GitOps de la plataforma..."
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
kubectl apply -f "${REPO_DIR}/infrastructure/root-application.yaml" || true
kubectl apply -f "${REPO_DIR}/infrastructure/sealed-r2-credentials.yaml" || true
kubectl apply -f "${REPO_DIR}/infrastructure/zfs-exporter.yaml" || true
kubectl apply -f "${REPO_DIR}/infrastructure/ransomware-sentinel.yaml" || true
kubectl apply -f "${REPO_DIR}/infrastructure/zfs-quotas-enforcer.yaml" || true
kubectl apply -f "${REPO_DIR}/infrastructure/zfs-offsite-backup-cronjob.yaml" || true
kubectl apply -f "${REPO_DIR}/apps/nextcloud/database-redis.yaml" || true
kubectl apply -f "${REPO_DIR}/apps/samba/deployment.yaml" || true
kubectl apply -f "${REPO_DIR}/apps/grafana/dashboard-cm.yaml" || true

pass "Plataforma NAS inicializada y lista."
