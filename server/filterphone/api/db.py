"""מסד הנתונים (SQLite). קובץ אחד, הרשאות 600, מצב WAL."""
from __future__ import annotations

import os
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS admins (
    id            INTEGER PRIMARY KEY,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at    INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS admin_sessions (
    token_hash TEXT PRIMARY KEY,
    admin_id   INTEGER NOT NULL REFERENCES admins(id) ON DELETE CASCADE,
    expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS codes (
    id         INTEGER PRIMARY KEY,
    code_hash  TEXT UNIQUE NOT NULL,
    hint       TEXT NOT NULL,
    label      TEXT NOT NULL DEFAULT '',
    level      INTEGER NOT NULL,
    uses_left  INTEGER NOT NULL,
    expires_at INTEGER,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS devices (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    label         TEXT NOT NULL DEFAULT '',
    platform      TEXT NOT NULL DEFAULT '',
    app_version   TEXT NOT NULL DEFAULT '',
    public_key    TEXT UNIQUE NOT NULL,
    preshared_key TEXT NOT NULL,
    vpn_ip        TEXT UNIQUE NOT NULL,
    level         INTEGER NOT NULL,
    status        TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
    token_hash    TEXT UNIQUE,
    code_id       INTEGER REFERENCES codes(id) ON DELETE SET NULL,
    legacy        INTEGER NOT NULL DEFAULT 0,
    created_at    INTEGER NOT NULL,
    last_seen_at  INTEGER
);
"""


def now() -> int:
    return int(time.time())


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=10, isolation_level=None)   # autocommit; טרנזקציות במפורש
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init(path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fresh = not os.path.exists(path)
    with closing(connect(path)) as c:
        c.executescript(SCHEMA)
    if fresh:
        os.chmod(path, 0o600)


@contextmanager
def closing(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """BEGIN IMMEDIATE — נועל לכתיבה מההתחלה, כדי ששתי הרשמות לא יקבלו אותה כתובת."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
