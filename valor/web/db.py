"""SQLite storage for the web service (WAL mode, schema versioned with PRAGMA user_version)."""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

MIGRATIONS: list[str] = [
    # 1: accounts, sessions, audit, settings, notifications
    """
    CREATE TABLE users (
        id INTEGER PRIMARY KEY,
        username TEXT NOT NULL UNIQUE COLLATE NOCASE,
        display_name TEXT NOT NULL DEFAULT '',
        email TEXT NOT NULL DEFAULT '',
        role TEXT NOT NULL CHECK (role IN ('admin', 'operator', 'viewer')),
        source TEXT NOT NULL DEFAULT 'local' CHECK (source IN ('local', 'ldap')),
        password_hash TEXT,
        totp_secret BLOB,
        totp_last_step INTEGER NOT NULL DEFAULT 0,
        failed_logins INTEGER NOT NULL DEFAULT 0,
        locked_until REAL NOT NULL DEFAULT 0,
        disabled INTEGER NOT NULL DEFAULT 0,
        must_change_password INTEGER NOT NULL DEFAULT 0,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        last_login_at REAL
    );
    CREATE TABLE recovery_codes (
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        code_hash TEXT NOT NULL,
        used_at REAL,
        PRIMARY KEY (user_id, code_hash)
    );
    CREATE TABLE sessions (
        id_hash TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        stage TEXT NOT NULL CHECK (stage IN ('mfa', 'enroll', 'full')),
        csrf TEXT NOT NULL,
        pending_totp BLOB,
        created_at REAL NOT NULL,
        last_seen REAL NOT NULL,
        ip TEXT NOT NULL DEFAULT '',
        user_agent TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX sessions_user ON sessions(user_id);
    CREATE TABLE audit (
        id INTEGER PRIMARY KEY,
        ts REAL NOT NULL,
        username TEXT NOT NULL DEFAULT '',
        ip TEXT NOT NULL DEFAULT '',
        action TEXT NOT NULL,
        target TEXT NOT NULL DEFAULT '',
        outcome TEXT NOT NULL,
        detail TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX audit_ts ON audit(ts);
    CREATE TABLE settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at REAL NOT NULL,
        updated_by TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE notifications (
        id INTEGER PRIMARY KEY,
        ts REAL NOT NULL,
        level TEXT NOT NULL CHECK (level IN ('info', 'success', 'warning', 'error')),
        event TEXT NOT NULL,
        title TEXT NOT NULL,
        body TEXT NOT NULL DEFAULT '',
        link TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE notification_reads (
        notification_id INTEGER NOT NULL REFERENCES notifications(id) ON DELETE CASCADE,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        PRIMARY KEY (notification_id, user_id)
    );
    """,
    # 2: AI chat builder usage (tokens per user and request)
    """
    CREATE TABLE ai_usage (
        id INTEGER PRIMARY KEY,
        ts REAL NOT NULL,
        username TEXT NOT NULL,
        provider TEXT NOT NULL,
        model TEXT NOT NULL,
        input_tokens INTEGER NOT NULL DEFAULT 0,
        output_tokens INTEGER NOT NULL DEFAULT 0,
        ok INTEGER NOT NULL DEFAULT 1
    );
    CREATE INDEX ai_usage_user_ts ON ai_usage(username, ts);
    """,
]


def connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=15, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 15000")
    return conn


def migrate(path: str | Path) -> int:
    """Create or upgrade the schema. Returns the schema version."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        for i, script in enumerate(MIGRATIONS[version:], start=version + 1):
            conn.executescript("BEGIN;" + script + f"PRAGMA user_version = {i}; COMMIT;")
        return conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


@contextmanager
def transaction(conn: sqlite3.Connection):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


# ---------------------------------------------------------------------- settings
def get_setting(conn: sqlite3.Connection, key: str, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def put_setting(conn: sqlite3.Connection, key: str, value, by: str = "") -> None:
    conn.execute("INSERT INTO settings(key, value, updated_at, updated_by) VALUES (?, ?, ?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at, "
                 "updated_by = excluded.updated_by", (key, json.dumps(value), time.time(), by))


# ---------------------------------------------------------------------- audit
def audit(conn: sqlite3.Connection, action: str, outcome: str, *, username: str = "", ip: str = "",
          target: str = "", detail: dict | str | None = None) -> None:
    if isinstance(detail, dict):
        detail = json.dumps(detail, default=str, sort_keys=True)
    conn.execute("INSERT INTO audit(ts, username, ip, action, target, outcome, detail) VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (time.time(), username or "", ip or "", action, target or "", outcome, (detail or "")[:4000]))
