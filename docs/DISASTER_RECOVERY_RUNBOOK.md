# Runbook Operativo de Disaster Recovery (DRP) — Enterprise NAS

## 1. Matriz de Objetivos de Recuperación (SLAs)

| Escenario de Desastre | Causa Posible | RPO (Pérdida Máxima de Datos) | RTO (Tiempo de Recuperación) | Método de Restauración |
| :--- | :--- | :--- | :--- | :--- |
| **A. Ataque de Ransomware / Cifrado Masivo** | Virus en PC de alumno o profesor en LAN | $< 1\text{ hora}$ | $< 5\text{ segundos}$ | `nas-recover.sh rollback-local <snapshot>` o 1 clic en Web GUI |
| **B. Borrado Accidental de Archivo Crítico** | Error humano de usuario en Samba/Nextcloud | Inmediato (desde Papelera o Snapshot) | $< 10\text{ segundos}$ | Explorador de Papelera Web o `nas-recover.sh restore-file` |
| **C. Fallo Físico de 1 Disco en RAID-Z1** | Rotura de hardware mecánico/SSD | $0\text{ datos}$ (Tolerancia $N+1$) | Inmediato ($0\text{ downtime}$) + Resilver en background | `zpool replace tank /dev/disk_viejo /dev/disk_nuevo` o Web GUI |
| **D. Catástrofe Total del Servidor (Bare-Metal)** | Incendio, robo o destrucción completa del hardware | $< 24\text{ horas}$ (Último backup R2) | $15\text{ a }60\text{ minutos}$ (según ancho de banda de red) | `import-master-keys.sh` + `bootstrap` + `nas-recover.sh restore-cloud` |

---

## 2. Procedimiento Preventivo Obligatorio: Custodia del "Emergency Kit"

> [!CAUTION]
> **REGLA DE VIDA O MUERTE DEL SISTEMA:**
> Si el servidor se destruye físicamente y no se cuenta con el **Emergency Kit** fuera del equipo, **los respaldos en Cloudflare R2 son ruido criptográfico 100% irrecuperable**. La clave privada maestra de Sealed Secrets y la clave de cifrado simétrico mueren con el host.

### Generación Periódica del Kit de Rescate:
Ejecutar inmediatamente tras la instalación o cambio de credenciales:
```bash
sudo bash scripts/export-master-keys.sh /media/pendrive_cifrado
```
El archivo generado (`nas-emergency-kit-YYYYMMDD.tar.gz`) debe ser almacenado en:
1. Una unidad USB cifrada resguardada en la caja fuerte de la escuela.
2. O en el gestor de contraseñas institucional (Bitwarden / KeePass).

---

## 3. Procedimientos Operativos Paso a Paso

### Escenario A: Ataque de Ransomware
1. **Contención Inmediata:**
   - En el **NAS Control Center Web**, activar el **"Kill-Switch" de Internet (Modo Solo LAN)** para aislar el tráfico externo.
2. **Identificación del Snapshot Limpio:**
   - Ejecutar `make list-backups` o revisar la pestaña **Backups** en la Web GUI.
   - Localizar el último snapshot previo a la hora del incidente (ej. `tank/nas@k8s-auto-20261030-1400`).
3. **Reversión Instantánea:**
   - Ejecutar:
     ```bash
     bash scripts/nas-recover.sh rollback-local tank/nas@k8s-auto-20261030-1400
     ```
     o pulsar **"⏪ Rollback Instantáneo"** en el panel web. En menos de 5 segundos, todos los archivos cifrados desaparecen y los datos limpios vuelven a estar en línea.

### Escenario B: Recuperación de Archivo Individual Borrado
1. Abrir la pestaña **Papelera** en el panel web o revisar el directorio oculto `.Papelera/` del recurso compartido.
2. Si fue vaciada, extraer el archivo desde el snapshot:
   ```bash
   bash scripts/nas-recover.sh restore-file tank/nas@k8s-auto-20261030-1400 profesores/planillas/notas.xlsx /tank/nas/profesores/planillas/notas.xlsx
   ```

### Escenario C: Fallo y Reemplazo de Disco en Hardware Físico (RAID-Z1 / Mirrors)
1. Comprobar estado del pool y localizar el disco defectuoso mediante `zpool status tank`.
2. Identificar el identificador persistente de hardware (`by-id` o número de serie):
   ```bash
   lsblk -o NAME,SIZE,MODEL,SERIAL
   ls -la /dev/disk/by-id/
   ```
3. Sustituir físicamente el disco dañado en el chasis por la nueva unidad.
4. Ejecutar el reemplazo en OpenZFS utilizando identificadores persistentes:
   ```bash
   # Reemplazo en servidor físico con identificador único de hardware:
   sudo zpool replace tank /dev/disk/by-id/ata-DISCO_DAÑADO /dev/disk/by-id/ata-DISCO_NUEVO
   ```
   *(Para entornos virtuales de prueba con archivos .img: `sudo zpool replace tank /zfs-disks/disk2.img /zfs-disks/disk2_nuevo.img`).*
5. El sistema inicia el proceso de **Resilver** en background. Supervisar el progreso mediante:
   ```bash
   watch -n 2 sudo zpool status tank
   ```

### Escenario D: Reconstrucción en Hardware Nuevo (Cloudflare R2 Restore)
1. **Aprovisionar el sistema operativo base** (Ubuntu Server / Debian) en el nuevo servidor.
2. **Clonar el repositorio:**
   ```bash
   git clone https://github.com/orielmeza22/NAS.git /home/oriel/nas-gitops
   cd /home/oriel/nas-gitops
   ```
3. **Instalar el software base y configurar ZFS:**
   ```bash
   sudo bash scripts/bootstrap-enterprise-nas.sh /dev/sdb /dev/sdc /dev/sdd
   ```
4. **Reinyectar el Emergency Kit ANTES de sincronizar aplicaciones:**
   ```bash
   sudo bash scripts/import-master-keys.sh /ruta/al/nas-emergency-kit-YYYYMMDD.tar.gz
   ```
   *(Esto recarga la clave privada de Sealed Secrets y las credenciales de Cloudflare R2).*
5. **Ejecutar la restauración real desde Cloudflare R2 hacia OpenZFS:**
   ```bash
   sudo bash scripts/nas-recover.sh restore-cloud tank/nas
   ```
   *(Descarga el flujo en streaming, descifra al vuelo con AES-256-CBC PBKDF2 y restaura la jerarquía completa de datasets).*
6. **Verificar estado de salud y aplicaciones:**
   ```bash
   sudo bash tests/smoke-test.sh
   ```

---

## 4. Inmutabilidad Offsite y Protección WORM (Cloudflare R2 Object Lock)

> [!IMPORTANT]
> **Defensa en Profundidad contra Destrucción Remota:**
> Si un atacante compromete el servidor local con privilegios `root`, las credenciales de backup almacenadas en el clúster podrían ser utilizadas para ejecutar `rclone delete` sobre el bucket remoto.
>
> Para neutralizar este vector en producción institucional:
> 1. **Activar Object Lock (WORM):** En la configuración del bucket Cloudflare R2, habilite **Object Lock** con modo de retención `Compliance` durante un mínimo de 30 días. Bajo este modo, ni siquiera el usuario root ni las credenciales de la API pueden borrar o sobreescribir un archivo antes de que expire el período.
> 2. **Principio de Mínimo Privilegio en Tokens API:** El API Token de Cloudflare configurado en el clúster (`r2-backup-credentials`) debe tener permisos exclusivos de **`Object Read & Write`** (`PutObject`, `GetObject`), denegando expresamente permisos de administración de bucket o eliminación masiva (`DeleteBucket`).
