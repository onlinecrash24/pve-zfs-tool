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

import logging
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger(__name__)

_TASKS: Dict[str, Dict[str, Any]] = {}
_TASKS_LOCK = threading.Lock()
_TASK_TTL_SECONDS = 6 * 3600  # finished tasks linger for six hours

# Every task is a thread, and some routes start one per click. A ceiling
# keeps a burst of requests (a stuck button, a script, a forged request)
# from stacking up threads and SSH sessions without bound.
MAX_RUNNING_TASKS = 32


class TaskLimitError(RuntimeError):
    """Raised by start_task when MAX_RUNNING_TASKS are already in flight."""


def _gc_tasks() -> None:
    cutoff = time.time() - _TASK_TTL_SECONDS
    with _TASKS_LOCK:
        for tid in [k for k, v in _TASKS.items()
                    if v.get("finished_at") and v["finished_at"] < cutoff]:
            _TASKS.pop(tid, None)


def start_task(name: str, fn: Callable, *args, prefix: str = "task",
               dedupe_key: Optional[str] = None, **kwargs) -> str:
    """Run ``fn(progress_cb, *args, **kwargs)`` in a background thread.

    ``progress_cb(message, **fields)`` lets the worker push status updates to
    the registry. Returns the task id the caller hands to the client.

    ``prefix`` is used in the thread name only (debugging aid).
    ``dedupe_key`` is stored on the record (see ``start_or_join``).
    Raises TaskLimitError when MAX_RUNNING_TASKS are already running.
    """
    return _start(name, fn, args, kwargs, prefix, dedupe_key, join=False)[0]


def start_or_join(name: str, fn: Callable, *args, prefix: str = "task",
                  dedupe_key: str, **kwargs):
    """Like start_task, but if a task with the same ``dedupe_key`` is still
    running, return ``(its_id, True)`` instead of starting another one.
    Lookup, ceiling check and insert happen under one lock acquisition.
    Returns ``(task_id, joined)``."""
    return _start(name, fn, args, kwargs, prefix, dedupe_key, join=True)


def _start(name, fn, args, kwargs, prefix, dedupe_key, join):
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
        "dedupe_key": dedupe_key,
    }
    with _TASKS_LOCK:
        running = [r for r in _TASKS.values() if r.get("status") == "running"]
        if join and dedupe_key:
            for r in running:
                if r.get("dedupe_key") == dedupe_key:
                    return r["id"], True
        if len(running) >= MAX_RUNNING_TASKS:
            raise TaskLimitError(f"{len(running)} tasks are already running -- try "
                                 "again once some have finished")
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
            # The traceback goes to the server log; the client gets the
            # message. Frames, paths and local names are for the operator
            # reading the log, not for the polling response.
            log.exception("task %s (%s) failed", name, tid)
            with _TASKS_LOCK:
                r = _TASKS.get(tid)
                if r:
                    r["status"] = "error"
                    r["error"] = str(e)[:500] or e.__class__.__name__
                    r["finished_at"] = time.time()

    threading.Thread(target=_runner, daemon=True, name=f"{prefix}-{name}-{tid}").start()
    return tid, False


def running_tasks(name_prefix: str = "") -> List[Dict[str, Any]]:
    """Tasks currently in flight, optionally filtered by name prefix.

    Lets a destructive action refuse while a related job is still running --
    e.g. deleting migration snapshots mid-transfer would destroy the very
    snapshot the incremental send is based on.
    """
    with _TASKS_LOCK:
        return [dict(r) for r in _TASKS.values()
                if r.get("status") == "running"
                and str(r.get("name", "")).startswith(name_prefix)]


def get_task(task_id: str) -> Optional[Dict[str, Any]]:
    """Return a shallow copy of the task record, or ``None`` if unknown."""
    with _TASKS_LOCK:
        r = _TASKS.get(task_id)
        if not r:
            return None
        return dict(r)
