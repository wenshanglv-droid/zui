"""The doctor is a rule-file driven engine, and rules can be refreshed remotely."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import Fixture
from zui.core import doctor, rules_sync


def _req(comfy: Path, node: str, body: str) -> None:
    entry = comfy / "custom_nodes" / node
    entry.mkdir(parents=True, exist_ok=True)
    (entry / "requirements.txt").write_text(body, encoding="utf-8")


def test_checks_follow_rule_file_order(env: Fixture, monkeypatch) -> None:
    conn, inst = env
    monkeypatch.setattr(
        doctor,
        "load_rules",
        lambda: [
            {"id": "port_conflict", "severity": "warning", "title": "端口"},
            {"id": "git_missing", "severity": "warning", "title": "Git"},
        ],
    )

    payload = doctor.report(inst, conn, include_disk_bench=False)

    assert [item["id"] for item in payload["findings"]] == ["port_conflict", "git_missing"]


def test_rules_without_evaluator_are_skipped(env: Fixture, monkeypatch) -> None:
    conn, inst = env
    monkeypatch.setattr(
        doctor, "load_rules", lambda: [{"id": "missing_runtime_dll", "title": "运行时"}]
    )
    assert doctor.report(inst, conn, include_disk_bench=False)["findings"] == []


def test_platform_gate_is_honoured(env: Fixture, monkeypatch) -> None:
    conn, inst = env
    monkeypatch.setattr(
        doctor,
        "load_rules",
        lambda: [{"id": "git_missing", "title": "Git", "platforms": ["plan9"]}],
    )
    assert doctor.report(inst, conn, include_disk_bench=False)["findings"] == []


def test_dependency_conflict_detects_mismatch(env: Fixture, monkeypatch) -> None:
    conn, inst = env
    assert inst.comfy_dir is not None
    _req(inst.comfy_dir, "Node-A", "numpy>=1.26\npillow\n")
    _req(inst.comfy_dir, "Node-B", "numpy==1.24\n# comment only\n")
    monkeypatch.setattr(
        doctor, "load_rules", lambda: [{"id": "dependency_conflict", "title": "依赖冲突"}]
    )

    findings = doctor.report(inst, conn, include_disk_bench=False)["findings"]

    assert findings[0]["status"] == "failed"
    assert "numpy" in findings[0]["detail"]


def test_dependency_conflict_passes_when_consistent(env: Fixture, monkeypatch) -> None:
    conn, inst = env
    assert inst.comfy_dir is not None
    _req(inst.comfy_dir, "Node-A", "numpy>=1.26\n")
    _req(inst.comfy_dir, "Node-B", "numpy>=1.26\n")
    monkeypatch.setattr(
        doctor, "load_rules", lambda: [{"id": "dependency_conflict", "title": "依赖冲突"}]
    )

    findings = doctor.report(inst, conn, include_disk_bench=False)["findings"]

    assert findings[0]["status"] == "passed"


def test_pagefile_check_uses_adapter(env: Fixture, monkeypatch) -> None:
    conn, inst = env

    class _Adapter:
        def pagefile_paths(self) -> list[Path]:
            return [Path("D:/pagefile.sys")]

        def disk_kind(self, path: Path) -> str:  # noqa: ARG002
            return "hdd"

        def power_plan_warning(self) -> str | None:
            return None

    monkeypatch.setattr(doctor, "get_platform", lambda: _Adapter())
    monkeypatch.setattr(
        doctor, "load_rules", lambda: [{"id": "pagefile_on_slow_disk", "title": "页面文件"}]
    )

    findings = doctor.report(inst, conn, include_disk_bench=False)["findings"]

    assert findings[0]["status"] == "failed"
    assert "pagefile.sys" in findings[0]["detail"]


def test_weights_check_only_runs_on_request(env: Fixture, monkeypatch) -> None:
    conn, inst = env
    monkeypatch.setattr(
        doctor, "load_rules", lambda: [{"id": "weights_exceed_ram", "title": "权重"}]
    )
    assert doctor.report(inst, conn, include_disk_bench=False)["findings"] == []
    assert doctor.report(inst, conn, include_disk_bench=False, include_model_scan=True)["findings"]


def test_local_state_lists_rule_files() -> None:
    state = rules_sync.local_state()
    names = {item["name"] for item in state["files"]}
    assert {"doctor_rules.yaml", "torch_matrix.json"} <= names
    assert all(item["exists"] for item in state["files"])


class _Response:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *args: object) -> bool:
        return False


def test_update_dry_run_reports_changes(monkeypatch) -> None:
    payload = json.dumps({"schema_version": 2, "stacks": []}).encode("utf-8")

    def _open(url, timeout=None):  # noqa: ANN001
        return _Response(payload)

    monkeypatch.setattr(rules_sync.urllib.request, "urlopen", _open)
    outcome = rules_sync.update("https://example.test/rules", ["torch_matrix.json"])

    assert outcome["ok"] is True
    assert outcome["applied_now"] is False
    assert "torch_matrix.json" in outcome["changed"]
    original = (rules_sync.rules_dir() / "torch_matrix.json").read_bytes()
    assert original != payload, "dry-run must not overwrite rules"


def test_update_apply_writes_and_backs_up(monkeypatch, tmp_path: Path) -> None:
    payload = json.dumps({"schema_version": 2, "stacks": []}).encode("utf-8")
    monkeypatch.setattr(
        rules_sync.urllib.request,
        "urlopen",
        lambda url, timeout=None: _Response(payload),  # noqa: ARG005
    )
    monkeypatch.setattr(rules_sync, "rules_dir", lambda: tmp_path)
    target = tmp_path / "torch_matrix.json"
    target.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")

    outcome = rules_sync.update("https://example.test/rules", ["torch_matrix.json"], apply=True)

    assert outcome["applied_now"] is True
    assert json.loads(target.read_text(encoding="utf-8"))["schema_version"] == 2
    assert (tmp_path / "torch_matrix.json.bak").exists()


def test_update_rejects_broken_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        rules_sync.urllib.request,
        "urlopen",
        lambda url, timeout=None: _Response(b"<html>not json</html>"),  # noqa: ARG005
    )
    outcome = rules_sync.update("https://example.test/rules", ["torch_matrix.json"])
    assert outcome["ok"] is False
    assert "unparseable" in outcome["files"][0]["error"]


def test_update_reports_network_failure(monkeypatch) -> None:
    import urllib.error

    def _boom(url, timeout=None):  # noqa: ANN001
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(rules_sync.urllib.request, "urlopen", _boom)
    outcome = rules_sync.update("https://example.test/rules", ["mirrors.json"])
    assert outcome["ok"] is False
    assert "download failed" in outcome["files"][0]["error"]


@pytest.mark.parametrize("name", ["doctor_rules.yaml", "log_patterns.yaml"])
def test_known_files_are_listed(name: str) -> None:
    assert name in rules_sync.known_files()
