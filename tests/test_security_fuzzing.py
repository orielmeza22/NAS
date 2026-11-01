#!/usr/bin/env python3
"""
Enterprise NAS Control Center — Security & Injection Fuzzing Test Suite
Validates that nas_agentd and auth_rbac strictly reject malicious payloads,
command injections, directory traversals, and unauthorized RBAC escalations.
"""
import unittest
import json
import subprocess
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "apps", "portal-qr")))

from nas_agentd import dispatch_request
from auth_rbac import (
    generate_totp_secret,
    verify_totp,
    check_permission,
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

if __name__ == "__main__":
    unittest.main()
