"""Process supervision.

State lives in SQLite (`proc_state`), never inferred from port scans.
Stopping kills the whole process tree, never "whatever owns the port".
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import psutil

from zui.core import pidfile
from zui.core.instance import Instance
from zui.core.launch_env import build_child_env
from zui.core.pidfile import ProcState
from zui.platform import get_platform

READY_TIMEOUT = 120.0


class Busy(ValueError):
    """The instance already has a live process."""


def is_alive(pid: int) -> bool:
    try:
        return bool(psutil.pid_exists(pid) and psutil.Process(pid).status() != psutil.STATUS_ZOMBIE)
    except psutil.NoSuchProcess:
        return False


def port_available(port: int, host: str = "127.0.0.1", timeout: float = 0.2) -> bool:
    """True when nothing is serving on `port`.

    A connect probe is used instead of a bind probe: on Windows SO_REUSEADDR
    lets a second socket bind to a port that is already being served.
    """
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(timeout)
        try:
            probe.connect((host, port))
        except OSError:
            return True
    return False


def next_free_port(preferred: int = 8188, host: str = "127.0.0.1", tries: int = 100) -> int | None:
    """First free port at or above `preferred`, so several instances can coexist."""
    for candidate in range(preferred, preferred + tries):
        if port_available(candidate, host):
            return candidate
    return None


def port_owner(port: int) -> str | None:
    """Human-readable owner of a listening port, for diagnostics only."""
    for conn in psutil.net_connections(kind="inet"):
        if conn.laddr and conn.laddr.port == port and conn.pid:
            try:
                name = psutil.Process(conn.pid).name()
            except psutil.NoSuchProcess:
                name = "unknown"
            return f"{name} (pid={conn.pid})"
    return None


class Supervisor:
    def __init__(self) -> None:
        self.adapter = get_platform()

    def start(
        self,
        conn: sqlite3.Connection,
        instance: Instance,
        argv: list[str],
        *,
        port: int | None = None,
        log_path: Path | None = None,
        pypi_mirror: str | None = None,
        hf_mirror: str | None = None,
    ) -> ProcState:
        existing = pidfile.read(conn, instance.id)
        if existing is not None and is_alive(existing.pid):
            raise Busy(f"instance already running (pid={existing.pid})")

        target_log = log_path or _default_log(instance)
        cwd = instance.comfy_dir or instance.root
        env = build_child_env(
            pypi_mirror=pypi_mirror,
            hf_mirror=hf_mirror,
            search_roots=[instance.root, instance.root.parent],
        )
        pid = self.adapter.spawn_detached(argv, cwd, env, stdout=target_log)
        state = ProcState(
            instance_id=instance.id,
            pid=pid,
            started_at=pidfile_timestamp(),
            heartbeat_at=pidfile_timestamp(),
            port=port,
            log_path=target_log,
            argv=list(argv),
            cwd=cwd,
        )
        pidfile.write(conn, state)
        return state

    def stop(self, conn: sqlite3.Connection, instance: Instance, grace_sec: float = 10.0) -> bool:
        state = pidfile.read(conn, instance.id)
        if state is None:
            pidfile.clear(conn, instance.id)
            return True
        if not is_alive(state.pid):
            pidfile.clear(conn, instance.id)
            return True
        killed = self.adapter.kill_tree(state.pid, grace_sec=grace_sec)
        deadline = time.monotonic() + 3.0
        while killed and time.monotonic() < deadline and is_alive(state.pid):
            time.sleep(0.2)
        pidfile.clear(conn, instance.id)
        return killed and not is_alive(state.pid)

    def status(self, conn: sqlite3.Connection, instance: Instance) -> dict[str, object]:
        state = pidfile.read(conn, instance.id)
        if state is None:
            return {"running": False, "pid": None}
        alive = is_alive(state.pid)
        return {
            "running": alive,
            "pid": state.pid if alive else None,
            "port": state.port,
            "started_at": state.started_at,
            "log_path": str(state.log_path) if state.log_path else None,
            "stale": not alive,
        }


def pidfile_timestamp() -> str:
    from zui.store import repo

    return repo.now_iso()


def _default_log(instance: Instance) -> Path:
    from zui.store import repo

    target = repo.log_dir() / f"{instance.id}.log"
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


__all__ = [
    "Busy",
    "Supervisor",
    "is_alive",
    "next_free_port",
    "port_available",
    "port_owner",
]
