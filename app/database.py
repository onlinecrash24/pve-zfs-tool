"""Shared SQLite database for metrics and audit log."""

import os
import sqlite3
import threading
import logging

log = logging.getLogger(__name__)

DATA_DIR = "/app/data"
DB_PATH = os.path.join(DATA_DIR, "pvezfs.db")

_init_lock = threading.Lock()
_initialized = False


def get_conn():
    """Return a new SQLite connection (WAL mode, row dict access)."""
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
    except sqlite3.Error:
        pass
    return conn


def init_db():
    """Create tables if missing. Safe to call multiple times."""
    global _initialized
    with _init_lock:
        if _initialized:
            return
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            conn = get_conn()
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS pool_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp INTEGER NOT NULL,
                    host TEXT NOT NULL,
                    pool TEXT NOT NULL,
                    size_bytes INTEGER,
                    alloc_bytes INTEGER,
                    free_bytes INTEGER,
                    frag_pct REAL,
                    cap_pct REAL,
                    health TEXT,
                    dedup_ratio REAL
                );
                CREATE INDEX IF NOT EXISTS idx_pm_host_pool_ts
                    ON pool_metrics(host, pool, timestamp);
                CREATE INDEX IF NOT EXISTS idx_pm_ts ON pool_metrics(timestamp);

                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp INTEGER NOT NULL,
                    user TEXT,
                    ip TEXT,
                    host TEXT,
                    action TEXT NOT NULL,
                    target TEXT,
                    details TEXT,
                    success INTEGER NOT NULL DEFAULT 1
                );
                CREATE INDEX IF NOT EXISTS idx_al_ts ON audit_log(timestamp);
                CREATE INDEX IF NOT EXISTS idx_al_action ON audit_log(action);
                CREATE INDEX IF NOT EXISTS idx_al_user ON audit_log(user);

                -- Monitor state: tracks last-seen values per (scope, key) so
                -- we can fire state-change notifications (pool health,
                -- host reachability, capacity thresholds, stale auto-snaps)
                -- without spamming on every sample.
                CREATE TABLE IF NOT EXISTS monitor_state (
                    scope TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT,
                    last_alert_ts INTEGER,
                    updated_ts INTEGER NOT NULL,
                    PRIMARY KEY (scope, key)
                );
                """
            )
            conn.commit()
            conn.close()
            _initialized = True
            log.info("Database initialised at %s", DB_PATH)
        except Exception as e:
            log.error("init_db failed: %s", e)
