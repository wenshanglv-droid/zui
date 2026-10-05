"""Instance registry: adopt, list and resolve existing ComfyUI installs."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zui.core.paths import python_in, sibling_model_roots, venv_python_for
from zui.store import repo


@dataclass(slots=True)
class Instance:
    id: str
    name: str
    root: Path
    comfy_dir: Path | None = None
    venv_dir: Path | None = None
    base_python: Path | None = None
    source: str = "adopt"
    comfy_version: str | None = None
    active: bool = False
    created_at: str = ""

    @property
    def python(self) -> Path | None:
        if self.venv_dir is not None:
            found = venv_python_for(self.venv_dir)
            if found is not None:
                return found
        if self.comfy_dir is not None:
            direct = python_in(self.comfy_dir.parent)
            if direct is not None:
                return direct
        return self.base_python

    @property
    def model_roots(self) -> list[Path]:
        roots = list(sibling_model_roots(self.root))
        if self.comfy_dir is not None:
            for path in sibling_model_roots(self.comfy_dir.parent):
                if path not in roots:
                    roots.append(path)
        return roots

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "root": str(self.root),
            "comfy_dir": str(self.comfy_dir) if self.comfy_dir else None,
            "venv_dir": str(self.venv_dir) if self.venv_dir else None,
            "base_python": str(self.base_python) if self.base_python else None,
            "source": self.source,
            "comfy_version": self.comfy_version,
            "active": self.active,
            "created_at": self.created_at,
            "python": str(self.python) if self.python else None,
            "model_roots": [str(path) for path in self.model_roots],
        }


def slugify(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-").lower()
    return slug or "instance"


def detect_layout(root: Path) -> dict[str, Any]:
    """Identify the ComfyUI / venv / base-python layout under `root`."""
    resolved = root.resolve()
    if (resolved / "main.py").exists() and (resolved / "nodes.py").exists():
        comfy_dir = resolved
    elif (resolved / "ComfyUI" / "main.py").exists():
        comfy_dir = resolved / "ComfyUI"
    else:
        comfy_dir = None

    venv_dir = comfy_dir / ".venv" if comfy_dir is not None else None
    if venv_dir is not None and not venv_dir.is_dir() and (resolved / ".venv").is_dir():
        venv_dir = resolved / ".venv"

    base_python = None
    search = [resolved, resolved.parent]
    if comfy_dir is not None:
        search.append(comfy_dir.parent)
    for candidate in search:
        found = python_in(candidate / "standalone-env") or python_in(candidate / "base")
        if found is not None:
            base_python = found
            break

    return {
        "root": resolved,
        "comfy_dir": comfy_dir if comfy_dir and comfy_dir.is_dir() else None,
        "venv_dir": venv_dir if venv_dir and venv_dir.is_dir() else None,
        "base_python": base_python,
        "comfy_version": _read_comfy_version(comfy_dir),
    }


def _read_comfy_version(comfy_dir: Path | None) -> str | None:
    if comfy_dir is None:
        return None
    path = comfy_dir / "comfyui_version.py"
    if not path.exists():
        return None
    match = re.search(r"__version__\s*=\s*[\"']([^\"']+)", path.read_text(encoding="utf-8"))
    return match.group(1) if match else None


def adopt(root: Path, name: str | None = None, conn: sqlite3.Connection | None = None) -> Instance:
    layout = detect_layout(root)
    if layout["comfy_dir"] is None:
        raise ValueError(f"no ComfyUI installation recognised under {root}")
    resolved_root = layout["root"]
    label = name or resolved_root.name
    instance = Instance(
        id=slugify(label),
        name=label,
        root=resolved_root,
        comfy_dir=layout["comfy_dir"],
        venv_dir=layout["venv_dir"],
        base_python=layout["base_python"],
        comfy_version=layout["comfy_version"],
        created_at=repo.now_iso(),
    )
    owns = conn is None
    connection = conn or repo.connect()
    try:
        upsert(connection, instance)
    finally:
        if owns:
            connection.close()
    return instance


def upsert(conn: sqlite3.Connection, instance: Instance) -> None:
    repo.execute(
        conn,
        """
        INSERT INTO instances (id, name, root_path, comfy_dir, venv_dir, base_python,
                               source, comfy_version, active, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name=excluded.name, root_path=excluded.root_path, comfy_dir=excluded.comfy_dir,
            venv_dir=excluded.venv_dir, base_python=excluded.base_python,
            source=excluded.source, comfy_version=excluded.comfy_version
        """,
        (
            instance.id,
            instance.name,
            str(instance.root),
            str(instance.comfy_dir) if instance.comfy_dir else None,
            str(instance.venv_dir) if instance.venv_dir else None,
            str(instance.base_python) if instance.base_python else None,
            instance.source,
            instance.comfy_version,
            int(instance.active),
            instance.created_at or repo.now_iso(),
        ),
    )


def list_instances(conn: sqlite3.Connection) -> list[Instance]:
    rows = repo.fetch_all(conn, "SELECT * FROM instances ORDER BY name")
    return [_from_row(row) for row in rows]


def get_instance(conn: sqlite3.Connection, ref: str) -> Instance | None:
    row = repo.fetch_one(conn, "SELECT * FROM instances WHERE id = ? OR name = ?", (ref, ref))
    return _from_row(row) if row else None


def require_instance(conn: sqlite3.Connection, ref: str | None) -> Instance:
    if ref is None:
        instance = active_instance(conn)
        if instance is None:
            raise KeyError("no active instance; run `zui instance adopt <path>` first")
        return instance
    instance = get_instance(conn, ref)
    if (
        ref
        not in {
            "",
        }
        and instance is None
    ):
        raise KeyError(f"unknown instance: {ref}")
    assert instance is not None
    return instance


def set_active(conn: sqlite3.Connection, ref: str) -> Instance:
    instance = get_instance(conn, ref)
    if instance is None:
        raise KeyError(f"unknown instance: {ref}")
    repo.execute(conn, "UPDATE instances SET active = 0")
    repo.execute(conn, "UPDATE instances SET active = 1 WHERE id = ?", (instance.id,))
    instance.active = True
    return instance


def forget(conn: sqlite3.Connection, ref: str) -> Instance:
    """Drop an instance from the registry. Files on disk are never touched."""
    instance = get_instance(conn, ref)
    if instance is None:
        raise KeyError(f"unknown instance: {ref}")
    from zui.core import pidfile, proc

    state = pidfile.read(conn, instance.id)
    if state is not None and proc.is_alive(state.pid):
        raise RuntimeError(f"instance still running (pid={state.pid}); stop it first")
    repo.execute(conn, "DELETE FROM instances WHERE id = ?", (instance.id,))
    return instance


def active_instance(conn: sqlite3.Connection) -> Instance | None:
    row = repo.fetch_one(conn, "SELECT * FROM instances WHERE active = 1 ORDER BY name LIMIT 1")
    return _from_row(row) if row else None


def _from_row(row: sqlite3.Row) -> Instance:
    def maybe(value: str | None) -> Path | None:
        return Path(value) if value else None

    return Instance(
        id=row["id"],
        name=row["name"],
        root=Path(row["root_path"]),
        comfy_dir=maybe(row["comfy_dir"]),
        venv_dir=maybe(row["venv_dir"]),
        base_python=maybe(row["base_python"]),
        source=row["source"],
        comfy_version=row["comfy_version"],
        active=bool(row["active"]),
        created_at=row["created_at"] or "",
    )


__all__ = [
    "forget",
    "Instance",
    "active_instance",
    "adopt",
    "detect_layout",
    "get_instance",
    "list_instances",
    "require_instance",
    "set_active",
    "slugify",
    "upsert",
]
