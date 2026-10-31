import csv
import hashlib
import io
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager


class QuotaExceeded(ValueError):
    pass


def positive(value):
    if type(value) is not int or not 1 <= value <= 1_000_000_000:
        raise ValueError("Expected an integer from 1 to 1000000000")


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value):
        raise ValueError("Invalid identifier")


def safe_cell(value):
    value = str(value)
    return "'" + value if value.lstrip(" \t\r\n").startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")) else value


class Meter:
    def __init__(self, path="meter.db", clock=time.time, window=86400):
        if str(path) == ":memory:" or type(window) is not int or not 1 <= window <= 2678400:
            raise ValueError("Use a filesystem database and window of 1..2678400 seconds")
        self.path, self.clock, self.window = str(path), clock, window
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS settings(id INTEGER PRIMARY KEY CHECK(id=1),window INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS clients(id TEXT PRIMARY KEY,key_hash TEXT UNIQUE NOT NULL,quota INTEGER NOT NULL,enabled INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS usage(client TEXT NOT NULL REFERENCES clients(id),bucket INTEGER NOT NULL,used INTEGER NOT NULL CHECK(used>=0),PRIMARY KEY(client,bucket));
                CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY,client TEXT NOT NULL REFERENCES clients(id),event_id TEXT NOT NULL,
                    bucket INTEGER NOT NULL,units INTEGER NOT NULL,label TEXT NOT NULL,at REAL NOT NULL,UNIQUE(client,event_id));
            """)
            db.execute("INSERT OR IGNORE INTO settings VALUES(1,?)", (window,))
            if db.execute("SELECT window FROM settings WHERE id=1").fetchone()[0] != window:
                raise ValueError("Database window differs from configured window")

    @contextmanager
    def db(self, write=False):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            if write:
                db.commit()
        except BaseException:
            if write:
                db.rollback()
            raise
        finally:
            db.close()

    def register(self, client, quota):
        identifier(client)
        positive(quota)
        key = secrets.token_urlsafe(32)
        with self.db(True) as db:
            if db.execute("SELECT id FROM clients WHERE id=?", (client,)).fetchone():
                raise ValueError("Client already exists")
            db.execute("INSERT INTO clients VALUES(?,?,?,1)", (client, hashlib.sha256(key.encode()).hexdigest(), quota))
        return key

    def configure(self, client, *, quota=None, enabled=None):
        if quota is not None:
            positive(quota)
        if enabled is not None and type(enabled) is not bool:
            raise ValueError("enabled must be boolean")
        with self.db(True) as db:
            if not db.execute("SELECT id FROM clients WHERE id=?", (client,)).fetchone():
                raise ValueError("Client not found")
            if quota is not None:
                db.execute("UPDATE clients SET quota=? WHERE id=?", (quota, client))
            if enabled is not None:
                db.execute("UPDATE clients SET enabled=? WHERE id=?", (int(enabled), client))

    def authenticate(self, key):
        if not isinstance(key, str) or not 1 <= len(key) <= 256:
            raise PermissionError("Invalid API key")
        with self.db() as db:
            row = db.execute("SELECT id FROM clients WHERE key_hash=? AND enabled=1", (hashlib.sha256(key.encode()).hexdigest(),)).fetchone()
            if not row:
                raise PermissionError("Invalid API key")
            return row["id"]

    def active(self, db, client):
        row = db.execute("SELECT quota FROM clients WHERE id=? AND enabled=1", (client,)).fetchone()
        if not row:
            raise PermissionError("Client missing or disabled")
        return row["quota"]

    def charge(self, client, event_id, units=1, label="request"):
        identifier(event_id)
        positive(units)
        if not isinstance(label, str) or not label or len(label) > 256:
            raise ValueError("Label must be 1..256 characters")
        with self.db(True) as db:
            quota = self.active(db, client)
            old = db.execute("SELECT * FROM events WHERE client=? AND event_id=?", (client, event_id)).fetchone()
            if old:
                if old["units"] != units or old["label"] != label:
                    raise ValueError("Event ID belongs to a different charge")
                return dict(old) | {"duplicate": True}
            bucket = int(self.clock() // self.window)
            row = db.execute("SELECT used FROM usage WHERE client=? AND bucket=?", (client, bucket)).fetchone()
            used = row["used"] if row else 0
            if units > quota - used:
                raise QuotaExceeded("Quota exhausted for the current window")
            db.execute("INSERT INTO usage VALUES(?,?,?) ON CONFLICT(client,bucket) DO UPDATE SET used=excluded.used", (client, bucket, used + units))
            cursor = db.execute("INSERT INTO events(client,event_id,bucket,units,label,at) VALUES(?,?,?,?,?,?)", (client, event_id, bucket, units, label, self.clock()))
            return dict(db.execute("SELECT * FROM events WHERE seq=?", (cursor.lastrowid,)).fetchone()) | {"duplicate": False}

    def status(self, client):
        with self.db() as db:
            db.execute("BEGIN")
            quota = self.active(db, client)
            bucket = int(self.clock() // self.window)
            row = db.execute("SELECT used FROM usage WHERE client=? AND bucket=?", (client, bucket)).fetchone()
            used = row["used"] if row else 0
            return {"client": client, "bucket": bucket, "used": used, "quota": quota, "remaining": max(0, quota - used), "resets_at": (bucket + 1) * self.window}

    def events(self, client, *, limit=100, offset=0):
        if type(limit) is not int or not 1 <= limit <= 1000 or type(offset) is not int or offset < 0:
            raise ValueError("limit must be 1..1000 and offset nonnegative")
        with self.db() as db:
            self.active(db, client)
            return [dict(row) for row in db.execute("SELECT * FROM events WHERE client=? ORDER BY seq LIMIT ? OFFSET ?", (client, limit, offset))]

    def export_csv(self, client, *, offset=0):
        rows = self.events(client, limit=1000, offset=offset)
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["event_id", "bucket", "units", "label", "at"])
        for row in rows:
            writer.writerow([safe_cell(row[key]) for key in ("event_id", "bucket", "units", "label", "at")])
        return output.getvalue()
