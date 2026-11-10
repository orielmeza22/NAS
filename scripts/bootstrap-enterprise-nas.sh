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
  RAID_LEVEL="raidz1"
  if [ "${1:-}" = "--raidz2" ]; then
    RAID_LEVEL="raidz2"
    shift
  elif [ $# -ge 4 ] && [ "${1:-}" != "--lab-mode" ] && [ "${1:-}" != "--demo-virtual" ]; then
    RAID_LEVEL="raidz2"
  fi

  if [ $# -ge 3 ] && [ "${1:-}" != "--lab-mode" ] && [ "${1:-}" != "--demo-virtual" ]; then
    if [ "$RAID_LEVEL" = "raidz2" ] && [ $# -lt 4 ]; then
      fail "Para conformar un arreglo RAID-Z2 se requieren al menos 4 discos físicos."
    fi
    info "Creando pool de producción ${RAID_LEVEL^^} con discos físicos reales: $*"
    sudo zpool create -f -o ashift=12 -O compression=lz4 -O atime=off \
      -O acltype=posixacl -O xattr=sa tank "$RAID_LEVEL" "$@"
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
    echo "Se requieren al menos 3 discos físicos dedicados para conformar el arreglo RAID-Z1."
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

# Buffer de emergencia anti-bloqueo COW (50M)
if ! sudo zfs list -H "tank/buffer" >/dev/null 2>&1; then
  sudo zfs create -o reservation=50M -o compression=off "tank/buffer"
fi

# Shadow Copies VSS: visibilidad del árbol de snapshots
sudo zfs set snapdir=visible tank/nas tank/nas/profesores tank/nas/alumnos tank/nas/publico 2>/dev/null || true

# Límite de memoria ZFS ARC permanente (2 GB)
echo "options zfs zfs_arc_max=2147483648" | sudo tee /etc/modprobe.d/zfs.conf >/dev/null
echo 2147483648 | sudo tee /sys/module/zfs/parameters/zfs_arc_max >/dev/null 2>&1 || true

sudo mkdir -p /var/lib/nas-samba-private /var/lib/nas-portal-state /tmp/nas-tunnel/tls /tank/nas/.Papelera /tank/nas/alumnos/entregas
sudo chmod 0700 /var/lib/nas-samba-private /var/lib/nas-portal-state
sudo chmod 0750 /tank/nas/profesores
sudo chmod 0755 /tank/nas/alumnos /tank/nas/publico
sudo chmod 1777 /tank/nas/alumnos/entregas
pass "Datasets ZFS, buffer COW (50M), snapdir VSS, permisos por rol y límite ARC (2GB) aplicados."

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

info "6. Verificando Certificados TLS (SAN LAN) y Secretos Criptográficos..."
CREDS_FILE="/etc/nas-initial-credentials.txt"
NC_PASS=""
PORTAL_ADMIN_PASS=""
PORTAL_OP_PASS=""
PORTAL_TOTP_SEC=""

HOST_LAN_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || true)
SAN_LIST="DNS:nas.escuela.local,DNS:localhost,IP:127.0.0.1"
if [ -n "$HOST_LAN_IP" ]; then
  SAN_LIST="${SAN_LIST},IP:${HOST_LAN_IP}"
else
  SAN_LIST="${SAN_LIST},IP:192.168.1.50"
fi

if ! kubectl get secret portal-tls-secret >/dev/null 2>&1; then
  TLS_DIR="/tmp/nas-tunnel/tls"
  sudo mkdir -p "$TLS_DIR"
  sudo openssl req -x509 -nodes -days 1825 -newkey rsa:2048 \
    -keyout "${TLS_DIR}/ca.key" -out "${TLS_DIR}/ca.crt" \
    -subj "/C=AR/ST=BA/L=CABA/O=Enterprise NAS/CN=Enterprise NAS Institutional Root CA" >/dev/null 2>&1
  sudo bash -c "cat <<EOF > '${TLS_DIR}/san.cnf'
subjectAltName=${SAN_LIST}
basicConstraints=CA:FALSE
keyUsage=digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
EOF"
  sudo openssl req -newkey rsa:2048 -nodes \
    -keyout "${TLS_DIR}/portal.key" -out "${TLS_DIR}/portal.csr" \
    -subj "/C=AR/ST=BA/L=CABA/O=Enterprise NAS/CN=nas.escuela.local" >/dev/null 2>&1
  sudo openssl x509 -req -in "${TLS_DIR}/portal.csr" \
    -CA "${TLS_DIR}/ca.crt" -CAkey "${TLS_DIR}/ca.key" -CAcreateserial \
    -out "${TLS_DIR}/portal.crt" -days 825 -sha256 -extfile "${TLS_DIR}/san.cnf" >/dev/null 2>&1
  sudo chmod 0600 "${TLS_DIR}/ca.key" "${TLS_DIR}/portal.key"
  sudo chmod 0644 "${TLS_DIR}/ca.crt" "${TLS_DIR}/portal.crt"
  sudo kubectl create secret tls portal-tls-secret \
    --cert="${TLS_DIR}/portal.crt" \
    --key="${TLS_DIR}/portal.key"
  info "Secreto TLS portal-tls-secret y Root CA institucional generados (SAN: ${SAN_LIST})."
fi

if ! kubectl get secret portal-admin-credentials >/dev/null 2>&1; then
  PORTAL_ADMIN_PASS=$(openssl rand -base64 16 | tr -d '\n=')
  PORTAL_OP_PASS=$(openssl rand -base64 12 | tr -d '\n=')
  PORTAL_TOTP_SEC=$(head -c 10 /dev/urandom | base32 | tr -d '=\n' | head -c 16)
  kubectl create secret generic portal-admin-credentials \
    --from-literal=admin-password="$PORTAL_ADMIN_PASS" \
    --from-literal=operator-password="$PORTAL_OP_PASS" \
    --from-literal=totp-secret="$PORTAL_TOTP_SEC"
  info "Secreto portal-admin-credentials generado (roles superadmin + operator + 2FA TOTP)."
fi

if ! kubectl get secret nextcloud-admin-credentials >/dev/null 2>&1; then
  NC_PASS=$(openssl rand -base64 16)
  kubectl create secret generic nextcloud-admin-credentials \
    --from-literal=username=admin \
    --from-literal=password="$NC_PASS"
  info "Secreto nextcloud-admin-credentials generado con contraseña criptográfica independiente."
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
NAS Control Center OS (Portal Web https://${HOST_LAN_IP:-localhost}:30081):
  SuperAdmin: admin    / ${PORTAL_ADMIN_PASS:-[Configurada previamente]}
  Operador:   operador / ${PORTAL_OP_PASS:-[Configurada previamente]}
  Secreto 2FA TOTP (RFC 6238 Base32): ${PORTAL_TOTP_SEC:-[Configurado previamente]}

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

info "7. Aplicando infraestructura GitOps (Root Application) y Portal Control Center..."
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
kubectl -n argocd rollout status deployment/argocd-server --timeout=120s || warn "ArgoCD Server aún iniciando."
kubectl -n kube-system rollout status deployment/sealed-secrets-controller --timeout=120s || warn "Sealed Secrets controller aún iniciando."
kubectl apply -f "${REPO_DIR}/apps/portal-qr/"
kubectl apply -f "${REPO_DIR}/infrastructure/root-application.yaml"

info "8. Verificando disponibilidad del Portal Web (HTTPS + IPC)..."
kubectl rollout status deployment/portal-qr --timeout=120s
if ! curl -sk https://localhost:30081/api/public/status | grep -q "raid_health"; then
  fail "El portal no respondió correctamente en https://localhost:30081/api/public/status tras el despliegue."
fi

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
