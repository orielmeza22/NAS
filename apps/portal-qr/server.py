#!/usr/bin/env python3
"""
Enterprise NAS Control Center OS — Backend REST API Server (Unprivileged Web Frontend)
Runs inside an unprivileged container (readOnlyRootFilesystem, capabilities drop ALL + NET_BIND_SERVICE)
and delegates all host/cluster actions over UNIX IPC socket (/run/nas-agentd/agent.sock)
with dual RBAC enforcement, TOTP 2FA, IP-bound sessions, and SQLite rate-limiting.
"""
import os
import sys
import json
import time
import urllib.parse
import secrets
import ssl
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

import portal_state
from task_engine import GLOBAL_TASK_ENGINE
from auth_rbac import (
    authenticate_portal_user,
    check_permission,
    resolve_client_ip,
)


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


PORT = int(os.environ.get("PORTAL_PORT", "80"))
SESSION_TTL_SECONDS = 28800  # 8 hours
MAX_FAILED_LOGINS = 5
LOCKOUT_SECONDS = 900  # 15 minutes
TRUSTED_PROXIES = [
    ip.strip()
    for ip in os.environ.get("PORTAL_TRUSTED_PROXIES", "").split(",")
    if ip.strip()
]


def _read_secret_env_or_file(env_name, file_path):
    val = os.environ.get(env_name, "").strip()
    if val:
        return val
    if file_path and os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            pass
    return ""


def get_admin_password():
    """Retrieves the portal superadmin password from environment or secret file."""
    return _read_secret_env_or_file("PORTAL_ADMIN_PASSWORD", "/etc/nas-credentials/admin-password")


def get_operator_password():
    """Retrieves the dedicated operator password from environment or secret file."""
    return _read_secret_env_or_file("PORTAL_OPERATOR_PASSWORD", "/etc/nas-credentials/operator-password")


def get_totp_secret():
    """Retrieves the RFC 6238 TOTP secret for 2FA enforcement."""
    return _read_secret_env_or_file("PORTAL_TOTP_SECRET", "/etc/nas-credentials/totp-secret")


class NasApiHandler(BaseHTTPRequestHandler):
    def get_client_ip(self):
        return resolve_client_ip(
            self.client_address[0],
            self.headers.get("X-Forwarded-For", ""),
            TRUSTED_PROXIES,
        )

    def check_rate_limit(self, ip):
        return portal_state.check_rate_limit(ip, MAX_FAILED_LOGINS, LOCKOUT_SECONDS)

    def record_failed_login(self, ip):
        portal_state.record_failed_login(ip, MAX_FAILED_LOGINS, LOCKOUT_SECONDS)

    def reset_failed_logins(self, ip):
        portal_state.reset_failed_logins(ip)

    def is_auth(self):
        auth_hdr = self.headers.get("Authorization", "")
        if auth_hdr.startswith("Bearer "):
            token = auth_hdr.split(" ", 1)[1].strip()
            valid, username, role = portal_state.validate_session(token, self.get_client_ip())
            if valid and role:
                self.auth_user = username
                self.auth_role = role
                return True
        return False

    def _get_allowed_origin(self):
        origin = self.headers.get("Origin", "").strip()
        if not origin:
            return ""
        allowed_env = os.environ.get("PORTAL_ALLOWED_ORIGIN", "").strip()
        if allowed_env and origin == allowed_env:
            return origin
        host = self.headers.get("Host", "").strip()
        if host and origin in (f"https://{host}", f"http://{host}"):
            return origin
        return ""

    def send_json(self, data, status=200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        allowed_origin = self._get_allowed_origin()
        if allowed_origin:
            self.send_header("Access-Control-Allow-Origin", allowed_origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        allowed_origin = self._get_allowed_origin()
        if allowed_origin:
            self.send_header("Access-Control-Allow-Origin", allowed_origin)
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
        if path == "/api/public/status":
            self.handle_public_status()
            return
        if path == "/api/samba/connector":
            self.serve_samba_connector()
            return

        if not self.is_auth():
            self.send_json({"error": "Unauthorized"}, 401)
            return

        if path == "/api/tasks":
            tasks = portal_state.list_tasks(limit=30)
            self.send_json({"success": True, "tasks": tasks})
            return
        if path.startswith("/api/tasks/"):
            task_id = path.replace("/api/tasks/", "").strip()
            task = portal_state.get_task(task_id)
            if not task:
                self.send_json({"success": False, "error": "Tarea no encontrada"}, 404)
            else:
                self.send_json({"success": True, "task": task})
            return
        if path == "/api/audit/logs":
            logs = portal_state.list_audit_events(limit=100)
            self.send_json({"success": True, "logs": logs})
            return

        self.send_json({"error": "Not Found"}, 404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        body = {}
        raw_len = self.headers.get("Content-Length", "0")
        try:
            content_len = int(raw_len)
            if content_len < 0:
                raise ValueError("Negative Content-Length")
        except ValueError:
            self.send_json({"success": False, "error": "Cabecera Content-Length inválida."}, 400)
            return
        if content_len > 1048576:
            self.send_json({"success": False, "error": "Payload excede el límite máximo permitido (1 MB)."}, 413)
            return
        if content_len > 0:
            try:
                body = json.loads(self.rfile.read(content_len).decode("utf-8"))
            except Exception:
                self.send_json({"success": False, "error": "Cuerpo JSON malformado."}, 400)
                return

        if path == "/api/auth/login":
            client_ip = self.get_client_ip()
            allowed, remaining = self.check_rate_limit(client_ip)
            if not allowed:
                mins = max(1, int(remaining // 60))
                self.send_json({
                    "success": False,
                    "error": f"Demasiados intentos fallidos. Bloqueo temporal de seguridad por {mins} minutos."
                }, 429)
                return

            username = str(body.get("username", "")).strip().lower()
            password = str(body.get("password", ""))
            totp_code = str(body.get("totp", "") or body.get("totp_code", "")).strip()
            real_admin_pass = get_admin_password()
            operator_pass = get_operator_password()
            totp_secret = get_totp_secret()
            if not real_admin_pass:
                self.send_json({
                    "success": False,
                    "error": "Servicio de autenticacion no configurado (PORTAL_ADMIN_PASSWORD ausente)."
                }, 500)
                return

            auth_ok, norm_user, user_role, err_msg = authenticate_portal_user(
                username,
                password,
                totp_code=totp_code,
                admin_pass=real_admin_pass,
                operator_pass=operator_pass,
                totp_secret=totp_secret,
            )
            if auth_ok:
                token = secrets.token_urlsafe(32)
                portal_state.create_session(token, norm_user, user_role, client_ip, SESSION_TTL_SECONDS)
                self.reset_failed_logins(client_ip)
                portal_state.log_audit_event(
                    norm_user, "auth.login", {"role": user_role}, True, "Inicio de sesión correcto", client_ip
                )
                self.send_json({
                    "success": True,
                    "token": token,
                    "username": norm_user,
                    "role": user_role,
                    "expires_in": SESSION_TTL_SECONDS,
                })
            else:
                self.record_failed_login(client_ip)
                portal_state.log_audit_event(
                    username or "unknown", "auth.login_failed", {}, False, err_msg or "Credenciales inválidas", client_ip
                )
                self.send_json({
                    "success": False,
                    "error": err_msg or "Usuario o contraseña incorrectos."
                }, 401)
            return

        if not self.is_auth():
            self.send_json({"success": False, "error": "No autorizado. Inicie sesión en el Centro de Control."}, 401)
            return

        if path == "/api/agent/action":
            action = str(body.get("action", "")).strip()
            params = body.get("params", {})
            if not isinstance(params, dict):
                params = {}
            user = getattr(self, "auth_user", "")
            role = getattr(self, "auth_role", "")
            client_ip = self.get_client_ip()

            if not role or not check_permission(role, action):
                portal_state.log_audit_event(user or "unknown", action, params, False, f"RBAC denegado para rol '{role}'", client_ip)
                self.send_json({
                    "success": False,
                    "error": f"Permiso denegado: el rol '{role or 'none'}' no tiene autorización para '{action}'."
                }, 403)
                return

            req_async = bool(body.get("async", False)) or GLOBAL_TASK_ENGINE.is_action_async(action)
            if req_async:
                task_id = GLOBAL_TASK_ENGINE.submit_task(action, params, user, client_ip, role=role)
                self.send_json({
                    "success": True,
                    "async": True,
                    "task_id": task_id,
                    "message": f"Operación '{action}' iniciada en segundo plano con ID {task_id}",
                    "action": action,
                }, 202)
                return

            if "/app-config" not in sys.path:
                sys.path.insert(0, "/app-config")
            from nas_agentd import send_agent_request
            req = {"user": user, "role": role, "action": action, "params": params, "client_ip": client_ip}
            res = send_agent_request(req)
            status_code = 403 if res.get("forbidden") else (200 if res.get("success") else 400)
            self.send_json(res, status_code)
            return

        if path == "/api/tasks/cancel" or (path.startswith("/api/tasks/") and path.endswith("/cancel")):
            task_id = str(body.get("task_id", "")).strip() or path.split("/")[3]
            ok = GLOBAL_TASK_ENGINE.cancel_task(task_id)
            self.send_json({"success": ok, "message": f"Señal de cancelación enviada a la tarea {task_id}"})
            return

        if path == "/api/auth/logout":
            token = self.headers.get("Authorization", "").replace("Bearer ", "").strip()
            portal_state.destroy_session(token)
            self.send_json({"success": True})
            return

        self.send_json({"error": "Endpoint no encontrado"}, 404)

    def serve_frontend(self):
        content = b"<h1>Consola de Administracion - Enterprise Storage Appliance</h1>"
        for fpath in ("/app-config/index.html", "/usr/share/nginx/html/index.html"):
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

    def handle_public_status(self):
        uptime_sec = 0
        if os.path.exists("/proc/uptime"):
            try:
                with open("/proc/uptime", "r", encoding="utf-8") as f:
                    uptime_sec = int(float(f.read().split()[0]))
            except Exception:
                uptime_sec = 0

        health = "OFFLINE"
        try:
            if "/app-config" not in sys.path:
                sys.path.insert(0, "/app-config")
            from nas_agentd import send_agent_request
            res = send_agent_request(
                {"user": "healthcheck", "role": "operator", "action": "zfs.pool.status", "params": {}},
                timeout=5,
            )
            if res.get("success") and isinstance(res.get("data"), dict):
                health = str(res["data"].get("health") or ("ONLINE" if "ONLINE" in str(res["data"].get("stdout", "")) else "ONLINE"))
        except Exception:
            health = "OFFLINE"

        self.send_json({
            "raid_health": health,
            "uptime_seconds": uptime_sec,
            "platform": "Enterprise Educational NAS 2026",
        })

    def serve_samba_connector(self):
        bat_content = r"""@echo off
setlocal
title Conector de Unidades de Red - Enterprise NAS
chcp 65001 >nul
color 1F

set "SERVER_HOST=192.168.1.50"
if "%~1" neq "" set "SERVER_HOST=%~1"

:MENU
cls
echo ==============================================================================
echo       CONECTOR DE UNIDADES DE RED ESCOLAR (SAMBA / SMB)
echo ==============================================================================
echo Servidor NAS: \\%SERVER_HOST%
echo.
echo Seleccione el recurso al que desea conectarse:
echo.
echo   [1] Carpeta de ALUMNOS       - Montar en unidad Z: (\\%SERVER_HOST%\alumnos)
echo   [2] Carpeta de PROFESORES    - Montar en unidad P: (\\%SERVER_HOST%\profesores)
echo   [3] Carpeta PUBLICA          - Montar en unidad X: (\\%SERVER_HOST%\publico)
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
echo CONECTANDO A RECURSO: \\%SERVER_HOST%\%SHARE% EN UNIDAD %DRIVE%
echo.
set /p NAS_USER="Usuario: "
if "%NAS_USER%"=="" (
    echo [ERROR] El usuario no puede estar vacio.
    pause
    goto MENU
)

echo.
echo Conectando... ingrese su contrasena de forma interactiva cuando Windows lo solicite...
net use %DRIVE% /delete /yes >nul 2>&1
net use %DRIVE% "\\%SERVER_HOST%\%SHARE%" /user:"%NAS_USER%" * /persistent:yes

if %errorlevel% equ 0 (
    echo [EXITO] Unidad %DRIVE% conectada correctamente a \\%SERVER_HOST%\%SHARE%
    start "" %DRIVE%
) else (
    echo [FALLO DE CONEXION] Error %errorlevel%
)
pause
goto MENU

:DESCONECTAR
cls
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


def resolve_tls_pair():
    """
    Locates the TLS certificate and private key from the mounted Kubernetes Secret
    (/etc/nas-tls) or the shared host tunnel directory (/tmp/nas-tunnel/tls) provisioned
    by bootstrap / nas-agentd. Waits up to 15s on cold boot for nas-agentd to finish.
    """
    candidates = [
        ("/etc/nas-tls/portal.crt", "/etc/nas-tls/portal.key"),
        ("/tmp/nas-tunnel/tls/portal.crt", "/tmp/nas-tunnel/tls/portal.key"),
        ("/tmp/nas-tls/portal.crt", "/tmp/nas-tls/portal.key"),
    ]
    for _ in range(15):
        for crt, key in candidates:
            if os.path.exists(crt) and os.path.exists(key):
                return crt, key
        time.sleep(1)
    raise RuntimeError(
        "No se encontró el par TLS (portal.crt / portal.key) en /etc/nas-tls ni en /tmp/nas-tunnel/tls."
    )


if __name__ == "__main__":
    portal_state.init_db()
    try:
        if "/app-config" not in sys.path:
            sys.path.insert(0, "/app-config")
        from nas_agentd import ACTION_CATALOG
        GLOBAL_TASK_ENGINE.register_handlers(ACTION_CATALOG)
    except Exception as e:
        sys.stderr.write(f"Advertencia al vincular ACTION_CATALOG: {e}\n")

    server = ThreadedHTTPServer(("0.0.0.0", PORT), NasApiHandler)
    cert_file, key_file = resolve_tls_pair()

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=cert_file, keyfile=key_file)
    server.socket = context.wrap_socket(server.socket, server_side=True)

    print(f"Enterprise NAS Control Center OS listening on https://0.0.0.0:{PORT}...", flush=True)
    server.serve_forever()
