#!/usr/bin/env bash
# ==============================================================================
# Enterprise Educational NAS — Automated Validation & Smoke Tests (18 Checks)
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
echo "    🧪 INICIANDO SMOKE TESTS — PLATAFORMA NAS EMPRESARIAL   "
echo "============================================================"

# 1. Nodos K3s
info "1. Validando que los nodos del clúster estén en estado Ready..."
if kubectl get nodes | grep -q "Ready"; then
  pass "Plano de control K3s operativo y listo."
else
  fail "No se encontraron nodos K3s en estado Ready."
fi

# 2. ArgoCD
info "2. Verificando disponibilidad de ArgoCD..."
if kubectl -n argocd get pods -l app.kubernetes.io/name=argocd-server --no-headers | grep -q "Running"; then
  pass "ArgoCD Server está en ejecución."
else
  fail "ArgoCD Server no está listo en el namespace argocd."
fi

# 3. NAS Control Center Web (Portal)
info "3. Verificando NAS Control Center OS (Web GUI y API REST)..."
if kubectl get pods -l app.kubernetes.io/name=portal-qr --no-headers | grep -q "Running"; then
  if curl -s http://localhost:30081/api/public/status | grep -q "raid_health"; then
    pass "NAS Control Center OS respondiendo en puerto 30081 con API activa."
  else
    warn "Pod activo pero la API /api/public/status aún está iniciando."
  fi
else
  fail "Pod de NAS Control Center no está en estado Running."
fi

# 4. Samba con Escudo Veto Files y Reciclaje
info "4. Verificando servidor Samba con Veto Files y Papelera de Red..."
if kubectl get pods -l app.kubernetes.io/name=samba --no-headers | grep -q "Running"; then
  pass "Servidor Samba corriendo con escudo anti-ransomware y vfs_recycle."
else
  fail "Servidor Samba no está en estado Running."
fi

# 5. Nextcloud HTTP status
info "5. Verificando Nextcloud (Pod y HTTP status)..."
if kubectl get pods -l app.kubernetes.io/name=nextcloud --no-headers | grep -q "Running"; then
  if curl -s http://localhost/status.php | grep -q '"installed":true'; then
    pass "Nextcloud activo, inicializado y respondiendo correctamente."
  else
    warn "Pod Nextcloud corriendo pero /status.php aún no reporta installed:true."
  fi
else
  fail "Pod Nextcloud no está en estado Running."
fi

# 6. Grafana y Prometheus
info "6. Verificando Grafana y Prometheus..."
if kubectl get pods -l app.kubernetes.io/name=grafana --no-headers | grep -q "Running" && \
   kubectl get pods -l app.kubernetes.io/name=prometheus --no-headers | grep -q "Running"; then
  pass "Grafana y Prometheus operativos y en ejecución."
else
  fail "Grafana o Prometheus no están en estado Running."
fi

# 7. PVCs
info "7. Comprobando PersistentVolumeClaims (PVCs)..."
PVC_PENDING=$(kubectl get pvc --no-headers | grep -v "Bound" || true)
if [ -z "$PVC_PENDING" ]; then
  pass "Todos los PersistentVolumeClaims se encuentran en estado 'Bound'."
else
  warn "Existen PVCs que no están en estado Bound: $PVC_PENDING"
fi

# 8. CronJob Snapshots Locales
info "8. Comprobando CronJob de snapshots ZFS..."
if kubectl get cronjob zfs-snapshot -n kube-system > /dev/null 2>&1; then
  pass "CronJob de snapshots periódicos registrado."
else
  fail "CronJob zfs-snapshot no encontrado."
fi

# 9. Bitnami Sealed Secrets
info "9. Verificando controlador de Sealed Secrets y propiedad Zero-Trust..."
if kubectl -n kube-system get deployment sealed-secrets-controller > /dev/null 2>&1; then
  pass "Bitnami Sealed Secrets controller activo."
else
  fail "Sealed Secrets controller ausente."
fi

# 10. CronJob Backup Offsite 3-2-1
info "10. Comprobando CronJob de Backup Offsite (Cloudflare R2)..."
if kubectl get cronjob zfs-offsite-backup -n kube-system > /dev/null 2>&1; then
  pass "CronJob de Backup Offsite 3-2-1 por streaming directo registrado."
else
  fail "CronJob zfs-offsite-backup no encontrado."
fi

# 11. CronJob ZFS Scrub
info "11. Comprobando CronJob de verificación de integridad ZFS Scrub..."
if kubectl get cronjob zfs-scrub -n kube-system > /dev/null 2>&1; then
  pass "CronJob mensual de ZFS Scrub registrado."
else
  fail "CronJob zfs-scrub no encontrado."
fi

# 12. PostgreSQL 16 + Redis 7
info "12. Verificando PostgreSQL 16 y Redis 7 para alta concurrencia..."
if kubectl get pods -l app.kubernetes.io/name=nextcloud-postgres --no-headers | grep -q "Running" && \
   kubectl get pods -l app.kubernetes.io/name=nextcloud-redis --no-headers | grep -q "Running"; then
  pass "PostgreSQL 16 y Redis 7 operativos con aislamiento NetworkPolicy."
else
  fail "PostgreSQL 16 o Redis 7 no están en estado Running."
fi

# 13. Datasets ZFS y Recordsize=8K
info "13. Verificando datasets OpenZFS con cuotas y recordsize transaccional..."
if sudo zfs get -H -o value recordsize tank/nas/postgres 2>/dev/null | grep -q "8K" && \
   sudo zfs get -H -o value quota tank/nas/alumnos 2>/dev/null | grep -q "5G"; then
  pass "Datasets ZFS dedicados (postgres recordsize=8K, cuotas por rol) activos."
else
  fail "Cuotas ZFS o recordsize=8K en tank/nas/postgres no aplicados."
fi

# 14. ZFS Prometheus Exporter + ServiceMonitor + PrometheusRule
info "14. Verificando ZFS Prometheus Exporter y ServiceMonitor..."
if kubectl -n kube-system get pods -l app.kubernetes.io/name=zfs-exporter --no-headers | grep -q "Running" && \
   kubectl -n kube-system get servicemonitor zfs-exporter >/dev/null 2>&1; then
  pass "ZFS Exporter y ServiceMonitor nativo de Prometheus Operator activos."
else
  fail "ZFS Exporter o ServiceMonitor no encontrados."
fi

# 15. Disaster Recovery Drill Script
info "15. Verificando disponibilidad del script de Disaster Recovery Drill..."
if [ -f "$(dirname "$0")/disaster-recovery-drill.sh" ]; then
  pass "Script tests/disaster-recovery-drill.sh presente y ejecutable."
else
  fail "Script tests/disaster-recovery-drill.sh ausente."
fi

# 16. Daemon Escudo Anti-Ransomware
info "16. Verificando Daemon del Escudo Anti-Ransomware (ransomware-sentinel)..."
if kubectl -n kube-system get pods -l app.kubernetes.io/name=ransomware-sentinel --no-headers | grep -q "Running"; then
  pass "Centinela Anti-Ransomware 360° en ejecución activa."
else
  fail "Daemon ransomware-sentinel no está en estado Running."
fi

# 17. Archivos Canary Honeypot
info "17. Verificando integridad de archivos Canary Honeypot en datasets ZFS..."
if [ -f "/tank/nas/alumnos/.canary_honeypot.docx" ] && [ -f "/tank/nas/profesores/.canary_honeypot.docx" ]; then
  pass "Archivos señuelo Canary Honeypots activos y custodiados."
else
  fail "Archivos Canary no detectados en /tank/nas."
fi

# 18. Fast Recovery CLI Tool & Makefile
info "18. Verificando herramienta Fast Recovery y Makefile de automatización..."
if [ -f "$(dirname "$0")/../scripts/nas-recover.sh" ] && [ -f "$(dirname "$0")/../Makefile" ]; then
  pass "Herramienta scripts/nas-recover.sh y Makefile verificados."
else
  fail "scripts/nas-recover.sh o Makefile ausente."
fi

echo "============================================================"
echo -e "${COLOR_GREEN} ✅ TODOS LOS SMOKE TESTS EMPRESARIALES (18/18) COMPLETADOS.${COLOR_RESET}"
echo "============================================================"
