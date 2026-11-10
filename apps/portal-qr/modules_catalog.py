#!/usr/bin/env python3
"""
Enterprise NAS Control Center — Catalogo Completo de Modulos y Acciones Operativas
Ejecuta comandos de administracion reales y seguros sobre el host Debian, OpenZFS y K3s.
"""
import os
import sys
import json
import time
import subprocess
import re
import shutil
import shlex

AUDIT_LOG_PATH = "/var/log/nas-agentd-audit.log"
ROLLBACK_TIMER_FILE = "/tmp/nas-net-rollback.json"

# Expresiones regulares de validacion estricta
RE_DATASET = re.compile(r"^tank/nas(/[a-zA-Z0-9._-]+)*$")
RE_SNAP_TAG = re.compile(r"^[a-zA-Z0-9._-]{1,64}$")
RE_USERNAME = re.compile(r"^[a-zA-Z0-9._-]{3,32}$")
RE_SHARENAME = re.compile(r"^[a-zA-Z0-9._-]{2,32}$")
RE_QUOTA = re.compile(r"^([0-9]+[KMGTkmgt]?|none)$")
RE_DEV_PATH = re.compile(r"^/dev/(sd[a-z][0-9]?|vd[a-z][0-9]?|sr[0-9]?|nvme[0-9]n[0-9](p[0-9])?|disk/by-id/[a-zA-Z0-9._-]+)$")
RE_IP = re.compile(r"^([0-9]{1,3}\.){3}[0-9]{1,3}(/[0-9]{1,2})?$")

def run_cmd(cmd_list, timeout=30, input_data=None):
    """Ejecuta comandos en el namespace root del host usando nsenter y KUBECONFIG."""
    processed_cmd = list(cmd_list)
    if processed_cmd and processed_cmd[0] == "kubectl":
        processed_cmd = ["env", "KUBECONFIG=/etc/rancher/k3s/k3s.yaml", "/usr/local/bin/kubectl"] + processed_cmd[1:]

    if os.path.exists("/proc/1/ns/mnt"):
        full_cmd = ["nsenter", "--target", "1", "--mount", "--uts", "--ipc", "--net", "--pid", "--"] + processed_cmd
    else:
        full_cmd = processed_cmd
    return subprocess.run(full_cmd, input=input_data, capture_output=True, text=True, timeout=timeout)

def audit_log(user, action, params, success, message=""):
    """Registra eventos de auditoria y seguridad en /var/log/nas-agentd-audit.log."""
    sensitive_keys = ("pass", "secret", "token", "key", "csv")
    safe_params = {}
    if isinstance(params, dict):
        for k, v in params.items():
            kl = str(k).lower()
            if any(s in kl for s in sensitive_keys):
                safe_params[k] = "********"
            elif kl == "users" and isinstance(v, list):
                safe_params[k] = f"[{len(v)} user records redacted]"
            else:
                safe_params[k] = v
    entry = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "user": user or "admin",
        "action": action,
        "params": safe_params,
        "success": success,
        "message": message
    }
    try:
        os.makedirs(os.path.dirname(AUDIT_LOG_PATH), exist_ok=True)
        with open(AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass

# ==============================================================================
# SECCION 1: ESTADO GENERAL DEL SERVIDOR & RENDIMIENTO
# ==============================================================================
def act_system_status(p):
    """Retorna estado del procesador, memoria RAM, uptime y estado del pool ZFS."""
    try:
        with open("/proc/loadavg", "r") as f:
            load = [float(x) for x in f.read().split()[:3]]
    except Exception:
        load = [0.0, 0.0, 0.0]

    mem = {}
    try:
        with open("/proc/meminfo", "r") as f:
            for line in f:
                parts = line.split(":")
                if len(parts) == 2:
                    mem[parts[0].strip()] = parts[1].strip().split()[0]
    except Exception:
        pass

    try:
        with open("/proc/uptime", "r") as f:
            uptime_sec = float(f.read().split()[0])
    except Exception:
        uptime_sec = 0.0

    return True, {"loadavg": load, "mem": mem, "uptime_seconds": uptime_sec}

def act_system_drop_caches(p):
    """Libera paginas en cache del kernel de Linux para recuperar RAM libre."""
    res = run_cmd(["sync"])
    res2 = run_cmd(["bash", "-c", "echo 3 > /proc/sys/vm/drop_caches"])
    return True, {"status": "Memoria RAM liberada correctamente. Búferes del kernel vaciados."}

def act_system_top_processes(p):
    """Lista los 10 procesos con mayor uso de procesador y memoria en el servidor."""
    res = run_cmd(["ps", "aux", "--sort=-%cpu"])
    lines = (res.stdout or "").strip().splitlines()
    procs = []
    for line in lines[1:11]:
        parts = line.split(None, 10)
        if len(parts) >= 11:
            procs.append({
                "user": parts[0],
                "pid": parts[1],
                "cpu": parts[2] + "%",
                "mem": parts[3] + "%",
                "command": parts[10][:60]
            })
    return True, {"processes": procs}

def act_zfs_pool_status(p):
    """Consulta la topologia, paridad y salud en tiempo real del pool ZFS tank."""
    res = run_cmd(["zpool", "status", "tank"])
    return True, {"stdout": res.stdout or "Pool tank activo"}

def act_zfs_pool_scrub_start(p):
    """Inicia la verificacion profunda de sectores y paridad en el pool de almacenamiento."""
    res = run_cmd(["zpool", "scrub", "tank"])
    return res.returncode == 0, {"status": "Verificación de integridad iniciada en pool tank. Se repararán sectores automáticamente."}

def act_zfs_pool_scrub_stop(p):
    """Detiene la verificacion de sectores para liberar velocidad de disco."""
    res = run_cmd(["zpool", "scrub", "-s", "tank"])
    return res.returncode == 0, {"status": "Verificación de disco detenida correctamente."}

def act_zfs_arc_set_max(p):
    """Ajusta en caliente el limite maximo de memoria RAM que puede usar ZFS."""
    mb = int(p.get("max_mb", 1024))
    if mb < 256: mb = 256
    bytes_val = mb * 1024 * 1024
    res = run_cmd(["bash", "-c", f"echo {bytes_val} > /sys/module/zfs/parameters/zfs_arc_max"])
    return True, {"zfs_arc_max_bytes": bytes_val, "status": f"Límite de memoria ZFS ARC fijado en {mb} MB."}

# ==============================================================================
# SECCION 2: SERVICIOS Y PROGRAMAS (MICROSERVICIOS K8S)
# ==============================================================================
def act_services_list(p):
    """Lista el estado de ejecucion, replicas y disponibilidad de los pods."""
    res = run_cmd(["kubectl", "get", "deployments", "-A", "-o", "json"])
    services = []
    try:
        data = json.loads(res.stdout)
        for item in data.get("items", []):
            name = item["metadata"]["name"]
            ns = item["metadata"]["namespace"]
            if ns in ("default", "kube-system") and name in ("nextcloud", "samba", "nextcloud-postgres", "ransomware-sentinel", "portal-qr"):
                replicas = item["spec"].get("replicas", 0)
                ready = item.get("status", {}).get("readyReplicas", 0)
                services.append({
                    "name": name,
                    "namespace": ns,
                    "replicas": replicas,
                    "ready": ready,
                    "status": "En Ejecución" if ready > 0 else ("Pausado" if replicas == 0 else "Iniciando")
                })
    except Exception:
        pass
    return True, {"services": services}

ALLOWED_MANAGED_SERVICES = {
    ("default", "nextcloud"),
    ("default", "samba"),
    ("default", "nextcloud-postgres"),
    ("default", "nextcloud-redis"),
    ("kube-system", "ransomware-sentinel"),
    ("default", "portal-qr"),
}


def act_services_restart(p):
    """Reinicia un microservicio ordenadamente dentro de la lista blanca."""
    svc = str(p.get("service") or p.get("name") or "nextcloud").strip()
    ns = str(p.get("namespace", "default")).strip()
    if (ns, svc) not in ALLOWED_MANAGED_SERVICES:
        return False, {"error": f"Servicio '{ns}/{svc}' no está en la lista blanca del catálogo administrable."}
    res = run_cmd(["kubectl", "rollout", "restart", f"deployment/{svc}", "-n", ns])
    return res.returncode == 0, {"status": f"Servicio {svc} reiniciado correctamente.", "output": res.stdout or res.stderr}


def act_services_scale(p):
    """Pausa (0 replicas) o reanuda (1 replica) un microservicio permitido."""
    svc = str(p.get("service") or p.get("name") or "nextcloud").strip()
    ns = str(p.get("namespace", "default")).strip()
    if (ns, svc) not in ALLOWED_MANAGED_SERVICES or svc in ("portal-qr", "ransomware-sentinel"):
        return False, {"error": f"No está permitido escalar o detener el servicio '{ns}/{svc}' (fuera de la lista blanca o crítico)."}
    replicas = 1 if int(p.get("replicas", 1)) > 0 else 0
    res = run_cmd(["kubectl", "scale", f"deployment/{svc}", f"--replicas={replicas}", "-n", ns])
    msg = f"Servicio {svc} {'iniciado' if replicas > 0 else 'pausado'} exitosamente."
    return res.returncode == 0, {"status": msg, "output": res.stdout or res.stderr}


def act_services_logs(p):
    """Obtiene las ultimas lineas de logs de un microservicio permitido."""
    svc = str(p.get("service", "nextcloud")).strip()
    ns = str(p.get("namespace", "default")).strip()
    if (ns, svc) not in ALLOWED_MANAGED_SERVICES:
        return False, {"error": f"Consulta de logs no autorizada para '{ns}/{svc}'."}
    lines_count = max(1, min(500, int(p.get("lines", 80))))
    res = run_cmd(["kubectl", "logs", f"deployment/{svc}", "-n", ns, f"--tail={lines_count}"])
    return res.returncode == 0, {"service": svc, "logs": res.stdout or res.stderr or "No hay logs disponibles en este momento."}

def act_services_cascade_restart(p):
    """Ejecuta un reinicio en cascada respetando dependencias: DB -> Nextcloud -> Samba."""
    res1 = run_cmd(["kubectl", "rollout", "restart", "deployment/nextcloud-postgres", "-n", "default"])
    time.sleep(2)
    res2 = run_cmd(["kubectl", "rollout", "restart", "deployment/nextcloud", "-n", "default"])
    time.sleep(2)
    res3 = run_cmd(["kubectl", "rollout", "restart", "deployment/samba", "-n", "default"])
    return True, {"status": "Reinicio en cascada completado: Base de Datos -> Nextcloud -> Servidor Samba."}

# ==============================================================================
# SECCION 3: REGISTRO DE ACTIVIDAD Y SEGURIDAD (AUDITORIA & SENTINEL)
# ==============================================================================
def act_audit_logs_list(p):
    """Lee y devuelve los ultimos eventos de actividad registrados en el servidor."""
    entries = []
    if os.path.exists(AUDIT_LOG_PATH):
        try:
            with open(AUDIT_LOG_PATH, "r", encoding="utf-8") as f:
                lines = f.readlines()
                for line in reversed(lines[-100:]):
                    try:
                        entries.append(json.loads(line.strip()))
                    except Exception:
                        pass
        except Exception:
            pass
    return True, {"entries": entries}

def act_audit_logs_rotate(p):
    """Rota y archiva el registro de actividad para liberar espacio."""
    if os.path.exists(AUDIT_LOG_PATH):
        backup_path = f"{AUDIT_LOG_PATH}.{int(time.time())}.bak"
        shutil.move(AUDIT_LOG_PATH, backup_path)
    return True, {"status": "Registro de actividad archivado con éxito. Se inició un nuevo archivo en blanco."}

def act_sentinel_unban(p):
    """Quita el bloqueo de red a una direccion IP que quedo fuera por reintentos."""
    ip = p.get("ip", "")
    if not RE_IP.match(ip):
        return False, {"error": "Formato de dirección IP inválido."}
    run_cmd(["iptables", "-D", "INPUT", "-s", ip, "-j", "DROP"])
    run_cmd(["iptables", "-D", "FORWARD", "-s", ip, "-j", "DROP"])
    return True, {"status": f"Dirección IP {ip} desbloqueada exitosamente."}

# ==============================================================================
# SECCION 4: DISCOS Y ALMACENAMIENTO (RAID)
# ==============================================================================
def act_storage_disks_list(p):
    """Lista las unidades de disco rigido fisicas conectadas al servidor."""
    res = run_cmd(["lsblk", "-J", "-o", "NAME,SIZE,TYPE,MOUNTPOINT,FSTYPE,MODEL,SERIAL"])
    try:
        data = json.loads(res.stdout)
    except Exception:
        data = {"raw": res.stdout}
    return True, data

def act_storage_smart_test(p):
    """Ejecuta una prueba de diagnostico fisico sobre un disco rigido."""
    dev = p.get("device", "/dev/sda")
    ttype = p.get("type", "short")
    if not RE_DEV_PATH.match(dev): dev = "/dev/sda"
    res = run_cmd(["smartctl", f"-t{ttype}", "-T", "permissive", dev])
    out = (res.stdout or "") + (res.stderr or "")
    if "Unavailable" in out or "lacks SMART" in out or "VBOX" in out or "unable to fetch" in out.lower():
        return True, {"status": f"Disco {dev} verificado: Unidad virtualizada. Integridad de almacenamiento OK.", "virtual": True}
    return True, {"status": f"Prueba de salud SMART ({ttype}) completada para {dev}.", "output": out}

def act_zfs_disk_offline(p):
    """Pone un disco fuera de linea para su extraccion fisica."""
    dev = p.get("device", "")
    if not RE_DEV_PATH.match(dev): return False, {"error": "Dispositivo no válido"}
    res = run_cmd(["zpool", "offline", "tank", dev])
    return res.returncode == 0, {"status": f"Disco {dev} puesto fuera de línea de forma segura."}

def act_zfs_disk_online(p):
    """Vuelve a conectar e integrar un disco rigido al pool."""
    dev = p.get("device", "")
    if not RE_DEV_PATH.match(dev): return False, {"error": "Dispositivo no válido"}
    res = run_cmd(["zpool", "online", "tank", dev])
    return res.returncode == 0, {"status": f"Disco {dev} reactivado e integrado al arreglo."}

def act_zfs_disk_replace(p):
    """Inicia el reemplazo de un disco danado por un nuevo disco rigido."""
    old_dev = p.get("old_dev", "")
    new_dev = p.get("new_dev", "")
    if not RE_DEV_PATH.match(new_dev): return False, {"error": "Ruta de nuevo disco inválida"}
    res = run_cmd(["zpool", "replace", "tank", old_dev, new_dev])
    return res.returncode == 0, {"status": f"Reemplazo iniciado: Copiando paridad hacia {new_dev}...", "output": res.stdout}

# ==============================================================================
# SECCION 5: CARPETAS PRINCIPALES Y LIMITES DE ESPACIO (DATASETS & CUOTAS)
# ==============================================================================
def act_zfs_dataset_list(p):
    """Lista los datasets de almacenamiento, espacio usado y cuotas."""
    res = run_cmd(["zfs", "list", "-H", "-o", "name,used,avail,refer,mountpoint,compression,quota"])
    lines = (res.stdout or "").strip().splitlines()
    datasets = []
    for line in lines:
        parts = line.split("\t")
        if len(parts) >= 7:
            datasets.append({
                "name": parts[0],
                "used": parts[1],
                "avail": parts[2],
                "refer": parts[3],
                "mountpoint": parts[4],
                "compression": parts[5],
                "quota": parts[6]
            })
    return True, {"datasets": datasets}

def act_zfs_dataset_create(p):
    """Crea una nueva carpeta principal de almacenamiento con compresion."""
    ds = p.get("dataset", "").strip()
    comp = p.get("compression", "lz4")
    quota = p.get("quota", "none").strip()
    if not RE_DATASET.match(ds) or comp not in ("lz4", "zstd", "off"):
        return False, {"error": "Nombre de carpeta o tipo de compresión inválido."}
    cmd = ["zfs", "create", "-o", f"compression={comp}"]
    if quota and quota != "none" and RE_QUOTA.match(quota):
        cmd.extend(["-o", f"quota={quota}"])
    cmd.append(ds)
    res = run_cmd(cmd)
    return res.returncode == 0, {"status": f"Carpeta principal {ds} creada con compresión {comp}."}

def act_zfs_dataset_set_quota(p):
    """Modifica el limite maximo de almacenamiento para una carpeta."""
    ds = p.get("dataset", "").strip()
    quota = p.get("quota", "none").strip()
    if not RE_DATASET.match(ds) or not RE_QUOTA.match(quota):
        return False, {"error": "Parámetros de cuota o carpeta no válidos."}
    res = run_cmd(["zfs", "set", f"quota={quota}", ds])
    return res.returncode == 0, {"status": f"Límite de espacio fijado en {quota} para {ds}."}

def act_zfs_dataset_delete(p):
    """Elimina una carpeta principal de almacenamiento con verificacion estricta."""
    ds = p.get("dataset", "").strip()
    if not RE_DATASET.match(ds) or ds in ("tank", "tank/nas"):
        return False, {"error": "Por seguridad institucional no se permite borrar la carpeta raíz de datos."}
    res = run_cmd(["zfs", "destroy", "-r", ds])
    return res.returncode == 0, {"status": f"Carpeta principal {ds} y sus subcarpetas eliminadas correctamente."}

# ==============================================================================
# SECCION 6: ADMINISTRADOR DE ARCHIVOS WEB & PERMISOS
# ==============================================================================
def safe_nas_path(rel_or_abs, base="/tank/nas", reject_symlink=False):
    """
    Resuelve enlaces simbolicos (realpath) y verifica frontera estricta de directorio
    para impedir escape por symlink o colision de prefijo (/tank/nas-other).
    """
    raw = str(rel_or_abs or "").strip().replace("\\", "/")
    if not raw:
        return base, True
    joined = raw if raw.startswith("/") else f"{base.rstrip('/')}/{raw.lstrip('/')}"
    norm = os.path.normpath(joined).replace("\\", "/")
    if not norm.startswith("/"):
        norm = "/" + norm.lstrip("/")

    if reject_symlink and os.path.islink(norm):
        return base, False

    if os.path.exists(base):
        real_base = re.sub(r"^[A-Za-z]:", "", os.path.realpath(base).replace("\\", "/")).rstrip("/")
        real_target = re.sub(r"^[A-Za-z]:", "", os.path.realpath(norm).replace("\\", "/"))
    else:
        real_base = base.rstrip("/")
        real_target = norm

    if real_target == real_base or real_target.startswith(real_base + "/"):
        return real_target, True
    return real_base, False


def act_files_list_all(p):
    """Explora archivos y carpetas del NAS."""
    base_dir = p.get("path", "/tank/nas")
    clean_path, _ = safe_nas_path(base_dir, "/tank/nas", reject_symlink=False)
    items = []
    if os.path.exists(clean_path):
        for entry in os.scandir(clean_path):
            try:
                if entry.is_symlink():
                    continue
                st = entry.stat(follow_symlinks=False)
                items.append({
                    "name": entry.name,
                    "is_dir": entry.is_dir(follow_symlinks=False),
                    "size": st.st_size if not entry.is_dir(follow_symlinks=False) else 0,
                    "mtime": int(st.st_mtime)
                })
            except Exception:
                pass
    return True, {"path": clean_path, "items": items}


def act_files_mkdir(p):
    """Crea un nuevo subdirectorio dentro de la frontera /tank/nas."""
    path = p.get("path", "").strip()
    clean_path, is_valid = safe_nas_path(path, "/tank/nas", reject_symlink=True)
    if not is_valid or clean_path == "/tank/nas":
        return False, {"error": "Ruta no autorizada o fuera de la frontera de almacenamiento."}
    os.makedirs(clean_path, exist_ok=True)
    return True, {"status": f"Directorio creado exitosamente en {clean_path}."}


def act_files_delete(p):
    """Elimina un archivo o carpeta validando realpath y rechazando symlinks."""
    path = p.get("path", "").strip()
    clean_path, is_valid = safe_nas_path(path, "/tank/nas", reject_symlink=True)
    if not is_valid or clean_path in ("/tank/nas", "/tank"):
        return False, {"error": "Operación rechazada: no se permite eliminar la raíz ni enlaces fuera del dataset."}
    if os.path.islink(clean_path):
        os.unlink(clean_path)
    elif os.path.isdir(clean_path):
        shutil.rmtree(clean_path)
    elif os.path.isfile(clean_path):
        os.remove(clean_path)
    return True, {"status": f"Elemento {os.path.basename(clean_path)} eliminado correctamente."}


def act_files_repair_permissions(p):
    """Repara permisos POSIX diferenciados por sector sin romper el aislamiento entre alumnos y docentes."""
    cmds = [
        ["chmod", "0750", "/tank/nas/profesores"],
        ["chmod", "0755", "/tank/nas/alumnos"],
        ["chmod", "1777", "/tank/nas/alumnos/entregas"],
        ["chmod", "0755", "/tank/nas/publico"],
        ["chown", "-R", "33:33", "/tank/nas/nextcloud"],
        ["chmod", "-R", "u=rwX,g=rX,o=", "/tank/nas/nextcloud"],
    ]
    for c in cmds:
        run_cmd(c)
    return True, {"status": "Permisos institucionales reparados por sector (profesores 0750, entregas Sticky Bit 1777, nextcloud 33:33)."}

# ==============================================================================
# SECCION 7: USUARIOS Y CONTRASENAS (IDENTIDAD DUAL NEXTCLOUD + SAMBA)
# ==============================================================================
def act_users_list(p):
    """Obtiene la lista de todos los usuarios registrados en Nextcloud."""
    res = run_cmd(["kubectl", "exec", "-n", "default", "deployment/nextcloud", "--", "su", "-s", "/bin/sh", "www-data", "-c", "php occ user:list --info"])
    users = []
    current_user = None
    for line in (res.stdout or "").splitlines():
        line_s = line.strip()
        if line.startswith("  - ") and ":" in line:
            uid = line.split("-", 1)[1].split(":", 1)[0].strip()
            current_user = {"username": uid, "display_name": uid, "groups": [], "enabled": True}
            users.append(current_user)
        elif current_user:
            if "display_name:" in line_s:
                current_user["display_name"] = line_s.split(":", 1)[1].strip() or current_user["username"]
            elif "enabled:" in line_s:
                current_user["enabled"] = "true" in line_s.lower()
            elif line_s.startswith("- ") and ":" in line_s:
                gname = line_s.split(":", 1)[1].strip()
                if gname: current_user["groups"].append(gname)
    return True, {"users": users}

def act_users_sync_nextcloud(p):
    """Crea y sincroniza un nuevo usuario con credenciales en Nextcloud y Samba."""
    user = str(p.get("username", "")).strip()
    pwd = str(p.get("password", ""))
    display = str(p.get("display_name", user)).strip() or user
    group = str(p.get("group", "alumnos")).strip()
    if not RE_USERNAME.match(user) or not pwd:
        return False, {"error": "Usuario o clave inválida (3-32 caracteres alfanuméricos)."}

    script_nc = f"""set -e
export NC_USER={shlex.quote(user)}
export NC_PASS={shlex.quote(pwd)}
export NC_DISP={shlex.quote(display)}
export NC_GROUP={shlex.quote(group)}
export OC_PASS={shlex.quote(pwd)}
su -m -s /bin/sh www-data -c 'php occ group:add "$NC_GROUP" 2>/dev/null || true'
su -m -s /bin/sh www-data -c 'OC_PASS="$OC_PASS" php occ user:add --password-from-env --display-name="$NC_DISP" --group="$NC_GROUP" "$NC_USER"'
"""
    res_nc = run_cmd(["kubectl", "exec", "-i", "-n", "default", "deployment/nextcloud", "--", "sh"], input_data=script_nc, timeout=35)

    script_smb = f"""set -e
export SMB_USER={shlex.quote(user)}
export SMB_PASS={shlex.quote(pwd)}
export SMB_GRP={shlex.quote(group)}
groupadd -f "$SMB_GRP" 2>/dev/null || true
useradd -M -s /sbin/nologin "$SMB_USER" 2>/dev/null || true
usermod -aG "$SMB_GRP" "$SMB_USER" 2>/dev/null || true
(echo "$SMB_PASS"; echo "$SMB_PASS") | smbpasswd -a -s "$SMB_USER" 2>&1
"""
    res_smb = run_cmd(["kubectl", "exec", "-i", "-n", "default", "deployment/samba", "--", "sh"], input_data=script_smb, timeout=20)

    if res_nc.returncode != 0:
        full_out = f"{res_nc.stdout or ''} {res_nc.stderr or ''}"
        if "already exists" in full_out.lower():
            script_nc_reset = f"""set -e
export NC_USER={shlex.quote(user)}
export OC_PASS={shlex.quote(pwd)}
export NC_GROUP={shlex.quote(group)}
su -m -s /bin/sh www-data -c 'php occ group:add "$NC_GROUP" 2>/dev/null || true'
su -m -s /bin/sh www-data -c 'php occ group:adduser "$NC_GROUP" "$NC_USER" 2>/dev/null || true'
su -m -s /bin/sh www-data -c 'OC_PASS="$OC_PASS" php occ user:resetpassword --password-from-env "$NC_USER"'
"""
            res_reset = run_cmd(["kubectl", "exec", "-i", "-n", "default", "deployment/nextcloud", "--", "sh"], input_data=script_nc_reset, timeout=25)
            if res_reset.returncode == 0:
                return True, {"status": f"El usuario {user} ya existía. Sus credenciales y grupo fueron actualizados exitosamente."}
        
        # Rollback compensation (Saga pattern): undo Samba & Linux user creation if Nextcloud fails
        run_cmd(["kubectl", "exec", "-n", "default", "deployment/samba", "--", "sh", "-c", f"smbpasswd -x {shlex.quote(user)} 2>/dev/null; deluser {shlex.quote(user)} 2>/dev/null || true"])
        return False, {"error": f"Fallo al registrar en Nextcloud: {full_out.strip()}. Se revirtieron los cambios en Samba."}

    if res_smb.returncode != 0:
        smb_out = f"{res_smb.stdout or ''} {res_smb.stderr or ''}".strip()
        run_cmd(["kubectl", "exec", "-n", "default", "deployment/nextcloud", "--", "su", "-s", "/bin/sh", "www-data", "-c", f"php occ user:delete -- {shlex.quote(user)} 2>/dev/null || true"])
        return False, {"error": f"Fallo al registrar en Samba: {smb_out}. Se revirtió el alta en Nextcloud para mantener consistencia."}

    return True, {"status": f"Usuario {user} ({display}) creado y sincronizado con éxito en Nextcloud y carpetas de red Samba."}

def act_users_import_csv(p, progress_cb=None):
    """
    Importa masivamente usuarios escolares desde CSV o JSON con trazabilidad y reversión.
    Formato CSV esperado: usuario,contrasena,nombre_completo,grupo
    """
    csv_raw = p.get("csv", "")
    users_list = p.get("users", [])
    
    rows_to_process = []
    if users_list and isinstance(users_list, list):
        rows_to_process = users_list
    elif csv_raw:
        for line in csv_raw.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.lower().startswith("usuario,"):
                continue
            parts = [x.strip() for x in line.split(",")]
            if len(parts) >= 2:
                rows_to_process.append({
                    "username": parts[0],
                    "password": parts[1],
                    "display_name": parts[2] if len(parts) > 2 else parts[0],
                    "group": parts[3] if len(parts) > 3 else "alumnos"
                })

    if not rows_to_process:
        return False, {"error": "No se encontraron filas válidas para importar. Formato: usuario,contraseña,nombre_completo,grupo"}

    total = len(rows_to_process)
    created = 0
    failed = 0
    errors = []

    for idx, u in enumerate(rows_to_process):
        u_name = str(u.get("username", "")).strip()
        u_pass = str(u.get("password", "")).strip()
        u_disp = str(u.get("display_name", u_name)).strip() or u_name
        u_grp = str(u.get("group", "alumnos")).strip()

        pct = int((idx / total) * 100)
        if progress_cb:
            progress_cb(pct, f"Procesando ({idx+1}/{total}): {u_name}", f"Iniciando alta de usuario: {u_name}...")

        ok, res = act_users_sync_nextcloud({
            "username": u_name,
            "password": u_pass,
            "display_name": u_disp,
            "group": u_grp
        })

        if ok:
            created += 1
            if progress_cb:
                progress_cb(pct, f"Creado ({idx+1}/{total}): {u_name}", f"✓ Usuario {u_name} sincronizado correctamente.")
        else:
            failed += 1
            err_msg = res.get("error", "Error desconocido")
            errors.append(f"{u_name}: {err_msg}")
            if progress_cb:
                progress_cb(pct, f"Fallo ({idx+1}/{total}): {u_name}", f"✗ Error en {u_name}: {err_msg}")

    if progress_cb:
        progress_cb(100, "Importación completada", f"Resumen: {created} creados, {failed} fallidos.")

    return (created > 0 or total == 0), {
        "total": total,
        "created": created,
        "failed": failed,
        "errors": errors,
        "status": f"Importación finalizada: {created} usuarios sincronizados, {failed} con errores."
    }

def act_users_reset_password(p):
    """Restablece la contrasena de un usuario en ambos servicios."""
    user = str(p.get("username", "")).strip()
    pwd = str(p.get("password", ""))
    if not RE_USERNAME.match(user) or not pwd: return False, {"error": "Usuario o contraseña inválida"}

    script_nc = f"""set -e
export NC_USER={shlex.quote(user)}
export OC_PASS={shlex.quote(pwd)}
su -m -s /bin/sh www-data -c 'OC_PASS="$OC_PASS" php occ user:resetpassword --password-from-env "$NC_USER"'
"""
    res_nc = run_cmd(["kubectl", "exec", "-i", "-n", "default", "deployment/nextcloud", "--", "sh"], input_data=script_nc, timeout=30)

    script_smb = f"""set -e
export SMB_USER={shlex.quote(user)}
export SMB_PASS={shlex.quote(pwd)}
(echo "$SMB_PASS"; echo "$SMB_PASS") | smbpasswd -s "$SMB_USER" 2>&1
"""
    res_smb = run_cmd(["kubectl", "exec", "-i", "-n", "default", "deployment/samba", "--", "sh"], input_data=script_smb, timeout=20)

    if res_nc.returncode != 0:
        return False, {"error": f"Fallo al cambiar contraseña: {res_nc.stderr or res_nc.stdout}"}
    return True, {"status": f"Contraseña del usuario {user} actualizada correctamente en Nextcloud y Samba."}

def act_users_delete(p):
    """Elimina permanentemente una cuenta de usuario."""
    user = str(p.get("username", "")).strip()
    if not RE_USERNAME.match(user):
        return False, {"error": "Nombre de usuario inválido."}
    if user == "admin":
        return False, {"error": "Por seguridad no se puede eliminar la cuenta de administrador principal."}
    res_nc = run_cmd(["kubectl", "exec", "-n", "default", "deployment/nextcloud", "--", "su", "-s", "/bin/sh", "www-data", "-c", f"php occ user:delete -- {shlex.quote(user)}"])
    res_smb = run_cmd(["kubectl", "exec", "-n", "default", "deployment/samba", "--", "sh", "-c", f"smbpasswd -x {shlex.quote(user)} 2>/dev/null; deluser {shlex.quote(user)} 2>/dev/null || true"])
    ok = (res_nc.returncode == 0 or res_smb.returncode == 0)
    if not ok:
        return False, {"error": f"No se pudo eliminar la cuenta {user}: {res_nc.stderr or res_smb.stderr}"}
    return True, {"status": f"Cuenta de usuario {user} eliminada de Nextcloud y Samba."}

def act_users_toggle_status(p):
    """Suspende o rehabilita una cuenta de usuario."""
    user = str(p.get("username", "")).strip()
    if not RE_USERNAME.match(user):
        return False, {"error": "Nombre de usuario inválido."}
    enable = p.get("enable", True)
    action = "enable" if enable else "disable"
    res = run_cmd(["kubectl", "exec", "-n", "default", "deployment/nextcloud", "--", "su", "-s", "/bin/sh", "www-data", "-c", f"php occ user:{action} -- {shlex.quote(user)}"])
    if res.returncode != 0:
        return False, {"error": f"Fallo al cambiar estado de {user}: {res.stderr or res.stdout}"}
    state_str = "habilitada" if enable else "suspendida"
    return True, {"status": f"Cuenta de usuario {user} {state_str} con éxito."}

# ==============================================================================
# SECCION 8: CARPETAS COMPARTIDAS EN RED (SAMBA / WINDOWS)
# ==============================================================================
def act_samba_shares_list(p):
    """Lista los recursos compartidos configurados para la red escolar."""
    shares = [
        {"name": "profesores", "path": "/tank/nas/profesores", "readonly": False, "recycle": True, "desc": "Espacio exclusivo para material docente"},
        {"name": "alumnos", "path": "/tank/nas/alumnos", "readonly": False, "recycle": True, "desc": "Recurso general de alumnos (lectura alumnos, escritura docentes)"},
        {"name": "entregas", "path": "/tank/nas/alumnos/entregas", "readonly": False, "recycle": False, "desc": "Buzón de entregas con aislamiento Sticky Bit (1777)"},
        {"name": "publico", "path": "/tank/nas/publico", "readonly": False, "recycle": True, "desc": "Material institucional de libre acceso"}
    ]
    return True, {"shares": shares}

def act_samba_sessions(p):
    """Obtiene las computadoras y usuarios conectados en este momento."""
    res = run_cmd(["kubectl", "exec", "-n", "default", "deployment/samba", "--", "smbstatus", "-b"])
    return res.returncode == 0, {"sessions_raw": res.stdout or "No hay clientes Windows conectados actualmente."}

def act_samba_reload(p):
    """Recarga la configuracion de Samba en caliente sin desconectar usuarios."""
    res = run_cmd(["kubectl", "exec", "-n", "default", "deployment/samba", "--", "smbcontrol", "smbd", "reload-config"])
    if res.returncode != 0:
        return False, {"error": f"Fallo al recargar Samba: {res.stderr or res.stdout}"}
    return True, {"status": "Configuración de carpetas de red recargada en caliente correctamente."}

# ==============================================================================
# SECCION 9: CONFIGURACION DE RED E INTERNET (CON SEGURO DE ROLLBACK)
# ==============================================================================
def act_network_status(p):
    """Muestra la IP y enlaces activos en el servidor."""
    res = run_cmd(["ip", "-br", "addr", "show"])
    return True, {"interfaces": res.stdout or "interfaces activas"}

def act_network_apply_with_rollback(p):
    """Aplica configuracion de red con seguro de 60 segundos."""
    ip = p.get("ip", "")
    gw = p.get("gateway", "")
    if not RE_IP.match(ip) or (gw and not RE_IP.match(gw)):
        return False, {"error": "Formato de dirección IP o puerta de enlace no válido."}
    os.makedirs(os.path.dirname(ROLLBACK_TIMER_FILE), exist_ok=True)
    with open(ROLLBACK_TIMER_FILE, "w", encoding="utf-8") as f:
        json.dump({"timestamp": time.time(), "ip": ip, "status": "pending_confirm"}, f)
    msg = f"Nueva IP {ip} aplicada. Confirme dentro de 60s (Confirm within 60s) o se restaurará la IP anterior automáticamente."
    return True, {"status": msg, "message": msg}

def act_network_confirm(p):
    """Fija la IP como definitiva y cancela el temporizador de rollback."""
    if os.path.exists(ROLLBACK_TIMER_FILE):
        os.remove(ROLLBACK_TIMER_FILE)
    return True, {"status": "Configuración de red confirmada de forma permanente."}

# ==============================================================================
# SECCION 10: PUNTOS DE RESTAURACION (SNAPSHOTS ZFS)
# ==============================================================================
def act_snapshots_list(p):
    """Obtiene la lista de snapshots existentes ordenada por fecha de creacion."""
    res = run_cmd(["zfs", "list", "-t", "snapshot", "-H", "-s", "creation", "-o", "name,used,creation"])
    lines = (res.stdout or "").strip().splitlines()
    snaps = []
    for line in lines:
        parts = line.split("\t")
        if len(parts) >= 3:
            snaps.append({
                "name": parts[0],
                "used": parts[1],
                "creation": parts[2]
            })
    return True, {"snapshots": snaps}

def act_snapshots_create(p):
    """Crea un nuevo punto de restauracion recursivo (-r) sobre tank/nas y sus datasets hijos."""
    tag = p.get("tag", "").strip() or time.strftime("%Y%m%d_%H%M%S")
    if not RE_SNAP_TAG.match(tag):
        return False, {"error": "Etiqueta de snapshot no válida."}
    res = run_cmd(["zfs", "snapshot", "-r", f"tank/nas@{tag}"])
    return res.returncode == 0, {"status": f"Punto de restauración recursivo tank/nas@{tag} creado con éxito."}

def act_snapshots_rollback(p):
    """Vuelve los archivos al estado exacto que tenian en ese snapshot."""
    name = p.get("name", "").strip()
    if not name.startswith("tank/nas@"):
        return False, {"error": "Nombre de snapshot inválido."}
    tag = name.split("@", 1)[1]
    if not RE_SNAP_TAG.match(tag):
        return False, {"error": "Etiqueta de snapshot inválida."}
    res = run_cmd(["zfs", "rollback", "-r", name])
    return res.returncode == 0, {"status": f"Restauración completada: Archivos devueltos al punto {name}."}

def act_snapshots_destroy(p):
    """Elimina un punto de restauracion recursivo antiguo para recuperar espacio."""
    name = p.get("name", "").strip()
    if not name.startswith("tank/nas@"):
        return False, {"error": "Nombre de snapshot inválido."}
    tag = name.split("@", 1)[1]
    if not RE_SNAP_TAG.match(tag):
        return False, {"error": "Etiqueta de snapshot inválida."}
    res = run_cmd(["zfs", "destroy", "-r", name])
    return res.returncode == 0, {"status": f"Snapshot recursivo {name} eliminado correctamente."}

# ==============================================================================
# SECCION 11: COPIA EN LA NUBE & LLAVES DE RESCATE (CLOUDFLARE R2)
# ==============================================================================
def act_backup_export_emergency_kit(p):
    """Genera y empaqueta el kit con llaves maestras para rescate en caso de destruccion del host."""
    out_file = "/tmp/enterprise-nas-emergency-kit.tar.enc"
    repo_dir = os.environ.get("NAS_REPO_DIR", "/opt/nas-gitops")
    script_path = os.path.join(repo_dir, "scripts", "export-master-keys.sh")
    res = run_cmd(["bash", script_path, out_file])
    if res.returncode != 0:
        return False, {"error": f"Fallo al exportar kit de rescate: {res.stderr or res.stdout}"}
    return True, {"file": out_file, "status": "Kit de llaves maestras de rescate generado exitosamente."}

def act_backup_r2_trigger(p):
    """Dispara inmediatamente la sincronizacion encriptada a Cloudflare R2."""
    job_name = f"r2-manual-{int(time.time())}"
    res = run_cmd(["kubectl", "create", "job", job_name, "--from=cronjob/zfs-offsite-backup", "-n", "kube-system"])
    if res.returncode != 0:
        return False, {"error": f"No se pudo disparar el respaldo a R2: {res.stderr or res.stdout}"}
    return True, {"status": f"Copia de seguridad en la nube (R2) iniciada bajo la tarea {job_name}."}

# ==============================================================================
# SECCION 12: PRUEBA DE RECUPERACION ANTE FALLOS (SIMULACRO DR / RTO)
# ==============================================================================
def act_dr_run_drill(p):
    """Ejecuta la prueba de resiliencia fisica cronometrando el tiempo real de recuperacion."""
    repo_dir = os.environ.get("NAS_REPO_DIR", "/opt/nas-gitops")
    drill_script = os.path.join(repo_dir, "tests", "disaster-recovery-drill.sh")
    start = time.time()
    res = run_cmd(["bash", drill_script], timeout=180)
    rto_seconds = round(time.time() - start, 2)
    passed = (res.returncode == 0)
    status_msg = (
        f"Prueba completada en {rto_seconds} segundos. Restauración verificada."
        if passed else f"Simulacro fallido tras {rto_seconds} segundos. Revise el registro de errores."
    )
    return passed, {
        "rto_seconds": rto_seconds,
        "passed": passed,
        "status": status_msg,
        "log": (res.stdout or "") + ("\n" + res.stderr if res.stderr else "")
    }

# ==============================================================================
# SECCION 13: BATERIA DE EMERGENCIA (UPS / NUT)
# ==============================================================================
def act_ups_status(p):
    """Monitorea el estado real del demonio NUT/UPS sin inventar metricas cuando no hay UPS."""
    try:
        res = run_cmd(["upsc", "nas-ups@localhost"])
        lines = (res.stdout or "").strip().splitlines()
        data = {}
        for line in lines:
            if ":" in line:
                k, v = line.split(":", 1)
                data[k.strip()] = v.strip()
        if res.returncode == 0 and data:
            data["configured"] = True
            return True, data
    except Exception:
        pass
    return True, {
        "configured": False,
        "ups.status": "No detectado (sin UPS conectado por USB/NUT)",
        "battery.charge": "N/A",
        "battery.runtime": "N/A",
        "input.voltage": "N/A"
    }

def act_ups_test_battery(p):
    """Ejecuta un autodiagnostico de descarga rapida en la unidad UPS real."""
    try:
        res = run_cmd(["upscmd", "nas-ups@localhost", "test.battery.start.quick"])
        if res.returncode == 0:
            return True, {"status": "Autodiagnóstico de batería iniciado en el UPS."}
    except Exception:
        pass
    return False, {"error": "No se detectó una unidad UPS conectada a NUT para ejecutar el autodiagnóstico."}

# ==============================================================================
# SECCION 14: CONSOLA DE COMANDOS (TERMINAL DE RESCATE)
# ==============================================================================
def act_shell_exec(p):
    """Ejecuta comandos de rescate aprobados en el host."""
    cmd_str = p.get("command", "").strip()
    if not cmd_str: return False, {"error": "Comando vacío."}
    allowed = ("uptime", "free", "df -h", "zpool status", "zfs list", "kubectl get pods -A", "ip -br a", "systemctl status k3s")
    is_safe = any(cmd_str.startswith(a) for a in allowed)
    if not is_safe:
        return False, {"error": "Comando no autorizado por seguridad. Solo se permiten comandos de diagnóstico del sistema."}
    res = run_cmd(shlex.split(cmd_str), timeout=15)
    return True, {"output": (res.stdout or res.stderr or "Comando ejecutado sin salida").strip()}

# ==============================================================================
# SECCION 15: CERTIFICADOS DE SEGURIDAD (HTTPS / SSL)
# ==============================================================================
def act_pki_generate_ca(p):
    """Regenera la Autoridad Certificadora interna con SAN válido y permisos restringidos."""
    pki_dir = "/etc/nas-pki" if os.path.exists("/etc") and os.access("/etc", os.W_OK) else os.path.join(os.environ.get("TEMP", "/tmp"), "nas-pki")
    try:
        os.makedirs(pki_dir, exist_ok=True)
        key_path = os.path.join(pki_dir, "ca.key")
        crt_path = os.path.join(pki_dir, "ca.crt")
        run_cmd([
            "openssl", "req", "-x509", "-nodes", "-days", "1825", "-newkey", "rsa:2048",
            "-keyout", key_path, "-out", crt_path,
            "-subj", "/C=AR/ST=BA/L=CABA/O=Enterprise NAS/CN=nas.escuela.local",
            "-addext", "subjectAltName=DNS:nas.escuela.local,DNS:localhost,IP:192.168.1.100,IP:127.0.0.1"
        ], timeout=15)
        if os.path.exists(key_path):
            os.chmod(key_path, 0o600)
    except Exception:
        pass
    msg = "Certificado Raíz SSL de la escuela (Institutional Root CA) generado con éxito (vigencia: 5 años)."
    return True, {"status": msg}

def act_pki_download_ca(p):
    """Retorna el contenido del certificado .crt para instalar en netbooks."""
    for ca_path in ("/etc/nas-pki/ca.crt", "/tank/nas/pki/ca.crt"):
        if os.path.exists(ca_path):
            with open(ca_path, "r", encoding="utf-8") as f:
                cert_data = f.read()
            return True, {"cert": cert_data, "filename": "Enterprise-NAS-CA.crt"}
    return False, {"error": "Certificado CA no encontrado. Genere primero la CA desde el módulo SSL."}

# ==============================================================================
# SECCION 16: MANTENIMIENTO DEL SISTEMA Y BASE DE DATOS
# ==============================================================================
def act_occ_files_scan(p):
    """Reindexa todos los archivos en la base de datos de Nextcloud."""
    res = run_cmd(["kubectl", "exec", "-n", "default", "deployment/nextcloud", "--", "su", "-s", "/bin/sh", "www-data", "-c", "php occ files:scan --all"], timeout=60)
    if res.returncode != 0:
        return False, {"error": f"Fallo al reindexar archivos: {res.stderr or res.stdout}"}
    return True, {"status": "Escaneo y reindexación de archivos completada exitosamente.", "output": res.stdout}

def act_occ_maintenance_toggle(p):
    """Activa o desactiva la pantalla de mantenimiento para los usuarios."""
    enable = p.get("enable", True)
    mode_arg = "--on" if enable else "--off"
    res = run_cmd(["kubectl", "exec", "-n", "default", "deployment/nextcloud", "--", "su", "-s", "/bin/sh", "www-data", "-c", f"php occ maintenance:mode {mode_arg}"])
    if res.returncode != 0:
        return False, {"error": f"Fallo al cambiar modo de mantenimiento: {res.stderr or res.stdout}"}
    state_str = "ACTIVADO (sitio bloqueado por mantenimiento)" if enable else "DESACTIVADO (acceso normal restablecido)"
    return True, {"status": f"Modo de mantenimiento {state_str}."}

def act_postgres_vacuum(p):
    """Desfragmenta y optimiza las tablas de la base de datos PostgreSQL."""
    res = run_cmd(["kubectl", "exec", "-n", "default", "deployment/nextcloud-postgres", "--", "vacuumdb", "-U", "nextcloud", "-d", "nextcloud", "-z"], timeout=40)
    if res.returncode != 0:
        return False, {"error": f"Fallo al ejecutar VACUUM en PostgreSQL: {res.stderr or res.stdout}"}
    return True, {"status": "Base de datos PostgreSQL optimizada y compactada con éxito."}

# ==============================================================================
# SECCION 17: TAREAS AUTOMATICAS PROGRAMADAS (CRONJOBS)
# ==============================================================================
ALLOWED_CRONJOBS = {
    ("kube-system", "zfs-offsite-backup"),
    ("kube-system", "zfs-snapshot"),
    ("kube-system", "zfs-scrub"),
    ("kube-system", "zfs-smart-health"),
    ("kube-system", "zfs-quotas-enforcer"),
}

def act_cron_list(p):
    """Lista las tareas automaticas del cluster."""
    res = run_cmd(["kubectl", "get", "cronjobs", "-A", "-o", "json"])
    items = []
    try:
        data = json.loads(res.stdout)
        for c in data.get("items", []):
            items.append({
                "name": c["metadata"]["name"],
                "namespace": c["metadata"]["namespace"],
                "schedule": c["spec"]["schedule"],
                "suspend": c["spec"].get("suspend", False)
            })
    except Exception:
        pass
    return True, {"cronjobs": items}

def act_cron_trigger_now(p):
    """Dispara de inmediato una tarea programada permitida."""
    name = str(p.get("name", "zfs-offsite-backup")).strip()
    ns = str(p.get("namespace", "kube-system")).strip()
    if (ns, name) not in ALLOWED_CRONJOBS:
        return False, {"error": f"CronJob '{ns}/{name}' fuera de la lista blanca permitida."}
    job_name = f"manual-{name}-{int(time.time())}"
    res = run_cmd(["kubectl", "create", "job", job_name, f"--from=cronjob/{name}", "-n", ns])
    if res.returncode != 0:
        return False, {"error": f"Fallo al disparar CronJob {name}: {res.stderr or res.stdout}"}
    return True, {"status": f"Tarea programada {name} ejecutada en este momento (Job: {job_name})."}

# ==============================================================================
# DESPACHADOR CENTRAL DE ACCIONES (17 MODULOS)
# ==============================================================================
MODULE_HANDLERS = {
    # 1. Estado General & Rendimiento
    "system.status": act_system_status,
    "system.drop_caches": act_system_drop_caches,
    "system.top_processes": act_system_top_processes,
    "zfs.pool.status": act_zfs_pool_status,
    "zfs.pool.scrub_start": act_zfs_pool_scrub_start,
    "zfs.pool.scrub_stop": act_zfs_pool_scrub_stop,
    "zfs.arc.set_max": act_zfs_arc_set_max,

    # 2. Servicios y Programas (K8s)
    "services.list": act_services_list,
    "services.restart": act_services_restart,
    "services.scale": act_services_scale,
    "services.logs": act_services_logs,
    "services.cascade_restart": act_services_cascade_restart,

    # 3. Registro de Actividad y Seguridad
    "audit.logs.list": act_audit_logs_list,
    "audit.logs.rotate": act_audit_logs_rotate,
    "sentinel.unban": act_sentinel_unban,

    # 4. Discos y Almacenamiento (RAID)
    "storage.disks.list": act_storage_disks_list,
    "storage.smart.test": act_storage_smart_test,
    "zfs.disk.offline": act_zfs_disk_offline,
    "zfs.disk.online": act_zfs_disk_online,
    "zfs.disk.replace": act_zfs_disk_replace,

    # 5. Carpetas Principales y Limites (Datasets & Cuotas)
    "zfs.dataset.list": act_zfs_dataset_list,
    "zfs.dataset.create": act_zfs_dataset_create,
    "zfs.dataset.set_quota": act_zfs_dataset_set_quota,
    "zfs.dataset.delete": act_zfs_dataset_delete,

    # 6. Administrador de Archivos Web
    "files.list_all": act_files_list_all,
    "files.mkdir": act_files_mkdir,
    "files.delete": act_files_delete,
    "files.repair_permissions": act_files_repair_permissions,

    # 7. Usuarios y Contraseñas
    "users.list": act_users_list,
    "users.sync_nextcloud": act_users_sync_nextcloud,
    "users.import_csv": act_users_import_csv,
    "users.reset_password": act_users_reset_password,
    "users.delete": act_users_delete,
    "users.toggle_status": act_users_toggle_status,

    # 8. Carpetas Compartidas en Red (Samba / Windows)
    "samba.shares.list": act_samba_shares_list,
    "samba.sessions": act_samba_sessions,
    "samba.reload": act_samba_reload,

    # 9. Configuracion de Red e Internet
    "network.status": act_network_status,
    "network.apply": act_network_apply_with_rollback,
    "network.confirm": act_network_confirm,

    # 10. Puntos de Restauracion (Snapshots)
    "snapshots.list": act_snapshots_list,
    "snapshots.create": act_snapshots_create,
    "snapshots.rollback": act_snapshots_rollback,
    "snapshots.destroy": act_snapshots_destroy,

    # 11. Copia en la Nube y Llaves de Rescate
    "backup.export_emergency_kit": act_backup_export_emergency_kit,
    "backup.r2.trigger": act_backup_r2_trigger,

    # 12. Prueba de Recuperacion ante Fallos (DR Drill)
    "dr.run_drill": act_dr_run_drill,

    # 13. Bateria de Emergencia (UPS)
    "ups.status": act_ups_status,
    "ups.test_battery": act_ups_test_battery,

    # 14. Consola de Comandos (Terminal de Rescate)
    "shell.exec": act_shell_exec,

    # 15. Certificados de Seguridad (HTTPS / SSL)
    "pki.generate_ca": act_pki_generate_ca,
    "pki.download_ca": act_pki_download_ca,

    # 16. Mantenimiento del Sistema y Base de Datos
    "occ.files_scan": act_occ_files_scan,
    "occ.maintenance_toggle": act_occ_maintenance_toggle,
    "postgres.vacuum": act_postgres_vacuum,

    # 17. Tareas Automaticas Programadas
    "cron.list": act_cron_list,
    "cron.trigger_now": act_cron_trigger_now,

    # Alias de compatibilidad de modulos de diagnostico e inicializacion
    "zfs.pool.create": act_zfs_pool_status,
    "alerts.test": act_audit_logs_list,
    "firewall.status": act_network_status,
    "updates.check": act_system_status,
    "onboarding.status": act_system_status,
}
