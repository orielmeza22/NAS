#!/usr/bin/env bash
# ==============================================================================
# Enterprise Educational NAS — Master Encryption & Secrets Disaster Recovery Restore
# Injects the original Bitnami Sealed Secrets master key and R2 credentials into
# a fresh cluster BEFORE applications deploy, ensuring all sealed secrets in Git
# can be successfully decrypted.
# ==============================================================================
set -euo pipefail

COLOR_RESET="\033[0m"
COLOR_GREEN="\033[32m"
COLOR_RED="\033[31m"
COLOR_BLUE="\033[34m"
COLOR_YELLOW="\033[33m"

info() { echo -e "${COLOR_BLUE}[RESTORE-KEYS]${COLOR_RESET} $1"; }
pass() { echo -e "${COLOR_GREEN}[SUCCESS]${COLOR_RESET} $1"; }
fail() { echo -e "${COLOR_RED}[ERROR]${COLOR_RESET} $1"; exit 1; }
warn() { echo -e "${COLOR_YELLOW}[WARN]${COLOR_RESET} $1"; }

SRC="${1:-}"
if [ -z "$SRC" ] || [ ! -e "$SRC" ]; then
  fail "Uso: $0 <ruta-al-tar.gz-o-directorio-del-emergency-kit>"
fi

WORKDIR=$(mktemp -d /tmp/nas-restore-keys-XXXXXX)
trap 'rm -rf "$WORKDIR"' EXIT

if [ -f "$SRC" ] && [[ "$SRC" =~ \.tar\.gz$ ]]; then
  info "Descomprimiendo kit de emergencia: $SRC..."
  tar -xzf "$SRC" -C "$WORKDIR"
  KIT_DIR=$(find "$WORKDIR" -maxdepth 1 -type d -name "emergency-kit-*" | head -n 1)
else
  KIT_DIR="$SRC"
fi

if [ -z "$KIT_DIR" ] || [ ! -d "$KIT_DIR" ]; then
  fail "Estructura del Emergency Kit inválida en $SRC."
fi

info "1. Verificando disponibilidad de Kubernetes (K3s)..."
if ! kubectl get nodes >/dev/null 2>&1; then
  fail "Kubernetes no está respondiendo. Asegúrese de que K3s esté instalado y en ejecución."
fi

info "2. Restaurando clave maestra de Bitnami Sealed Secrets..."
SEALED_KEY_FILE="${KIT_DIR}/sealed-secrets-master-key.yaml"
if [ -f "$SEALED_KEY_FILE" ]; then
  kubectl apply -f "$SEALED_KEY_FILE"
  pass "Clave maestra de Sealed Secrets reinyectada en kube-system."
  
  # Si el controlador ya está corriendo, reiniciarlo para que cargue la clave restaurada
  if kubectl -n kube-system get deployment sealed-secrets-controller >/dev/null 2>&1; then
    info "Reiniciando sealed-secrets-controller para cargar la clave restaurada..."
    kubectl -n kube-system rollout restart deployment/sealed-secrets-controller
    kubectl -n kube-system rollout status deployment/sealed-secrets-controller --timeout=60s
    pass "Controlador de Sealed Secrets sincronizado con la clave maestra original."
  fi
else
  warn "No se encontró sealed-secrets-master-key.yaml en el kit."
fi

info "3. Restaurando credenciales de Cloudflare R2..."
R2_KEY_FILE="${KIT_DIR}/r2-backup-credentials.yaml"
if [ -f "$R2_KEY_FILE" ]; then
  kubectl apply -f "$R2_KEY_FILE"
  pass "Credenciales de Cloudflare R2 restauradas en kube-system."
fi

info "4. Restaurando credenciales locales de aplicaciones..."
if [ -f "${KIT_DIR}/nextcloud-admin-credentials.yaml" ]; then
  kubectl apply -f "${KIT_DIR}/nextcloud-admin-credentials.yaml"
fi
if [ -f "${KIT_DIR}/samba-credentials.yaml" ]; then
  kubectl apply -f "${KIT_DIR}/samba-credentials.yaml"
fi

pass "================================================================================"
pass "  RESTAURACIÓN DE CLAVES COMPLETADA CON ÉXITO"
pass "  El nuevo clúster ahora puede descifrar todos los SealedSecrets de Git y R2."
pass "================================================================================"
