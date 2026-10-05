"""Contract tests for the CLI output and exit code contract."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from zui.cli.exitcodes import ExitCode, Timeout, UsageError, ZuiError, exit_code_for
from zui.cli.output import Result, emit

ROOT = Path(__file__).resolve().parents[1]


def test_exit_code_values() -> None:
    assert ExitCode.OK == 0
    assert ExitCode.RUNTIME_ERROR == 1
    assert ExitCode.USAGE_ERROR == 2
    assert ExitCode.TARGET_NOT_RUNNING == 3
    assert ExitCode.TIMEOUT == 4


def test_zui_error_default_is_runtime() -> None:
    err = ZuiError("boom")
    assert err.code == "RUNTIME_ERROR"
    assert exit_code_for(err) == 1


def test_specialised_errors() -> None:
    assert exit_code_for(UsageError("bad flag")) == 2
    assert exit_code_for(Timeout()) == 4


def test_emit_json_is_single_line(capsys) -> None:  # type: ignore[no-untyped-def]
    emit(Result.success(answer=42), as_json=True)
    out = capsys.readouterr().out
    assert "\n" not in out.strip()
    assert json.loads(out)["data"]["answer"] == 42


def test_emit_human_readable(capsys) -> None:  # type: ignore[no-untyped-def]
    emit(Result.success(answer=42), as_json=False)
    assert "answer: 42" in capsys.readouterr().out


def test_failure_result_exit_code_mapping() -> None:
    assert Result.failure("TARGET_NOT_RUNNING", "not running").exit_code() == 3
    assert Result.failure("TIMEOUT", "too slow").exit_code() == 4
    assert Result.failure("SOMETHING_NEW", "unknown").exit_code() == 1


def test_architecture_gate_passes() -> None:
    proc = subprocess.run(  # noqa: S603
        [sys.executable, "scripts/check_architecture.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
