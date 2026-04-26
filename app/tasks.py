"""Generic in-memory async task registry.

Long-running operations (replication runs, AI report generation, etc.)
regularly outlast a normal HTTP request. Instead of blocking the request
until the worker finishes — which freezes the UI on browsers/proxies with a
short timeout and queues every other API call behind it on single-worker
Flask deployments — we start a daemon thread that publishes progress into
this registry and return a task id immediately. The client polls
``GET /api/tasks/<id>`` at its leisure.

The registry is process-local. With multi-worker Gunicorn each worker has
its own copy; that is acceptable here because each task lives in exactly
one worker (the one that received the start request) and the same client
session keeps its sticky id.
"""

from __future__ import annotations

import threading
import time
import traceback
import uuid
from typing import Any, Callable, Dict, Optional

_TASKS: Dict[str, Dict[str, Any]] = {}
_TASKS_LOCK = threading.Lock()
_TASK_TTL_SECONDS = 6 * 3600  # finished tasks linger for six hours


def _gc_tasks() -> None:
    cutoff = time.time() - _TASK_TTL_SECONDS
    with _TASKS_LOCK:
        for tid in [k for k, v in _TASKS.items()
                    if v.get("finished_at") and v["finished_at"] < cutoff]:
            _TASKS.pop(tid, None)


def start_task(name: str, fn: Callable, *args, prefix: str = "task", **kwargs) -> str:
    """Run ``fn(progress_cb, *args, **kwargs)`` in a background thread.

    ``progress_cb(message, **fields)`` lets the worker push status updates to
    the registry. Returns the task id the caller hands to the client.

    ``prefix`` is used in the thread name only (debugging aid).
    """
    _gc_tasks()
    tid = uuid.uuid4().hex[:12]
    record: Dict[str, Any] = {
        "id": tid,
        "name": name,
        "status": "running",
        "progress": "",
        "started_at": time.time(),
        "finished_at": None,
        "result": None,
        "error": "",
        "log": [],
    }
    with _TASKS_LOCK:
        _TASKS[tid] = record

    def _progress(msg: str, **fields):
        with _TASKS_LOCK:
            r = _TASKS.get(tid)
            if not r:
                return
            r["progress"] = msg
            r["log"].append({"t": time.time(), "msg": msg, **fields})
            # Cap log so a runaway task doesn't eat memory.
            if len(r["log"]) > 500:
                r["log"] = r["log"][-500:]

    def _runner():
        try:
            result = fn(_progress, *args, **kwargs)
            with _TASKS_LOCK:
                r = _TASKS.get(tid)
                if r:
                    r["status"] = "done"
                    r["result"] = result
                    r["finished_at"] = time.time()
        except Exception as e:
            with _TASKS_LOCK:
                r = _TASKS.get(tid)
                if r:
                    r["status"] = "error"
                    r["error"] = f"{e}\n{traceback.format_exc()[-1500:]}"
                    r["finished_at"] = time.time()

    threading.Thread(target=_runner, daemon=True, name=f"{prefix}-{name}-{tid}").start()
    return tid


def get_task(task_id: str) -> Optional[Dict[str, Any]]:
    """Return a shallow copy of the task record, or ``None`` if unknown."""
    with _TASKS_LOCK:
        r = _TASKS.get(task_id)
        if not r:
            return None
        return dict(r)
