"""Model asset index: fast walk, safetensors headers, dedupe and migration advice."""

from __future__ import annotations

import json
import sqlite3
import struct
from collections import defaultdict
from pathlib import Path
from typing import Any

from zui.core.instance import Instance
from zui.platform import get_platform
from zui.store import repo

_SUFFIXES = {".safetensors", ".ckpt", ".pt", ".gguf", ".bin", ".pth", ".onnx"}


def safetensors_meta(path: Path) -> dict[str, Any]:
    """Read only the header. Never loads weights."""
    try:
        with open(path, "rb") as handle:
            raw = handle.read(8)
            if len(raw) < 8:
                return {}
            (length,) = struct.unpack("<Q", raw)
            if length > 64 * 1024 * 1024:
                return {}
            header: dict[str, Any] = json.loads(handle.read(length))
    except Exception:
        return {}
    dtypes: set[str] = set()
    params = 0
    for key, value in header.items():
        if key == "__metadata__":
            continue
        dtype = value.get("dtype")
        if dtype:
            dtypes.add(str(dtype))
        shape = value.get("shape") or []
        count = 1
        for dim in shape:
            count *= int(dim)
        params += count
    return {"dtypes": sorted(dtypes), "param_count": params}


def scan(
    instance: Instance, conn: sqlite3.Connection, *, with_hash: bool = False
) -> dict[str, Any]:
    files = 0
    total = 0
    families: dict[str, int] = defaultdict(int)
    largest: list[dict[str, Any]] = []

    for root in instance.model_roots:
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in _SUFFIXES:
                continue
            size = path.stat().st_size
            family = _family(root, path)
            meta = safetensors_meta(path) if path.suffix == ".safetensors" else {}
            digest: str | None = None
            if with_hash and size > 0:
                digest = _blake3(path)
            repo.execute(
                conn,
                """
                INSERT INTO assets (id, instance_id, rel_path, size, family, dtype,
                                    param_count, hash, first_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET size=excluded.size, family=excluded.family,
                    dtype=excluded.dtype, param_count=excluded.param_count, hash=excluded.hash
                """,
                (
                    f"{instance.id}:{path}",
                    instance.id,
                    str(path),
                    size,
                    family,
                    ",".join(meta.get("dtypes") or []) or None,
                    meta.get("param_count"),
                    digest,
                    repo.now_iso(),
                ),
            )
            files += 1
            total += size
            families[family] += size
            largest.append({"name": path.name, "gb": round(size / 2**30, 2), "family": family})

    largest.sort(key=lambda item: item["gb"], reverse=True)
    return {
        "instance": instance.id,
        "files": files,
        "total_bytes": total,
        "total_gb": round(total / 2**30, 2),
        "families": {key: round(value / 2**30, 2) for key, value in sorted(families.items())},
        "largest": largest[:15],
    }


def report(conn: sqlite3.Connection, instance: Instance) -> dict[str, Any]:
    rows = repo.fetch_all(
        conn, "SELECT * FROM assets WHERE instance_id = ? ORDER BY size DESC", (instance.id,)
    )
    if not rows:
        return {"files": 0, "total_gb": 0, "duplicates": [], "families": {}, "largest": []}

    total = sum(int(row["size"] or 0) for row in rows)
    duplicates = _duplicates(rows)
    families: dict[str, float] = defaultdict(float)
    for row in rows:
        families[row["family"] or "unknown"] += int(row["size"] or 0)

    adapter = get_platform()
    disks: dict[str, str] = {}
    for row in rows:
        path = Path(row["rel_path"])
        key = str(path.drive or path.root)
        if key not in disks:
            try:
                disks[key] = f"{adapter.disk_kind(path)} ({adapter.disk_bench(path)} MB/s)"
            except Exception:
                disks[key] = "unknown"

    return {
        "files": len(rows),
        "total_gb": round(total / 2**30, 2),
        "duplicates": duplicates,
        "families": {key: round(value / 2**30, 2) for key, value in sorted(families.items())},
        "largest": [
            {"name": Path(row["rel_path"]).name, "gb": round(int(row["size"]) / 2**30, 2)}
            for row in rows[:15]
        ],
        "volumes": disks,
    }


def _duplicates(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    """Group identical files by content hash when known, else by file name.

    Hash grouping catches "same weights, different name"; name grouping is the
    fallback for assets that were scanned without `--hash`.
    """
    by_hash: dict[str, list[sqlite3.Row]] = defaultdict(list)
    unhashed: list[sqlite3.Row] = []
    for row in rows:
        digest = row["hash"]
        if digest:
            by_hash[str(digest)].append(row)
        else:
            unhashed.append(row)

    groups: list[tuple[str, str, list[sqlite3.Row]]] = [
        (str(digest)[:12], "hash", rows_for)
        for digest, rows_for in sorted(by_hash.items())
        if len(rows_for) > 1
    ]
    by_name: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in unhashed:
        by_name[Path(row["rel_path"]).name].append(row)
    groups += [
        (name, "name", rows_for) for name, rows_for in sorted(by_name.items()) if len(rows_for) > 1
    ]

    duplicates: list[dict[str, Any]] = []
    for key, how, rows_for in groups:
        sizes = [int(row["size"] or 0) for row in rows_for]
        duplicates.append(
            {
                "key": key,
                "by": how,
                "count": len(rows_for),
                "gb": round(sizes[0] / 2**30, 2),
                "reclaimable_gb": round(sum(sizes[1:]) / 2**30, 2),
                "paths": [str(row["rel_path"]) for row in rows_for],
            }
        )
    duplicates.sort(key=lambda item: item["reclaimable_gb"], reverse=True)
    return duplicates


def usage(conn: sqlite3.Connection, instance: Instance) -> dict[str, Any]:
    """Which indexed assets are referenced by a saved workflow, and which are not.

    Writes `assets.last_used_at` for referenced assets so repeated calls stay cheap
    and the column stops being decorative.
    """
    from zui.core import budget

    rows = repo.fetch_all(
        conn, "SELECT * FROM assets WHERE instance_id = ? ORDER BY size DESC", (instance.id,)
    )
    if not rows:
        return {"files": 0, "workflows": [], "used": [], "unused": [], "unused_gb": 0}

    referenced: set[str] = set()
    workflows: list[str] = []
    for path in budget.workflows_for(instance):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        workflows.append(str(path))
        referenced |= budget.referenced_strings(data)

    now = repo.now_iso()
    used: list[dict[str, Any]] = []
    unused: list[dict[str, Any]] = []
    for row in rows:
        name = Path(row["rel_path"]).name
        entry = {
            "name": name,
            "path": str(row["rel_path"]),
            "gb": round(int(row["size"] or 0) / 2**30, 2),
            "family": row["family"],
            "last_used_at": row["last_used_at"],
        }
        if name in referenced:
            repo.execute(
                conn,
                "UPDATE assets SET last_used_at = ? WHERE id = ?",
                (now, row["id"]),
            )
            entry["last_used_at"] = now
            used.append(entry)
        else:
            unused.append(entry)

    return {
        "files": len(rows),
        "workflows": workflows,
        "used": used,
        "unused": unused,
        "unused_gb": round(sum(item["gb"] for item in unused), 2),
        "note": "未使用 = 未被 user/default/workflows 下任何工作流引用；删除前请人工确认",
    }


def _family(root: Path, path: Path) -> str:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return path.parent.name or "unknown"
    parts = relative.parts
    return parts[0] if len(parts) > 1 else "root"


def _blake3(path: Path) -> str | None:
    try:
        from blake3 import blake3  # type: ignore[import-not-found]

        digest = blake3(max_threads=1)
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(8 << 20), b""):
                digest.update(chunk)
        value: str = digest.hexdigest()
        return value
    except Exception:
        return None


__all__ = ["report", "safetensors_meta", "scan", "usage"]
