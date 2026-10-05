"""Exit code contract. Single source of truth: see docs/cli.md."""

from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    OK = 0
    RUNTIME_ERROR = 1
    USAGE_ERROR = 2
    TARGET_NOT_RUNNING = 3
    TIMEOUT = 4


class ZuiError(Exception):
    """Error carrying a stable machine-readable code and exit code."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "RUNTIME_ERROR",
        exit_code: ExitCode = ExitCode.RUNTIME_ERROR,
        hint: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code
        self.hint = hint


class TargetNotRunning(ZuiError):
    def __init__(self, message: str = "target is not running") -> None:
        super().__init__(message, code="TARGET_NOT_RUNNING", exit_code=ExitCode.TARGET_NOT_RUNNING)


class Timeout(ZuiError):
    def __init__(self, message: str = "operation timed out") -> None:
        super().__init__(message, code="TIMEOUT", exit_code=ExitCode.TIMEOUT)


class UsageError(ZuiError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="USAGE_ERROR", exit_code=ExitCode.USAGE_ERROR)


def exit_code_for(error: ZuiError) -> int:
    return int(error.exit_code)


__all__ = ["ExitCode", "ZuiError", "TargetNotRunning", "Timeout", "UsageError", "exit_code_for"]
