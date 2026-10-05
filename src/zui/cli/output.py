"""Single-line JSON output contract. See docs/cli.md."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from rich.console import Console

console = Console()


@dataclass(slots=True)
class Result:
    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: dict[str, Any] | None = None

    @classmethod
    def success(cls, **data: Any) -> Result:
        return cls(ok=True, data=dict(data))

    @classmethod
    def failure(cls, code: str, message: str, hint: str | None = None, **extra: Any) -> Result:
        payload: dict[str, Any] = {"code": code, "msg": message}
        if hint is not None:
            payload["hint"] = hint
        payload.update(extra)
        return cls(ok=False, error=payload)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"ok": self.ok}
        out["data"] = self.data
        if self.error is not None:
            out["error"] = self.error
        return out

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))

    def exit_code(self) -> int:
        if self.ok:
            return 0
        assert self.error is not None
        mapping = {
            "USAGE_ERROR": 2,
            "TARGET_NOT_RUNNING": 3,
            "TIMEOUT": 4,
        }
        return mapping.get(str(self.error.get("code")), 1)


def emit(result: Result, *, as_json: bool) -> str:
    """Render and print the result. Returns the printed string."""
    text = result.to_json() if as_json else _render_human(result)
    print(text)
    return text


def _render_human(result: Result) -> str:
    if result.ok:
        if not result.data:
            return "ok"
        return "\n".join(f"{key}: {value}" for key, value in result.data.items())
    assert result.error is not None
    lines = [f"failed: {result.error.get('msg')} ({result.error.get('code')})"]
    hint = result.error.get("hint")
    if hint:
        lines.append(f"hint: {hint}")
    return "\n".join(lines)


__all__ = ["Result", "emit", "console"]
