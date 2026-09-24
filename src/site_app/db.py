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
        conn.execute('BEGIN IMMEDIATE')
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
        conn.execute("""CREATE TABLE IF NOT EXISTS drops (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            domain TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            registered_at TEXT
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS drop_brands (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            drop_id INTEGER NOT NULL REFERENCES drops(id),
            brand_id INTEGER NOT NULL,
            brand_name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'new',
            parent_id INTEGER REFERENCES drop_brands(id),
            assigned_at TEXT NOT NULL,
            removed_at TEXT
        )""")
        conn.execute('CREATE INDEX IF NOT EXISTS drop_brands_drop ON drop_brands (drop_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS drop_brands_brand ON drop_brands (brand_id, removed_at)')
        conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS drop_brands_active_unique '
                     'ON drop_brands (drop_id, brand_id) WHERE removed_at IS NULL')
        _migrate_drops(conn)
        conn.execute(f"CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK (id = 1), {columns})")
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(settings)")}
        for name, ddl in SETTINGS_COLUMNS.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE settings ADD COLUMN {name} {ddl}")
        conn.execute("INSERT OR IGNORE INTO settings (id) VALUES (1)")


def _migrate_drops(conn):
    """Вызывается внутри транзакции init_db: схема и история меняются атомарно."""
    if 'brand_id' not in {r['name'] for r in conn.execute('PRAGMA table_info(drops)')}:
        return
    has_history = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='drop_brand_history'").fetchone()
    history = list(conn.execute('SELECT * FROM drop_brand_history ORDER BY id')) if has_history else []
    opened = {(r['drop_id'], r['brand_id']): r for r in history if r['removed_at'] is None}
    active = list(conn.execute('SELECT * FROM drops WHERE brand_id IS NOT NULL'))
    active_brands = {r['id']: r['brand_id'] for r in active}
    now = datetime.now(timezone.utc).isoformat()
    new_ids = {}
    used_history_ids = set()
    for row in active:
        # Имя и дата чужого бренда не относятся к текущему назначению.
        old = opened.get((row['id'], row['brand_id']))
        if old is not None:
            used_history_ids.add(old['id'])
        cursor = conn.execute(
            'INSERT INTO drop_brands (drop_id, brand_id, brand_name, status, assigned_at) '
            'VALUES (?, ?, ?, ?, ?)',
            (row['id'], row['brand_id'], old['brand_name'] if old else f"Бренд #{row['brand_id']}",
             row['status'], old['assigned_at'] if old else row['created_at']))
        new_ids[row['id']] = cursor.lastrowid
    # Старые parent_id указывали на домены, новые — на конкретный период в бренде.
    for row in active:
        # Даже повреждённая старая база не должна создавать клей между брендами.
        parent = (new_ids.get(row['parent_id'])
                  if active_brands.get(row['parent_id']) == row['brand_id'] else None)
        if parent is not None:
            conn.execute('UPDATE drop_brands SET parent_id=? WHERE id=?', (parent, new_ids[row['id']]))
        elif row['status'] == 'glued':
            conn.execute("UPDATE drop_brands SET status='used' WHERE id=?", (new_ids[row['id']],))
    conn.executemany(
        "INSERT INTO drop_brands (drop_id, brand_id, brand_name, status, assigned_at, removed_at) "
        "VALUES (?, ?, ?, 'used', ?, ?)",
        # Всё, что не стало источником активной строки, сохраняем закрытой историей.
        # У открытых записей без назначения или вытесненных дублей дата закрытия — сейчас.
        [(r['drop_id'], r['brand_id'], r['brand_name'], r['assigned_at'],
          r['removed_at'] if r['removed_at'] is not None else now)
         for r in history if r['id'] not in used_history_ids])
    # Пересоздание совместимо со SQLite без ALTER TABLE DROP COLUMN.
    conn.execute('CREATE TABLE drops_new (id INTEGER PRIMARY KEY AUTOINCREMENT, '
                 'domain TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, registered_at TEXT)')
    conn.execute('INSERT INTO drops_new (id, domain, created_at, registered_at) '
                 'SELECT id, domain, created_at, registered_at FROM drops')
    conn.execute('DROP TABLE drops')
    conn.execute('ALTER TABLE drops_new RENAME TO drops')
    # sqlite_sequence не имеет UNIQUE(name), поэтому UPSERT здесь неприменим.
    conn.execute("DELETE FROM sqlite_sequence WHERE name='drops'")
    conn.execute("INSERT INTO sqlite_sequence (name, seq) SELECT 'drops', COALESCE(MAX(id), 0) FROM drops")
    conn.execute('DROP TABLE IF EXISTS drop_brand_history')


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
