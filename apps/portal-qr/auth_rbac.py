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
        "services.*",
        "shell.*"
    },
    ROLE_OPERATOR: {
        "users.list",
        "users.create",
        "users.reset_password",
        "zfs.quota.set",
        "system.status",
        "storage.smart.status",
        "backups.snapshots.list"
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
    perms = ROLE_PERMISSIONS.get(user_role, set())
    if "zfs.*" in perms and required_action.startswith("zfs."):
        return True
    if "storage.*" in perms and required_action.startswith("storage."):
        return True
    if "system.*" in perms and required_action.startswith("system."):
        return True
    if "users.*" in perms and required_action.startswith("users."):
        return True
    if "network.*" in perms and required_action.startswith("network."):
        return True
    if "backups.*" in perms and required_action.startswith("backups."):
        return True
    if "services.*" in perms and required_action.startswith("services."):
        return True
    if "shell.*" in perms and required_action.startswith("shell."):
        return True
    return required_action in perms
