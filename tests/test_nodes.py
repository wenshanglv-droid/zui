"""Custom node management: list, install, update, rollback and enable/disable."""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import Fixture
from zui.core import nodes


@pytest.fixture()
def fake_git(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    calls: list[list[str]] = []

    def _run(command, cwd=None):  # noqa: ANN001, ANN002, ANN003
        calls.append(list(command))
        joined = " ".join(command)
        text = ""
        if len(command) > 1 and command[1] == "clone":
            # Pretend the clone produced a node with requirements.
            cloned = Path(command[-1])
            cloned.mkdir(parents=True, exist_ok=True)
            (cloned / "requirements.txt").write_text("numpy\n", encoding="utf-8")
        elif "rev-parse" in joined:
            text = "abc1234"
        elif "log" in joined:
            text = "abc1234\ndef5678\nghi9012"
        return {"ok": True, "command": list(command), "stdout": text, "stderr": ""}

    monkeypatch.setattr(nodes, "_run", _run)
    monkeypatch.setattr(nodes, "git_exe", lambda instance: "git")  # noqa: ARG005
    monkeypatch.setattr(nodes.shutil, "which", lambda name: "uv")
    return calls


def _node(comfy: Path, name: str, *, git: bool = False, requirements: bool = False) -> Path:
    entry = comfy / "custom_nodes" / name
    entry.mkdir(parents=True, exist_ok=True)
    if git:
        (entry / ".git").mkdir(exist_ok=True)
    if requirements:
        (entry / "requirements.txt").write_text("numpy\n", encoding="utf-8")
    return entry


def test_list_reports_state(env: Fixture, fake_git: list[list[str]]) -> None:
    conn, inst = env
    assert inst.comfy_dir is not None
    _node(inst.comfy_dir, "ComfyUI-AnimateDiff", git=True, requirements=True)
    _node(inst.comfy_dir, "ComfyUI-Old.disabled")
    (inst.comfy_dir / "custom_nodes" / "__pycache__").mkdir()

    listing = nodes.list_nodes(inst)

    by_name = {item["name"]: item for item in listing["nodes"]}
    assert by_name["ComfyUI-AnimateDiff"]["enabled"] is True
    assert by_name["ComfyUI-AnimateDiff"]["commit"] == "abc1234"
    assert by_name["ComfyUI-AnimateDiff"]["requirements"] is True
    assert by_name["ComfyUI-Old.disabled"]["enabled"] is False
    assert "__pycache__" not in by_name


def test_list_without_comfy_dir() -> None:
    from zui.core.instance import Instance

    assert nodes.list_nodes(Instance(id="x", name="x", root=Path("/x")))["nodes"] == []


def test_install_dry_run_only_previews(env: Fixture, fake_git: list[list[str]]) -> None:
    conn, inst = env
    outcome = nodes.install(inst, "https://github.com/a/ComfyUI-Demo.git")

    assert outcome["applied_now"] is False
    assert any("clone" in command for command in outcome["commands"])
    assert not fake_git, "dry-run must not run git"


def test_install_apply_clones_and_installs_requirements(
    venv_env: Fixture, fake_git: list[list[str]]
) -> None:
    conn, inst = venv_env
    outcome = nodes.install(inst, "https://github.com/a/ComfyUI-Demo.git", apply=True)

    assert outcome["ok"] is True
    assert outcome["applied_now"] is True
    assert any("clone" in command for command in fake_git)
    assert any("-r" in command for command in fake_git), "requirements must be installed"


def test_install_refuses_existing_target(env: Fixture, fake_git: list[list[str]]) -> None:
    conn, inst = env
    assert inst.comfy_dir is not None
    occupied = inst.comfy_dir / "custom_nodes" / "ComfyUI-Demo"
    occupied.mkdir(parents=True)
    (occupied / "keep.txt").write_text("x", encoding="utf-8")

    outcome = nodes.install(inst, "https://github.com/a/ComfyUI-Demo.git", apply=True)

    assert outcome["ok"] is False
    assert "already exists" in outcome["error"]


def test_install_without_git(env: Fixture, monkeypatch) -> None:
    conn, inst = env
    monkeypatch.setattr(nodes, "git_exe", lambda instance: None)  # noqa: ARG005
    outcome = nodes.install(inst, "https://github.com/a/x.git")
    assert outcome["ok"] is False
    assert "git" in outcome["error"]


def test_update_dry_run(env: Fixture, fake_git: list[list[str]]) -> None:
    conn, inst = env
    assert inst.comfy_dir is not None
    _node(inst.comfy_dir, "ComfyUI-Demo", git=True, requirements=True)

    outcome = nodes.update(inst, "ComfyUI-Demo")

    assert outcome["applied_now"] is False
    assert any("pull" in command for command in outcome["commands"])
    assert not any("pull" in command for command in fake_git), "dry-run must not pull"


def test_update_apply_reports_commits(env: Fixture, fake_git: list[list[str]]) -> None:
    conn, inst = env
    assert inst.comfy_dir is not None
    _node(inst.comfy_dir, "ComfyUI-Demo", git=True)

    outcome = nodes.update(inst, "ComfyUI-Demo", apply=True)

    assert outcome["ok"] is True
    assert outcome["from"] == outcome["to"]  # fake git keeps HEAD stable
    assert any("pull" in command for command in fake_git)


def test_update_rejects_non_git_node(env: Fixture, fake_git: list[list[str]]) -> None:
    conn, inst = env
    assert inst.comfy_dir is not None
    _node(inst.comfy_dir, "ComfyUI-Demo")
    assert nodes.update(inst, "ComfyUI-Demo", apply=True)["ok"] is False


def test_rollback_targets_previous_commit(env: Fixture, fake_git: list[list[str]]) -> None:
    conn, inst = env
    assert inst.comfy_dir is not None
    _node(inst.comfy_dir, "ComfyUI-Demo", git=True)

    outcome = nodes.rollback(inst, "ComfyUI-Demo")

    assert outcome["to"] == "def5678"
    assert outcome["from"] == "abc1234"
    assert any("checkout" in command for command in outcome["commands"])
    assert not any(
        "checkout" in command for command in fake_git
    ), "dry-run must not checkout"


def test_rollback_apply(env: Fixture, fake_git: list[list[str]]) -> None:
    conn, inst = env
    assert inst.comfy_dir is not None
    _node(inst.comfy_dir, "ComfyUI-Demo", git=True)

    outcome = nodes.rollback(inst, "ComfyUI-Demo", apply=True)

    assert outcome["ok"] is True
    assert any("checkout" in command for command in fake_git)


def test_rollback_without_history(env: Fixture, monkeypatch) -> None:
    conn, inst = env
    assert inst.comfy_dir is not None
    entry = _node(inst.comfy_dir, "ComfyUI-Demo", git=True)
    monkeypatch.setattr(nodes, "git_exe", lambda instance: "git")  # noqa: ARG005
    monkeypatch.setattr(
        nodes, "_run", lambda command, cwd=None: {"ok": True, "command": command, "stdout": ""}
    )

    outcome = nodes.rollback(inst, "ComfyUI-Demo", apply=True)

    assert outcome["ok"] is False
    assert "no earlier commit" in outcome["error"]
    assert entry.exists()


def test_set_enabled_renames_directory(env: Fixture) -> None:
    conn, inst = env
    assert inst.comfy_dir is not None
    _node(inst.comfy_dir, "ComfyUI-Demo")

    preview = nodes.set_enabled(inst, "ComfyUI-Demo", False)
    assert preview["applied_now"] is False
    assert (inst.comfy_dir / "custom_nodes" / "ComfyUI-Demo").exists()

    applied = nodes.set_enabled(inst, "ComfyUI-Demo", False, apply=True)
    assert applied["ok"] is True
    disabled = inst.comfy_dir / "custom_nodes" / "ComfyUI-Demo.disabled"
    assert disabled.exists()

    back = nodes.set_enabled(inst, "ComfyUI-Demo.disabled", True, apply=True)
    assert back["ok"] is True
    assert (inst.comfy_dir / "custom_nodes" / "ComfyUI-Demo").exists()


def test_set_enabled_on_missing_node(env: Fixture) -> None:
    conn, inst = env
    assert nodes.set_enabled(inst, "nope", True, apply=True)["ok"] is False


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("https://github.com/a/ComfyUI-Demo.git", "ComfyUI-Demo"),
        ("https://github.com/a/ComfyUI-Demo", "ComfyUI-Demo"),
        ("C:\\nodes\\ComfyUI-Demo", "ComfyUI-Demo"),
    ],
)
def test_repo_name(source: str, expected: str) -> None:
    assert nodes._repo_name(source) == expected  # noqa: SLF001
