# *hugs the database* OwO valor/web/db.py is the web service's SQLite database! ^w^
# One file (/var/lib/valor/web.sqlite3) holds accounts, sessions, the audit log, settings, notifications, AI usage
# and saved chats. WAL mode lets readers and a writer work at the same time, and the schema version lives in
# SQLite's own PRAGMA user_version, so upgrades know which migrations still need to run *wags tail*
"""SQLite storage for the web service (WAL mode, schema versioned with PRAGMA user_version)."""

# Lazy type hints UwU
from __future__ import annotations

# json stores settings and audit details; sqlite3 is the database; time stamps rows; contextmanager makes
# `with transaction(conn):`; Path makes the folder >w<
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

# MIGRATIONS: the schema, as an ordered list of SQL scripts. Script N upgrades the database from version N-1 to N.
# NEVER edit an old script (existing databases already ran it); add a new one at the end instead OwO
MIGRATIONS: list[str] = [
    # Migration 1 creates ^w^:
    #   users               accounts: role, local or LDAP, password hash, TOTP secret + last used step, lockout fields
    #   recovery_codes      hashed one-time MFA backup codes (deleted with their user: ON DELETE CASCADE)
    #   sessions            sign-in sessions by token hash: stage (mfa / enroll / full), CSRF token, a TOTP secret being
    #                       set up, times, address and browser
    #   audit               who did what, when, from where, to what, and how it went
    #   settings            key -> JSON value, with who changed it last
    #   notifications (+ notification_reads)  the in-app list and who has read what
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
    # Migration 2: ai_usage, tokens used per AI request (per person, provider and model), for the usage page UwU
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
    # Migration 3: range_chat, the first version of range chats (replaced by migration 4) >w<
    # 3: range chat (question / plan / code), kept per range
    """
    CREATE TABLE range_chat (
        id INTEGER PRIMARY KEY,
        range TEXT NOT NULL,
        ts REAL NOT NULL,
        username TEXT NOT NULL,
        role TEXT NOT NULL,
        mode TEXT NOT NULL,
        content TEXT NOT NULL,
        meta TEXT NOT NULL DEFAULT '{}'
    );
    CREATE INDEX range_chat_range ON range_chat(range, id);
    """,
    # Migration 4: saved chats. `chats` = one conversation (builder or range, owned by a user); `chat_messages` = its
    # messages, with the live feed in `meta` and what the model saw in `model` (so a turn can resume). The old
    # range_chat rows are copied in (one chat per range + user, titled after its first question) and range_chat is
    # dropped OwO
    # 4: saved chats (chat builder and range chats), owned by their user; range_chat moves in (one chat per range+user)
    """
    CREATE TABLE chats (
        id INTEGER PRIMARY KEY,
        owner TEXT NOT NULL COLLATE NOCASE,
        kind TEXT NOT NULL,                 -- builder | range
        range TEXT,
        title TEXT NOT NULL DEFAULT '',
        created REAL NOT NULL,
        updated REAL NOT NULL,
        spec TEXT NOT NULL DEFAULT '',      -- builder: the spec the conversation produced
        state TEXT NOT NULL DEFAULT '{}'    -- {allow_all}
    );
    CREATE INDEX chats_owner ON chats(owner, updated);
    CREATE INDEX chats_range ON chats(range, updated);
    CREATE TABLE chat_messages (
        id INTEGER PRIMARY KEY,
        chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
        ts REAL NOT NULL,
        role TEXT NOT NULL,                 -- user | assistant
        mode TEXT NOT NULL DEFAULT '',
        content TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'done',  -- running | waiting | done | failed | stopped
        version INTEGER NOT NULL DEFAULT 0,
        meta TEXT NOT NULL DEFAULT '{}',    -- the feed (steps), questions, problems, draft, plan, usage
        model TEXT NOT NULL DEFAULT '{}'    -- the turn as the model saw it (system, history, tool calls): resumable
    );
    CREATE INDEX chat_messages_chat ON chat_messages(chat_id, id);
    INSERT INTO chats(owner, kind, range, title, created, updated)
        SELECT username, 'range', range, '', MIN(ts), MAX(ts) FROM range_chat GROUP BY range, username;
    INSERT INTO chat_messages(chat_id, ts, role, mode, content, meta)
        SELECT c.id, r.ts, r.role, r.mode, r.content, r.meta FROM range_chat r
        JOIN chats c ON c.kind = 'range' AND c.range = r.range AND c.owner = r.username ORDER BY r.id;
    UPDATE chats SET title = COALESCE((SELECT substr(content, 1, 80) FROM chat_messages m
        WHERE m.chat_id = chats.id AND m.role = 'user' ORDER BY m.id LIMIT 1), '');
    DROP TABLE range_chat;
    """,
]


# connect: open the database for one request/thread ^w^
#   timeout=15            wait up to 15 s for a lock instead of failing at once
#   isolation_level=None  autocommit: each statement commits by itself unless we start a transaction ourselves
#   check_same_thread     False, because FastAPI may hand a connection to another thread within one request
def connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=15, isolation_level=None, check_same_thread=False)
    # Rows act like dicts (row["username"]) UwU
    conn.row_factory = sqlite3.Row
    # Enforce foreign keys (SQLite leaves them off by default!), and wait up to 15 s when the database is busy >w<
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 15000")
    return conn


# migrate: create or upgrade the schema; returns the version now in place OwO
def migrate(path: str | Path) -> int:
    """Create or upgrade the schema. Returns the schema version."""
    # Make the folder if needed and open the database ^w^
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        # WAL journal mode: readers don't block the writer (it's remembered in the file) UwU
        conn.execute("PRAGMA journal_mode = WAL")
        # The current version, then run every migration after it, each in its own transaction together with setting the
        # new version, so a failed migration leaves the database at the old version >w<
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        for i, script in enumerate(MIGRATIONS[version:], start=version + 1):
            conn.executescript("BEGIN;" + script + f"PRAGMA user_version = {i}; COMMIT;")
        # Report the version we ended at OwO
        return conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


# transaction: `with transaction(conn):` runs its block as ONE transaction ^w^. BEGIN IMMEDIATE takes the write
# lock right away (no surprise "database is locked" halfway). Any error (even Ctrl+C: BaseException) rolls
# everything back; otherwise it commits
@contextmanager
def transaction(conn: sqlite3.Connection):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


# ---- Settings: a tiny key/value store, values as JSON UwU
# ---------------------------------------------------------------------- settings
# get_setting: a setting's value, or `default` when it was never set >w<
def get_setting(conn: sqlite3.Connection, key: str, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


# put_setting: insert or replace a setting (an "upsert": ON CONFLICT ... DO UPDATE), with when and by whom OwO
def put_setting(conn: sqlite3.Connection, key: str, value, by: str = "") -> None:
    conn.execute("INSERT INTO settings(key, value, updated_at, updated_by) VALUES (?, ?, ?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at, "
                 "updated_by = excluded.updated_by", (key, json.dumps(value), time.time(), by))


# ---- The audit log ^w^
# ---------------------------------------------------------------------- audit
# audit: write one audit entry. A dict detail is stored as sorted JSON; the detail is cut to 4000 chars UwU *writes
# it down in the little book*
def audit(conn: sqlite3.Connection, action: str, outcome: str, *, username: str = "", ip: str = "",
          target: str = "", detail: dict | str | None = None) -> None:
    if isinstance(detail, dict):
        detail = json.dumps(detail, default=str, sort_keys=True)
    conn.execute("INSERT INTO audit(ts, username, ip, action, target, outcome, detail) VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (time.time(), username or "", ip or "", action, target or "", outcome, (detail or "")[:4000]))
