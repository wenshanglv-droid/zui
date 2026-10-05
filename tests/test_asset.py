"""Model asset index: scan, content-hash dedupe, usage detection and workflow budget."""

from __future__ import annotations

import json
import struct
from pathlib import Path

from conftest import Fixture
from zui.core import asset, budget
from zui.store import repo


class _FakePlatform:
    """Avoids real disk benchmarks in tests."""

    def disk_kind(self, path: Path) -> str:  # noqa: ARG002
        return "ssd"

    def disk_bench(self, path: Path) -> float:  # noqa: ARG002
        return 1234.0


def _safetensors(path: Path, dtype: str = "F16", shape: tuple[int, ...] = (4, 4)) -> None:
    header = json.dumps({"weight": {"dtype": dtype, "shape": list(shape)}}).encode("utf-8")
    header += b" " * ((-len(header)) % 8)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(struct.pack("<Q", len(header)) + header + b"\0" * 16)


def _workflow(path: Path, *models: str) -> None:
    widgets = [{"name": "ckpt_name", "value": model} for model in models]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"nodes": [{"type": "CheckpointLoaderSimple", "widgets_values": widgets}]}),
        encoding="utf-8",
    )


def test_scan_indexes_files_and_families(env: Fixture) -> None:
    conn, inst = env
    assert inst.comfy_dir is not None
    _safetensors(inst.comfy_dir / "models" / "checkpoints" / "a.safetensors")
    (inst.comfy_dir / "models" / "loras").mkdir(parents=True)
    (inst.comfy_dir / "models" / "loras" / "b.ckpt").write_bytes(b"0" * 2048)

    summary = asset.scan(inst, conn)

    assert summary["files"] == 2
    assert set(summary["families"]) == {"checkpoints", "loras"}
    rows = repo.fetch_all(conn, "SELECT * FROM assets WHERE instance_id = ?", (inst.id,))
    assert len(rows) == 2
    assert any(row["dtype"] == "F16" for row in rows)


def test_safetensors_meta_reads_header_only(tmp_path: Path) -> None:
    path = tmp_path / "x.safetensors"
    _safetensors(path, dtype="BF16", shape=(8, 8))
    meta = asset.safetensors_meta(path)
    assert meta["dtypes"] == ["BF16"]
    assert meta["param_count"] == 64

    junk = tmp_path / "junk.safetensors"
    junk.write_bytes(b"not a real file")
    assert asset.safetensors_meta(junk) == {}


def test_report_groups_duplicates_by_hash(env: Fixture, monkeypatch) -> None:
    conn, inst = env
    monkeypatch.setattr(asset, "get_platform", _FakePlatform)
    for name in ("one.safetensors", "copy-of-one.safetensors"):
        repo.execute(
            conn,
            "INSERT INTO assets (id, instance_id, rel_path, size, family, hash, first_seen)"
            " VALUES (?,?,?,?,?,?,?)",
            (
                f"{inst.id}:{name}",
                inst.id,
                f"/models/{name}",
                4 << 30,
                "checkpoints",
                "deadbeef",
                repo.now_iso(),
            ),
        )

    report = asset.report(conn, inst)

    assert report["duplicates"][0]["by"] == "hash"
    assert report["duplicates"][0]["count"] == 2
    assert report["duplicates"][0]["reclaimable_gb"] == 4.0


def test_report_falls_back_to_name_when_unhashed(env: Fixture, monkeypatch) -> None:
    conn, inst = env
    monkeypatch.setattr(asset, "get_platform", _FakePlatform)
    for path in ("/models/shared.safetensors", "/backup/shared.safetensors"):
        repo.execute(
            conn,
            "INSERT INTO assets (id, instance_id, rel_path, size, family, first_seen)"
            " VALUES (?,?,?,?,?,?)",
            (f"{inst.id}:{path}", inst.id, path, 2 << 30, "checkpoints", repo.now_iso()),
        )

    report = asset.report(conn, inst)

    assert report["duplicates"][0]["by"] == "name"
    assert report["duplicates"][0]["key"] == "shared.safetensors"


def test_usage_marks_referenced_assets_and_lists_unused(env: Fixture) -> None:
    conn, inst = env
    assert inst.comfy_dir is not None
    root = inst.comfy_dir / "models" / "checkpoints"
    _safetensors(root / "used.safetensors")
    _safetensors(root / "orphan.safetensors")
    asset.scan(inst, conn)
    _workflow(inst.comfy_dir / "user" / "default" / "workflows" / "main.json", "used.safetensors")

    usage = asset.usage(conn, inst)

    assert {Path(item["path"]).name for item in usage["used"]} == {"used.safetensors"}
    assert {Path(item["path"]).name for item in usage["unused"]} == {"orphan.safetensors"}
    assert usage["unused_gb"] >= 0
    stamped = repo.fetch_one(
        conn,
        "SELECT last_used_at FROM assets WHERE instance_id = ? AND rel_path LIKE ?",
        (inst.id, "%used.safetensors"),
    )
    assert stamped is not None and stamped["last_used_at"], "last_used_at must be written"


def test_usage_is_empty_without_assets(env: Fixture) -> None:
    conn, inst = env
    assert asset.usage(conn, inst)["files"] == 0


def test_budget_estimates_referenced_models(env: Fixture) -> None:
    conn, inst = env
    assert inst.comfy_dir is not None
    _safetensors(inst.comfy_dir / "models" / "checkpoints" / "big.safetensors")
    asset.scan(inst, conn)
    workflow = inst.comfy_dir / "user" / "default" / "workflows" / "wf.json"
    _workflow(workflow, "big.safetensors", "missing.safetensors")

    estimate = budget.estimate(inst, conn, workflow)

    assert estimate["nodes"] == 1
    assert [item["name"] for item in estimate["matched"]] == ["big.safetensors"]
    assert estimate["missing_in_index"] == ["missing.safetensors"]
    assert estimate["verdict"] in {"fits", "tight", "will-thrash"}


def test_budget_without_match_is_unknown(env: Fixture) -> None:
    conn, inst = env
    assert inst.comfy_dir is not None
    workflow = inst.comfy_dir / "user" / "default" / "workflows" / "empty.json"
    _workflow(workflow)

    estimate = budget.estimate(inst, conn, workflow)

    assert estimate["verdict"] == "unknown"
    assert any("model scan" in line for line in estimate["advice"])


def test_workflows_for_lists_saved_workflows(env: Fixture) -> None:
    conn, inst = env
    assert inst.comfy_dir is not None
    _workflow(inst.comfy_dir / "user" / "default" / "workflows" / "a.json")

    found = budget.workflows_for(inst)

    assert [path.name for path in found] == ["a.json"]


def test_referenced_strings_walks_nested_structures() -> None:
    data = {"nodes": [{"widgets_values": ["deep.safetensors", 3]}]}
    assert "deep.safetensors" in budget.referenced_strings(data)
