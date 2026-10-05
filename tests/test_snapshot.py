"""Snapshots: create / diff / restore really move files, and nodes stay advisory."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from conftest import Fixture
from zui.core import profile, snapshot
from zui.core.instance import Instance


def _pyvenv(comfy: Path, home: str) -> Path:
    venv = comfy / ".venv"
    venv.mkdir(parents=True, exist_ok=True)
    (venv / "pyvenv.cfg").write_text(
        f"home = {home}\nimplementation = CPython\nversion_info = 3.13.12\n",
        encoding="utf-8",
    )
    return venv


def test_create_copies_tracked_files(env: Fixture) -> None:
    conn, inst = env
    assert inst.comfy_dir is not None
    _pyvenv(inst.comfy_dir, "C:\\base-python")

    created = snapshot.create(inst, conn, kind="manual", note="first")

    assert created["files"] == [".venv/pyvenv.cfg"]
    stored = repo_snapshot_file(created["id"], "venv", "pyvenv.cfg")
    assert stored.exists()
    assert "CPython" in stored.read_text(encoding="utf-8")


def repo_snapshot_file(snap_id: str, label: str, name: str) -> Path:
    from zui.store import repo

    return repo.snapshot_dir() / snap_id / "files" / label / name


def test_snapshots_chain_by_parent(env: Fixture) -> None:
    conn, inst = env
    first = snapshot.create(inst, conn, kind="boot")
    second = snapshot.create(inst, conn, kind="pre-optimize", note="sage")
    assert second["parent_id"] == first["id"]
    listed = snapshot.list_snapshots(conn, inst.id)
    assert {row["id"] for row in listed} == {first["id"], second["id"]}


def test_diff_reports_profile_and_python_changes(env: Fixture) -> None:
    conn, inst = env
    before = snapshot.create(inst, conn, kind="boot")
    profile.save(conn, inst.id, "default", ["--use-sage-attention"])
    after = snapshot.create(inst, conn, kind="manual")

    changes = snapshot.diff(conn, before["id"], after["id"])["changes"]

    kinds = {change["kind"] for change in changes}
    assert "profile" in kinds
    profile_change = next(change for change in changes if change["kind"] == "profile")
    assert profile_change["to"] == ["--use-sage-attention"]


def test_restore_applies_file_changes(env: Fixture) -> None:
    conn, inst = env
    assert inst.comfy_dir is not None
    cfg = _pyvenv(inst.comfy_dir, "C:\\base-python") / "pyvenv.cfg"
    snap = snapshot.create(inst, conn, kind="boot")

    cfg.write_text("home = D:\\moved\nimplementation = CPython\n", encoding="utf-8")
    outcome = snapshot.restore(inst, conn, snap["id"], apply=True)

    assert outcome["applied"] is True
    assert outcome["files"] == [".venv/pyvenv.cfg"]
    assert "C:\\base-python" in cfg.read_text(encoding="utf-8")


def test_restore_without_apply_is_read_only(env: Fixture) -> None:
    conn, inst = env
    assert inst.comfy_dir is not None
    cfg = _pyvenv(inst.comfy_dir, "C:\\base-python") / "pyvenv.cfg"
    snap = snapshot.create(inst, conn, kind="boot")
    cfg.write_text("home = D:\\moved\n", encoding="utf-8")

    outcome = snapshot.restore(inst, conn, snap["id"], apply=False)

    assert outcome["applied"] is False
    assert "D:\\moved" in cfg.read_text(encoding="utf-8")


def test_node_inventory_marks_disabled_and_reports_diff(env: Fixture) -> None:
    conn, inst = env
    assert inst.comfy_dir is not None
    nodes = inst.comfy_dir / "custom_nodes"
    (nodes / "ComfyUI-Impact-Pack").mkdir()
    (nodes / "ComfyUI-AnimateDiff.disabled").mkdir()
    (nodes / "__pycache__").mkdir()

    inventory = snapshot.inventory_nodes(inst)

    assert inventory["ComfyUI-Impact-Pack"]["enabled"] is True
    assert inventory["ComfyUI-AnimateDiff.disabled"]["enabled"] is False
    assert "__pycache__" not in inventory

    before = snapshot.create(inst, conn, kind="boot")
    (nodes / "ComfyUI-Impact-Pack").rename(nodes / "ComfyUI-Impact-Pack.disabled")
    after = snapshot.create(inst, conn, kind="manual")

    changes = snapshot.diff(conn, before["id"], after["id"])["changes"]
    node_changes = [change for change in changes if change["kind"] == "node"]
    assert node_changes, "renaming a node dir must show up in the diff"

    restored = snapshot.restore(inst, conn, before["id"], apply=True)
    assert restored["node_changes"]
    assert "人工确认" in restored["note"]


def test_unknown_snapshot_raises(env: Fixture) -> None:
    conn, inst = env
    try:
        snapshot.restore(inst, conn, "nope")
    except KeyError:
        return
    raise AssertionError("unknown snapshot id must raise KeyError")


def test_payload_keeps_env_and_profiles(env: Fixture) -> None:
    conn, inst = env
    profile.save(conn, inst.id, "fast", ["--highvram"])
    snap_id = snapshot.create(inst, conn, kind="boot")["id"]

    payload = snapshot.get(conn, snap_id)

    assert payload is not None
    assert "env" in payload and "nodes" in payload
    assert "fast" in payload["profiles"]
    json.dumps(payload, ensure_ascii=False)  # payload must stay serialisable


def test_empty_instance_has_no_tracked_files(tmp_path: Path) -> None:
    inst = Instance(id="empty", name="empty", root=tmp_path)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    from zui.store import repo

    conn.executescript((repo._SCHEMA_PATH).read_text(encoding="utf-8"))  # noqa: SLF001
    created = snapshot.create(inst, conn)
    assert created["files"] == []
