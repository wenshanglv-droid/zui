"""One-click acceleration: dry-run by default, real install on --apply, rollback on failure."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from conftest import Fixture
from zui.core import accelerate, env_runtime, profile
from zui.core.instance import Instance


class _Result:
    def __init__(self, returncode: int = 0, stderr: str = "") -> None:
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = ""


@pytest.fixture()
def fake_uv(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    calls: list[list[str]] = []

    def _run(command, **kwargs):  # noqa: ANN001, ANN003
        calls.append(list(command))
        return _Result()

    import subprocess

    monkeypatch.setattr(subprocess, "run", _run)
    monkeypatch.setattr(accelerate.shutil, "which", lambda name: "uv" if name == "uv" else None)
    return calls


def test_install_command_carries_index_url(env: Fixture) -> None:
    conn, inst = env
    command = accelerate.install_command(inst, "sage-attention", "https://mirror/simple")
    assert "--index-url" in command
    assert command[command.index("--index-url") + 1] == "https://mirror/simple"
    assert command[-1] == "sage-attention"


def test_install_command_without_mirror(env: Fixture) -> None:
    conn, inst = env
    assert "--index-url" not in accelerate.install_command(inst, "sage-attention")


def test_preview_does_not_install(env: Fixture, monkeypatch) -> None:
    conn, inst = env

    def _boom(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("preview must not run an install")

    monkeypatch.setattr(accelerate.subprocess, "run", _boom)
    preview = accelerate.preview_rule(
        inst, conn, "sage-attention-ampere", index_url="https://mirror/simple"
    )

    assert preview["ok"] is True
    assert preview["applied_now"] is False
    assert preview["package"] == "sage-attention"
    assert "--use-sage-attention" in preview["args_added"]
    assert preview["command"][-1] == "sage-attention"


def test_preview_reports_unknown_rule(env: Fixture) -> None:
    conn, inst = env
    assert accelerate.preview_rule(inst, conn, "nope")["ok"] is False


def test_apply_installs_and_wires_flag(
    venv_env: Fixture, fake_uv: list[list[str]], monkeypatch
) -> None:
    conn, inst = venv_env
    monkeypatch.setattr(
        env_runtime,
        "inspect",
        lambda *args, **kwargs: {"packages": {"sage-attention": "2.0.0"}, "issues": []},
    )

    outcome = accelerate.apply_rule(
        inst, conn, "sage-attention-ampere", index_url="https://mirror/simple"
    )

    assert outcome["ok"] is True
    assert "--index-url" in fake_uv[0]
    assert outcome["index_url"] == "https://mirror/simple"
    saved = profile.get(conn, inst.id, "default")
    assert "--use-sage-attention" in saved["args"]


def test_apply_failure_returns_error(
    venv_env: Fixture, fake_uv: list[list[str]], monkeypatch
) -> None:
    conn, inst = venv_env

    def _fail(command, **kwargs):  # noqa: ANN001, ANN003
        fake_uv.append(list(command))
        return _Result(returncode=1, stderr="no matching distribution")

    monkeypatch.setattr(accelerate.subprocess, "run", _fail)
    outcome = accelerate.apply_rule(inst, conn, "sage-attention-ampere")

    assert outcome["ok"] is False
    assert outcome["error"] == "install failed"


def test_apply_rolls_back_flags_when_not_importable(
    venv_env: Fixture, fake_uv: list[list[str]], monkeypatch
) -> None:
    conn, inst = venv_env
    monkeypatch.setattr(
        env_runtime, "inspect", lambda *args, **kwargs: {"packages": {}, "issues": []}
    )

    outcome = accelerate.apply_rule(inst, conn, "sage-attention-ampere")

    assert outcome["ok"] is False
    assert "not importable" in outcome["error"]
    assert "--use-sage-attention" not in profile.get(conn, inst.id, "default")["args"]


def test_apply_without_interpreter(fake_uv: list[list[str]], conn: sqlite3.Connection) -> None:
    inst = Instance(id="bare", name="bare", root=Path("/bare"))
    outcome = accelerate.apply_rule(inst, conn, "sage-attention-ampere")
    assert outcome["ok"] is False
    assert "no interpreter" in outcome["error"]


def test_analyze_lists_candidates(env: Fixture, monkeypatch) -> None:
    conn, inst = env
    monkeypatch.setattr(
        env_runtime, "inspect", lambda *args, **kwargs: {"packages": {}, "issues": []}
    )
    analysis = accelerate.analyze(inst, conn)
    assert analysis["instance"] == inst.id
    assert {"sage-attention-ampere", "xformers"} <= {item["id"] for item in analysis["candidates"]}
