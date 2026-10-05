"""The whole process tree must die, on every platform, without port scans."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import psutil
import pytest

from zui.core.launch_env import build_child_env
from zui.platform import get_platform

SPAWNER = Path(__file__).with_name("spawn_tree.py")
TREE_DEPTH = 3


def _wait_for_pids(path: Path, count: int, timeout: float = 25.0) -> list[int]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            raw = [
                line.strip()
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            if len(raw) >= count:
                return [int(value) for value in raw]
        time.sleep(0.2)
    return [
        int(line.strip()) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def _alive(pids: list[int]) -> list[int]:
    return [pid for pid in pids if psutil.pid_exists(pid)]


def test_kill_tree_leaves_no_descendants(tmp_path: Path) -> None:
    adapter = get_platform()
    pid_file = tmp_path / "pids.txt"

    root = adapter.spawn_detached(
        [sys.executable, str(SPAWNER), str(TREE_DEPTH), str(pid_file)],
        Path(tmp_path),
        dict(os.environ),
    )
    pids = _wait_for_pids(pid_file, TREE_DEPTH)
    assert len(pids) == TREE_DEPTH, f"expected a {TREE_DEPTH} level tree, saw {pids}"
    # On Windows a venv `python.exe` is a shim that re-execs the real interpreter,
    # so the pid we get back is the shim, not the writer of the pid file. Track both.
    tracked = pids + [root]
    assert len(_alive(tracked)) >= TREE_DEPTH, "spawned processes died before the kill"

    try:
        assert adapter.kill_tree(root, grace_sec=5.0)
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline and _alive(tracked):
            time.sleep(0.3)
        assert not _alive(tracked), f"processes survived kill_tree: {_alive(tracked)}"
    finally:
        # Never leak a python process, even if the assertion above fails.
        adapter.kill_tree(root, grace_sec=2.0)


def test_spawn_injects_environment(tmp_path: Path) -> None:
    """The child must receive a real environment, never an empty one."""
    adapter = get_platform()
    out = tmp_path / "env.txt"
    code = (
        "import os,pathlib;"
        f"pathlib.Path(r'{out}').write_text("
        "os.environ.get('PYTHONUTF8','') + '|' + str(len(os.environ.get('PATH','')) > 0)"
        ", encoding='utf-8')"
    )
    pid = adapter.spawn_detached(
        [sys.executable, "-c", code],
        Path(tmp_path),
        build_child_env(),
    )
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline and not out.exists():
        if not psutil.pid_exists(pid):
            break
        time.sleep(0.2)
    try:
        assert out.exists(), "child never wrote its environment snapshot"
        value = out.read_text(encoding="utf-8")
        assert value.startswith("1|True"), f"child environment was not injected: {value!r}"
    finally:
        adapter.kill_tree(pid, grace_sec=2.0)


@pytest.mark.parametrize("argv", [[sys.executable, "-c", "import time; time.sleep(30)"]])
def test_spawn_rejects_missing_cwd(tmp_path: Path, argv: list[str]) -> None:
    from zui.platform.base import PlatformError

    adapter = get_platform()
    with pytest.raises(PlatformError):
        adapter.spawn_detached(argv, tmp_path / "nope", dict(os.environ))


def test_stdout_is_captured_to_log(tmp_path: Path) -> None:
    adapter = get_platform()
    log = tmp_path / "child.log"
    pid = adapter.spawn_detached(
        [sys.executable, "-c", "print('zui-hello', flush=True)"],
        Path(tmp_path),
        dict(os.environ),
        stdout=log,
    )
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        if log.exists() and "zui-hello" in log.read_text(encoding="utf-8", errors="replace"):
            break
        if not psutil.pid_exists(pid):
            break
        time.sleep(0.2)
    assert log.exists(), "no log file was created"
    assert "zui-hello" in log.read_text(encoding="utf-8", errors="replace")
