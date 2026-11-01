#!/usr/bin/env python3
"""
Enterprise NAS Control Center — Hardened Host Agent (nas-agentd)
Listens on a local UNIX Domain Socket (/run/nas-agentd/agent.sock) or localhost port.
Executes privileged host operations ONLY from a strict, typed catalog.
Zero arbitrary command execution (no 'sh -c', no arbitrary shell input).
"""
import os
import sys
import json
import time
import socket
import subprocess
import re
import hmac
import hashlib

SOCKET_PATH = "/run/nas-agentd/agent.sock"
AUDIT_LOG_PATH = "/var/log/nas-agentd-audit.log"

# Strict parameter validation regex patterns
RE_DATASET = re.compile(r"^tank/nas(/[a-zA-Z0-9._-]+)*$")
RE_SNAP_TAG = re.compile(r"^[a-zA-Z0-9._-]{1,64}$")
RE_USERNAME = re.compile(r"^[a-zA-Z0-9._-]{3,32}$")
RE_SHARENAME = re.compile(r"^[a-zA-Z0-9._-]{2,32}$")
RE_QUOTA = re.compile(r"^([0-9]+[KMGTkmgt]?|none)$")
RE_PODNAME = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
RE_NAMESPACE = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
RE_DEV_PATH = re.compile(r"^/dev/(sd[a-z][0-9]?|vd[a-z][0-9]?|nvme[0-9]n[0-9](p[0-9])?|disk/by-id/[a-zA-Z0-9._-]+)$")

def run_cmd(cmd_list, timeout=20, input_data=None):
    # If running inside container with hostPID, nsenter into host namespace 1
    processed_cmd = list(cmd_list)
    if processed_cmd and processed_cmd[0] == "kubectl":
        processed_cmd = ["env", "KUBECONFIG=/etc/rancher/k3s/k3s.yaml", "/usr/local/bin/kubectl"] + processed_cmd[1:]

    if os.path.exists("/proc/1/ns/mnt"):
        full_cmd = ["nsenter", "--target", "1", "--mount", "--uts", "--ipc", "--net", "--pid", "--"] + processed_cmd
    else:
        full_cmd = processed_cmd
    return subprocess.run(full_cmd, input=input_data, capture_output=True, text=True, timeout=timeout)

def audit_log(user, action, params, success, message=""):
    entry = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "user": user,
        "action": action,
        "params": {k: (v if "password" not in k.lower() and "secret" not in k.lower() else "********") for k, v in params.items()},
        "success": success,
        "message": message
    }
    try:
        os.makedirs(os.path.dirname(AUDIT_LOG_PATH), exist_ok=True)
        with open(AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        sys.stderr.write(f"Failed to write audit log: {e}\n")

# Action Catalog Handlers (Typed, Safe Executables)
def action_zfs_pool_status(params):
    res = run_cmd(["zpool", "status", "tank"], timeout=10)
    return res.returncode == 0, {"stdout": res.stdout, "stderr": res.stderr}

def action_zfs_snapshot_create(params):
    dataset = params.get("dataset", "tank/nas")
    tag = params.get("tag", "")
    if not RE_DATASET.match(dataset):
        return False, {"error": "Invalid dataset identifier"}
    if not RE_SNAP_TAG.match(tag):
        return False, {"error": "Invalid snapshot tag format"}
    snap_target = f"{dataset}@{tag}"
    res = run_cmd(["zfs", "snapshot", "-r", snap_target], timeout=15)
    return res.returncode == 0, {"snapshot": snap_target, "output": res.stdout}

def action_zfs_snapshot_rollback(params):
    dataset = params.get("dataset", "tank/nas")
    tag = params.get("tag", "")
    if not RE_DATASET.match(dataset) or not RE_SNAP_TAG.match(tag):
        return False, {"error": "Invalid dataset or snapshot tag"}
    target = f"{dataset}@{tag}"
    res = run_cmd(["zfs", "rollback", "-r", target], timeout=20)
    return res.returncode == 0, {"target": target, "output": res.stdout}

def action_zfs_set_quota(params):
    dataset = params.get("dataset", "")
    quota = params.get("quota", "")
    if not RE_DATASET.match(dataset) or not RE_QUOTA.match(quota):
        return False, {"error": "Invalid dataset or quota specification"}
    res = run_cmd(["zfs", "set", f"quota={quota}", dataset], timeout=10)
    return res.returncode == 0, {"dataset": dataset, "quota": quota}

def action_zfs_scrub(params):
    subaction = params.get("subaction", "start")
    if subaction not in ("start", "stop", "status"):
        return False, {"error": "Invalid scrub subaction"}
    if subaction == "status":
        res = run_cmd(["zpool", "status", "tank"], timeout=10)
        return res.returncode == 0, {"output": res.stdout}
    cmd = ["zpool", "scrub", "-s", "tank"] if subaction == "stop" else ["zpool", "scrub", "tank"]
    res = run_cmd(cmd, timeout=10)
    return res.returncode == 0, {"action": subaction, "output": res.stdout}

def action_storage_smart_status(params):
    device = params.get("device", "")
    if not RE_DEV_PATH.match(device):
        return False, {"error": "Invalid device path"}
    res = run_cmd(["smartctl", "-H", "-A", "-j", device], timeout=15)
    try:
        data = json.loads(res.stdout) if res.stdout else {"raw": res.stdout}
    except Exception:
        data = {"raw": res.stdout}
    return res.returncode in (0, 4), {"device": device, "smart": data}

def action_system_power(params):
    command = params.get("command", "")
    if command not in ("reboot", "poweroff"):
        return False, {"error": "Invalid power command"}
    res = run_cmd(["systemctl", command], timeout=10)
    return res.returncode == 0, {"status": f"Initiated {command}"}

def action_system_status(params):
    mem_info = {}
    if os.path.exists("/proc/meminfo"):
        with open("/proc/meminfo", "r") as f:
            for line in f:
                parts = line.split(":")
                if len(parts) == 2:
                    mem_info[parts[0].strip()] = parts[1].strip()
    loadavg = os.getloadavg() if hasattr(os, "getloadavg") else (0, 0, 0)
    return True, {"loadavg": loadavg, "mem": mem_info}

from modules_catalog import MODULE_HANDLERS

ACTION_CATALOG = {
    "zfs.pool.status": action_zfs_pool_status,
    "zfs.snapshot.create": action_zfs_snapshot_create,
    "zfs.snapshot.rollback": action_zfs_snapshot_rollback,
    "zfs.quota.set": action_zfs_set_quota,
    "zfs.scrub": action_zfs_scrub,
    "storage.smart.status": action_storage_smart_status,
    "system.power": action_system_power,
    "system.status": action_system_status,
}
# Merge all 17 modules into the active action catalog
ACTION_CATALOG.update(MODULE_HANDLERS)

def dispatch_request(req):
    user = req.get("user", "anonymous")
    action = req.get("action", "")
    params = req.get("params", {})
    
    if action not in ACTION_CATALOG:
        audit_log(user, action, params, False, "Action not found in catalog")
        return {"success": False, "error": f"Action '{action}' is not in the safe catalog"}
        
    handler = ACTION_CATALOG[action]
    try:
        ok, result = handler(params)
        audit_log(user, action, params, ok, result.get("error", "OK") if isinstance(result, dict) else "OK")
        return {"success": ok, "data": result}
    except Exception as e:
        audit_log(user, action, params, False, str(e))
        return {"success": False, "error": f"Internal execution exception: {str(e)}"}

def serve():
    if os.path.exists(SOCKET_PATH):
        os.remove(SOCKET_PATH)
    os.makedirs(os.path.dirname(SOCKET_PATH), exist_ok=True)
    
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(SOCKET_PATH)
    os.chmod(SOCKET_PATH, 0o660)
    server.listen(16)
    
    while True:
        conn, _ = server.accept()
        try:
            data = conn.recv(65536)
            if not data:
                conn.close()
                continue
            req = json.loads(data.decode("utf-8"))
            resp = dispatch_request(req)
            conn.sendall(json.dumps(resp).encode("utf-8"))
        except Exception as e:
            err_resp = {"success": False, "error": str(e)}
            conn.sendall(json.dumps(err_resp).encode("utf-8"))
        finally:
            conn.close()

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--oneshot":
        raw = sys.stdin.read()
        req = json.loads(raw)
        print(json.dumps(dispatch_request(req)))
    else:
        serve()
