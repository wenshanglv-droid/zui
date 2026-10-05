"""One-click acceleration: pick a backend for this GPU, install it, wire the flag."""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
from typing import Any

from zui.core import env_runtime, profile
from zui.core.instance import Instance
from zui.core.paths import rules_dir
from zui.gpu import resolve_provider

_BACKEND_PACKAGES = ("sage-attention", "flash_attn", "xformers")


def rules() -> dict[str, Any]:
    path = rules_dir() / "attention_wheels.json"
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def analyze(instance: Instance, conn: sqlite3.Connection) -> dict[str, Any]:
    provider = resolve_provider()
    facts = env_runtime.inspect(instance, conn=None, bench=False)
    packages = facts.get("packages") or {}
    arch = provider.snapshot().get("arch")

    installed = [name for name in _BACKEND_PACKAGES if packages.get(name)]
    candidates: list[dict[str, Any]] = []
    for rule in rules().get("rules", []):
        allowed = {f"sm_{value.replace('.', '')}" for value in rule.get("compute_capabilities", [])}
        if arch and allowed and str(arch) not in allowed:
            continue
        candidates.append(
            {
                "id": rule["id"],
                "package": rule["package"],
                "cli_flag": rule.get("cli_flag"),
                "risk": rule.get("risk", "unknown"),
                "installed": bool(packages.get(rule["package"])),
                "purpose": rule.get("purpose", ""),
                "note": rule.get("note", ""),
            }
        )
    return {
        "instance": instance.id,
        "arch": arch,
        "gpu": provider.snapshot().values.get("name"),
        "installed_backends": installed,
        "candidates": candidates,
        "always_safe_flags": rules().get("always_safe_flags", []),
        "not_recommended": rules().get("not_recommended_by_default", []),
        "current_profile": profile.get(conn, instance.id),
    }


def apply_rule(
    instance: Instance,
    conn: sqlite3.Connection,
    rule_id: str,
    *,
    profile_name: str = "default",
    index_url: str | None = None,
) -> dict[str, Any]:
    rule = next((item for item in rules().get("rules", []) if item.get("id") == rule_id), None)
    if rule is None:
        return {"ok": False, "error": f"unknown rule: {rule_id}"}

    python = instance.python
    if python is None:
        return {"ok": False, "error": "instance has no interpreter"}
    uv = shutil.which("uv")
    if uv is None:
        return {"ok": False, "error": "uv not found on PATH"}

    command = install_command(instance, rule["package"], index_url)

    result = subprocess.run(  # noqa: S603
        command,
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )
    if result.returncode != 0:
        return {"ok": False, "error": "install failed", "stderr": result.stderr[-2000:]}

    current = profile.get(conn, instance.id, profile_name)
    args = list(current.get("args") or [])
    for token in _tokens(rule.get("cli_flag")) + _flatten(rules().get("always_safe_flags", [])):
        if token.startswith("--") and token not in args:
            args.append(token)
    saved = profile.save(conn, instance.id, profile_name, args)

    verify = env_runtime.inspect(instance, bench=False)
    installed = bool((verify.get("packages") or {}).get(rule["package"]))
    if not installed:
        previous = [arg for arg in args if arg not in _tokens(rule.get("cli_flag"))]
        profile.save(conn, instance.id, profile_name, previous)
        return {
            "ok": False,
            "error": "installed but not importable; flags reverted",
            "args": saved["args"],
        }
    return {
        "ok": True,
        "rule": rule_id,
        "package": rule["package"],
        "args": saved["args"],
        "index_url": index_url,
        "verify_log": rule.get("verify_log"),
        "note": "重启后应在日志中看到相应注意力后端；可用 `zui run report` 复核",
    }


def install_command(instance: Instance, package: str, index_url: str | None = None) -> list[str]:
    """The exact `uv pip install` command zui would run, for previews."""
    uv = shutil.which("uv") or "uv"
    command = [uv, "pip", "install", "--python", str(instance.python or "<python>")]
    if index_url:
        command += ["--index-url", index_url]
    command.append(package)
    return command


def preview_rule(
    instance: Instance,
    conn: sqlite3.Connection,
    rule_id: str,
    *,
    profile_name: str = "default",
    index_url: str | None = None,
) -> dict[str, Any]:
    """Describe what `apply` would do, without touching the environment."""
    rule = next((item for item in rules().get("rules", []) if item.get("id") == rule_id), None)
    if rule is None:
        return {"ok": False, "error": f"unknown rule: {rule_id}"}
    current = profile.get(conn, instance.id, profile_name)
    args = list(current.get("args") or [])
    wanted = _tokens(rule.get("cli_flag")) + _flatten(rules().get("always_safe_flags", []))
    additions = [token for token in wanted if token.startswith("--") and token not in args]
    return {
        "ok": True,
        "instance": instance.id,
        "rule": rule_id,
        "package": rule["package"],
        "risk": rule.get("risk", "unknown"),
        "index_url": index_url,
        "command": install_command(instance, rule["package"], index_url),
        "args_added": additions,
        "args_after": args + additions,
        "applied_now": False,
    }


def _tokens(flag: str | None) -> list[str]:
    return flag.split() if flag else []


def _flatten(items: list[str]) -> list[str]:
    tokens: list[str] = []
    for item in items:
        tokens.extend(item.split())
    return tokens


__all__ = ["analyze", "apply_rule", "install_command", "preview_rule", "rules"]
