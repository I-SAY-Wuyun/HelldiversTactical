"""SQLite storage foundation for the v2 feature set.

This module owns only infrastructure: the connection, PRAGMAs, schema versioning
and thin query helpers. Feature code (notes / events / providers) builds its own
SQL on top of it, so nothing here knows about a specific feature.

Design notes
------------
* One connection for the whole process.  The app has a scheduler thread plus the
  webview bridge thread, so the connection is opened with `check_same_thread=False`
  and every statement goes through :data:`_LOCK`.
* WAL mode: readers never block the writer, and the file survives a hard kill.
* The database lives next to the existing JSON state (`USER_DIR / "hd2.db"`), so a
  user who cannot write there already gets the TEMP fallback from `config`.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

from .config import USER_DIR

LOG = logging.getLogger(__name__)

SCHEMA_VERSION = 1
DB_PATH = USER_DIR / "hd2.db"

_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT
);
CREATE TABLE IF NOT EXISTS notes (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL DEFAULT '',
  body TEXT NOT NULL DEFAULT '',
  tags TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'todo',
  game TEXT,
  due_utc TEXT,
  pinned INTEGER NOT NULL DEFAULT 0,
  reminded_at TEXT,
  created_utc TEXT NOT NULL,
  updated_utc TEXT NOT NULL,
  deleted INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_notes_due ON notes(deleted, due_utc);
CREATE TABLE IF NOT EXISTS matches (
  id TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  game TEXT,
  tournament TEXT,
  series TEXT,
  team_a TEXT,
  team_b TEXT,
  start_utc TEXT,
  end_utc TEXT,
  stream TEXT,
  format TEXT,
  payload TEXT,
  fetched_utc TEXT
);
CREATE INDEX IF NOT EXISTS idx_matches_start ON matches(start_utc);
CREATE TABLE IF NOT EXISTS follows (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  provider TEXT NOT NULL,
  ref TEXT NOT NULL,
  label TEXT,
  added_utc TEXT NOT NULL,
  UNIQUE(kind, provider, ref)
);
CREATE TABLE IF NOT EXISTS http_cache (
  url TEXT PRIMARY KEY,
  etag TEXT,
  body TEXT,
  status INTEGER,
  fetched_at REAL,
  ttl_sec INTEGER,
  next_try REAL
);
"""

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None
_path: Path | None = None


def db_path() -> Path:
    """Return the path of the database currently open (or the default one)."""
    return _path or DB_PATH


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    """Open (or return) the shared connection, creating the schema on first use."""
    global _conn, _path
    with _lock:
        if _conn is not None:
            return _conn
        target = Path(path) if path is not None else DB_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(target), check_same_thread=False, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _migrate(conn)
        _conn = conn
        _path = target
        LOG.info("SQLite ready: %s (schema v%s)", target, SCHEMA_VERSION)
        return conn


def _migrate(conn: sqlite3.Connection) -> None:
    version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if version >= SCHEMA_VERSION:
        return
    if version < 1:
        conn.executescript(_SCHEMA_V1)
    conn.execute("PRAGMA user_version=%d" % SCHEMA_VERSION)
    conn.commit()
    LOG.info("SQLite migrated from v%s to v%s", version, SCHEMA_VERSION)


def close() -> None:
    """Close the shared connection (tests and shutdown only)."""
    global _conn, _path
    with _lock:
        if _conn is not None:
            try:
                _conn.close()
            except sqlite3.Error:
                pass
        _conn = None
        _path = None


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """Hold the connection under the lock and commit/rollback as a unit."""
    conn = connect()
    with _lock:
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def execute(sql: str, params: Iterable[Any] | None = None) -> int:
    """Run a write statement, commit, and return the affected row count."""
    conn = connect()
    with _lock:
        cursor = conn.execute(sql, tuple(params or ()))
        conn.commit()
        return cursor.rowcount


def executemany(sql: str, rows: Iterable[Iterable[Any]]) -> int:
    conn = connect()
    with _lock:
        cursor = conn.executemany(sql, [tuple(row) for row in rows])
        conn.commit()
        return cursor.rowcount


def query(sql: str, params: Iterable[Any] | None = None) -> list[sqlite3.Row]:
    conn = connect()
    with _lock:
        return conn.execute(sql, tuple(params or ())).fetchall()


def query_one(sql: str, params: Iterable[Any] | None = None) -> sqlite3.Row | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def table_names() -> list[str]:
    return [row["name"] for row in query("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
