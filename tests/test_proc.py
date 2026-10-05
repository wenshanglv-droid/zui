"""Process supervision: state in SQLite, mirror env reaching the child, tree kill."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from conftest import Fixture
from zui.core import pidfile, proc
from zui.core.instance import Instance
from zui.core.pidfile import ProcState


class _FakeAdapter:
    def __init__(self, pid: int = 4321) -> None:
        self.pid = pid
        self.alive: set[int] = set()
        self.spawned: list[dict[str, object]] = []
        self.killed: list[int] = []

    def spawn_detached(self, argv, cwd, env, stdout=None):  # noqa: ANN001, ANN002, ANN003
        self.spawned.append({"argv": argv, "cwd": cwd, "env": env, "stdout": stdout})
        self.alive.add(self.pid)
        return self.pid

    def kill_tree(self, pid: int, grace_sec: float = 10.0) -> bool:  # noqa: ARG002
        self.killed.append(pid)
        self.alive.discard(pid)
        return True


@pytest.fixture()
def adapter(monkeypatch: pytest.MonkeyPatch) -> _FakeAdapter:
    fake = _FakeAdapter()
    monkeypatch.setattr(proc, "get_platform", lambda: fake)
    monkeypatch.setattr(proc, "is_alive", lambda pid: pid in fake.alive)
    return fake


def test_start_records_state(adapter: _FakeAdapter, env: Fixture) -> None:
    conn, inst = env
    supervisor = proc.Supervisor()

    state = supervisor.start(conn, inst, ["python", "main.py"], port=8188)

    assert state.pid == adapter.pid
    assert state.port == 8188
    assert pidfile.read(conn, inst.id) is not None
    assert adapter.spawned[0]["argv"] == ["python", "main.py"]


def test_start_passes_mirror_into_child_env(adapter: _FakeAdapter, env: Fixture) -> None:
    conn, inst = env
    supervisor = proc.Supervisor()

    supervisor.start(conn, inst, ["python"], pypi_mirror="tsinghua", hf_mirror="hf-mirror")

    env_map: dict[str, str] = adapter.spawned[0]["env"]  # type: ignore[assignment]
    assert "tsinghua" in env_map["PIP_INDEX_URL"]
    assert env_map["UV_INDEX_URL"] == env_map["PIP_INDEX_URL"]
    assert env_map["HF_ENDPOINT"] == "https://hf-mirror.com"
    assert env_map["PYTHONUTF8"] == "1"


def test_start_refuses_a_second_instance(adapter: _FakeAdapter, env: Fixture) -> None:
    conn, inst = env
    supervisor = proc.Supervisor()
    supervisor.start(conn, inst, ["python"])

    with pytest.raises(proc.Busy):
        supervisor.start(conn, inst, ["python"])


def test_status_reports_stale_after_death(adapter: _FakeAdapter, env: Fixture) -> None:
    conn, inst = env
    supervisor = proc.Supervisor()
    supervisor.start(conn, inst, ["python"], port=8188)

    adapter.alive.clear()  # process died on its own
    status = supervisor.status(conn, inst)

    assert status["running"] is False
    assert status["stale"] is True


def test_stop_kills_tree_and_clears_state(adapter: _FakeAdapter, env: Fixture) -> None:
    conn, inst = env
    supervisor = proc.Supervisor()
    supervisor.start(conn, inst, ["python"])
    pid = adapter.pid

    stopped = supervisor.stop(conn, inst)

    assert stopped is True
    assert pid in adapter.killed
    assert pidfile.read(conn, inst.id) is None


def test_stop_is_idempotent(adapter: _FakeAdapter, env: Fixture) -> None:
    conn, inst = env
    supervisor = proc.Supervisor()
    assert supervisor.stop(conn, inst) is True


def test_pidfile_round_trip(conn: sqlite3.Connection) -> None:
    state = ProcState(
        instance_id="x",
        pid=99,
        started_at="now",
        heartbeat_at="now",
        port=8188,
        log_path=Path("/tmp/x.log"),
        argv=["python"],
        cwd=Path("/tmp"),
    )
    pidfile.write(conn, state)
    loaded = pidfile.read(conn, "x")

    assert loaded is not None
    assert loaded.pid == 99
    assert loaded.port == 8188
    assert loaded.log_path == Path("/tmp/x.log")
    assert loaded.argv == ["python"]

    pidfile.touch(conn, "x")
    pidfile.clear(conn, "x")
    assert pidfile.read(conn, "x") is None


def test_pidfile_lists_all_states(conn: sqlite3.Connection) -> None:
    for name in ("a", "b"):
        pidfile.write(
            conn,
            ProcState(instance_id=name, pid=1, started_at="t", heartbeat_at="t"),
        )
    assert {state.instance_id for state in pidfile.list_states(conn)} == {"a", "b"}


def test_default_log_lives_in_data_dir(env: Fixture) -> None:
    conn, inst = env
    log = proc._default_log(inst)  # noqa: SLF001
    assert log.parent.is_dir()
    assert log.name == f"{inst.id}.log"


def test_port_owner_is_diagnostic_only() -> None:
    assert proc.port_owner(1) in (None, "") or isinstance(proc.port_owner(1), str)


def test_status_without_state(env: Fixture) -> None:
    conn, inst = env
    status = proc.Supervisor().status(conn, Instance(id=inst.id, name=inst.name, root=inst.root))
    assert status == {"running": False, "pid": None}
