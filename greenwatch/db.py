import json
import sqlite3
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runs (
 id INTEGER PRIMARY KEY, started_at REAL NOT NULL, finished_at REAL,
 trigger TEXT NOT NULL, status TEXT NOT NULL, product_count INTEGER DEFAULT 0,
 apac_count INTEGER DEFAULT 0, match_count INTEGER DEFAULT 0, error TEXT
);
CREATE TABLE IF NOT EXISTS products (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, location TEXT NOT NULL, apac INTEGER NOT NULL,
 price_cents INTEGER NOT NULL, currency TEXT NOT NULL, billing TEXT NOT NULL,
 stock INTEGER NOT NULL, orderable INTEGER NOT NULL, url TEXT NOT NULL,
 specs TEXT NOT NULL, present INTEGER NOT NULL DEFAULT 1, last_seen_at REAL NOT NULL,
 eligible INTEGER NOT NULL DEFAULT 0, active_event_id INTEGER
);
CREATE TABLE IF NOT EXISTS observations (
 id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
 product_id TEXT NOT NULL, observed_at REAL NOT NULL, stock INTEGER NOT NULL,
 price_cents INTEGER NOT NULL, eligible INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS observations_time ON observations(observed_at);
CREATE INDEX IF NOT EXISTS observations_product ON observations(product_id, observed_at);
CREATE TABLE IF NOT EXISTS notifications (
 id INTEGER PRIMARY KEY, product_id TEXT NOT NULL, created_at REAL NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
 next_attempt_at REAL NOT NULL, sent_at REAL, error TEXT, body TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS notifications_due ON notifications(status, next_attempt_at);
"""


class Database:
    def __init__(self, path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            if db.execute("PRAGMA auto_vacuum").fetchone()[0] != 2:
                db.execute("PRAGMA auto_vacuum=INCREMENTAL")
                # SQLite requires a rebuild to change an existing database from NONE.
                db.execute("VACUUM")
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)
            db.execute("UPDATE runs SET status='error', error='Interrupted by restart', finished_at=started_at WHERE status='running'")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=15000")
        try:
            with db:
                yield db
        finally:
            db.close()

    def get_meta(self, key, default=None):
        with self.connect() as db:
            row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_meta(self, key, value):
        with self.connect() as db:
            self.put_meta(db, key, value)

    @staticmethod
    def put_meta(db, key, value):
        db.execute("INSERT INTO meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))
