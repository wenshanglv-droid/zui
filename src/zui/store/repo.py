"""Single writer for zui's SQLite database."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterable, Sequence
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from zui.platform import get_platform

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def now_iso() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="seconds")


def data_dir() -> Path:
    """Root for zui's own files; `ZUI_DATA_DIR` overrides it (tests, portable use)."""
    override = os.environ.get("ZUI_DATA_DIR")
    if override:
        return Path(override)
    return get_platform().app_data_dir()


def db_path() -> Path:
    return data_dir() / "zui.db"


def log_dir() -> Path:
    return data_dir() / "logs"


def snapshot_dir() -> Path:
    return data_dir() / "snapshots"


def connect(path: Path | None = None) -> sqlite3.Connection:
    target = path or db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
    return conn


def execute(conn: sqlite3.Connection, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
    with closing(conn.execute(sql, tuple(params))) as cur:
        conn.commit()
        return cur


def fetch_one(conn: sqlite3.Connection, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
    with closing(conn.execute(sql, tuple(params))) as cur:
        row: sqlite3.Row | None = cur.fetchone()
        return row


def fetch_all(conn: sqlite3.Connection, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
    with closing(conn.execute(sql, tuple(params))) as cur:
        return cur.fetchall()


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


__all__ = [
    "connect",
    "data_dir",
    "db_path",
    "execute",
    "fetch_all",
    "fetch_one",
    "log_dir",
    "now_iso",
    "rows_to_dicts",
    "snapshot_dir",
]
