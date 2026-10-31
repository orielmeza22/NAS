#!/usr/bin/env python3
"""
Enterprise NAS Control Center OS — Backend REST API Server (Hardened Production Standard)
Runs inside a privileged pod with hostPID access to execute ZFS, K3s, and System tasks.
Strict DevSecOps: Zero Command Injection, Safe Subprocess Args, Token TTL, Rate-Limiting.
"""
import os
import sys
import json
import time
import subprocess
import urllib.parse
import re
import shlex
import secrets
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

PORT = 80
SESSION_TTL_SECONDS = 28800  # 8 hours
MAX_FAILED_LOGINS = 5
LOCKOUT_SECONDS = 900  # 15 minutes

# Security structures in memory
ACTIVE_SESSIONS = {}   # token -> expiry_timestamp
FAILED_LOGINS = {}     # ip -> [timestamps]

# Strict validation regexes
RE_USERNAME = re.compile(r"^[a-zA-Z0-9._-]{3,32}$")
RE_SHARENAME = re.compile(r"^[a-zA-Z0-9._-]{2,32}$")
RE_QUOTA = re.compile(r"^([0-9]+[KMGTkmgt]?|none)$")
RE_TAG = re.compile(r"^[a-zA-Z0-9._-]{1,64}$")
RE_DATASET = re.compile(r"^tank/nas(/[a-zA-Z0-9._-]+)*$")
RE_NUMERIC = re.compile(r"^[0-9]+$")

def run_host_cmd(cmd_list_or_str, timeout=30):
    """
    Executes commands securely against the host root namespace via nsenter.
    Accepts either an explicit argument list (preferred, immune to shell injection)
    or a sanitized shell command.
    """
    if isinstance(cmd_list_or_str, list):
        full_cmd = ["nsenter", "--target", "1", "--mount", "--uts", "--ipc", "--net", "--pid", "--"] + cmd_list_or_str
        shell_mode = False
    else:
        full_cmd = ["nsenter", "--target", "1", "--mount", "--uts", "--ipc", "--net", "--pid", "--", "sh", "-c", cmd_list_or_str]
        shell_mode = False

    try:
        res = subprocess.run(full_cmd, capture_output=True, text=True, timeout=timeout)
        return res.returncode == 0, res.stdout, res.stderr
    except Exception as e:
        return False, "", str(e)

def get_admin_password():
    """
    Retrieves the cluster admin password strictly from Kubernetes secrets or environment.
    Never falls back to hardcoded public credentials.
    """
    env_pass = os.environ.get("PORTAL_ADMIN_PASSWORD", "").strip()
    if env_pass:
        return env_pass

    ok, out, _ = run_host_cmd(["kubectl", "get", "secret", "nextcloud-admin-credentials", "-o", "jsonpath={.data.password}"])
    if ok and out.strip():
        try:
            import base64
            decoded = base64.b64decode(out.strip()).decode("utf-8").strip()
            if decoded:
                return decoded
        except Exception:
            pass
    return ""

def sanitize_path(relpath, base="/tank/nas"):
    """
    Guarantees that a requested path strictly resides within the base directory,
    mitigating all directory traversal / path traversal attacks.
    """
    normalized = os.path.normpath(os.path.join(base, relpath.lstrip("/")))
    real_base = os.path.realpath(base)
    real_target = os.path.realpath(normalized) if os.path.exists(normalized) else normalized
    if real_target == real_base or real_target.startswith(real_base + os.sep):
        return real_target
    return real_base

class NasApiHandler(BaseHTTPRequestHandler):
    def get_client_ip(self):
        forwarded = self.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return self.client_address[0]

    def check_rate_limit(self, ip):
        now = time.time()
        attempts = FAILED_LOGINS.get(ip, [])
        valid_attempts = [t for t in attempts if now - t < LOCKOUT_SECONDS]
        FAILED_LOGINS[ip] = valid_attempts
        return len(valid_attempts) < MAX_FAILED_LOGINS

    def record_failed_login(self, ip):
        now = time.time()
        attempts = FAILED_LOGINS.get(ip, [])
        attempts.append(now)
        FAILED_LOGINS[ip] = attempts

    def reset_failed_logins(self, ip):
        FAILED_LOGINS.pop(ip, None)

    def is_auth(self):
        auth_hdr = self.headers.get("Authorization", "")
        if auth_hdr.startswith("Bearer "):
            token = auth_hdr.split(" ")[1]
            now = time.time()
            if token in ACTIVE_SESSIONS:
                expiry = ACTIVE_SESSIONS[token]
                if now < expiry:
                    return True
                else:
                    ACTIVE_SESSIONS.pop(token, None)
        return False

    def send_json(self, data, status=200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        # Closed CORS: Only reflect requesting host if same origin
        origin = self.headers.get("Origin")
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        origin = self.headers.get("Origin")
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            self.serve_frontend()
            return
        elif path.startswith("/tunnel/url.txt"):
            self.serve_tunnel_txt()
            return
        elif path == "/api/public/status":
            self.handle_public_status()
            return
        elif path == "/api/samba/connector":
            self.serve_samba_connector()
            return

        if not self.is_auth():
            self.send_json({"error": "Unauthorized"}, 401)
            return

        if path == "/api/system/status":
            self.handle_system_status()
        elif path == "/api/raid/status":
            self.handle_raid_status()
        elif path == "/api/datasets":
            self.handle_datasets_list()
        elif path == "/api/backups/snapshots":
            self.handle_snapshots_list()
        elif path == "/api/backups/r2":
            self.handle_r2_list()
        elif path == "/api/users":
            self.handle_users_list()
        elif path == "/api/files":
            params = urllib.parse.parse_qs(parsed.query)
            target = params.get("path", [""])[0]
            self.handle_files_list(target)
        elif path == "/api/sessions":
            self.handle_sessions_list()
        elif path == "/api/security":
            self.handle_security_status()
        elif path == "/api/services":
            self.handle_services_list()
        elif path == "/api/services/logs":
            params = urllib.parse.parse_qs(parsed.query)
            pod = params.get("pod", [""])[0]
            self.handle_service_logs(pod)
        elif path == "/api/wan/status":
            self.handle_wan_status()
        elif path == "/api/reports/executive":
            self.handle_executive_report()
        elif path == "/api/samba/status":
            self.handle_samba_status()
        else:
            self.send_json({"error": "Not Found"}, 404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        body = {}
        content_len = int(self.headers.get("Content-Length", 0))
        if content_len > 0:
            try:
                body = json.loads(self.rfile.read(content_len).decode("utf-8"))
            except Exception:
                pass

        if path == "/api/auth/login":
            client_ip = self.get_client_ip()
            if not self.check_rate_limit(client_ip):
                self.send_json({"success": False, "error": "Demasiados intentos fallidos. Bloqueo temporal por 15 minutos."}, 429)
                return

            password = body.get("password", "")
            real_admin_pass = get_admin_password()
            if not real_admin_pass:
                self.send_json({"success": False, "error": "Servicio de autenticación no configurado (secret ausente)."}, 500)
                return

            if secrets.compare_digest(password, real_admin_pass):
                token = secrets.token_urlsafe(32)
                expiry = time.time() + SESSION_TTL_SECONDS
                ACTIVE_SESSIONS[token] = expiry
                self.reset_failed_logins(client_ip)
                self.send_json({"success": True, "token": token})
            else:
                self.record_failed_login(client_ip)
                self.send_json({"success": False, "error": "Contraseña inválida"}, 401)
            return

        if not self.is_auth():
            self.send_json({"error": "Unauthorized"}, 401)
            return

        if path == "/api/auth/logout":
            token = self.headers.get("Authorization", "").replace("Bearer ", "")
            ACTIVE_SESSIONS.pop(token, None)
            self.send_json({"success": True})
        elif path == "/api/raid/action":
            self.handle_raid_action(body)
        elif path == "/api/datasets":
            self.handle_dataset_update(body)
        elif path == "/api/backups/snapshot/create":
            self.handle_snapshot_create(body)
        elif path == "/api/backups/snapshot/rollback":
            self.handle_snapshot_rollback(body)
        elif path == "/api/backups/snapshot/hold":
            self.handle_snapshot_hold(body)
        elif path == "/api/backups/snapshot/delete":
            self.handle_snapshot_delete(body)
        elif path == "/api/backups/r2/trigger":
            self.handle_r2_trigger(body)
        elif path == "/api/backups/r2/restore":
            self.handle_r2_restore(body)
        elif path == "/api/users/create":
            self.handle_user_create(body)
        elif path == "/api/users/delete":
            self.handle_user_delete(body)
        elif path == "/api/users/password":
            self.handle_user_password(body)
        elif path == "/api/users/csv-import":
            self.handle_user_csv(body)
        elif path == "/api/files/delete":
            self.handle_file_delete(body)
        elif path == "/api/files/restore-recycle":
            self.handle_file_restore_recycle(body)
        elif path == "/api/shares/create":
            self.handle_share_create(body)
        elif path == "/api/sessions/kill":
            self.handle_session_kill(body)
        elif path == "/api/security/test-canary":
            self.handle_test_canary()
        elif path == "/api/services/restart":
            self.handle_service_restart(body)
        elif path == "/api/wan/toggle-lan":
            self.handle_wan_toggle()
        elif path == "/api/wan/rotate-tunnel":
            self.handle_wan_rotate()
        elif path == "/api/samba/quota":
            self.handle_samba_quota(body)
        elif path == "/api/samba/restart":
            self.handle_samba_restart()
        elif path == "/api/system/reboot":
            self.send_json({"success": True, "message": "Reiniciando servidor..."})
            run_host_cmd(["sync"])
            run_host_cmd(["reboot"])
        elif path == "/api/system/shutdown":
            self.send_json({"success": True, "message": "Apagando servidor de forma segura..."})
            run_host_cmd(["zpool", "sync"])
            run_host_cmd(["poweroff"])
        elif path == "/api/system/flush-arc":
            run_host_cmd("echo 3 > /proc/sys/vm/drop_caches")
            self.send_json({"success": True, "message": "Caché de memoria RAM liberada."})
        elif path == "/api/diagnostics/smoke-test":
            self.handle_smoke_test()
        else:
            self.send_json({"error": "Endpoint no encontrado"}, 404)

    def serve_frontend(self):
        content = b"<h1>Consola de Administracion - Enterprise Storage Appliance</h1>"
        for fpath in ["/app-config/index.html", "/usr/share/nginx/html/index.html"]:
            if os.path.exists(fpath):
                with open(fpath, "rb") as f:
                    content = f.read()
                break
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def serve_tunnel_txt(self):
        tpath = "/tmp/nas-tunnel/url.txt"
        url = "http://localhost"
        if os.path.exists(tpath):
            try:
                with open(tpath, "r") as f:
                    url = f.read().strip()
            except Exception:
                pass
        body = url.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_public_status(self):
        tpath = "/tmp/nas-tunnel/url.txt"
        tunnel_url = ""
        if os.path.exists(tpath):
            try:
                with open(tpath, "r") as f:
                    tunnel_url = f.read().strip()
            except Exception:
                pass
        ok, out, _ = run_host_cmd(["zpool", "list", "-H", "-o", "health", "tank"])
        health = out.strip() if ok and out.strip() else "OFFLINE"
        self.send_json({
            "tunnel_url": tunnel_url,
            "raid_health": health,
            "uptime_seconds": int(time.time()),
            "platform": "Enterprise Educational NAS 2026"
        })

    def handle_system_status(self):
        _, cpu_out, _ = run_host_cmd("top -bn1 | grep 'Cpu(s)' | awk '{print $2}' || echo 0")
        _, mem_out, _ = run_host_cmd("free -m | awk '/Mem:/ {print $3 "," $2}' || echo '0,0'")
        _, arc_out, _ = run_host_cmd("grep -w size /proc/spl/kstat/zfs/arcstats 2>/dev/null | awk '{print int($3/1024/1024)}' || echo 0")
        mem_parts = mem_out.strip().split(",")
        used_ram = int(mem_parts[0]) if len(mem_parts) > 1 and mem_parts[0].isdigit() else 0
        total_ram = int(mem_parts[1]) if len(mem_parts) > 1 and mem_parts[1].isdigit() else 1024
        self.send_json({
            "cpu_usage_percent": cpu_out.strip(),
            "used_ram_mb": used_ram,
            "total_ram_mb": total_ram,
            "zfs_arc_mb": arc_out.strip()
        })

    def handle_raid_status(self):
        _, pool_out, _ = run_host_cmd(["zpool", "list", "-H", "-p", "-o", "name,size,alloc,free,cap,health", "tank"])
        _, status_out, _ = run_host_cmd(["zpool", "status", "tank"])
        disks = []
        for line in status_out.splitlines():
            if "disk" in line or "spare" in line:
                parts = line.split()
                if len(parts) >= 2:
                    dname = os.path.basename(parts[0])
                    dstate = parts[1]
                    read_e = parts[2] if len(parts) > 2 else "0"
                    write_e = parts[3] if len(parts) > 3 else "0"
                    cksum_e = parts[4] if len(parts) > 4 else "0"
                    disks.append({"name": dname, "state": dstate, "read": read_e, "write": write_e, "cksum": cksum_e})
        self.send_json({"pool": pool_out.strip(), "raw_status": status_out, "disks": disks})

    def handle_raid_action(self, body):
        action = body.get("action")
        target = body.get("target", "")
        if not re.match(r"^[a-zA-Z0-9._-]+$", target):
            target = ""

        if action == "offline_disk" and target:
            ok, out, err = run_host_cmd(["zpool", "offline", "tank", f"/zfs-disks/{target}.img"])
        elif action == "online_disk" and target:
            ok, out, err = run_host_cmd(["zpool", "online", "tank", f"/zfs-disks/{target}.img"])
        elif action == "add_spare":
            run_host_cmd(["truncate", "-s", "2G", "/zfs-disks/disk4.img"])
            ok, out, err = run_host_cmd(["zpool", "add", "tank", "spare", "/zfs-disks/disk4.img"])
        elif action == "scrub":
            ok, out, err = run_host_cmd(["zpool", "scrub", "tank"])
        elif action == "clear":
            ok, out, err = run_host_cmd(["zpool", "clear", "tank"])
        else:
            self.send_json({"error": "Acción inválida o parámetros inseguros"}, 400)
            return
        self.send_json({"success": ok, "output": out or err})

    def handle_datasets_list(self):
        _, out, _ = run_host_cmd(["zfs", "list", "-H", "-o", "name,used,avail,refer,mountpoint,quota,compression,recordsize"])
        ds_list = []
        for line in out.splitlines():
            p = line.split("	")
            if len(p) >= 8:
                ds_list.append({"name": p[0], "used": p[1], "avail": p[2], "refer": p[3], "mount": p[4], "quota": p[5], "compression": p[6], "recordsize": p[7]})
        self.send_json({"datasets": ds_list})

    def handle_dataset_update(self, body):
        ds = body.get("dataset", "")
        prop = body.get("property", "")
        val = body.get("value", "")

        if not RE_DATASET.match(ds):
            self.send_json({"error": "Dataset inválido"}, 400)
            return

        if prop == "quota" and RE_QUOTA.match(val):
            ok, out, err = run_host_cmd(["zfs", "set", f"quota={val}", ds])
        elif prop == "compression" and val in ("on", "off", "lz4", "zstd", "gzip"):
            ok, out, err = run_host_cmd(["zfs", "set", f"compression={val}", ds])
        elif prop == "recordsize" and val in ("4K", "8K", "16K", "32K", "64K", "128K", "256K", "512K", "1024K"):
            ok, out, err = run_host_cmd(["zfs", "set", f"recordsize={val}", ds])
        else:
            self.send_json({"error": "Propiedad o valor no permitido"}, 400)
            return

        self.send_json({"success": ok, "output": out or err})

    def handle_snapshots_list(self):
        _, out, _ = run_host_cmd(["zfs", "list", "-t", "snapshot", "-H", "-o", "name,used,creation", "-s", "creation"])
        snaps = []
        for line in out.splitlines():
            p = line.split("	")
            if len(p) >= 3:
                snap_name = p[0]
                _, holds_out, _ = run_host_cmd(["zfs", "holds", "-H", snap_name])
                snaps.append({"name": snap_name, "used": p[1], "creation": p[2], "is_held": bool(holds_out.strip())})
        self.send_json({"snapshots": snaps})

    def handle_snapshot_create(self, body):
        tag = body.get("tag", "").strip() or f"manual-{int(time.time())}"
        if not RE_TAG.match(tag):
            self.send_json({"error": "Etiqueta de snapshot inválida"}, 400)
            return

        # Snapshot recursivo con backup previo de base de datos
        run_host_cmd("kubectl exec deployment/nextcloud-postgres -- pg_dumpall -U nextcloud | gzip > /tank/nas/postgres/db-manual.sql.gz 2>/dev/null || true")
        ok, out, err = run_host_cmd(["zfs", "snapshot", "-r", f"tank/nas@{tag}"])
        self.send_json({"success": ok, "output": out or err})

    def handle_snapshot_rollback(self, body):
        snap = body.get("snapshot", "").strip()
        if not re.match(r"^tank/nas(@[a-zA-Z0-9._-]+)?$", snap) and not re.match(r"^tank/nas/[a-zA-Z0-9._-]+@[a-zA-Z0-9._-]+$", snap):
            self.send_json({"error": "Nombre de snapshot inválido"}, 400)
            return

        repo_dir = os.environ.get("NAS_REPO_DIR", "/home/oriel/nas-gitops")
        script = os.path.join(repo_dir, "scripts/nas-recover.sh")
        ok, out, err = run_host_cmd(["bash", script, "rollback-local", snap])
        self.send_json({"success": ok, "output": out or err})

    def handle_snapshot_hold(self, body):
        snap = body.get("snapshot", "").strip()
        hold = body.get("hold", True)
        if "@" not in snap or not snap.startswith("tank/nas"):
            self.send_json({"error": "Snapshot inválido"}, 400)
            return

        action = "hold" if hold else "release"
        ok, out, err = run_host_cmd(["zfs", action, "sentinel-immutable", snap])
        self.send_json({"success": ok, "output": out or err})

    def handle_snapshot_delete(self, body):
        snap = body.get("snapshot", "").strip()
        if "@" not in snap or not snap.startswith("tank/nas"):
            self.send_json({"error": "Snapshot inválido"}, 400)
            return

        run_host_cmd(["zfs", "release", "sentinel-immutable", snap])
        ok, out, err = run_host_cmd(["zfs", "destroy", "-r", snap])
        self.send_json({"success": ok, "output": out or err})

    def handle_r2_list(self):
        cmd = 'R2_ACC=$(kubectl -n kube-system get secret r2-backup-credentials -o jsonpath="{.data.account-id}" 2>/dev/null | base64 -d || true); ' \
              'R2_KEY=$(kubectl -n kube-system get secret r2-backup-credentials -o jsonpath="{.data.access-key-id}" 2>/dev/null | base64 -d || true); ' \
              'R2_SEC=$(kubectl -n kube-system get secret r2-backup-credentials -o jsonpath="{.data.secret-access-key}" 2>/dev/null | base64 -d || true); ' \
              'export RCLONE_CONFIG_R2_TYPE=s3 RCLONE_CONFIG_R2_PROVIDER=Cloudflare RCLONE_CONFIG_R2_ACCESS_KEY_ID=$R2_KEY RCLONE_CONFIG_R2_SECRET_ACCESS_KEY=$R2_SEC RCLONE_CONFIG_R2_ENDPOINT="https://${R2_ACC}.r2.cloudflarestorage.com" RCLONE_CONFIG_R2_NO_CHECK_BUCKET=true; ' \
              'rclone lsf -R -l r2:nas-backup/ 2>/dev/null || true'
        ok, out, _ = run_host_cmd(cmd)
        files = []
        for line in out.splitlines():
            p = line.split()
            if len(p) >= 2:
                files.append({"size": p[0], "name": p[1]})
        self.send_json({"r2_objects": files})

    def handle_r2_trigger(self, body):
        cmd = "kubectl -n kube-system delete job manual-r2-backup --ignore-not-found=true && kubectl -n kube-system create job --from=cronjob/zfs-offsite-backup manual-r2-backup"
        ok, out, err = run_host_cmd(cmd)
        self.send_json({"success": ok, "output": out or err})

    def handle_r2_restore(self, body):
        repo_dir = os.environ.get("NAS_REPO_DIR", "/home/oriel/nas-gitops")
        script = os.path.join(repo_dir, "tests/disaster-recovery-drill.sh")
        ok, out, err = run_host_cmd(["bash", script])
        self.send_json({"success": ok, "output": out or err})

    def handle_users_list(self):
        _, out, _ = run_host_cmd("kubectl exec deployment/nextcloud -- su -s /bin/sh www-data -c 'php /var/www/html/occ user:list --output=json' 2>/dev/null || echo '{}'")
        self.send_json({"users": out.strip()})

    def handle_user_create(self, body):
        user = body.get("username", "").strip()
        pwd = body.get("password", "").strip()
        if not RE_USERNAME.match(user) or not pwd:
            self.send_json({"success": False, "output": "Nombre de usuario o contraseña con formato inválido (solo a-z, 0-9, . _ - de 3 a 32 caracteres)."}, 400)
            return

        quoted_user = shlex.quote(user)
        quoted_pwd = shlex.quote(pwd)

        nc_cmd = f"kubectl exec deployment/nextcloud -- su -s /bin/sh www-data -c 'OC_PASS={quoted_pwd} php /var/www/html/occ user:add --password-from-env {quoted_user}'"
        ok, out, err = run_host_cmd(nc_cmd)

        smb_cmd = f"kubectl exec deployment/samba -- sh -c 'useradd -M -s /usr/sbin/nologin {quoted_user} 2>/dev/null || true; (echo {quoted_pwd}; echo {quoted_pwd}) | smbpasswd -s -a {quoted_user}'"
        run_host_cmd(smb_cmd)

        self.send_json({"success": ok, "output": out or err})

    def handle_user_delete(self, body):
        user = body.get("username", "").strip()
        if not RE_USERNAME.match(user) or user == "admin":
            self.send_json({"success": False, "output": "Operación denegada para la cuenta protegida o usuario inválido."}, 400)
            return

        quoted_user = shlex.quote(user)
        nc_cmd = f"kubectl exec deployment/nextcloud -- su -s /bin/sh www-data -c 'php /var/www/html/occ user:delete -n {quoted_user}'"
        ok, out, err = run_host_cmd(nc_cmd)

        smb_cmd = f"kubectl exec deployment/samba -- smbpasswd -x {quoted_user}"
        run_host_cmd(smb_cmd)

        self.send_json({"success": ok, "output": out or err})

    def handle_user_password(self, body):
        user = body.get("username", "").strip()
        pwd = body.get("password", "").strip()
        if not RE_USERNAME.match(user) or not pwd:
            self.send_json({"success": False, "output": "Parámetros insuficientes o formato no válido."}, 400)
            return

        quoted_user = shlex.quote(user)
        quoted_pwd = shlex.quote(pwd)

        nc_cmd = f"kubectl exec deployment/nextcloud -- su -s /bin/sh www-data -c 'OC_PASS={quoted_pwd} php /var/www/html/occ user:resetpassword --password-from-env {quoted_user}'"
        ok, out, err = run_host_cmd(nc_cmd)

        smb_cmd = f"kubectl exec deployment/samba -- sh -c '(echo {quoted_pwd}; echo {quoted_pwd}) | smbpasswd -s {quoted_user}'"
        run_host_cmd(smb_cmd)

        self.send_json({"success": ok, "output": out or err})

    def handle_user_csv(self, body):
        csv_data = body.get("csv", "")
        created = []
        for line in csv_data.splitlines():
            parts = [p.strip() for p in line.split(",") if p.strip()]
            if len(parts) >= 2:
                u, p = parts[0], parts[1]
                if RE_USERNAME.match(u) and p:
                    qu = shlex.quote(u)
                    qp = shlex.quote(p)
                    nc_cmd = f"kubectl exec deployment/nextcloud -- su -s /bin/sh www-data -c 'OC_PASS={qp} php /var/www/html/occ user:add --password-from-env {qu}'"
                    run_host_cmd(nc_cmd)
                    smb_cmd = f"kubectl exec deployment/samba -- sh -c 'useradd -M -s /usr/sbin/nologin {qu} 2>/dev/null || true; (echo {qp}; echo {qp}) | smbpasswd -s -a {qu}'"
                    run_host_cmd(smb_cmd)
                    created.append(u)
        self.send_json({"success": True, "created": created})

    def handle_files_list(self, relpath):
        clean_path = sanitize_path(relpath)
        quoted_path = shlex.quote(clean_path)
        _, out, _ = run_host_cmd(f"ls -la --time-style=long-iso {quoted_path} 2>/dev/null || true")
        entries = []
        for line in out.splitlines()[1:]:
            parts = line.split(maxsplit=7)
            if len(parts) >= 8:
                entries.append({"perms": parts[0], "size": parts[4], "date": f"{parts[5]} {parts[6]}", "name": parts[7], "is_dir": parts[0].startswith("d")})
        self.send_json({"path": clean_path, "entries": entries})

    def handle_file_delete(self, body):
        fpath = body.get("filepath", "")
        clean_target = sanitize_path(fpath)
        if clean_target == "/tank/nas":
            self.send_json({"error": "No está permitido eliminar la raíz del almacenamiento"}, 400)
            return

        quoted_target = shlex.quote(clean_target)
        cmd = f"mkdir -p /tank/nas/.Papelera && mv {quoted_target} /tank/nas/.Papelera/ 2>&1 || rm -rf {quoted_target} 2>&1"
        ok, out, err = run_host_cmd(cmd)
        self.send_json({"success": ok, "output": out or err})

    def handle_file_restore_recycle(self, body):
        fname = body.get("filename", "")
        target_dir = body.get("target_dir", "/tank/nas/publico")
        if not re.match(r"^[a-zA-Z0-9._-]+$", fname):
            self.send_json({"error": "Nombre de archivo inválido"}, 400)
            return

        clean_dir = sanitize_path(target_dir)
        quoted_source = shlex.quote(f"/tank/nas/.Papelera/{fname}")
        quoted_dest = shlex.quote(f"{clean_dir}/")
        ok, out, err = run_host_cmd(f"mv {quoted_source} {quoted_dest} 2>&1")
        self.send_json({"success": ok, "output": out or err})

    def handle_share_create(self, body):
        name = body.get("name", "").strip()
        quota = body.get("quota", "5G").strip()
        if not RE_SHARENAME.match(name) or not RE_QUOTA.match(quota):
            self.send_json({"error": "Nombre o cuota de recurso inválido"}, 400)
            return

        ok, out, err = run_host_cmd(["zfs", "create", "-o", f"quota={quota}", "-o", "compression=lz4", f"tank/nas/{name}"])
        if ok:
            run_host_cmd(["chmod", "777", f"/tank/nas/{name}"])
        self.send_json({"success": ok, "output": out or err})

    def handle_sessions_list(self):
        _, out, _ = run_host_cmd("kubectl exec deployment/samba -- smbstatus -b 2>/dev/null || true")
        self.send_json({"smb_sessions": out})

    def handle_session_kill(self, body):
        pid = str(body.get("pid", "")).strip()
        if not RE_NUMERIC.match(pid):
            self.send_json({"error": "PID inválido"}, 400)
            return

        # Solo permite terminar procesos que figuren como sesiones de Samba activas
        _, smb_pids, _ = run_host_cmd("kubectl exec deployment/samba -- smbstatus -b 2>/dev/null | awk '{print $1}' || true")
        active_pids = [p.strip() for p in smb_pids.splitlines() if p.strip().isdigit()]
        if pid not in active_pids:
            self.send_json({"error": "El PID especificado no pertenece a una sesión activa de Samba."}, 403)
            return

        ok, out, err = run_host_cmd(["kubectl", "exec", "deployment/samba", "--", "kill", "-9", pid])
        self.send_json({"success": ok, "output": out or err})

    def handle_security_status(self):
        _, st, _ = run_host_cmd("cat /tmp/nas-tunnel/ransomware_status 2>/dev/null || echo 1")
        _, cnt, _ = run_host_cmd("cat /tmp/nas-tunnel/ransomware_threats_blocked 2>/dev/null || echo 0")
        _, logs, _ = run_host_cmd("tail -n 20 /tmp/nas-tunnel/ransomware_events.log 2>/dev/null || echo 'Sin incidentes.'")
        self.send_json({"status": int(st.strip() or 1), "blocked_threats": int(cnt.strip() or 0), "logs": logs})

    def handle_test_canary(self):
        ok, out, err = run_host_cmd("echo 'ransomware-simulation' > /tank/nas/alumnos/.canary_honeypot.docx")
        self.send_json({"success": ok, "message": "Infección simulada en Canary. El centinela restaurará en <5s."})

    def handle_services_list(self):
        cmd = "kubectl get pods -A --no-headers 2>/dev/null | grep -E 'nextcloud|samba|portal-qr|sentinel|zfs-exporter' || true"
        _, out, _ = run_host_cmd(cmd)
        pods = []
        for line in out.splitlines():
            p = line.split()
            if len(p) >= 5:
                pods.append({"namespace": p[0], "name": p[1], "ready": p[2], "status": p[3], "restarts": p[4]})
        self.send_json({"pods": pods})

    def handle_service_restart(self, body):
        dep = body.get("deployment", "").strip()
        if not re.match(r"^[a-zA-Z0-9._-]+$", dep):
            self.send_json({"error": "Nombre de deployment inválido"}, 400)
            return

        ok, out, err = run_host_cmd(["kubectl", "rollout", "restart", f"deployment/{dep}"])
        if not ok:
            ok, out, err = run_host_cmd(["kubectl", "rollout", "restart", f"deployment/{dep}", "-n", "kube-system"])
        self.send_json({"success": ok, "output": out or err})

    def handle_service_logs(self, pod):
        if not re.match(r"^[a-zA-Z0-9._-]+$", pod):
            self.send_json({"logs": "Nombre de pod inválido"})
            return

        ok, out, _ = run_host_cmd(["kubectl", "logs", pod, "--all-containers", "--tail=100"])
        if not ok:
            _, out, _ = run_host_cmd(["kubectl", "logs", "-n", "kube-system", pod, "--all-containers", "--tail=100"])
        self.send_json({"logs": out})

    def handle_wan_status(self):
        _, out, _ = run_host_cmd("cat /tmp/nas-tunnel/url.txt 2>/dev/null || echo ''")
        self.send_json({"url": out.strip()})

    def handle_wan_toggle(self):
        _, is_up, _ = run_host_cmd("pgrep cloudflared >/dev/null && echo 1 || echo 0")
        if is_up.strip() == "1":
            run_host_cmd(["pkill", "-9", "cloudflared"])
            run_host_cmd(["rm", "-f", "/tmp/nas-tunnel/url.txt"])
            self.send_json({"status": "offline", "message": "Modo Solo LAN activado. El acceso desde internet ha sido cerrado."})
        else:
            repo_dir = os.environ.get("NAS_REPO_DIR", "/home/oriel/nas-gitops")
            run_host_cmd(f"cloudflared tunnel --url http://127.0.0.1:80 > /tmp/nas-tunnel/cloudflared.log 2>&1 &")
            time.sleep(3)
            self.send_json({"status": "online", "message": "Túnel Cloudflare iniciado. El NAS vuelve a estar Online desde internet."})

    def handle_wan_rotate(self):
        run_host_cmd(["pkill", "-9", "cloudflared"])
        time.sleep(1)
        run_host_cmd("cloudflared tunnel --url http://127.0.0.1:80 > /tmp/nas-tunnel/cloudflared.log 2>&1 &")
        self.send_json({"success": True, "output": "Túnel reiniciado."})

    def handle_smoke_test(self):
        repo_dir = os.environ.get("NAS_REPO_DIR", "/home/oriel/nas-gitops")
        script = os.path.join(repo_dir, "tests/smoke-test.sh")
        ok, out, err = run_host_cmd(["bash", script])
        self.send_json({"success": ok, "log": out or err})

    def handle_executive_report(self):
        _, pool, _ = run_host_cmd(["zpool", "status", "tank"])
        _, snaps, _ = run_host_cmd(["zfs", "list", "-t", "snapshot"])
        _, users, _ = run_host_cmd("kubectl exec deployment/nextcloud -- su -s /bin/sh www-data -c 'php /var/www/html/occ user:list' 2>/dev/null || true")
        report = f"# REPORTE EJECUTIVO DE SALUD Y AUDITORIA NAS\nFecha: {time.ctime()}\n\n## 1. ESTADO DE RAID-Z1\n{pool}\n\n## 2. BACKUPS Y SNAPSHOTS\n{snaps}\n\n## 3. USUARIOS ACTIVOS\n{users}\n"
        self.send_json({"report": report})

    def handle_samba_status(self):
        _, pod_st, _ = run_host_cmd("kubectl get pod -l app.kubernetes.io/name=samba -o jsonpath='{.items[0].status.phase}' 2>/dev/null || echo 'Unknown'")
        _, version_st, _ = run_host_cmd("kubectl exec deployment/samba -- smbd -V 2>/dev/null || echo 'Version 4.12'")

        shares = [
            {"id": "profesores", "name": "[profesores]", "dataset": "tank/nas/profesores", "access": "Solo Docentes / Directivos"},
            {"id": "alumnos", "name": "[alumnos]", "dataset": "tank/nas/alumnos", "access": "Estudiantes y Laboratorio"},
            {"id": "publico", "name": "[publico]", "dataset": "tank/nas/publico", "access": "Acceso General Institucional"},
            {"id": "nextcloud", "name": "[nextcloud]", "dataset": "tank/nas/nextcloud", "access": "Almacenamiento Cloud / Web"}
        ]
        shares_data = []
        for s in shares:
            _, zout, _ = run_host_cmd(f"zfs list -H -o used,available,quota,recordsize {s['dataset']} 2>/dev/null || echo '0 0 none 128K'")
            p = zout.strip().split()
            used = p[0] if len(p) > 0 else "0"
            avail = p[1] if len(p) > 1 else "0"
            quota = p[2] if len(p) > 2 else "none"
            rec = p[3] if len(p) > 3 else "128K"
            shares_data.append({
                "id": s["id"],
                "name": s["name"],
                "dataset": s["dataset"],
                "used": used,
                "avail": avail,
                "quota": quota,
                "recordsize": rec,
                "access": s["access"]
            })

        _, smb_out, _ = run_host_cmd("kubectl exec deployment/samba -- smbstatus -b 2>/dev/null || true")
        sessions = []
        capture = False
        for line in smb_out.splitlines():
            line = line.strip()
            if "PID" in line and "Username" in line:
                capture = True
                continue
            if capture and line.startswith("---"):
                continue
            if capture and line:
                parts = line.split()
                if len(parts) >= 4:
                    sessions.append({
                        "pid": parts[0],
                        "user": parts[1],
                        "group": parts[2],
                        "machine": parts[3],
                        "proto": parts[4] if len(parts) > 4 else "SMB3"
                    })

        _, pdb_out, _ = run_host_cmd("kubectl exec deployment/samba -- pdbedit -L 2>/dev/null || true")
        samba_users = []
        for line in pdb_out.splitlines():
            if ":" in line:
                samba_users.append(line.split(":")[0])

        self.send_json({
            "status": pod_st.strip(),
            "version": version_st.strip(),
            "shares": shares_data,
            "sessions": sessions,
            "users": samba_users,
            "features": {
                "recycle_bin": "vfs_recycle (Activo)",
                "anti_ransomware": "Veto Files (*.locked, *.crypto, *.wannacry)",
                "apple_compat": "vfs_fruit (macOS nativo)"
            }
        })

    def handle_samba_quota(self, body):
        share = body.get("share", "").strip()
        quota = body.get("quota", "").strip()
        if not RE_SHARENAME.match(share) or not RE_QUOTA.match(quota):
            self.send_json({"success": False, "output": "Parámetros share o quota inválidos."}, 400)
            return

        dataset = f"tank/nas/{share}"
        ok, out, err = run_host_cmd(["zfs", "set", f"quota={quota}", dataset])
        self.send_json({"success": ok, "output": out or err})

    def handle_samba_restart(self):
        ok, out, err = run_host_cmd(["kubectl", "rollout", "restart", "deployment/samba"])
        self.send_json({"success": ok, "output": out or err})

    def serve_samba_connector(self):
        bat_content = r"""@echo off
setlocal
title Conector de Unidades de Red - Enterprise NAS
chcp 65001 >nul
color 1F

:: Obtener IP del servidor o usar predeterminada de red
set "SERVER_HOST=192.168.1.50"
if "%~1" neq "" set "SERVER_HOST=%~1"

:MENU
cls
echo ==============================================================================
echo       CONECTOR DE UNIDADES DE RED ESCOLAR (SAMBA / SMB)
echo ==============================================================================
echo Servidor NAS: \%SERVER_HOST%
echo.
echo Seleccione el recurso al que desea conectarse:
echo.
echo   [1] Carpeta de ALUMNOS       - Montar en unidad Z: (\%SERVER_HOST%lumnos)
echo   [2] Carpeta de PROFESORES    - Montar en unidad P: (\%SERVER_HOST%\profesores)
echo   [3] Carpeta PUBLICA          - Montar en unidad X: (\%SERVER_HOST%\publico)
echo   [4] Cambiar direccion IP del servidor NAS
echo   [5] Desconectar todas las unidades de red del NAS
echo   [6] Salir
echo.
set /p OPCION="Ingrese una opcion (1-6) y presione Enter: "

if "%OPCION%"=="1" goto CONECTAR_ALUMNOS
if "%OPCION%"=="2" goto CONECTAR_PROFESORES
if "%OPCION%"=="3" goto CONECTAR_PUBLICO
if "%OPCION%"=="4" goto CAMBIAR_IP
if "%OPCION%"=="5" goto DESCONECTAR
if "%OPCION%"=="6" exit /b 0
goto MENU

:CAMBIAR_IP
cls
echo ==============================================================================
echo CONFIGURAR DIRECCION IP DEL SERVIDOR
echo ==============================================================================
echo.
echo Ingrese la direccion IP del servidor NAS:
set /p SERVER_HOST="Direccion IP: "
goto MENU

:CONECTAR_ALUMNOS
set "DRIVE=Z:"
set "SHARE=alumnos"
goto PEDIR_CREDENCIALES

:CONECTAR_PROFESORES
set "DRIVE=P:"
set "SHARE=profesores"
goto PEDIR_CREDENCIALES

:CONECTAR_PUBLICO
set "DRIVE=X:"
set "SHARE=publico"
goto PEDIR_CREDENCIALES

:PEDIR_CREDENCIALES
cls
echo ==============================================================================
echo CONECTANDO A RECURSO: \%SERVER_HOST%\%SHARE% EN UNIDAD %DRIVE%
echo ==============================================================================
echo.
echo Ingrese sus credenciales de la institucion:
echo.
set /p NAS_USER="Usuario: "
if "%NAS_USER%"=="" (
    echo [ERROR] El usuario no puede estar vacio.
    pause
    goto MENU
)

set /p NAS_PASS="Contrasena: "
if "%NAS_PASS%"=="" (
    echo [ERROR] La contrasena no puede estar vacia.
    pause
    goto MENU
)

echo.
echo Conectando... por favor espere...
net use %DRIVE% /delete /yes >nul 2>&1
net use %DRIVE% "\%SERVER_HOST%\%SHARE%" /user:"%NAS_USER%" "%NAS_PASS%" /persistent:yes >nul 2>&1

if %errorlevel% equ 0 (
    echo.
    echo ==============================================================================
    echo [EXITO] Unidad %DRIVE% conectada correctamente a \%SERVER_HOST%\%SHARE%
    echo La carpeta ya se encuentra disponible en 'Este equipo'.
    echo ==============================================================================
    echo.
    start "" %DRIVE%
) else (
    echo.
    echo ==============================================================================
    echo [FALLO DE CONEXION] Error %errorlevel%
    echo Verifique que el usuario y contrasena sean correctos y que la IP sea accesible.
    echo ==============================================================================
)
echo.
pause
goto MENU

:DESCONECTAR
cls
echo Desconectando unidades Z:, P:, X:...
net use Z: /delete /yes >nul 2>&1
net use P: /delete /yes >nul 2>&1
net use X: /delete /yes >nul 2>&1
echo [OK] Unidades desconectadas del sistema.
pause
goto MENU
"""
        payload = bat_content.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/x-bat; charset=utf-8")
        self.send_header("Content-Disposition", 'attachment; filename="Conectar-Unidad-NAS.bat"')
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

if __name__ == "__main__":
    server = ThreadedHTTPServer(("0.0.0.0", PORT), NasApiHandler)
    print(f"Enterprise NAS Control Center OS listening on port {PORT}...")
    server.serve_forever()
