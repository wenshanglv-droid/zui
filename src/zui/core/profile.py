"""Launch profiles: named bundles of ComfyUI arguments."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from zui.store import repo

DEFAULT_NAME = "default"


def get(conn: sqlite3.Connection, instance_id: str, name: str = DEFAULT_NAME) -> dict[str, Any]:
    row = repo.fetch_one(
        conn, "SELECT * FROM profiles WHERE instance_id = ? AND name = ?", (instance_id, name)
    )
    if row is None:
        return {"name": name, "args": [], "env": {}}
    return {
        "name": row["name"],
        "args": [str(item) for item in json.loads(row["args_json"] or "[]")],
        "env": dict(json.loads(row["env_json"] or "{}")),
    }


def save(
    conn: sqlite3.Connection,
    instance_id: str,
    name: str,
    args: list[str],
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    repo.execute(
        conn,
        """
        INSERT INTO profiles (id, instance_id, name, args_json, env_json)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET args_json=excluded.args_json, env_json=excluded.env_json
        """,
        (
            f"{instance_id}:{name}",
            instance_id,
            name,
            json.dumps(list(args)),
            json.dumps(dict(env or {})),
        ),
    )
    return get(conn, instance_id, name)


def list_profiles(conn: sqlite3.Connection, instance_id: str) -> list[dict[str, Any]]:
    rows = repo.fetch_all(conn, "SELECT name FROM profiles WHERE instance_id = ?", (instance_id,))
    return [get(conn, instance_id, str(row["name"])) for row in rows]


__all__ = ["DEFAULT_NAME", "get", "list_profiles", "save"]
