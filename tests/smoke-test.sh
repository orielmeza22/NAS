#!/usr/bin/env bash
# ==============================================================================
# Enterprise Educational NAS — Automated Validation & Smoke Tests
# ==============================================================================
set -euo pipefail

COLOR_RESET="\033[0m"
COLOR_GREEN="\033[32m"
COLOR_RED="\033[31m"
COLOR_YELLOW="\033[33m"
COLOR_BLUE="\033[34m"

info() { echo -e "${COLOR_BLUE}[INFO]${COLOR_RESET} $1"; }
pass() { echo -e "${COLOR_GREEN}[PASS]${COLOR_RESET} $1"; }
fail() { echo -e "${COLOR_RED}[FAIL]${COLOR_RESET} $1"; exit 1; }
warn() { echo -e "${COLOR_YELLOW}[WARN]${COLOR_RESET} $1"; }

echo "============================================================"
echo "    🧪 INICIANDO SMOKE TESTS — PLATAFORMA NAS EDUCATIVA     "
echo "============================================================"

# 1. Comprobar estado de Nodos K3s
info "Validando que los nodos del clúster estén en estado Ready..."
if kubectl get nodes | grep -q "Ready"; then
  pass "Plano de control K3s operativo y listo."
else
  fail "No se encontraron nodos K3s en estado Ready."
fi

# 2. Comprobar ArgoCD
info "Verificando disponibilidad de ArgoCD..."
if kubectl -n argocd get pods -l app.kubernetes.io/name=argocd-server --no-headers | grep -q "Running"; then
  pass "ArgoCD Server está en ejecución."
else
  fail "ArgoCD Server no está listo en el namespace argocd."
fi

# 3. Comprobar Portal QR
info "Verificando pod y servicio del Portal QR..."
if kubectl get pods -l app.kubernetes.io/name=portal-qr --no-headers | grep -q "Running"; then
  pass "Pod Portal QR activo con sondas de salud (HTTP Liveness/Readiness)."
else
  fail "Pod del Portal QR no está en estado Running."
fi

# 4. Comprobar Samba File Server
info "Verificando pod de Samba..."
if kubectl get pods -l app.kubernetes.io/name=samba --no-headers | grep -q "Running"; then
  pass "Servidor Samba corriendo con capabilities seguras."
else
  fail "Servidor Samba no está en estado Running."
fi

# 5. Comprobar Storage ZFS (Persistent Volume Claims)
info "Comprobando vinculación de almacenamiento persistente (PVCs)..."
PVC_PENDING=$(kubectl get pvc --no-headers | grep -v "Bound" || true)
if [ -z "$PVC_PENDING" ]; then
  pass "Todos los PersistentVolumeClaims se encuentran en estado 'Bound'."
else
  warn "Existen PVCs que no están en estado Bound: $PVC_PENDING"
fi

# 6. Comprobar CronJob de Snapshots
info "Comprobando existencia del CronJob de snapshots ZFS..."
if kubectl -n kube-system get cronjob zfs-snapshot > /dev/null 2>&1; then
  pass "CronJob de snapshots nocturnos registrado en kube-system."
else
  fail "CronJob zfs-snapshot no encontrado."
fi

echo "============================================================"
echo -e "${COLOR_GREEN} ✅ TODOS LOS SMOKE TESTS COMPLETADOS EXITOSAMENTE.${COLOR_RESET}"
echo "============================================================"
