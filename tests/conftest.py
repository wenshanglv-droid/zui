"""Shared fixtures: a throw-away data dir plus a synthetic ComfyUI install."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from zui.core import instance as instance_mod
from zui.core.instance import Instance
from zui.store import repo

Fixture = tuple[sqlite3.Connection, Instance]


@pytest.fixture()
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "zui-data"
    monkeypatch.setenv("ZUI_DATA_DIR", str(target))
    return target


@pytest.fixture()
def conn(data_dir: Path) -> sqlite3.Connection:
    return repo.connect()


@pytest.fixture()
def comfy(tmp_path: Path) -> Path:
    """A minimal but structurally honest ComfyUI install."""
    root = tmp_path / "install"
    comfy = root / "ComfyUI"
    (comfy / "comfy").mkdir(parents=True)
    (comfy / "main.py").write_text("# synthetic", encoding="utf-8")
    (comfy / "nodes.py").write_text("# synthetic", encoding="utf-8")
    (comfy / "comfyui_version.py").write_text('__version__ = "0.0.0-test"\n', encoding="utf-8")
    (comfy / "models" / "checkpoints").mkdir(parents=True)
    (comfy / "custom_nodes").mkdir(parents=True)
    (comfy / "user" / "default" / "workflows").mkdir(parents=True)
    return comfy


@pytest.fixture()
def env(comfy: Path, conn: sqlite3.Connection) -> Fixture:
    inst = instance_mod.adopt(comfy, name="test-instance", conn=conn)
    return conn, inst


@pytest.fixture()
def venv_env(comfy: Path, conn: sqlite3.Connection) -> Fixture:
    """Same install, but with a venv (and therefore an interpreter) in place."""
    venv = comfy / ".venv"
    (venv / "Scripts").mkdir(parents=True, exist_ok=True)
    (venv / "Scripts" / "python.exe").write_bytes(b"")
    (venv / "pyvenv.cfg").write_text(
        "home = C:\\base-python\nimplementation = CPython\n", encoding="utf-8"
    )
    inst = instance_mod.adopt(comfy, name="test-instance", conn=conn)
    return conn, inst


def re_adopt(conn: sqlite3.Connection, inst: Instance) -> Instance:
    """Re-run layout detection after the fixture mutated the install."""
    assert inst.comfy_dir is not None
    return instance_mod.adopt(inst.comfy_dir, name=inst.name, conn=conn)
