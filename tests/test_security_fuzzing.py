#!/usr/bin/env python3
"""
Enterprise NAS Control Center — Security & Injection Fuzzing Test Suite
Validates that nas_agentd and auth_rbac strictly reject malicious payloads,
command injections, directory traversals, and unauthorized RBAC escalations.
"""
import unittest
import json
import subprocess
import shlex
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "apps", "portal-qr")))

from nas_agentd import dispatch_request
from auth_rbac import (
    generate_totp_secret,
    verify_totp,
    check_permission,
    authenticate_portal_user,
    resolve_client_ip,
    ROLE_SUPERADMIN,
    ROLE_OPERATOR
)

class TestNasSecurityArchitecture(unittest.TestCase):
    
    def test_rejection_of_unregistered_actions(self):
        """Must reject any action not explicitly defined in the safe catalog."""
        req = {
            "user": "admin",
            "action": "system.arbitrary_bash",
            "params": {"cmd": "rm -rf /"}
        }
        res = dispatch_request(req)
        self.assertFalse(res["success"])
        self.assertIn("not in the safe catalog", res["error"])

    def test_rejection_of_command_injections_in_dataset(self):
        """Must reject shell metacharacters in dataset names."""
        injections = [
            "tank/nas; rm -rf /",
            "tank/nas | cat /etc/shadow",
            "tank/nas`reboot`",
            "tank/nas$(id)",
            "tank/nas && ls",
            "../../etc/passwd"
        ]
        for inj in injections:
            req = {
                "user": "admin",
                "action": "zfs.snapshot.create",
                "params": {"dataset": inj, "tag": "test"}
            }
            res = dispatch_request(req)
            self.assertFalse(res["success"], f"Failed to reject injection: {inj}")
            self.assertIn("Invalid dataset", res["data"]["error"])

    def test_rejection_of_command_injections_in_snapshot_tags(self):
        """Must reject malicious snapshot tags."""
        injections = [
            "snap1; touch /tmp/pwn",
            "snap1 && id",
            "snap1 $(whoami)",
            "snap/with/slash",
            "snap\nnewline"
        ]
        for inj in injections:
            req = {
                "user": "admin",
                "action": "zfs.snapshot.create",
                "params": {"dataset": "tank/nas", "tag": inj}
            }
            res = dispatch_request(req)
            self.assertFalse(res["success"], f"Failed to reject injection in tag: {inj}")

    def test_rejection_of_invalid_quotas(self):
        """Must reject arbitrary quota string parameters."""
        injections = [
            "10G; rm -rf /",
            "invalid_size",
            "10GB", # only K, M, G, T or none allowed
            "-50G"
        ]
        for inj in injections:
            req = {
                "user": "admin",
                "action": "zfs.quota.set",
                "params": {"dataset": "tank/nas/alumnos", "quota": inj}
            }
            res = dispatch_request(req)
            self.assertFalse(res["success"], f"Failed to reject invalid quota: {inj}")

    def test_rejection_of_unauthorized_device_paths(self):
        """Must reject invalid device paths in smartctl queries."""
        injections = [
            "/dev/null; id",
            "/etc/shadow",
            "/dev/sdb && ls",
            "../../dev/sda"
        ]
        for inj in injections:
            req = {
                "user": "admin",
                "action": "storage.smart.status",
                "params": {"device": inj}
            }
            res = dispatch_request(req)
            self.assertFalse(res["success"], f"Failed to reject device: {inj}")

    def test_rbac_privilege_enforcement(self):
        """Must verify that Operator cannot perform SuperAdmin operations."""
        # Operator can do user operations
        self.assertTrue(check_permission(ROLE_OPERATOR, "users.create"))
        self.assertTrue(check_permission(ROLE_OPERATOR, "zfs.quota.set"))
        
        # Operator cannot rollback snapshots or poweroff system
        self.assertFalse(check_permission(ROLE_OPERATOR, "zfs.snapshot.rollback"))
        self.assertFalse(check_permission(ROLE_OPERATOR, "system.power"))
        self.assertFalse(check_permission(ROLE_OPERATOR, "shell.execute"))
        
        # SuperAdmin can perform all operations
        self.assertTrue(check_permission(ROLE_SUPERADMIN, "zfs.snapshot.rollback"))
        self.assertTrue(check_permission(ROLE_SUPERADMIN, "system.power"))
        self.assertTrue(check_permission(ROLE_SUPERADMIN, "shell.execute"))

    def test_totp_rfc6238_validation(self):
        """Must generate and accurately validate 2FA tokens."""
        import time
        import hmac
        import hashlib
        import struct
        import base64
        
        secret = generate_totp_secret()
        self.assertGreaterEqual(len(secret), 16)
        
        # Test valid code generation and validation
        secret_bytes = base64.b32decode(secret + '=' * ((8 - len(secret) % 8) % 8), casefold=True)
        step = int(time.time() // 30)
        msg = struct.pack(">Q", step)
        h = hmac.new(secret_bytes, msg, hashlib.sha1).digest()
        offset = h[19] & 0x0F
        valid_code = str((struct.unpack(">I", h[offset:offset+4])[0] & 0x7FFFFFFF) % 1000000).zfill(6)
        
        self.assertTrue(verify_totp(secret, valid_code))
        
        # False on invalid token
        self.assertFalse(verify_totp(secret, "000000" if valid_code != "000000" else "111111"))
        self.assertFalse(verify_totp(secret, "abc123"))

    def test_runtime_rbac_dispatch_blocks_operator_destructive_actions(self):
        """Must enforce RBAC inside dispatch_request at runtime when role is operator."""
        denied_actions = [
            ("zfs.snapshot.rollback", {"dataset": "tank/nas", "tag": "snap1"}),
            ("system.power", {"command": "reboot"}),
            ("shell.exec", {"command": "uptime"}),
            ("users.delete", {"username": "alumno01"}),
        ]
        for act, params in denied_actions:
            res = dispatch_request({
                "user": "operador",
                "role": ROLE_OPERATOR,
                "action": act,
                "params": params,
            })
            self.assertFalse(res["success"], f"Operator should be denied on {act}")
            self.assertTrue(res.get("forbidden"), f"Expected forbidden=True on {act}")

        allowed_res = dispatch_request({
            "user": "operador",
            "role": ROLE_OPERATOR,
            "action": "system.status",
            "params": {},
        })
        self.assertTrue(allowed_res["success"])

    def test_x_forwarded_for_spoofing_protection(self):
        """Must ignore X-Forwarded-For unless peer IP is explicitly in trusted_proxies."""
        # Attacker on LAN tries to spoof X-Forwarded-For to bypass 5-attempt lockout
        self.assertEqual(
            resolve_client_ip("192.168.1.45", "10.0.0.99", trusted_proxies=[]),
            "192.168.1.45",
        )
        self.assertEqual(
            resolve_client_ip("192.168.1.45", "10.0.0.99", trusted_proxies=["127.0.0.1"]),
            "192.168.1.45",
        )
        # Trusted reverse proxy forwards real client IP
        self.assertEqual(
            resolve_client_ip("127.0.0.1", "192.168.1.88, 10.0.0.1", trusted_proxies=["127.0.0.1"]),
            "192.168.1.88",
        )

    def test_portal_login_role_separation_and_totp(self):
        """Must assign superadmin vs operator roles accurately and enforce TOTP when configured."""
        ok, user, role, _ = authenticate_portal_user(
            "admin", "Adm!nSecret2026", admin_pass="Adm!nSecret2026", operator_pass="OpSecret2026"
        )
        self.assertTrue(ok)
        self.assertEqual(role, ROLE_SUPERADMIN)

        ok_op, user_op, role_op, _ = authenticate_portal_user(
            "operador", "OpSecret2026", admin_pass="Adm!nSecret2026", operator_pass="OpSecret2026"
        )
        self.assertTrue(ok_op)
        self.assertEqual(role_op, ROLE_OPERATOR)

        # Operator cannot log in as admin with operator password
        ok_bad, _, _, _ = authenticate_portal_user(
            "admin", "OpSecret2026", admin_pass="Adm!nSecret2026", operator_pass="OpSecret2026"
        )
        self.assertFalse(ok_bad)

        # When TOTP secret is set, login without TOTP code must fail
        secret = generate_totp_secret()
        ok_no_totp, _, _, _ = authenticate_portal_user(
            "admin", "Adm!nSecret2026", totp_code="", admin_pass="Adm!nSecret2026", totp_secret=secret
        )
        self.assertFalse(ok_no_totp)

    def test_single_quote_in_password_safe_stdin_quoting(self):
        """Must pass passwords via stdin (not argv) and safely quote single quotes."""
        from unittest.mock import patch, MagicMock
        import modules_catalog

        captured_calls = []

        def fake_run_cmd(cmd_list, timeout=30, input_data=None):
            captured_calls.append((cmd_list, input_data))
            res = MagicMock()
            res.returncode = 0
            res.stdout = "OK"
            res.stderr = ""
            return res

        tricky_pwd = "Clav3'Con'Comilla$(id)`reboot`"
        with patch.object(modules_catalog, "run_cmd", side_effect=fake_run_cmd):
            ok, _ = modules_catalog.act_users_sync_nextcloud({
                "username": "alumno_test",
                "password": tricky_pwd,
                "display_name": "Alumno Test",
                "group": "alumnos",
            })
            self.assertTrue(ok)
            self.assertEqual(len(captured_calls), 2)
            for cmd_list, stdin_payload in captured_calls:
                # Password must never travel in argv
                for arg in cmd_list:
                    self.assertNotIn(tricky_pwd, arg)
                # Password must be safely shlex.quoted in stdin script
                self.assertIsNotNone(stdin_payload)
                self.assertIn(shlex.quote(tricky_pwd), stdin_payload)

if __name__ == "__main__":
    unittest.main()
