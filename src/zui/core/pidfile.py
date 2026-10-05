"""Process state persistence (pid-file semantics, stored in SQLite)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from zui.store import repo


@dataclass(slots=True)
class ProcState:
    instance_id: str
    pid: int
    started_at: str
    heartbeat_at: str
    port: int | None = None
    log_path: Path | None = None
    argv: list[str] = field(default_factory=list)
    cwd: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "pid": self.pid,
            "started_at": self.started_at,
            "heartbeat_at": self.heartbeat_at,
            "port": self.port,
            "log_path": str(self.log_path) if self.log_path else None,
            "argv": self.argv,
            "cwd": str(self.cwd) if self.cwd else None,
        }


def write(conn: sqlite3.Connection, state: ProcState) -> None:
    repo.execute(
        conn,
        """
        INSERT INTO proc_state (instance_id, pid, started_at, heartbeat_at, port,
                                log_path, argv_json, cwd)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(instance_id) DO UPDATE SET
            pid=excluded.pid, heartbeat_at=excluded.heartbeat_at, port=excluded.port,
            log_path=excluded.log_path, argv_json=excluded.argv_json, cwd=excluded.cwd
        """,
        (
            state.instance_id,
            state.pid,
            state.started_at,
            state.heartbeat_at,
            state.port,
            str(state.log_path) if state.log_path else None,
            json.dumps(state.argv or []),
            str(state.cwd) if state.cwd else None,
        ),
    )


def read(conn: sqlite3.Connection, instance_id: str) -> ProcState | None:
    row = repo.fetch_one(conn, "SELECT * FROM proc_state WHERE instance_id = ?", (instance_id,))
    return _from_row(row) if row else None


def clear(conn: sqlite3.Connection, instance_id: str) -> None:
    repo.execute(conn, "DELETE FROM proc_state WHERE instance_id = ?", (instance_id,))


def touch(conn: sqlite3.Connection, instance_id: str) -> None:
    repo.execute(
        conn,
        "UPDATE proc_state SET heartbeat_at = ? WHERE instance_id = ?",
        (repo.now_iso(), instance_id),
    )


def list_states(conn: sqlite3.Connection) -> list[ProcState]:
    return [_from_row(row) for row in repo.fetch_all(conn, "SELECT * FROM proc_state")]


def _from_row(row: sqlite3.Row) -> ProcState:
    return ProcState(
        instance_id=row["instance_id"],
        pid=int(row["pid"]),
        started_at=row["started_at"] or "",
        heartbeat_at=row["heartbeat_at"] or "",
        port=row["port"],
        log_path=Path(row["log_path"]) if row["log_path"] else None,
        argv=[str(item) for item in json.loads(row["argv_json"] or "[]")],
        cwd=Path(row["cwd"]) if row["cwd"] else None,
    )


__all__ = ["ProcState", "clear", "list_states", "read", "touch", "write"]
