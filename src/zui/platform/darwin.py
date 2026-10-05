"""macOS adapter: process groups, same strategy as Linux."""

from __future__ import annotations

import subprocess
from pathlib import Path

from zui.platform.base import BasePlatformAdapter, PlatformError, psutil_tree_kill


class DarwinAdapter(BasePlatformAdapter):
    name = "darwin"

    def app_data_dir(self) -> Path:
        return Path.home() / "Library" / "Application Support" / "zui"

    def default_instance_root(self) -> Path:
        return Path.home() / "Library" / "Application Support" / "zui" / "instances"

    def spawn_detached(
        self,
        argv: list[str],
        cwd: Path,
        env: dict[str, str],
        *,
        new_group: bool = True,
        stdout: Path | None = None,
    ) -> int:
        if not Path(cwd).exists():
            raise PlatformError(f"working directory does not exist: {cwd}")

        log_handle = None
        try:
            if stdout is not None:
                stdout.parent.mkdir(parents=True, exist_ok=True)
                log_handle = open(stdout, "ab", buffering=0)  # noqa: SIM115
                popen = subprocess.Popen(  # noqa: S603
                    argv,
                    cwd=str(cwd),
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    start_new_session=new_group,
                )
            else:
                popen = subprocess.Popen(  # noqa: S603
                    argv,
                    cwd=str(cwd),
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=new_group,
                )
        finally:
            if log_handle is not None:
                log_handle.close()
        return int(popen.pid)

    def kill_tree(self, pid: int, grace_sec: float = 5.0) -> bool:
        return psutil_tree_kill(pid, grace_sec)

    def venv_bindir(self) -> str:
        return "bin"


__all__ = ["DarwinAdapter"]
