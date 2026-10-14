"""מסד הנתונים (SQLite). קובץ אחד, הרשאות 600, מצב WAL.

גרסאות הסכמה מנוהלות ב-PRAGMA user_version: init() מריץ את ההגירות החסרות לפי הסדר,
כל אחת בטרנזקציה. מסד קיים מהגרסה הקודמת מתעדכן בעלייה בלי לאבד מכשירים.
"""
from __future__ import annotations

import os
import queue
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

# גרסה 0 — הסכמה המקורית (נשמרת כמו שהיא; ההגירות בונות עליה)
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

# גרסה 1 — לקוחות, בקשות הצטרפות/שינוי, יומן פעולות, ומכשיר "ממתין לאישור" עם מדיניות חסימות
_V1 = """
CREATE TABLE customers (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    phone       TEXT NOT NULL DEFAULT '',
    email       TEXT NOT NULL DEFAULT '',
    notes       TEXT NOT NULL DEFAULT '',
    payment     TEXT NOT NULL DEFAULT 'unpaid' CHECK (payment IN ('unpaid', 'paid', 'free')),
    paid_until  INTEGER,
    created_at  INTEGER NOT NULL
);
CREATE INDEX customers_phone ON customers(phone);

CREATE TABLE devices_v1 (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    label         TEXT NOT NULL DEFAULT '',
    platform      TEXT NOT NULL DEFAULT '',
    app_version   TEXT NOT NULL DEFAULT '',
    public_key    TEXT UNIQUE NOT NULL,
    preshared_key TEXT NOT NULL DEFAULT '',
    vpn_ip        TEXT UNIQUE,                     -- NULL עד שהמנהל מאשר
    level         INTEGER NOT NULL,
    status        TEXT NOT NULL DEFAULT 'active'
                  CHECK (status IN ('pending', 'active', 'disabled', 'rejected')),
    token_hash    TEXT UNIQUE,
    code_id       INTEGER REFERENCES codes(id) ON DELETE SET NULL,
    customer_id   INTEGER REFERENCES customers(id) ON DELETE SET NULL,
    policy        TEXT NOT NULL DEFAULT '{}',      -- JSON: חסימות שהמנהל הפעיל (catalog.DeviceSettings)
    apps          TEXT,                            -- JSON: האפליקציות שמותקנות בטלפון (דיווח מהאפליקציה)
    apps_at       INTEGER,
    guard         TEXT,                            -- JSON: מצב ההגנה בטלפון (נגישות, מנהל מכשיר...)
    guard_at      INTEGER,
    legacy        INTEGER NOT NULL DEFAULT 0,
    created_at    INTEGER NOT NULL,
    approved_at   INTEGER,
    last_seen_at  INTEGER
);
INSERT INTO devices_v1 (id, name, label, platform, app_version, public_key, preshared_key, vpn_ip, level,
                        status, token_hash, code_id, legacy, created_at, approved_at, last_seen_at)
    SELECT id, name, label, platform, app_version, public_key, preshared_key, vpn_ip, level,
           status, token_hash, code_id, legacy, created_at, created_at, last_seen_at FROM devices;
DROP TABLE devices;
ALTER TABLE devices_v1 RENAME TO devices;
CREATE INDEX devices_customer ON devices(customer_id);

CREATE TABLE requests (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL CHECK (kind IN ('join', 'change')),
    device_id   INTEGER REFERENCES devices(id) ON DELETE CASCADE,
    customer_id INTEGER REFERENCES customers(id) ON DELETE SET NULL,
    level       INTEGER,                           -- הרמה שהלקוח ביקש
    options     TEXT NOT NULL DEFAULT '[]',        -- JSON: אפשרויות שהלקוח סימן
    note        TEXT NOT NULL DEFAULT '',          -- מה עוד הלקוח רוצה (טקסט חופשי)
    status      TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'approved', 'rejected', 'cancelled')),
    admin_note  TEXT NOT NULL DEFAULT '',
    created_at  INTEGER NOT NULL,
    resolved_at INTEGER
);
CREATE INDEX requests_status ON requests(status, created_at);
CREATE INDEX requests_device ON requests(device_id);

CREATE TABLE audit (
    id     INTEGER PRIMARY KEY,
    ts     INTEGER NOT NULL,
    actor  TEXT NOT NULL,
    action TEXT NOT NULL,
    target TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT ''
);
"""

# גרסה 2 — גרסאות מדיניות ואישור מהטלפון, דיווח תקופתי (heartbeat), יומן אירועי מכשיר
_V2 = """
ALTER TABLE devices ADD COLUMN policy_version INTEGER NOT NULL DEFAULT 1;   -- עולה בכל שינוי במה שהטלפון אוכף
ALTER TABLE devices ADD COLUMN policy_updated_at INTEGER;
ALTER TABLE devices ADD COLUMN policy_checksum TEXT NOT NULL DEFAULT '';
ALTER TABLE devices ADD COLUMN policy_revision INTEGER NOT NULL DEFAULT 0;  -- מונה גלובלי ברגע השינוי
ALTER TABLE devices ADD COLUMN device_policy_version INTEGER;               -- מה שהטלפון אישר שהחיל
ALTER TABLE devices ADD COLUMN policy_ack_at INTEGER;
ALTER TABLE devices ADD COLUMN heartbeat TEXT;                              -- JSON: הדיווח האחרון מהטלפון
ALTER TABLE devices ADD COLUMN heartbeat_at INTEGER;
ALTER TABLE devices ADD COLUMN managed_at INTEGER;                          -- מתי דווח לראשונה Device Owner
CREATE INDEX devices_heartbeat ON devices(heartbeat_at);

CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
INSERT INTO meta (key, value) VALUES ('policy_revision', '0');

CREATE TABLE device_log (
    id        INTEGER PRIMARY KEY,
    device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    ts        INTEGER NOT NULL,
    kind      TEXT NOT NULL,      -- vpn_down / vpn_up / accessibility_off / accessibility_on / management_removed ...
    detail    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX device_log_device ON device_log(device_id, ts);
CREATE INDEX device_log_ts ON device_log(ts);
"""

# גרסה 3 — השהיית הגנה מאושרת (pause) והסרת ניהול יזומה, להעברת מכשיר ול"מצב תחזוקה"
_V3 = """
ALTER TABLE devices ADD COLUMN pause_until INTEGER;          -- עד מתי ההגנה מושהית (NULL = פעילה)
ALTER TABLE devices ADD COLUMN pause_reason TEXT NOT NULL DEFAULT '';
ALTER TABLE devices ADD COLUMN pause_by TEXT NOT NULL DEFAULT '';
ALTER TABLE devices ADD COLUMN pause_nonce TEXT NOT NULL DEFAULT '';
ALTER TABLE devices ADD COLUMN unmanage_at INTEGER;          -- המנהל ביקש להסיר ניהול; הטלפון יבצע
ALTER TABLE devices ADD COLUMN transfer_token TEXT NOT NULL DEFAULT '';  -- טוקן העברה חד-פעמי (hash)
ALTER TABLE devices ADD COLUMN transfer_expires INTEGER;
CREATE INDEX devices_pause ON devices(pause_until);
"""

# גרסה 4 — פרופילים מוכנים (חבילות מדיניות בשם, שהמנהל מחיל על מכשיר)
_V4 = """
CREATE TABLE profiles (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,
    level      INTEGER NOT NULL DEFAULT 1,
    settings   TEXT NOT NULL DEFAULT '{}',   -- DeviceSettings (JSON)
    builtin    INTEGER NOT NULL DEFAULT 0,   -- פרופיל ברירת מחדל (אפשר לערוך, אי אפשר למחוק)
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
"""

# גרסה 5 — קטלוג אפליקציות מאושרות (המנהל מאשר; במנהל מכשיר אפשר להתקין APK שמתארח אצלנו)
_V5 = """
CREATE TABLE managed_apps (
    id           INTEGER PRIMARY KEY,
    package      TEXT NOT NULL UNIQUE,
    label        TEXT NOT NULL DEFAULT '',
    category     TEXT NOT NULL DEFAULT '',
    note         TEXT NOT NULL DEFAULT '',
    apk_url      TEXT NOT NULL DEFAULT '',   -- ריק = התקנה ידנית מחנות Play; אחרת APK שמתארח אצלנו
    apk_sha256   TEXT NOT NULL DEFAULT '',
    version_code INTEGER,
    required     INTEGER NOT NULL DEFAULT 0, -- מותקן אוטומטית במנהל מכשיר
    enabled      INTEGER NOT NULL DEFAULT 1,
    sort         INTEGER NOT NULL DEFAULT 0,
    created_at   INTEGER NOT NULL,
    updated_at   INTEGER NOT NULL
);
"""

MIGRATIONS: list[str] = [_V1, _V2, _V3, _V4, _V5]
VERSION = len(MIGRATIONS)


def now() -> int:
    return int(time.time())


def connect(path: str, shared: bool = False) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=10, isolation_level=None,    # autocommit; טרנזקציות במפורש
                           check_same_thread=not shared)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")       # ב-WAL: עמיד לקריסת תהליך, מהיר בהרבה
    return conn


class PoolTimeout(sqlite3.OperationalError):
    pass


class Pool:
    """מאגר חיבורים ל-SQLite: עד size חיבורים פתוחים, כל אחד בשימוש של חוט אחד בכל רגע.

    חוסך פתיחה והגדרה (PRAGMA) בכל בקשה, ומגביל כמה חיבורים פתוחים בו-זמנית.
    חיבור שחוזר באמצע טרנזקציה (לא אמור לקרות — transaction() מבטל בשגיאה) מבוטל לפני שימוש חוזר.
    """

    def __init__(self, path: str, size: int = 8, wait_s: float = 10.0):
        self.path = path
        self.size = size
        self.wait_s = wait_s
        self._idle: queue.LifoQueue[sqlite3.Connection] = queue.LifoQueue()
        self._lock = threading.Lock()
        self._created = 0

    def _acquire(self) -> sqlite3.Connection:
        try:
            return self._idle.get_nowait()
        except queue.Empty:
            pass
        with self._lock:
            if self._created < self.size:
                self._created += 1
                try:
                    return connect(self.path, shared=True)
                except BaseException:
                    self._created -= 1
                    raise
        try:
            return self._idle.get(timeout=self.wait_s)
        except queue.Empty as e:
            raise PoolTimeout("database pool exhausted") from e

    def _release(self, conn: sqlite3.Connection) -> None:
        try:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
        except sqlite3.Error:
            conn.close()
            with self._lock:
                self._created -= 1
            return
        self._idle.put(conn)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = self._acquire()
        try:
            yield conn
        finally:
            self._release(conn)

    def close(self) -> None:
        while True:
            try:
                self._idle.get_nowait().close()
            except queue.Empty:
                break
        with self._lock:
            self._created = 0


def init(path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fresh = not os.path.exists(path)
    with closing(connect(path)) as c:
        c.executescript(SCHEMA)
        migrate(c)
    if fresh:
        os.chmod(path, 0o600)


def migrate(c: sqlite3.Connection) -> int:
    """מריץ את ההגירות החסרות. מחזיר את הגרסה הסופית."""
    version = c.execute("PRAGMA user_version").fetchone()[0]
    for target in range(version + 1, VERSION + 1):
        # בניית טבלה מחדש דורשת foreign_keys כבוי — ואי אפשר לשנות אותו בתוך טרנזקציה
        c.execute("PRAGMA foreign_keys=OFF")
        try:
            c.execute("BEGIN IMMEDIATE")
            try:
                for stmt in _statements(MIGRATIONS[target - 1]):
                    c.execute(stmt)
                bad = c.execute("PRAGMA foreign_key_check").fetchall()
                if bad:
                    raise sqlite3.IntegrityError(f"migration {target}: foreign key violations {bad[:3]}")
                c.execute(f"PRAGMA user_version = {target}")
            except BaseException:
                c.execute("ROLLBACK")
                raise
            c.execute("COMMIT")
        finally:
            c.execute("PRAGMA foreign_keys=ON")
    return max(version, VERSION)


def _statements(script: str) -> list[str]:
    """מפצל סקריפט SQL לפקודות (executescript מבצע COMMIT בעצמו — לא מתאים לטרנזקציה)."""
    out, buf = [], []
    for line in script.splitlines():
        code = line.split("--", 1)[0]
        buf.append(code)
        if code.rstrip().endswith(";"):
            stmt = "\n".join(buf).strip()
            if stmt:
                out.append(stmt)
            buf = []
    rest = "\n".join(buf).strip()
    if rest:
        out.append(rest)
    return out


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
