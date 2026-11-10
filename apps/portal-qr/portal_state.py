#!/usr/bin/env python3
"""
Enterprise NAS Control Center — Persistent State & Security Storage (SQLite Engine)
Persists active admin sessions, brute-force IP rate limits, async tasks, and audit trails.
Survives pod restarts, rollouts, and host reboots when stored on /tank/nas/.portal-state.
"""
import os
import time
import json
import sqlite3
import threading
import tempfile
from contextlib import contextmanager

if os.environ.get("PORTAL_STATE_DIR"):
    STATE_DIR = os.environ["PORTAL_STATE_DIR"]
elif os.path.exists("/var/lib/nas-portal-state"):
    STATE_DIR = "/var/lib/nas-portal-state"
elif os.path.exists("/tmp/nas-tunnel"):
    STATE_DIR = "/tmp/nas-tunnel"
else:
    STATE_DIR = tempfile.gettempdir()

DB_PATH = os.path.join(STATE_DIR, "portal_state.db")

_db_lock = threading.Lock()


@contextmanager
def _db_session():
    """
    Context manager that opens a WAL-enabled SQLite connection, commits on success,
    and strictly closes the file descriptor in a finally block to prevent FD leaks.
    """
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        yield conn
        conn.commit()
    finally:
        conn.close()


def sanitize_audit_params(params):
    """Masks passwords, secrets, tokens, and bulk CSV payloads before logging."""
    if not isinstance(params, dict):
        return {}
    sensitive_keys = ("pass", "secret", "token", "key", "csv")
    cleaned = {}
    for k, v in params.items():
        kl = str(k).lower()
        if any(s in kl for s in sensitive_keys):
            cleaned[k] = "********"
        elif kl == "users" and isinstance(v, list):
            cleaned[k] = f"[{len(v)} user records redacted]"
        else:
            cleaned[k] = v
    return cleaned


def init_db():
    """Initializes schema tables if not existing."""
    with _db_lock, _db_session() as conn:
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                role TEXT NOT NULL,
                client_ip TEXT NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL
            );
        """)
        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS failed_logins (
                ip TEXT PRIMARY KEY,
                attempts INTEGER NOT NULL DEFAULT 1,
                last_attempt REAL NOT NULL,
                locked_until REAL NOT NULL DEFAULT 0
            );
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                action TEXT NOT NULL,
                params TEXT NOT NULL,
                user TEXT NOT NULL,
                status TEXT NOT NULL,
                progress INTEGER NOT NULL DEFAULT 0,
                phase TEXT NOT NULL,
                started_at REAL NOT NULL,
                finished_at REAL NOT NULL DEFAULT 0,
                exit_code INTEGER NOT NULL DEFAULT 0,
                result TEXT NOT NULL DEFAULT '{}',
                log TEXT NOT NULL DEFAULT ''
            );
        """)
        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_tasks_started ON tasks(started_at DESC);
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                user TEXT NOT NULL,
                action TEXT NOT NULL,
                params TEXT NOT NULL,
                success INTEGER NOT NULL,
                message TEXT NOT NULL,
                client_ip TEXT NOT NULL
            );
        """)
        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_events(id DESC);
        """)


# --- SESSIONS ---
def create_session(token, username, role, client_ip, ttl_seconds=28800):
    now = time.time()
    expires = now + ttl_seconds
    with _db_lock, _db_session() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO sessions (token, username, role, client_ip, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
            (token, username, role, client_ip or "local", now, expires)
        )


def validate_session(token, client_ip=None):
    now = time.time()
    with _db_lock, _db_session() as conn:
        row = conn.execute("SELECT username, role, client_ip, expires_at FROM sessions WHERE token = ?", (token,)).fetchone()
        if not row:
            return False, None, None
        if row["expires_at"] < now:
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
            return False, None, None
        bound_ip = row["client_ip"]
        if client_ip and bound_ip and bound_ip not in ("", "local") and bound_ip != client_ip:
            return False, None, None
        return True, row["username"], row["role"]


def destroy_session(token):
    with _db_lock, _db_session() as conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))


def cleanup_expired_sessions():
    now = time.time()
    with _db_lock, _db_session() as conn:
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))


# --- RATE LIMITING / BRUTE FORCE ---
def check_rate_limit(client_ip, max_attempts=5, lockout_seconds=900):
    now = time.time()
    with _db_lock, _db_session() as conn:
        row = conn.execute("SELECT attempts, last_attempt, locked_until FROM failed_logins WHERE ip = ?", (client_ip,)).fetchone()
        if not row:
            return True, 0
        if row["locked_until"] > now:
            remaining = int(row["locked_until"] - now)
            return False, remaining
        if now - row["last_attempt"] > lockout_seconds:
            conn.execute("DELETE FROM failed_logins WHERE ip = ?", (client_ip,))
            return True, 0
        if row["attempts"] >= max_attempts:
            locked_until = now + lockout_seconds
            conn.execute("UPDATE failed_logins SET locked_until = ? WHERE ip = ?", (locked_until, client_ip))
            return False, lockout_seconds
        return True, 0


def record_failed_login(client_ip, max_attempts=5, lockout_seconds=900):
    now = time.time()
    with _db_lock, _db_session() as conn:
        row = conn.execute("SELECT attempts FROM failed_logins WHERE ip = ?", (client_ip,)).fetchone()
        if not row:
            conn.execute("INSERT INTO failed_logins (ip, attempts, last_attempt, locked_until) VALUES (?, 1, ?, 0)", (client_ip, now))
        else:
            new_attempts = row["attempts"] + 1
            locked_until = (now + lockout_seconds) if new_attempts >= max_attempts else 0
            conn.execute("UPDATE failed_logins SET attempts = ?, last_attempt = ?, locked_until = ? WHERE ip = ?", (new_attempts, now, locked_until, client_ip))


def reset_failed_logins(client_ip):
    with _db_lock, _db_session() as conn:
        conn.execute("DELETE FROM failed_logins WHERE ip = ?", (client_ip,))


# --- ASYNC TASKS ---
def create_task(task_id, action, params, user):
    now = time.time()
    params_str = json.dumps(sanitize_audit_params(params))
    with _db_lock, _db_session() as conn:
        conn.execute(
            "INSERT INTO tasks (task_id, action, params, user, status, progress, phase, started_at, finished_at, exit_code, result, log) "
            "VALUES (?, ?, ?, ?, 'running', 0, 'Iniciando ejecución...', ?, 0, 0, '{}', '')",
            (task_id, action, params_str, user, now)
        )


def update_task_progress(task_id, progress=None, phase=None, append_log=None):
    with _db_lock, _db_session() as conn:
        row = conn.execute("SELECT progress, phase, log FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if not row:
            return
        new_prog = row["progress"] if progress is None else int(progress)
        new_phase = row["phase"] if phase is None else str(phase)
        current_log = row["log"] or ""
        new_log = current_log + (str(append_log) if append_log else "")
        if len(new_log) > 200000:
            new_log = "... [Log truncado] ...\n" + new_log[-180000:]
        conn.execute(
            "UPDATE tasks SET progress = ?, phase = ?, log = ? WHERE task_id = ?",
            (new_prog, new_phase, new_log, task_id)
        )


def finish_task(task_id, status, exit_code, result_dict, append_log=None):
    now = time.time()
    result_str = json.dumps(result_dict if isinstance(result_dict, dict) else {"raw": str(result_dict)})
    with _db_lock, _db_session() as conn:
        row = conn.execute("SELECT log FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        current_log = (row["log"] if row else "") or ""
        final_log = current_log + (str(append_log) if append_log else "")
        if len(final_log) > 200000:
            final_log = "... [Log truncado] ...\n" + final_log[-180000:]
        conn.execute(
            "UPDATE tasks SET status = ?, progress = 100, phase = ?, finished_at = ?, exit_code = ?, result = ?, log = ? WHERE task_id = ?",
            (status, "Completado" if status == "completed" else "Fallido", now, exit_code, result_str, final_log, task_id)
        )


def get_task(task_id):
    with _db_lock, _db_session() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if not row:
            return None
        return dict(row)


def list_tasks(limit=30):
    with _db_lock, _db_session() as conn:
        rows = conn.execute("SELECT task_id, action, user, status, progress, phase, started_at, finished_at, exit_code FROM tasks ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


# --- AUDIT LOGS ---
def log_audit_event(user, action, params, success, message, client_ip):
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    sanitized_params = sanitize_audit_params(params)
    with _db_lock, _db_session() as conn:
        conn.execute(
            "INSERT INTO audit_events (timestamp, user, action, params, success, message, client_ip) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (now_iso, user or "admin", action, json.dumps(sanitized_params), 1 if success else 0, str(message), client_ip or "local")
        )


def list_audit_events(limit=100):
    with _db_lock, _db_session() as conn:
        rows = conn.execute("SELECT * FROM audit_events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


# Auto-initialize on import
init_db()
