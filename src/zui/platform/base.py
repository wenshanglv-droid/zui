"""Platform abstraction. OS-specific code is allowed ONLY in this package."""

from __future__ import annotations

import os
import time
from contextlib import suppress
from pathlib import Path
from typing import Protocol, runtime_checkable

import psutil


@runtime_checkable
class PtyLike(Protocol):
    """Minimal pseudo-terminal surface (TTY support lands with M0-TTY)."""

    def spawn(self, argv: list[str], cwd: Path, env: dict[str, str]) -> int: ...

    def read(self) -> str: ...

    def write(self, data: str) -> None: ...

    def close(self) -> None: ...


@runtime_checkable
class PlatformAdapter(Protocol):
    """Everything zui needs from an operating system."""

    name: str

    def spawn_detached(
        self,
        argv: list[str],
        cwd: Path,
        env: dict[str, str],
        *,
        new_group: bool = True,
        stdout: Path | None = None,
    ) -> int: ...

    def kill_tree(self, pid: int, grace_sec: float = 5.0) -> bool: ...

    def pty_factory(self) -> PtyLike: ...

    def app_data_dir(self) -> Path: ...

    def default_instance_root(self) -> Path: ...

    def disk_kind(self, path: Path) -> str: ...

    def disk_bench(self, path: Path, mb: int = 64) -> float: ...

    def power_plan_warning(self) -> str | None: ...

    def supports_symlink(self) -> bool: ...

    def pagefile_paths(self) -> list[Path]: ...

    def venv_bindir(self) -> str: ...

    def python_exe_names(self) -> tuple[str, ...]: ...


class PlatformError(RuntimeError):
    """Raised when the platform adapter cannot fulfil a request."""


class BasePlatformAdapter:
    """Shared behaviour. Concrete adapters override what actually differs."""

    name: str = "unknown"

    # --- paths -----------------------------------------------------------

    def app_data_dir(self) -> Path:
        raise NotImplementedError

    def default_instance_root(self) -> Path:
        return Path.home() / "zui-instances"

    # --- disk ------------------------------------------------------------

    def disk_bench(self, path: Path, mb: int = 64) -> float:
        """Approximate sequential throughput in MB/s (write then cold-ish read).

        Small enough to stay quick; classification only needs an order of magnitude.
        """
        directory = path if path.is_dir() else path.parent
        if not directory.exists():
            raise PlatformError(f"cannot benchmark missing path: {directory}")
        tmp = directory / f".zui-bench-{os.getpid()}-{time.time_ns()}.tmp"
        block = b"0" * (1 << 20)
        total = mb * (1 << 20)
        try:
            start = time.perf_counter()
            with open(tmp, "wb", buffering=0) as handle:
                written = 0
                while written < total:
                    written += handle.write(block)
                handle.flush()
                os.fsync(handle.fileno())
            write_speed = written / (1 << 20) / max(time.perf_counter() - start, 1e-6)

            start = time.perf_counter()
            read = 0
            with open(tmp, "rb", buffering=0) as handle:
                while True:
                    chunk = handle.read(1 << 20)
                    if not chunk:
                        break
                    read += len(chunk)
            read_speed = read / (1 << 20) / max(time.perf_counter() - start, 1e-6)
        finally:
            tmp.unlink(missing_ok=True)
        return round(min(write_speed, read_speed), 1)

    def disk_kind(self, path: Path, *, sample_mb: int = 64) -> str:
        """Classify storage as nvme / ssd / hdd from measured throughput."""
        del sample_mb
        speed = self.disk_bench(path)
        if speed >= 800:
            return "nvme"
        if speed >= 250:
            return "ssd"
        return "hdd"

    def power_plan_warning(self) -> str | None:
        return None

    def supports_symlink(self) -> bool:
        return True

    def pagefile_paths(self) -> list[Path]:
        """Where the OS swaps to. Empty when unknown or when the OS has none."""
        return []

    def venv_bindir(self) -> str:
        return "bin"

    def python_exe_names(self) -> tuple[str, ...]:
        return ("python3", "python")

    # --- methods that must never fall back silently ----------------------

    def spawn_detached(
        self,
        argv: list[str],
        cwd: Path,
        env: dict[str, str],
        *,
        new_group: bool = True,
        stdout: Path | None = None,
    ) -> int:
        raise NotImplementedError("planned for M0")

    def kill_tree(self, pid: int, grace_sec: float = 5.0) -> bool:
        raise NotImplementedError("planned for M0")

    def pty_factory(self) -> PtyLike:
        raise NotImplementedError("planned for M0-TTY")


def psutil_tree_kill(pid: int, grace_sec: float) -> bool:
    """Vendor-neutral tree kill used by POSIX adapters and Windows fallback."""
    try:
        parent = psutil.Process(pid)
        children = parent.children(recursive=True)
    except psutil.NoSuchProcess:
        return True
    procs = children + [parent]
    for proc in procs:
        with suppress(psutil.Error):
            proc.terminate()
    _, alive = psutil.wait_procs(procs, timeout=grace_sec)
    for proc in alive:
        with suppress(psutil.Error):
            proc.kill()
    return not psutil.pid_exists(pid)


def decode_output(raw: bytes) -> str:
    """Decode child output that may be UTF-16, UTF-8 or the legacy ANSI codepage."""
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16", errors="replace")
    if b"\x00" in raw[:256]:
        try:
            return raw.decode("utf-16-le")
        except UnicodeDecodeError:
            pass
    for encoding in (_ansi_encoding(), "utf-8", "latin-1"):
        if not encoding:
            continue
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def _ansi_encoding() -> str:
    import locale

    return locale.getencoding()


__all__ = [
    "PlatformAdapter",
    "BasePlatformAdapter",
    "PlatformError",
    "PtyLike",
    "decode_output",
    "psutil_tree_kill",
]
