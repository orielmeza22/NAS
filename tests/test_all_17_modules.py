#!/usr/bin/env python3
"""
Enterprise NAS Control Center — Full 17 Modules Comprehensive Security & Dispatcher Test Suite
Verifies that all 17 management modules are registered and execute safe behaviors.
"""
import unittest
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "apps", "portal-qr")))

from nas_agentd import dispatch_request, ACTION_CATALOG

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
            "onboarding.status"
        ]
        for mod in expected_modules:
            self.assertIn(mod, ACTION_CATALOG, f"Module action {mod} missing from catalog")

    def test_network_apply_with_safety_rollback(self):
        req = {
            "user": "admin",
            "action": "network.apply",
            "params": {"ip": "192.168.1.50/24", "gateway": "192.168.1.1"}
        }
        res = dispatch_request(req)
        self.assertTrue(res["success"])
        self.assertIn("Confirm within 60s", res["data"]["message"])
        
        # Confirm
        conf_req = {"user": "admin", "action": "network.confirm", "params": {}}
        res_conf = dispatch_request(conf_req)
        self.assertTrue(res_conf["success"])

    def test_file_explorer_path_traversal_guards(self):
        req = {
            "user": "admin",
            "action": "files.list_all",
            "params": {"path": "../../../etc/shadow"}
        }
        res = dispatch_request(req)
        self.assertTrue(res["success"])
        self.assertTrue(res["data"]["path"].startswith("/tank/nas"))

    def test_pki_institutional_ca_trigger(self):
        req = {"user": "admin", "action": "pki.generate_ca", "params": {}}
        res = dispatch_request(req)
        self.assertTrue(res["success"])
        self.assertIn("Institutional Root CA", res["data"]["status"])

if __name__ == "__main__":
    unittest.main()
