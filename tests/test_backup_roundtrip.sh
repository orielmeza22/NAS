#!/usr/bin/env bash
# Verifica que el cifrado del backup y el descifrado de la recuperación usen los
# mismos parámetros, y que la firma HMAC detecte un objeto alterado.
# No necesita red, clúster ni ZFS: solo openssl.
set -euo pipefail

ROJO="\033[31m"; VERDE="\033[32m"; AZUL="\033[34m"; RESET="\033[0m"
ok()   { echo -e "${VERDE}[OK]${RESET} $1"; }
mal()  { echo -e "${ROJO}[FALLA]${RESET} $1"; exit 1; }
info() { echo -e "${AZUL}[INFO]${RESET} $1"; }

ITER=600000
export BACKUP_ENCRYPTION_KEY="clave-de-prueba-para-test-backup"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# 1. Los tres scripts que cifran o descifran deben usar el mismo número de iteraciones
info "1. Comprobando parámetros en los scripts..."
for f in scripts/nas-recover.sh infrastructure/zfs-offsite-backup-cronjob.yaml tests/disaster-recovery-drill.sh; do
  [ -f "$f" ] || mal "No existe $f"
  grep -q -- "-iter ${ITER}" "$f" || mal "$f no usa -iter ${ITER}"
done
ok "Backup, recuperación y simulacro comparten -iter ${ITER}"

# 2. Ida y vuelta con los parámetros reales
info "2. Cifrando y descifrando un bloque de prueba..."
head -c 262144 /dev/urandom > "$TMP/plain.bin"
openssl enc -aes-256-cbc -salt -pbkdf2 -iter "$ITER" \
  -pass env:BACKUP_ENCRYPTION_KEY -in "$TMP/plain.bin" -out "$TMP/plain.enc"
[ -s "$TMP/plain.enc" ] || mal "No se generó el archivo cifrado"

openssl enc -d -aes-256-cbc -salt -pbkdf2 -iter "$ITER" \
  -pass env:BACKUP_ENCRYPTION_KEY -in "$TMP/plain.enc" -out "$TMP/roundtrip.bin" 2>/dev/null \
  || mal "El descifrado falló con los parámetros actuales"
cmp -s "$TMP/plain.bin" "$TMP/roundtrip.bin" || mal "El archivo descifrado no coincide con el original"
ok "Ida y vuelta correcta (262144 bytes idénticos)"

# 3. El descifrado viejo (sin -iter) debe fallar; si funciona, el test de parámetros no sirve
info "3. Comprobando que sin -iter efectivamente no se puede descifrar..."
if openssl enc -d -aes-256-cbc -salt -pbkdf2 \
     -pass env:BACKUP_ENCRYPTION_KEY -in "$TMP/plain.enc" -out "$TMP/viejo.bin" 2>/dev/null; then
  cmp -s "$TMP/plain.bin" "$TMP/viejo.bin" && mal "Descifrar sin -iter funcionó: los parámetros no coinciden con el backup"
fi
ok "Sin -iter falla, como se esperaba"

# 4. La firma HMAC detecta un byte alterado
info "4. Comprobando la detección de alteraciones con HMAC..."
FIRMA="$(openssl dgst -sha256 -hmac "$BACKUP_ENCRYPTION_KEY" -r "$TMP/plain.enc" | awk '{print $1}')"
[ -n "$FIRMA" ] || mal "No se pudo calcular el HMAC"

printf '\x00' | dd of="$TMP/plain.enc" bs=1 seek=100 conv=notrunc 2>/dev/null
ALTERADA="$(openssl dgst -sha256 -hmac "$BACKUP_ENCRYPTION_KEY" -r "$TMP/plain.enc" | awk '{print $1}')"
[ "$FIRMA" != "$ALTERADA" ] || mal "El HMAC no cambió tras alterar el archivo"
ok "Un byte alterado invalida la firma"

echo
echo -e "${VERDE}Pruebas de backup superadas.${RESET}"
