"""Snapshots: before/after records with diff and file-level restore."""

from __future__ import annotations

import json
import shutil
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from zui.core import env_runtime
from zui.core.instance import Instance
from zui.store import repo

_TRACKED_FILES = (
    ("venv", ".venv/pyvenv.cfg"),
    ("extra_model_paths", "extra_model_paths.yaml"),
    ("comfy_settings", "user/default/comfy.settings.json"),
    ("manager_config", "user/__manager/config.ini"),
)


def _base(instance: Instance) -> Path:
    """Files are tracked relative to the ComfyUI dir when we can find it."""
    return instance.comfy_dir or instance.root


def inventory_nodes(instance: Instance) -> dict[str, dict[str, Any]]:
    if instance.comfy_dir is None:
        return {}
    base = instance.comfy_dir / "custom_nodes"
    if not base.is_dir():
        return {}
    nodes: dict[str, dict[str, Any]] = {}
    for entry in sorted(base.iterdir()):
        if entry.name.startswith("__") or entry.is_file():
            continue
        enabled = not entry.name.endswith(".disabled")
        nodes[entry.name] = {"enabled": enabled, "commit": _head_commit(entry / ".git")}
    return nodes


def create(
    instance: Instance, conn: sqlite3.Connection, kind: str = "manual", note: str = ""
) -> dict:
    parent = _latest_id(conn, instance.id)
    payload: dict[str, Any] = {
        "instance_id": instance.id,
        "kind": kind,
        "note": note,
        "root": str(instance.root),
        "comfy_version": instance.comfy_version,
        "env": env_runtime.inspect(instance, conn=None, bench=False),
        "nodes": inventory_nodes(instance),
        "profiles": _profiles(conn, instance),
    }
    snap_id = uuid.uuid4().hex[:12]
    files_dir = repo.snapshot_dir() / snap_id / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    base = _base(instance)
    copied: list[str] = []
    for label, relative in _TRACKED_FILES:
        source = base / relative
        if source.exists():
            target = files_dir / label / source.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copied.append(relative)
    payload["files"] = copied
    repo.execute(
        conn,
        "INSERT INTO snapshots (id, instance_id, kind, parent_id, payload_json, created_at)"
        " VALUES (?,?,?,?,?,?)",
        (
            snap_id,
            instance.id,
            kind,
            parent,
            json.dumps(payload, ensure_ascii=False),
            repo.now_iso(),
        ),
    )
    return {"id": snap_id, "kind": kind, "parent_id": parent, "files": copied}


def list_snapshots(conn: sqlite3.Connection, instance_id: str) -> list[dict[str, Any]]:
    rows = repo.fetch_all(
        conn,
        "SELECT id, kind, parent_id, created_at FROM snapshots WHERE instance_id = ?"
        " ORDER BY created_at DESC, id DESC",
        (instance_id,),
    )
    return [dict(row) for row in rows]


def get(conn: sqlite3.Connection, snap_id: str) -> dict[str, Any] | None:
    row = repo.fetch_one(conn, "SELECT * FROM snapshots WHERE id = ?", (snap_id,))
    if row is None:
        return None
    payload: dict[str, Any] = json.loads(row["payload_json"])
    payload["id"] = row["id"]
    payload["created_at"] = row["created_at"]
    return payload


def diff(conn: sqlite3.Connection, left_id: str, right_id: str) -> dict[str, Any]:
    left, right = get(conn, left_id), get(conn, right_id)
    if left is None or right is None:
        raise KeyError("unknown snapshot id")
    changes: list[dict[str, Any]] = []

    for field in ("comfy_version",):
        if left.get(field) != right.get(field):
            changes.append({"kind": field, "from": left.get(field), "to": right.get(field)})

    left_env = (left.get("env") or {}).get("python_version")
    right_env = (right.get("env") or {}).get("python_version")
    if left_env != right_env:
        changes.append({"kind": "python", "from": left_env, "to": right_env})
    left_torch = (left.get("env") or {}).get("torch_version")
    right_torch = (right.get("env") or {}).get("torch_version")
    if left_torch != right_torch:
        changes.append({"kind": "torch", "from": left_torch, "to": right_torch})

    left_nodes, right_nodes = left.get("nodes") or {}, right.get("nodes") or {}
    for name in sorted(set(left_nodes) | set(right_nodes)):
        before, after = left_nodes.get(name), right_nodes.get(name)
        if before != after:
            changes.append({"kind": "node", "name": name, "from": before, "to": after})

    for name in sorted(set(left.get("profiles") or {}) | set(right.get("profiles") or {})):
        before_args = (left.get("profiles") or {}).get(name, {}).get("args") or []
        after_args = (right.get("profiles") or {}).get(name, {}).get("args") or []
        if before_args != after_args:
            changes.append({"kind": "profile", "name": name, "from": before_args, "to": after_args})
    return {"left": left_id, "right": right_id, "changes": changes}


def restore(
    instance: Instance, conn: sqlite3.Connection, snap_id: str, *, apply: bool = False
) -> dict[str, Any]:
    snap = get(conn, snap_id)
    if snap is None:
        raise KeyError("unknown snapshot id")
    files_dir = repo.snapshot_dir() / snap_id / "files"
    base = _base(instance)
    restored: list[str] = []
    for label, relative in _TRACKED_FILES:
        source = files_dir / label / Path(relative).name
        if not source.exists():
            continue
        restored.append(relative)
        if apply:
            target = base / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists() or target.read_bytes() != source.read_bytes():
                shutil.copy2(source, target)

    current_nodes = inventory_nodes(instance)
    snap_nodes = snap.get("nodes") or {}
    node_changes: list[dict[str, Any]] = []
    for name in sorted(set(snap_nodes) | set(current_nodes)):
        if snap_nodes.get(name) != current_nodes.get(name):
            node_changes.append(
                {"name": name, "snapshot": snap_nodes.get(name), "current": current_nodes.get(name)}
            )
    return {
        "snapshot": snap_id,
        "files": restored,
        "applied": apply,
        "node_changes": node_changes,
        "note": "节点差异不会自动安装/删除，需人工确认后操作",
    }


def _latest_id(conn: sqlite3.Connection, instance_id: str) -> str | None:
    row = repo.fetch_one(
        conn,
        "SELECT id FROM snapshots WHERE instance_id = ? ORDER BY created_at DESC, id DESC LIMIT 1",
        (instance_id,),
    )
    return str(row["id"]) if row else None


def _head_commit(git_dir: Path) -> str | None:
    head = git_dir / "HEAD"
    if not head.exists():
        return None
    raw = head.read_text(encoding="utf-8").strip()
    if raw.startswith("ref: "):
        ref = git_dir / raw.split(" ", 1)[1]
        if ref.exists():
            return ref.read_text(encoding="utf-8").strip()[:12]
        packed = git_dir / "packed-refs"
        target = raw.split(" ", 1)[1].strip()
        if packed.exists():
            for line in packed.read_text(encoding="utf-8").splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[1] == target:
                    return parts[0][:12]
        return None
    return raw[:12]


def _profiles(conn: sqlite3.Connection, instance: Instance) -> dict[str, Any]:
    from zui.core import profile

    return {item["name"]: item for item in profile.list_profiles(conn, instance.id)}


__all__ = ["create", "diff", "get", "inventory_nodes", "list_snapshots", "restore"]
