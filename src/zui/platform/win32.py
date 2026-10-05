"""Windows adapter: Job Objects guarantee the whole process tree dies.

Why Job Objects and not `taskkill /F <pid>`: a venv `python.exe` can be a shim
that re-execs the real interpreter (uv / python-build-standalone venvs do this),
so `Popen.pid` is the shim, not the process that actually does the work.
Job membership is inherited by every descendant, so `TerminateJobObject` still
kills the real interpreter tree — even after the shim itself has exited.
Process groups are also far safer than "kill whatever owns port 8188".
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import time
from contextlib import suppress
from ctypes import wintypes
from pathlib import Path

import psutil

from zui.platform.base import (
    BasePlatformAdapter,
    PlatformError,
    decode_output,
    psutil_tree_kill,
)

CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008

JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JobObjectBasicLimitInformation = 2

_kernel32: ctypes.WinDLL | None = None


def _k32() -> ctypes.WinDLL:
    global _kernel32
    if _kernel32 is None:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.TerminateJobObject.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        _kernel32 = kernel
    return _kernel32


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


def _local_app_data() -> Path:
    override = os.environ.get("LOCALAPPDATA")
    if override:
        return Path(override)
    return Path.home() / "AppData" / "Local"


def _headless_python(argv: list[str]) -> list[str]:
    """把解释器换成同目录的 pythonw.exe（GUI 子系统），让整条进程链都不分配控制台。

    CREATE_NO_WINDOW 只对直接子进程生效；venv 的 python.exe 内部会再次拉起解释器，
    链路上的后续 console 程序会重新分配控制台，于是弹出可见的终端窗口（表现为新建
    Windows Terminal / PowerShell 窗口）。pythonw.exe 属于 GUI 子系统，天然没有控制台，
    其后代也不会新建，从根上消除弹窗。stdout 仍由调用方重定向到日志文件，输出不受影响。
    """
    if not argv:
        return argv
    head = Path(argv[0])
    if head.name.lower() != "python.exe":
        return argv
    twin = head.with_name("pythonw.exe")
    return [str(twin), *argv[1:]] if twin.exists() else argv


class WindowsAdapter(BasePlatformAdapter):
    name = "windows"

    def __init__(self) -> None:
        # pid -> job handle. Job objects die with zui, taking the children with them.
        self._jobs: dict[int, int] = {}

    def app_data_dir(self) -> Path:
        return _local_app_data() / "zui"

    def default_instance_root(self) -> Path:
        return _local_app_data() / "zui" / "instances"

    def _create_job(self) -> int | None:
        try:
            kernel = _k32()
            handle = kernel.CreateJobObjectW(None, None)
            if not handle:
                return None
            info = _JOBOBJECT_BASIC_LIMIT_INFORMATION()
            info.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            ok = kernel.SetInformationJobObject(
                handle,
                JobObjectBasicLimitInformation,
                ctypes.byref(info),
                ctypes.sizeof(info),
            )
            if not ok:
                kernel.CloseHandle(handle)
                return None
            return int(handle)
        except Exception:  # noqa: BLE001
            return None

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

        argv = _headless_python(list(argv))
        # 关键：不要叠加 DETACHED_PROCESS。
        # MSDN 明确：CREATE_NO_WINDOW 与 CREATE_NEW_CONSOLE 或 DETACHED_PROCESS 同时使用时会被忽略。
        # 叠加后 python.exe（console 子系统）会重新分配控制台，弹出可见的终端窗口
        # （表现为新建 Windows Terminal / PowerShell 窗口）。
        # 只用 CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW 才能真正隐藏窗口；
        # `new_group` 仅为兼容调用签名保留，不再映射到 DETACHED_PROCESS。
        _ = new_group
        flags = CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW

        log_handle = None
        # 双保险：即使父进程是可见控制台，子进程也绝不弹出窗口（输出已重定向到日志文件）。
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags = subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0  # SW_HIDE
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
                    creationflags=flags,
                    startupinfo=startupinfo,
                )
            else:
                popen = subprocess.Popen(  # noqa: S603
                    argv,
                    cwd=str(cwd),
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=flags,
                    startupinfo=startupinfo,
                )
        finally:
            if log_handle is not None:
                log_handle.close()

        job = self._create_job()
        if job is not None:
            try:
                if _k32().AssignProcessToJobObject(job, wintypes.HANDLE(popen.pid)):
                    self._jobs[popen.pid] = job
                else:
                    _k32().CloseHandle(job)
            except Exception:  # noqa: BLE001
                _k32().CloseHandle(job)
        return int(popen.pid)

    def kill_tree(self, pid: int, grace_sec: float = 5.0) -> bool:
        job = self._jobs.pop(pid, None)
        if job is not None:
            try:
                _k32().TerminateJobObject(job, 1)
                deadline = time.monotonic() + grace_sec
                while time.monotonic() < deadline:
                    if not psutil.pid_exists(pid):
                        break
                    time.sleep(0.2)
            finally:
                with suppress(Exception):
                    _k32().CloseHandle(job)
            if not psutil.pid_exists(pid):
                return True
        return psutil_tree_kill(pid, grace_sec)

    def venv_bindir(self) -> str:
        return "Scripts"

    def python_exe_names(self) -> tuple[str, ...]:
        return ("python.exe",)

    def pagefile_paths(self) -> list[Path]:
        """Paging file locations from the Memory Management registry value."""
        try:
            result = subprocess.run(  # noqa: S603
                [
                    "reg",
                    "query",
                    r"HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management",
                    "/v",
                    "PagingFiles",
                ],
                capture_output=True,
                timeout=5,
                check=False,
            )
        except Exception:  # noqa: BLE001
            return []
        paths: list[Path] = []
        for line in decode_output(result.stdout).splitlines():
            if "REG_MULTI_SZ" not in line:
                continue
            value = line.split("REG_MULTI_SZ", 1)[1].strip()
            for entry in value.split("\\0"):
                entry = entry.strip()
                if not entry:
                    continue
                target = entry.split()[0]
                if target.startswith("?"):
                    target = target.replace("?", str(Path.home().drive or "C:"), 1)
                paths.append(Path(target))
        return paths

    def power_plan_warning(self) -> str | None:
        try:
            result = subprocess.run(  # noqa: S603
                ["powercfg", "/getactivescheme"],  # noqa: S607
                capture_output=True,
                timeout=5,
                check=False,
            )
        except Exception:  # noqa: BLE001
            return None
        output = decode_output(result.stdout).lower()
        if "381b4222-f694-41f0-9685-ff5bb260df2e" in output:  # Balanced plan GUID
            return "当前电源计划为「平衡」，建议改为高性能/卓越性能以保持 GPU 频率"
        return None


__all__ = ["WindowsAdapter"]
