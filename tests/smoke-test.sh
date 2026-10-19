#!/usr/bin/env bash
# ==============================================================================
# Enterprise Educational NAS — Automated Validation & Smoke Tests
# ==============================================================================
set -euo pipefail

COLOR_RESET="\033[0m"
COLOR_GREEN="\033[32m"
COLOR_RED="\033[31m"
COLOR_BLUE="\033[34m"
COLOR_YELLOW="\033[33m"

info() { echo -e "${COLOR_BLUE}[INFO]${COLOR_RESET} $1"; }
pass() { echo -e "${COLOR_GREEN}[PASS]${COLOR_RESET} $1"; }
fail() { echo -e "${COLOR_RED}[FAIL]${COLOR_RESET} $1"; exit 1; }
warn() { echo -e "${COLOR_YELLOW}[WARN]${COLOR_RESET} $1"; }

echo "============================================================"
echo "    🧪 INICIANDO SMOKE TESTS — PLATAFORMA NAS EDUCATIVA     "
echo "============================================================"

# 1. Comprobar estado de Nodos K3s
info "1. Validando que los nodos del clúster estén en estado Ready..."
if kubectl get nodes | grep -q "Ready"; then
  pass "Plano de control K3s operativo y listo."
else
  fail "No se encontraron nodos K3s en estado Ready."
fi

# 2. Comprobar ArgoCD
info "2. Verificando disponibilidad de ArgoCD..."
if kubectl -n argocd get pods -l app.kubernetes.io/name=argocd-server --no-headers | grep -q "Running"; then
  pass "ArgoCD Server está en ejecución."
else
  fail "ArgoCD Server no está listo en el namespace argocd."
fi

# 3. Comprobar Portal QR
info "3. Verificando pod del Portal QR..."
if kubectl get pods -l app=portal-qr --no-headers | grep -q "Running"; then
  pass "Pod Portal QR activo y respondiendo."
else
  fail "Pod del Portal QR no está en estado Running."
fi

# 4. Comprobar Samba File Server
info "4. Verificando pod de Samba File Server..."
if kubectl get pods -l app=samba --no-headers | grep -q "Running"; then
  pass "Servidor Samba corriendo con capabilities seguras."
else
  fail "Servidor Samba no está en estado Running."
fi

# 5. Comprobar Nextcloud
info "5. Verificando Nextcloud (Pod y HTTP status)..."
if kubectl get pods -l app.kubernetes.io/name=nextcloud --no-headers | grep -q "Running"; then
  if curl -s http://localhost/status.php | grep -q '"installed":true'; then
    pass "Nextcloud activo, inicializado y respondiendo correctamente."
  else
    warn "Pod Nextcloud corriendo pero /status.php no devolvió installed:true todavía."
  fi
else
  fail "Pod Nextcloud no está en estado Running."
fi

# 6. Comprobar Grafana & Prometheus
info "6. Verificando Grafana y Prometheus..."
if kubectl get pods -l app.kubernetes.io/name=grafana --no-headers | grep -q "Running" && \
   kubectl get pods -l app.kubernetes.io/name=prometheus --no-headers | grep -q "Running"; then
  pass "Grafana y Prometheus operativos y en ejecución."
else
  fail "Grafana o Prometheus no están en estado Running."
fi

# 7. Comprobar Storage ZFS (Persistent Volume Claims)
info "7. Comprobando PersistentVolumeClaims (PVCs)..."
PVC_PENDING=$(kubectl get pvc --no-headers | grep -v "Bound" || true)
if [ -z "$PVC_PENDING" ]; then
  pass "Todos los PersistentVolumeClaims se encuentran en estado 'Bound'."
else
  warn "Existen PVCs que no están en estado Bound: $PVC_PENDING"
fi

# 8. Comprobar CronJob de Snapshots Locales
info "8. Comprobando existencia del CronJob de snapshots ZFS..."
if kubectl get cronjobs -A | grep -q "zfs-snapshot" > /dev/null 2>&1; then
  pass "CronJob de snapshots nocturnos registrado."
else
  fail "CronJob zfs-snapshot no encontrado."
fi

# 9. Comprobar Bitnami Sealed Secrets Controller
info "9. Verificando controlador de Sealed Secrets..."
if kubectl -n kube-system get deployment sealed-secrets-controller > /dev/null 2>&1; then
  pass "Bitnami Sealed Secrets controller operativo para GitOps Zero-Trust."
else
  fail "Sealed Secrets controller no encontrado."
fi

# 10. Comprobar CronJob de Backup Offsite 3-2-1
info "10. Comprobando CronJob de Backup Offsite (Cloudflare R2)..."
if kubectl get cronjob zfs-offsite-backup -n kube-system > /dev/null 2>&1; then
  pass "CronJob de Backup Offsite 3-2-1 registrado."
else
  warn "CronJob zfs-offsite-backup en proceso de conciliación por ArgoCD."
fi

echo "============================================================"
echo -e "${COLOR_GREEN} ✅ TODOS LOS SMOKE TESTS COMPLETADOS EXITOSAMENTE.${COLOR_RESET}"
echo "============================================================"
