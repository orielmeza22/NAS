#!/usr/bin/env python3
"""
Enterprise NAS Control Center — Authentication & Role-Based Access Control (RBAC)
Handles TOTP (RFC 6238 2FA), Session tokens, and Role enforcement.
"""
import time
import hmac
import hashlib
import struct
import base64
import secrets

ROLE_SUPERADMIN = "superadmin"
ROLE_OPERATOR = "operator"

ROLE_PERMISSIONS = {
    ROLE_SUPERADMIN: {
        "zfs.*",
        "storage.*",
        "system.*",
        "users.*",
        "network.*",
        "backups.*",
        "backup.*",
        "services.*",
        "shell.*",
        "audit.*",
        "sentinel.*",
        "files.*",
        "samba.*",
        "snapshots.*",
        "dr.*",
        "ups.*",
        "pki.*",
        "occ.*",
        "postgres.*",
        "cron.*",
    },
    ROLE_OPERATOR: {
        "users.list",
        "users.create",
        "users.sync_nextcloud",
        "users.import_csv",
        "users.reset_password",
        "zfs.quota.set",
        "zfs.dataset.list",
        "zfs.dataset.set_quota",
        "zfs.pool.status",
        "system.status",
        "system.top_processes",
        "storage.disks.list",
        "storage.smart.status",
        "storage.smart.test",
        "backups.snapshots.list",
        "snapshots.list",
        "snapshots.create",
        "services.list",
        "services.logs",
        "samba.shares.list",
        "samba.sessions",
        "files.list_all",
        "files.mkdir",
        "ups.status",
        "cron.list",
        "audit.logs.list",
        "network.status",
        "pki.download_ca",
    }
}

def generate_totp_secret():
    """Generates a standard Base32 encoded 160-bit secret."""
    raw = secrets.token_bytes(20)
    return base64.b32encode(raw).decode('utf-8').replace('=', '')

def verify_totp(secret, token, window=1):
    """
    Verifies a 6-digit TOTP token against secret using HMAC-SHA1.
    Allows a time window drift of +/- `window` steps (30 seconds each).
    """
    if not secret or not token:
        return False
    try:
        token = int(token.strip())
        secret_bytes = base64.b32decode(secret + '=' * ((8 - len(secret) % 8) % 8), casefold=True)
    except Exception:
        return False
        
    current_time_step = int(time.time() // 30)
    for step_offset in range(-window, window + 1):
        step = current_time_step + step_offset
        msg = struct.pack(">Q", step)
        h = hmac.new(secret_bytes, msg, hashlib.sha1).digest()
        offset = h[19] & 0x0F
        code = (struct.unpack(">I", h[offset:offset+4])[0] & 0x7FFFFFFF) % 1000000
        if code == token:
            return True
    return False

def check_permission(user_role, required_action):
    """Validates whether user_role has permission for required_action."""
    if not user_role or not required_action:
        return False
    perms = ROLE_PERMISSIONS.get(user_role, set())
    if required_action in perms:
        return True
    prefix = required_action.split(".", 1)[0] + ".*"
    return prefix in perms


def authenticate_portal_user(username, password, totp_code="", admin_pass="", operator_pass="", totp_secret=""):
    """
    Validates user credentials against role-specific passwords and optional RFC 6238 TOTP.
    Returns (authenticated: bool, normalized_user: str, role: str, error_msg: str).
    """
    norm_user = (username or "").strip().lower() or "admin"
    if not password:
        return False, norm_user, "", "Usuario o contraseña incorrectos."

    role = ""
    if norm_user in ("admin", "directivo", "root"):
        if admin_pass and secrets.compare_digest(password, admin_pass):
            role = ROLE_SUPERADMIN
    elif norm_user in ("operador", "operator", "soporte", "docente"):
        if not operator_pass:
            return False, norm_user, "", "La credencial del rol operador no se encuentra configurada."
        if secrets.compare_digest(password, operator_pass):
            role = ROLE_OPERATOR

    if not role:
        return False, norm_user, "", "Usuario o contraseña incorrectos."

    if totp_secret:
        if not totp_code or not verify_totp(totp_secret, str(totp_code)):
            return False, norm_user, "", "Código de segundo factor (2FA TOTP) inválido o ausente."

    return True, norm_user, role, ""


def resolve_client_ip(remote_addr, x_forwarded_for="", trusted_proxies=None):
    """
    Returns remote_addr unless remote_addr is explicitly listed in trusted_proxies,
    preventing X-Forwarded-For spoofing to bypass rate-limiting.
    """
    peer_ip = (remote_addr or "").strip() or "0.0.0.0"
    trusted = set(trusted_proxies or ())
    if peer_ip in trusted and x_forwarded_for:
        first_hop = x_forwarded_for.split(",")[0].strip()
        if first_hop:
            return first_hop
    return peer_ip
