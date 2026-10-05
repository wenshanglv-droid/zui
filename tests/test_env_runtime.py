"""Environment inspection and self-repair, including the pyvenv.cfg home rewrite."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from conftest import Fixture, re_adopt
from zui.core import env_runtime
from zui.core import instance as instance_mod


def _make_venv(comfy: Path, home: str) -> Path:
    venv = comfy / ".venv"
    (venv / "Scripts").mkdir(parents=True, exist_ok=True)
    (venv / "Scripts" / "python.exe").write_bytes(b"")
    (venv / "pyvenv.cfg").write_text(
        f"home = {home}\nimplementation = CPython\nversion_info = 3.13.12\n", encoding="utf-8"
    )
    return venv


def _make_base(root: Path) -> Path:
    base = root / "standalone-env"
    base.mkdir(parents=True, exist_ok=True)
    (base / "python.exe").write_bytes(b"")
    return base


def _broken(env: Fixture, home: str = "Z:\\deleted\\standalone-env") -> Fixture:
    """Install whose venv points at a home that no longer exists."""
    conn, inst = env
    assert inst.comfy_dir is not None
    _make_venv(inst.comfy_dir, home)
    return conn, re_adopt(conn, inst)


def test_read_pyvenv_home(tmp_path: Path) -> None:
    venv = _make_venv(tmp_path / "ComfyUI", str(tmp_path / "base"))
    home, raw = env_runtime.read_pyvenv_home(venv)
    assert home == tmp_path / "base"
    assert raw == str(tmp_path / "base")


def test_inspect_flags_missing_home(env: Fixture) -> None:
    conn, inst = _broken(env)

    facts = env_runtime.inspect(inst, conn=conn, bench=False)

    assert "venv_home_missing" in {issue["id"] for issue in facts["issues"]}


def test_inspect_is_clean_on_healthy_venv(env: Fixture, monkeypatch) -> None:
    conn, seeded = env
    assert seeded.comfy_dir is not None
    base = _make_base(seeded.comfy_dir.parent)
    conn, inst = _broken(env, home=str(base))
    monkeypatch.setattr(
        env_runtime,
        "probe_python",
        lambda *args, **kwargs: {"ok": True, "python": "3.13.12", "torch": "2.12.1+cu130"},
    )

    facts = env_runtime.inspect(inst, conn=conn, bench=False)

    assert "venv_home_missing" not in {issue["id"] for issue in facts["issues"]}


def test_plan_repair_points_at_a_directory(env: Fixture) -> None:
    conn, inst = _broken(env)
    base = _make_base(inst.comfy_dir.parent if inst.comfy_dir else inst.root)

    plan = env_runtime.plan_repair(inst)

    change = plan["changes"][0]
    assert change["id"] == "repair_pyvenv_home"
    assert change["possible"] is True
    assert change["to"] == str(base), "PEP 405: home must be a directory, not the exe"


def test_plan_repair_is_impossible_without_base_python(env: Fixture) -> None:
    conn, inst = _broken(env)

    plan = env_runtime.plan_repair(inst)

    assert plan["changes"][0]["possible"] is False


def test_apply_repair_rewrites_home_and_verifies(env: Fixture, monkeypatch) -> None:
    conn, inst = _broken(env)
    assert inst.comfy_dir is not None
    base = _make_base(inst.comfy_dir.parent)
    monkeypatch.setattr(
        env_runtime,
        "probe_python",
        lambda *args, **kwargs: {"ok": True, "python": "3.13.12", "torch": "2.12.1+cu130"},
    )

    outcome = env_runtime.apply_repair(inst, "repair_pyvenv_home")

    assert outcome["ok"] is True
    assert outcome["to"] == str(base)
    cfg = (inst.comfy_dir / ".venv" / "pyvenv.cfg").read_text(encoding="utf-8")
    assert f"home = {base}" in cfg
    assert Path(outcome["backup"]).exists(), "a backup must survive a successful repair"


def test_apply_repair_rolls_back_when_verification_fails(env: Fixture, monkeypatch) -> None:
    conn, inst = _broken(env)
    assert inst.comfy_dir is not None
    _make_base(inst.comfy_dir.parent)
    cfg = inst.comfy_dir / ".venv" / "pyvenv.cfg"
    original = cfg.read_text(encoding="utf-8")
    monkeypatch.setattr(
        env_runtime, "probe_python", lambda *args, **kwargs: {"ok": False, "reason": "boom"}
    )

    outcome = env_runtime.apply_repair(inst, "repair_pyvenv_home")

    assert outcome["ok"] is False
    assert cfg.read_text(encoding="utf-8") == original


def test_apply_repair_rejects_unsupported_change(env: Fixture) -> None:
    conn, inst = _broken(env)
    assert env_runtime.apply_repair(inst, "teleport")["ok"] is False


def test_probe_python_parses_marker(tmp_path: Path, monkeypatch) -> None:
    class _Result:
        stdout = "__ZUI_PROBE__" + '{"python": "3.13.12", "torch": "2.12.1+cu130"}'
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: _Result())
    probe = env_runtime.probe_python(tmp_path / "python.exe", tmp_path)
    assert probe["ok"] is True
    assert probe["torch"] == "2.12.1+cu130"


def test_probe_python_reports_oserror(tmp_path: Path, monkeypatch) -> None:
    def _boom(*args, **kwargs):  # noqa: ANN002, ANN003
        raise OSError("not an executable")

    monkeypatch.setattr(subprocess, "run", _boom)
    probe = env_runtime.probe_python(tmp_path / "python.exe", tmp_path)
    assert probe["ok"] is False
    assert "cannot execute" in probe["reason"]


@pytest.mark.parametrize("version", ["2.12.1+cu130"])
def test_known_stack_is_not_flagged(version: str) -> None:
    assert env_runtime._stack_known(version) is True  # noqa: SLF001


def test_unknown_stack_is_flagged() -> None:
    assert env_runtime._stack_known("0.0.0+zz99") is False  # noqa: SLF001


def test_base_home_prefers_directory(tmp_path: Path) -> None:
    from zui.core.paths import base_home_for, base_python_for

    root = tmp_path / "install"
    base = _make_base(root)
    assert base_home_for(root) == base
    assert base_python_for(root) == base / "python.exe"
    assert base_home_for(tmp_path / "empty") is None


def test_repair_roots_are_deduplicated() -> None:
    from zui.core.instance import Instance

    root = Path("C:/install")
    inst = Instance(id="x", name="x", root=root, comfy_dir=root / "ComfyUI")
    roots = env_runtime._repair_roots(inst)  # noqa: SLF001
    assert len(roots) == len(set(roots))
    assert roots[0] == root


def test_adopt_is_idempotent_for_same_layout(env: Fixture) -> None:
    conn, inst = env
    again = instance_mod.adopt(inst.comfy_dir or inst.root, name=inst.name, conn=conn)
    assert again.id == inst.id
