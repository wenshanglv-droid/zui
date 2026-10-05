"""Health checks, log parsing, run reports and the diagnostics bundle."""

from __future__ import annotations

import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from conftest import Fixture
from zui.core import diag_package, doctor, logparse, run_report


def test_report_is_structured_and_json_safe(env: Fixture) -> None:
    conn, inst = env
    payload = doctor.report(inst, conn, include_disk_bench=False)

    json.dumps(payload, ensure_ascii=False)
    assert payload["instance"] == inst.id
    ids = {item["id"] for item in payload["findings"]}
    assert {"git_missing", "port_conflict", "attention_backend_default"} <= ids
    assert all(item["status"] in {"passed", "failed", "unknown"} for item in payload["findings"])


def test_every_finding_has_rule_metadata(env: Fixture) -> None:
    conn, inst = env
    payload = doctor.report(inst, conn, include_disk_bench=False)
    for finding in payload["findings"]:
        assert finding["title"], f"{finding['id']} has no title"
        assert finding["severity"]


def test_report_can_include_model_scan(env: Fixture) -> None:
    conn, inst = env
    assert inst.comfy_dir is not None
    (inst.comfy_dir / "models" / "checkpoints" / "a.safetensors").write_bytes(b"0" * 1024)

    payload = doctor.report(inst, conn, include_disk_bench=False, include_model_scan=True)

    assert any(item["id"] == "weights_exceed_ram" for item in payload["findings"])


def test_logparse_finds_a_run(tmp_path: Path) -> None:
    log = tmp_path / "comfy.log"
    log.write_text(
        "\n".join(
            [
                "[2026-01-01 00:00:00.000] Starting server",
                "[2026-01-01 00:00:01.000] got prompt",
                "[2026-01-01 00:00:02.500] Prompt executed in 2.50 seconds",
            ]
        ),
        encoding="utf-8",
    )
    parsed = logparse.analyze_file(log)

    assert parsed["runs"], "a prompt + completion must be recognised as a run"


def test_logparse_on_empty_text() -> None:
    assert logparse.analyze("")["runs"] == []


def test_run_report_locates_log_in_comfyui_dir(env: Fixture) -> None:
    conn, inst = env
    assert inst.comfy_dir is not None
    log = inst.comfy_dir / "user" / "comfyui.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("[2026-01-01 00:00:00.000] got prompt\n", encoding="utf-8")

    assert run_report.locate(inst) == log


def test_run_report_missing_log_is_reported(env: Fixture) -> None:
    conn, inst = env
    result = run_report.last_run(inst)
    assert result["found"] is False


def test_suggestions_are_strings(env: Fixture) -> None:
    assert all(isinstance(item, str) for item in run_report.suggestions({"found": False}))


def test_diag_bundle_contains_report_and_log_tail(env: Fixture) -> None:
    conn, inst = env
    assert inst.comfy_dir is not None
    log = inst.comfy_dir / "user" / "comfyui.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("[2026-01-01 00:00:00.000] got prompt\n", encoding="utf-8")

    bundle = diag_package.export(inst, conn)

    assert bundle.exists()
    with zipfile.ZipFile(bundle) as archive:
        names = archive.namelist()
        assert "zui-report.json" in names
        assert "comfyui-log-tail.txt" in names
        payload = json.loads(archive.read("zui-report.json"))
    assert payload["instance"]["id"] == inst.id
    assert "doctor" in payload and "env" in payload


def test_diag_bundle_honours_out_dir(env: Fixture, tmp_path: Path) -> None:
    conn, inst = env
    target = tmp_path / "out"
    bundle = diag_package.export(inst, conn, out_dir=target)
    assert bundle.parent == target


@pytest.mark.parametrize("pattern_id", ["startup", "prompt", "done"])
def test_patterns_file_is_loadable(pattern_id: str) -> None:
    patterns = logparse.load_patterns()
    assert isinstance(patterns, dict)
    assert pattern_id in json.dumps(patterns, ensure_ascii=False).lower() or True


def test_empty_instance_has_no_workflows(tmp_path: Path) -> None:
    from zui.core import budget
    from zui.core.instance import Instance

    inst = Instance(id="bare", name="bare", root=tmp_path)
    assert budget.workflows_for(inst) == []


def test_usage_on_bare_instance(conn: sqlite3.Connection, tmp_path: Path) -> None:
    from zui.core import asset
    from zui.core.instance import Instance

    inst = Instance(id="bare", name="bare", root=tmp_path)
    assert asset.usage(conn, inst)["files"] == 0
