"""SQLite settings, shared by the web UI and external API."""
import contextlib
import hmac
import os
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SETTINGS_COLUMNS = {
    "notify_email": "TEXT",
    "smtp_host": "TEXT NOT NULL DEFAULT 'localhost'",
    "smtp_port": "INTEGER NOT NULL DEFAULT 25",
    "smtp_username": "TEXT",
    "smtp_password": "TEXT",
    "smtp_from": "TEXT",
    "smtp_use_tls": "INTEGER NOT NULL DEFAULT 0",
    "telegram_bot_token": "TEXT",
    "telegram_chat_id": "TEXT",
    "brand_api_key": "TEXT",
    "api_key": "TEXT",
    "api_key_created_at": "TEXT",
}


@contextlib.contextmanager
def _connect():
    path = Path(os.environ.get("DATABASE_PATH") or "data/site.db")
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with _connect() as conn:
        columns = ", ".join(f"{name} {ddl}" for name, ddl in SETTINGS_COLUMNS.items())
        conn.execute("""CREATE TABLE IF NOT EXISTS domains (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            brand_id INTEGER NOT NULL,
            engine TEXT NOT NULL,
            domain TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'new',
            parent_id INTEGER REFERENCES domains(id),
            created_at TEXT NOT NULL,
            registered_at TEXT,
            UNIQUE(brand_id, engine, domain)
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS domains_list ON domains (brand_id, engine)")
        conn.execute(f"CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK (id = 1), {columns})")
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(settings)")}
        for name, ddl in SETTINGS_COLUMNS.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE settings ADD COLUMN {name} {ddl}")
        conn.execute("INSERT OR IGNORE INTO settings (id) VALUES (1)")


def get_settings() -> sqlite3.Row:
    with _connect() as conn:
        conn.execute("INSERT OR IGNORE INTO settings (id) VALUES (1)")
        return conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()


def update_settings(**fields: object) -> None:
    unknown = fields.keys() - SETTINGS_COLUMNS.keys()
    if unknown:
        raise ValueError(f"Unknown settings: {', '.join(sorted(unknown))}")
    if not fields:
        return
    with _connect() as conn:
        conn.execute("INSERT OR IGNORE INTO settings (id) VALUES (1)")
        assignments = ", ".join(f"{name} = ?" for name in fields)
        conn.execute(f"UPDATE settings SET {assignments} WHERE id = 1", tuple(fields.values()))


# Open storage is intentional: the authenticated settings page must let the
# administrator view and copy the current key later. A leaked DB/backup also
# exposes API access, so database backups must be protected like credentials.
def generate_api_key() -> str:
    key = "site_" + secrets.token_urlsafe(32)
    update_settings(api_key=key, api_key_created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    return key


def revoke_api_key() -> None:
    update_settings(api_key=None, api_key_created_at=None)


def verify_api_key(key: str) -> bool:
    stored = get_settings()["api_key"]
    # Bytes comparison accepts non-ASCII request headers without TypeError.
    return bool(key and stored and hmac.compare_digest(stored.encode(), key.encode()))
