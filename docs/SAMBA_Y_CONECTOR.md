# Servicio SMB, Gestion de Identidades y Conector de Red

Documento operativo del Centro de Control (`apps/portal-qr`, puerto NodePort `30081`).
Complementa el RFC-001 (ver Anexo A).

## 1. Pestana "Servicio SMB (Samba)"

| Elemento | Detalle |
|---|---|
| Recursos | `[profesores]` (10G), `[alumnos]` (5G), `[publico]` (5G), `[nextcloud]` (sin cuota). Datasets `tank/nas/<recurso>`. |
| Cuotas | "Modificar cuota" ejecuta `zfs set quota=<valor> tank/nas/<recurso>` en caliente. |
| Sesiones | Tabla en tiempo real basada en `smbstatus`. |
| Mantenimiento | "Reiniciar Servicio SMB" ejecuta `kubectl rollout restart deployment/samba`. |
| Extensiones VFS | `vfs_recycle` (papelera de red) y veto de extensiones maliciosas. |

## 2. Conector de unidades para Windows

- Descarga: botón "Descargar Conector de Red (.bat)" o `GET /api/samba/connector`.
- Unidades montadas: `Z:` alumnos, `P:` profesores, `X:` publico.
- Servidor por defecto: `192.168.1.50`. Se puede pasar otra IP como primer argumento: `Conectar-Unidad-NAS.bat 10.0.0.5`.

> [!WARNING]
> **Seguridad y Buenas Prácticas Escolares:**
> Aunque el script `.bat` solicita usuario y clave interactivamente y no almacena credenciales, **acostumbrar a docentes y alumnos a descargar y ejecutar archivos `.bat` desde un navegador web es una mala práctica de seguridad** (susceptible a suplantación o phishing en la LAN).
>
> **Alternativa Recomendada para Entornos de Producción (GPO / Active Directory):**
> En un dominio escolar o red gestionada, distribuya el mapeo de unidades mediante directivas de grupo (GPO) o ejecute un script firmado centralmente al iniciar sesión de Windows:
> ```powershell
> # Mapeo transparente mediante PowerShell centralizado:
> New-PSDrive -Name "Z" -PSProvider FileSystem -Root "\\192.168.1.50\alumnos" -Persist
> New-PSDrive -Name "P" -PSProvider FileSystem -Root "\\192.168.1.50\profesores" -Persist
> New-PSDrive -Name "X" -PSProvider FileSystem -Root "\\192.168.1.50\publico" -Persist
> ```

## 3. Gestion de identidades (pestana "Identidad y Accesos")

- Alta individual y por lote CSV (`usuario,contrasena,rol`): crea la cuenta en Nextcloud (`occ user:add`) y en Samba (`smbpasswd`).
- Restablecer clave y Eliminar cuenta (`occ user:delete -n` + `smbpasswd -x`). La cuenta `admin` esta protegida.
- Las cuentas **no viven en git**: estan en la base PostgreSQL de Nextcloud y en la base `smbpasswd`. Tras reconstruir la VM hay que recrearlas (CSV de lote).

## 4. API del portal

Publicas: `GET /api/public/status`, `GET /api/samba/connector`, `POST /api/auth/login`.
Autenticadas (`Authorization: Bearer <token>`):

| Metodo | Ruta | Funcion |
|---|---|---|
| GET | `/api/samba/status` | Estado, version, recursos, cuotas y sesiones SMB |
| POST | `/api/samba/quota` | `{share, quota}` fija la cuota ZFS |
| POST | `/api/samba/restart` | Reinicia el deployment `samba` |
| POST | `/api/users/create`, `/delete`, `/password`, `/csv-import` | Gestion de cuentas |
| GET | `/api/services`, `/api/services/logs?pod=` | Pods de nextcloud, samba, portal-qr, postgres, redis, sentinel y zfs-exporter |
| POST | `/api/services/restart` | Reinicia un deployment (prueba `default`, luego `kube-system`) |

## 5. Decisiones de ingenieria

| Decision | Motivo |
|---|---|
| `ThreadedHTTPServer` | Con un solo hilo, una peticion lenta bloqueaba el portal y fallaban las sondas de K3s (crashloop). |
| Sondas `timeoutSeconds: 5`, `failureThreshold: 5` | Tolerar picos de carga sin reinicios innecesarios. |
| Nextcloud sobre PostgreSQL 16 + Redis 7 | Concurrencia y bloqueo de archivos fiables frente a SQLite. |
| Listado de servicios por nombre de pod | El filtro por etiqueta `part-of=nas-platform` omitia samba y nextcloud, y la tabla salia vacia. |
| Cambios en `server.py`/`index.html` | Se regenera `configmap.yaml` (`build_configmap.py`) y se reinicia `portal-qr`: el pod no recarga el codigo solo. |

## 6. Credenciales y Hardening DevSecOps

- **Cero Claves Hardcodeadas:** `ADMIN_PASS_FALLBACK` ha sido completamente eliminado del código. El portal lee las credenciales administrativas de forma estricta desde el Secret de Kubernetes (`nextcloud-admin-credentials`) o variables de entorno del contenedor. Si el Secret no existe, el servidor rechaza el inicio de sesión y no arranca en modo inseguro.
- **Tokens Criptográficos y Rate-Limiting:** Los tokens de sesión se generan mediante `secrets.token_urlsafe(32)` con expiración estricta (TTL de 8 horas) y bloqueo temporal tras 5 intentos fallidos consecutivos por IP.
- **Cifrado en Tránsito (TLS Obligatorio):** El portal se sirve obligatoriamente bajo HTTPS (TLS 1.3/1.2) con certificados generados al vuelo o provistos por el clúster.
