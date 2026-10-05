"""Torch stack swapping: plan, install with rollback, and fresh env creation."""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import Fixture
from zui.core import env_runtime


class _Ok:
    returncode = 0
    stderr = ""
    stdout = ""


class _Fail:
    returncode = 1
    stderr = "no matching distribution"
    stdout = ""


@pytest.fixture()
def recording(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    calls: list[list[str]] = []

    def _run(command, **kwargs):  # noqa: ANN001, ANN003
        calls.append(list(command))
        return _Ok()

    monkeypatch.setattr(env_runtime.subprocess, "run", _run)
    return calls


@pytest.fixture()
def healthy_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        env_runtime,
        "probe_python",
        lambda *args, **kwargs: {"ok": True, "python": "3.13.12", "torch": "2.12.1+cu130"},
    )


def test_stack_plan_builds_command(venv_env: Fixture) -> None:
    conn, inst = venv_env
    plan = env_runtime.stack_plan(inst, stack_id="nvidia-cu130")

    assert plan["ok"] is True
    assert "torch==2.12.1+cu130" in plan["requirements"]
    assert "--index-url" in plan["command"]
    assert str(inst.python) in plan["command"]


def test_stack_plan_rejects_unknown_stack(venv_env: Fixture) -> None:
    conn, inst = venv_env
    assert env_runtime.stack_plan(inst, stack_id="nope")["ok"] is False


def test_stack_plan_honours_mirror_override(venv_env: Fixture) -> None:
    conn, inst = venv_env
    plan = env_runtime.stack_plan(inst, stack_id="nvidia-cu130", index_url="https://mirror")
    assert plan["command"][plan["command"].index("--index-url") + 1] == "https://mirror"


def test_install_stack_dry_run_does_not_execute(venv_env: Fixture, monkeypatch) -> None:
    conn, inst = venv_env

    def _boom(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("dry-run must not install")

    monkeypatch.setattr(env_runtime.subprocess, "run", _boom)
    outcome = env_runtime.install_stack(inst, stack_id="nvidia-cu130", dry_run=True)
    assert outcome["applied_now"] is False


def test_install_stack_verifies_and_reports(
    venv_env: Fixture, recording: list[list[str]], healthy_probe: None
) -> None:
    conn, inst = venv_env
    outcome = env_runtime.install_stack(inst, stack_id="nvidia-cu130")

    assert outcome["ok"] is True
    assert outcome["applied_now"] is True
    assert outcome["torch"] == "2.12.1+cu130"
    assert len(recording) == 1


def test_install_stack_rolls_back_on_failure(
    venv_env: Fixture, recording: list[list[str]], monkeypatch
) -> None:
    conn, inst = venv_env
    # First probe = versions already installed, second probe = post-install check.
    probes = [
        {"ok": True, "python": "3.13.12", "torch": "2.11.0+cu130"},
        {"ok": True, "python": "3.13.12", "torch": None},
    ]
    monkeypatch.setattr(env_runtime, "probe_python", lambda *args, **kwargs: probes.pop(0))

    outcome = env_runtime.install_stack(inst, stack_id="nvidia-cu130")

    assert outcome["ok"] is False
    assert outcome["rolled_back"]["ok"] is True
    assert len(recording) == 2, "a failed install must trigger a rollback install"


def test_install_stack_reports_install_failure(venv_env: Fixture, monkeypatch) -> None:
    conn, inst = venv_env
    monkeypatch.setattr(env_runtime.subprocess, "run", lambda command, **kwargs: _Fail())
    outcome = env_runtime.install_stack(inst, stack_id="nvidia-cu130")
    assert outcome["ok"] is False
    assert outcome["error"] == "install failed"


def test_install_stack_without_interpreter(env: Fixture) -> None:
    conn, inst = env
    assert env_runtime.install_stack(inst, stack_id="nvidia-cu130")["ok"] is False


def test_create_env_dry_run_creates_nothing(tmp_path: Path, monkeypatch) -> None:
    def _boom(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("dry-run must not create a venv")

    monkeypatch.setattr(env_runtime.subprocess, "run", _boom)
    outcome = env_runtime.create_env("fresh", "nvidia-cu130", parent=tmp_path)

    assert outcome["applied_now"] is False
    assert not (tmp_path / "fresh").exists()
    assert [step["kind"] for step in outcome["steps"]] == ["venv", "torch"]


def test_create_env_apply_runs_both_steps(
    tmp_path: Path, recording: list[list[str]], healthy_probe: None
) -> None:
    outcome = env_runtime.create_env("fresh", "nvidia-cu130", parent=tmp_path, apply=True)

    assert outcome["applied_now"] is True
    assert len(recording) == 2
    assert "adopt" in outcome["next"]


def test_create_env_reports_failure(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(env_runtime.subprocess, "run", lambda command, **kwargs: _Fail())
    outcome = env_runtime.create_env("fresh", "nvidia-cu130", parent=tmp_path, apply=True)

    assert outcome["ok"] is False
    assert outcome["error"] == "venv step failed"


def test_create_env_rejects_unknown_stack(tmp_path: Path) -> None:
    assert env_runtime.create_env("x", "nope", parent=tmp_path)["ok"] is False


def test_repair_routes_stack_change(
    venv_env: Fixture, recording: list[list[str]], healthy_probe: None
) -> None:
    conn, inst = venv_env
    outcome = env_runtime.apply_repair(inst, "reinstall_torch_stack", stack_id="nvidia-cu130")
    assert outcome["applied_now"] is True


def test_plan_repair_previews_stack_command(venv_env: Fixture, monkeypatch) -> None:
    conn, inst = venv_env
    monkeypatch.setattr(
        env_runtime,
        "probe_python",
        lambda *args, **kwargs: {"ok": True, "python": "3.13.12", "torch": "0.0.0+zz99"},
    )

    plan = env_runtime.plan_repair(inst)
    stack_change = next(
        (item for item in plan["changes"] if item["id"] == "reinstall_torch_stack"), None
    )

    assert stack_change is not None
    assert stack_change["to"] == "nvidia-cu130"
    assert stack_change["command"], "the planned install command must be shown"
