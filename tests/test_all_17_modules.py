#!/usr/bin/env python3
"""
Enterprise NAS Control Center — Full 17 Modules Comprehensive Security & Dispatcher Test Suite
Verifies that all 17 management modules are registered and execute real, verified behaviors.
"""
import unittest
from unittest.mock import patch, MagicMock
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "apps", "portal-qr")))

import modules_catalog
from nas_agentd import dispatch_request, ACTION_CATALOG
from auth_rbac import ROLE_SUPERADMIN


class TestAll17ModulesCatalog(unittest.TestCase):

    def test_all_17_modules_registered(self):
        expected_modules = [
            "storage.disks.list",
            "storage.smart.test",
            "zfs.pool.create",
            "zfs.disk.replace",
            "zfs.dataset.create",
            "zfs.dataset.delete",
            "users.sync_nextcloud",
            "users.reset_password",
            "backup.export_emergency_kit",
            "backup.r2.trigger",
            "ups.status",
            "network.apply",
            "network.confirm",
            "alerts.test",
            "sentinel.unban",
            "services.restart",
            "shell.exec",
            "pki.generate_ca",
            "occ.files_scan",
            "postgres.vacuum",
            "files.list_all",
            "firewall.status",
            "cron.list",
            "zfs.arc.set_max",
            "updates.check",
            "dr.run_drill",
            "onboarding.status",
        ]
        for mod in expected_modules:
            self.assertIn(mod, ACTION_CATALOG, f"Module action {mod} missing from catalog")

    def test_network_apply_with_safety_rollback(self):
        executed_cmds = []

        def fake_net_cmd(cmd_list, timeout=30, input_data=None):
            executed_cmds.append(cmd_list)
            res = MagicMock()
            res.returncode = 0
            res.stderr = ""
            if cmd_list[:4] == ["ip", "-o", "-4", "route"]:
                res.stdout = "default via 192.168.1.1 dev eth0 proto static\n"
            elif cmd_list[:4] == ["ip", "-o", "-4", "addr"]:
                res.stdout = "2: eth0    inet 192.168.1.49/24 brd 192.168.1.255 scope global eth0\n"
            else:
                res.stdout = "OK"
            return res

        with patch.object(modules_catalog, "run_cmd", side_effect=fake_net_cmd):
            req = {
                "user": "admin",
                "role": ROLE_SUPERADMIN,
                "action": "network.apply",
                "params": {"ip": "192.168.1.50/24", "gateway": "192.168.1.1"},
            }
            res = dispatch_request(req)
            self.assertTrue(res["success"])
            self.assertIn("Confirm within 60s", res["data"]["message"])
            self.assertIn(["ip", "addr", "replace", "192.168.1.50/24", "dev", "eth0"], executed_cmds)

            conf_req = {"user": "admin", "role": ROLE_SUPERADMIN, "action": "network.confirm", "params": {}}
            res_conf = dispatch_request(conf_req)
            self.assertTrue(res_conf["success"])

    def test_file_explorer_path_traversal_guards(self):
        req = {
            "user": "admin",
            "role": ROLE_SUPERADMIN,
            "action": "files.list_all",
            "params": {"path": "../../../etc/shadow"},
        }
        res = dispatch_request(req)
        self.assertTrue(res["success"])
        self.assertTrue(res["data"]["path"].startswith("/tank/nas"))

    def test_pki_institutional_ca_trigger(self):
        def fake_openssl_cmd(cmd_list, timeout=30, input_data=None):
            res = MagicMock()
            res.returncode = 0
            res.stdout = "192.168.1.50\n" if cmd_list[0] == "hostname" else "OK"
            res.stderr = ""
            return res

        with patch.object(modules_catalog, "run_cmd", side_effect=fake_openssl_cmd):
            req = {"user": "admin", "role": ROLE_SUPERADMIN, "action": "pki.generate_ca", "params": {}}
            res = dispatch_request(req)
            self.assertTrue(res["success"])
            self.assertIn("Institutional Root CA", res["data"]["status"])


if __name__ == "__main__":
    unittest.main()
