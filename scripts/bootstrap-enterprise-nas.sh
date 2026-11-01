#!/usr/bin/env bash
# ==============================================================================
# Enterprise Educational NAS — Zero-Touch Bare-Metal Bootstrap Script
# Idempotently configures OpenZFS RAID-Z1, K3s, ArgoCD, Sealed Secrets and applies all manifests
# ==============================================================================
set -euo pipefail

COLOR_RESET="\033[0m"
COLOR_GREEN="\033[32m"
COLOR_BLUE="\033[34m"
COLOR_YELLOW="\033[33m"
COLOR_RED="\033[31m"

info() { echo -e "${COLOR_BLUE}[BOOTSTRAP]${COLOR_RESET} $1"; }
pass() { echo -e "${COLOR_GREEN}[SUCCESS]${COLOR_RESET} $1"; }
warn() { echo -e "${COLOR_YELLOW}[WARN]${COLOR_RESET} $1"; }
fail() { echo -e "${COLOR_RED}[FAIL]${COLOR_RESET} $1"; exit 1; }

echo "============================================================"
echo "    🚀 INICIANDO DESPLIEGUE ZERO-TOUCH ENTERPRISE NAS       "
echo "============================================================"

info "1. Verificando dependencias del sistema operativo..."
sudo apt-get update -qq
sudo apt-get install -y -qq zfsutils-linux openssl curl rclone jq smartmontools nut-client >/dev/null

info "2. Configurando pool OpenZFS (tank)..."
if ! sudo zpool list tank >/dev/null 2>&1; then
  if [ $# -ge 2 ] && [ "$1" != "--lab-mode" ]; then
    info "Creando pool de producción con discos físicos reales: $*"
    sudo zpool create -f -o ashift=12 -O compression=lz4 -O atime=off \
      -O acltype=posixacl -O xattr=sa tank "$@"
  elif [ "${1:-}" = "--lab-mode" ] || [ "${1:-}" = "--demo-virtual" ]; then
    warn "MODO LABORATORIO / VM DETECTADO: Creando pool sobre discos virtuales (/zfs-disks/disk*.img)."
    warn "ADVERTENCIA: Este modo NO provee redundancia física y es únicamente para pruebas/desarrollo."
    sudo mkdir -p /zfs-disks
    for i in 1 2 3; do
      if [ ! -f "/zfs-disks/disk${i}.img" ]; then
        sudo truncate -s 5G "/zfs-disks/disk${i}.img"
      fi
    done
    sudo zpool create -f -o ashift=12 -O compression=lz4 -O atime=off \
      -O acltype=posixacl -O xattr=sa tank raidz1 \
      /zfs-disks/disk1.img /zfs-disks/disk2.img /zfs-disks/disk3.img
  else
    echo -e "${COLOR_RED}[ERROR FATAL] BLOQUEO DE SEGURIDAD OPERACIONAL:${COLOR_RESET}"
    echo "No se especificaron discos físicos para el almacenamiento institucional."
    echo "Para producción en servidor físico ejecute pasando los discos dedicados:"
    echo "  sudo $0 /dev/sdb /dev/sdc /dev/sdd"
    echo ""
    echo "Discos detectados actualmente en el sistema:"
    lsblk -d -o NAME,SIZE,MODEL,TRAN 2>/dev/null || true
    echo ""
    echo "Si desea probar en un entorno de laboratorio/VM sin discos físicos, ejecute:"
    echo "  sudo $0 --lab-mode"
    exit 1
  fi
  pass "Pool OpenZFS tank creado exitosamente."
else
  pass "Pool OpenZFS tank ya existe y está activo."
fi

info "3. Configurando datasets departamentales y cuotas..."
ensure_ds() {
  local ds="$1" quota="$2" rs="${3:-128K}"
  if ! sudo zfs list -H "$ds" >/dev/null 2>&1; then
    sudo zfs create -o compression=lz4 -o atime=off -o "recordsize=${rs}" -o "quota=${quota}" "$ds"
  fi
}
ensure_ds tank/nas "none" "128K"
ensure_ds tank/nas/profesores "10G" "128K"
ensure_ds tank/nas/alumnos "5G" "128K"
ensure_ds tank/nas/publico "5G" "128K"
ensure_ds tank/nas/postgres "10G" "8K"
ensure_ds tank/nas/nextcloud "50G" "128K"
ensure_ds tank/nas/samba "20G" "128K"
sudo zfs set logbias=latency tank/nas/postgres
sudo mkdir -p /tank/nas/.samba-private /tank/nas/.Papelera
pass "Datasets ZFS y optimizaciones de recordsize aplicadas."

info "4. Verificando e instalando K3s (Versión fijada v1.31.1+k3s1)..."
if ! command -v k3s >/dev/null 2>&1; then
  export INSTALL_K3S_VERSION="v1.31.1+k3s1"
  curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC="--write-kubeconfig-mode 600 --disable traefik" sh -
  pass "K3s instalado con permisos seguros (kubeconfig 0600 exclusivo root)."
else
  sudo chmod 600 /etc/rancher/k3s/k3s.yaml 2>/dev/null || true
  pass "K3s ya instalado. Permisos de /etc/rancher/k3s/k3s.yaml endurecidos a 0600."
fi

# Esperar a que el nodo esté Ready
info "Esperando disponibilidad del nodo K3s..."
for _ in {1..30}; do
  if kubectl get nodes 2>/dev/null | grep -q "Ready"; then break; fi
  sleep 2
done

info "5. Verificando controladores GitOps (ArgoCD y Sealed Secrets)..."
kubectl create namespace argocd --dry-run=client -o yaml | kubectl apply -f -
if ! kubectl -n argocd get deployment argocd-server >/dev/null 2>&1; then
  info "Desplegando ArgoCD Server..."
  kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/v2.13.2/manifests/install.yaml
fi

if ! kubectl -n kube-system get deployment sealed-secrets-controller >/dev/null 2>&1; then
  info "Desplegando Bitnami Sealed Secrets controller..."
  kubectl apply -f https://github.com/bitnami-labs/sealed-secrets/releases/download/v0.27.3/controller.yaml
fi

info "6. Verificando Secretos Iniciales con Contraseñas Criptográficas..."
CREDS_FILE="/etc/nas-initial-credentials.txt"
NC_PASS=""
if ! kubectl get secret nextcloud-admin-credentials >/dev/null 2>&1; then
  NC_PASS=$(openssl rand -base64 16)
  kubectl create secret generic nextcloud-admin-credentials \
    --from-literal=username=admin \
    --from-literal=password="$NC_PASS"
  info "Secreto nextcloud-admin-credentials generado con contraseña criptográfica."
fi

if ! kubectl get secret samba-credentials >/dev/null 2>&1; then
  PASS_PROF=$(openssl rand -base64 12)
  PASS_ALUM=$(openssl rand -base64 12)
  PASS_ADMIN=$(openssl rand -base64 16)
  kubectl create secret generic samba-credentials \
    --from-literal=user-profesores="profesor;${PASS_PROF}" \
    --from-literal=user-alumnos="alumno;${PASS_ALUM}" \
    --from-literal=user-admin="admin;${PASS_ADMIN}"
  info "Secreto samba-credentials generado con credenciales aleatorias (sin defaults públicos)."

  sudo bash -c "cat <<EOF > '$CREDS_FILE'
================================================================================
CREDENCIALES INICIALES DEL NAS (GENERADAS AUTOMÁTICAMENTE)
Fecha: $(date -Iseconds)
Host: $(hostname)
================================================================================
Nextcloud Web Console:
  Usuario:    admin
  Contraseña: ${NC_PASS:-[Configurada previamente]}

Recursos Compartidos Samba (SMB):
  Profesor:   profesor / ${PASS_PROF}
  Alumno:     alumno   / ${PASS_ALUM}
  Admin IT:   admin    / ${PASS_ADMIN}
================================================================================
ADVERTENCIA: Archivo restringido (chmod 600). Guárdelo en su gestor de contraseñas.
EOF"
  sudo chmod 600 "$CREDS_FILE"
  info "Credenciales guardadas en $CREDS_FILE."
fi

info "7. Aplicando infraestructura GitOps (Root Application)..."
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
kubectl apply -f "${REPO_DIR}/infrastructure/root-application.yaml"

pass "============================================================"
pass "  ✅ PLATAFORMA NAS ENTERPRISE INICIALIZADA EXITOSAMENTE    "
pass "============================================================"
warn "PASO OBLIGATORIO DE RESILIENCIA (EMERGENCY KIT):"
warn "Ejecute inmediatamente para respaldar la clave de Sealed Secrets fuera del host:"
warn "  sudo bash ${REPO_DIR}/scripts/export-master-keys.sh /media/pendrive_cifrado"
warn ""
warn "HIGIENE OPERACIONAL:"
warn "Una vez transferidas las credenciales al gestor institucional, elimine el archivo:"
warn "  sudo rm -f ${CREDS_FILE}"
