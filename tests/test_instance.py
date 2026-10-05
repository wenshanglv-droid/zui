"""Instance registry: adopt, activate and forget (never touching files)."""

from __future__ import annotations

import socket

import pytest

from conftest import Fixture
from zui.core import instance as instance_mod
from zui.core import proc


def _busy_port() -> int:
    """Reserve a real port so allocation logic is exercised for real."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as holder:
        holder.bind(("127.0.0.1", 0))
        holder.listen(1)
        return int(holder.getsockname()[1])


def test_adopt_registers_layout(env: Fixture) -> None:
    conn, inst = env
    assert inst.id == "test-instance"
    assert inst.comfy_dir is not None
    assert instance_mod.get_instance(conn, "test-instance") is not None


def test_set_active_moves_the_pointer(env: Fixture) -> None:
    conn, inst = env
    active = instance_mod.set_active(conn, inst.id)
    assert active.active is True
    assert instance_mod.active_instance(conn) is not None


def test_forget_dry_run_then_remove(env: Fixture) -> None:
    conn, inst = env
    assert instance_mod.get_instance(conn, inst.id) is not None

    removed = instance_mod.forget(conn, inst.id)

    assert removed.id == inst.id
    assert instance_mod.get_instance(conn, inst.id) is None


def test_forget_unknown_ref(env: Fixture) -> None:
    conn, inst = env
    with pytest.raises(KeyError):
        instance_mod.forget(conn, "nope")


def test_forget_refuses_a_running_instance(env: Fixture, monkeypatch) -> None:
    conn, inst = env
    from zui.core import pidfile
    from zui.core.pidfile import ProcState

    pidfile.write(
        conn,
        ProcState(instance_id=inst.id, pid=1, started_at="t", heartbeat_at="t", port=8188),
    )
    monkeypatch.setattr(proc, "is_alive", lambda pid: True)

    with pytest.raises(RuntimeError, match="still running"):
        instance_mod.forget(conn, inst.id)


def test_port_available_false_while_serving() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as holder:
        holder.bind(("127.0.0.1", 0))
        holder.listen(1)
        port = int(holder.getsockname()[1])
        assert proc.port_available(port) is False


def test_next_free_port_skips_occupied_ports() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as holder:
        holder.bind(("127.0.0.1", 0))
        holder.listen(1)
        occupied = int(holder.getsockname()[1])
        chosen = proc.next_free_port(occupied)

    assert chosen is not None
    assert chosen != occupied
    assert proc.port_available(chosen) is True


def test_next_free_port_returns_preferred_when_free() -> None:
    preferred = _busy_port()
    assert proc.next_free_port(preferred) == preferred
