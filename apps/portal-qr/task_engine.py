#!/usr/bin/env python3
"""
Enterprise NAS Control Center — Asynchronous Task Runner Engine (Job Queue)
Executes heavy appliance operations in background daemon threads without blocking HTTP.
Streams logs, tracks progress phases, and records outcomes in persistent storage.
"""
import sys
import os
import time
import json
import secrets
import threading
import queue
import traceback

import portal_state

# Identify heavy or long-running operations that should be handled asynchronously
ASYNC_ACTIONS = {
    "zfs.pool.scrub_start",
    "occ.files_scan",
    "backup.r2.trigger",
    "dr.run_drill",
    "postgres.vacuum",
    "services.cascade_restart",
    "storage.smart.test",
    "users.import_csv",
    "system.drop_caches",
}

class TaskEngine:
    def __init__(self, max_workers=2):
        self.queue = queue.Queue()
        self.lock = threading.Lock()
        self.cancel_flags = set()
        self.handlers_catalog = {}
        
        # Start background worker threads
        self.workers = []
        for i in range(max_workers):
            t = threading.Thread(target=self._worker_loop, daemon=True, name=f"TaskWorker-{i}")
            t.start()
            self.workers.append(t)

    def register_handlers(self, handlers):
        """Registers the action handlers catalog from modules_catalog."""
        with self.lock:
            self.handlers_catalog.update(handlers)

    def is_action_async(self, action):
        """Checks if an action is naturally asynchronous or long-running."""
        return action in ASYNC_ACTIONS

    def submit_task(self, action, params, user="admin", client_ip="local"):
        """Submits an action to the background queue and returns the unique task ID."""
        task_id = f"task_{secrets.token_hex(6)}"
        portal_state.create_task(task_id, action, params, user)
        portal_state.log_audit_event(user, action, params, True, f"Tarea asíncrona '{action}' encolada con ID {task_id}", client_ip)
        self.queue.put((task_id, action, params, user, client_ip))
        return task_id

    def cancel_task(self, task_id):
        """Signals cancellation for a running task."""
        with self.lock:
            self.cancel_flags.add(task_id)
        task = portal_state.get_task(task_id)
        if task and task.get("status") == "running":
            portal_state.finish_task(task_id, "cancelled", 130, {"status": "Tarea cancelada por el operador."}, "\n[!] Operación cancelada por el usuario.")
            return True
        return False

    def is_cancelled(self, task_id):
        with self.lock:
            return task_id in self.cancel_flags

    def _worker_loop(self):
        while True:
            task_id, action, params, user, client_ip = self.queue.get()
            try:
                self._run_task(task_id, action, params, user, client_ip)
            except Exception as e:
                err_msg = f"Fallo no capturado en tarea {task_id}: {str(e)}\n{traceback.format_exc()}"
                portal_state.finish_task(task_id, "failed", 1, {"error": str(e)}, err_msg)
                portal_state.log_audit_event(user, action, params, False, f"Fallo fatal en tarea {task_id}: {str(e)}", client_ip)
            finally:
                with self.lock:
                    self.cancel_flags.discard(task_id)
                self.queue.task_done()

    def _run_task(self, task_id, action, params, user, client_ip):
        portal_state.update_task_progress(task_id, progress=5, phase="Ejecutando...", append_log=f"[{time.strftime('%H:%M:%S')}] Iniciando acción: {action}\n")

        handler = self.handlers_catalog.get(action)
        if not handler:
            portal_state.finish_task(task_id, "failed", 1, {"error": f"Acción '{action}' no encontrada en el catálogo."}, f"Error: Acción '{action}' no soportada.\n")
            portal_state.log_audit_event(user, action, params, False, "Acción desconocida", client_ip)
            return

        # Progress tracking callback wrapper for handlers that support it
        def progress_cb(pct, phase_str, log_line=None):
            if self.is_cancelled(task_id):
                raise InterruptedError("Operación cancelada")
            portal_state.update_task_progress(task_id, progress=pct, phase=phase_str, append_log=(log_line + "\n") if log_line else None)

        try:
            # Check if handler accepts progress_cb kwarg
            import inspect
            sig = inspect.signature(handler)
            if "progress_cb" in sig.parameters:
                ok, result = handler(params, progress_cb=progress_cb)
            else:
                ok, result = handler(params)

            if self.is_cancelled(task_id):
                return

            status = "completed" if ok else "failed"
            exit_code = 0 if ok else 1
            log_suffix = f"\n[{time.strftime('%H:%M:%S')}] Tarea finalizada con estado: {status.upper()}.\n"
            if isinstance(result, dict) and "output" in result:
                log_suffix = f"\nSalida del comando:\n{result['output']}\n" + log_suffix

            portal_state.finish_task(task_id, status, exit_code, result, log_suffix)
            portal_state.log_audit_event(user, action, params, ok, f"Tarea {task_id} finalizada ({status})", client_ip)

        except InterruptedError:
            portal_state.finish_task(task_id, "cancelled", 130, {"status": "Cancelado"}, "\n[!] Operación interrumpida por el operador.\n")
        except Exception as e:
            err_details = f"Excepción durante la ejecución: {str(e)}\n{traceback.format_exc()}"
            portal_state.finish_task(task_id, "failed", 1, {"error": str(e)}, err_details)
            portal_state.log_audit_event(user, action, params, False, f"Error en tarea {task_id}: {str(e)}", client_ip)

# Global singleton
GLOBAL_TASK_ENGINE = TaskEngine(max_workers=2)
