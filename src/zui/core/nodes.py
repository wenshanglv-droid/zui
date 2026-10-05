"""Custom node management.

ComfyUI stays a black box: nodes are handled as git checkouts plus their own
requirements, never imported. Every mutating operation is dry-run by default.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from zui.core.instance import Instance
from zui.core.launch_env import detect_portable_git

DISABLED_SUFFIX = ".disabled"
_TIMEOUT = 300.0


def nodes_dir(instance: Instance) -> Path | None:
    if instance.comfy_dir is None:
        return None
    return instance.comfy_dir / "custom_nodes"


def git_exe(instance: Instance) -> str | None:
    found = detect_portable_git(instance.root, instance.root.parent)
    if found is not None:
        return str(found)
    return shutil.which("git")


def _run(command: list[str], cwd: Path | None = None) -> dict[str, Any]:
    result = subprocess.run(  # noqa: S603
        command,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=_TIMEOUT,
        check=False,
    )
    return {
        "ok": result.returncode == 0,
        "command": command,
        "stdout": result.stdout.strip()[-2000:],
        "stderr": result.stderr.strip()[-2000:],
    }


def list_nodes(instance: Instance) -> dict[str, Any]:
    base = nodes_dir(instance)
    if base is None or not base.is_dir():
        return {"instance": instance.id, "nodes": [], "git": git_exe(instance)}

    exe = git_exe(instance)
    nodes: list[dict[str, Any]] = []
    for entry in sorted(base.iterdir()):
        if entry.is_file() or entry.name.startswith("__"):
            continue
        enabled = not entry.name.endswith(DISABLED_SUFFIX)
        nodes.append(
            {
                "name": entry.name,
                "enabled": enabled,
                "is_git": (entry / ".git").exists(),
                "commit": _commit(exe, entry),
                "dirty": _is_dirty(exe, entry),
                "requirements": (entry / "requirements.txt").exists(),
            }
        )
    return {
        "instance": instance.id,
        "custom_nodes_dir": str(base),
        "git": exe,
        "nodes": nodes,
    }


def install(
    instance: Instance, source: str, *, apply: bool = False, index_url: str | None = None
) -> dict[str, Any]:
    """Clone `source` into custom_nodes and install its requirements."""
    base = nodes_dir(instance)
    exe = git_exe(instance)
    if base is None:
        return {"ok": False, "error": "instance has no ComfyUI dir"}
    if exe is None:
        return {"ok": False, "error": "git not found; install git or bundle PortableGit"}

    target = base / _repo_name(source)
    clone = [exe, "clone", "--depth", "1", source, str(target)]

    if not apply:
        return {
            "ok": True,
            "applied_now": False,
            "target": str(target),
            "commands": [clone],
            "note": "requirements.txt 会在 clone 之后按需安装",
        }
    if target.exists() and any(target.iterdir()):
        return {"ok": False, "error": f"already exists: {target}"}

    base.mkdir(parents=True, exist_ok=True)
    outcomes = [_run(clone, cwd=base)]
    if not outcomes[0]["ok"]:
        return {
            "ok": False,
            "target": str(target),
            "steps": outcomes,
            "error": outcomes[0]["stderr"],
        }
    # requirements.txt only exists after the clone, so plan that step now.
    for step in _requirements_steps(instance, target, index_url):
        outcomes.append(_run(step))
    failed = next((item for item in outcomes if not item["ok"]), None)
    return {
        "ok": failed is None,
        "applied_now": True,
        "target": str(target),
        "steps": outcomes,
        "error": failed["stderr"] if failed else None,
    }


def update(
    instance: Instance, name: str, *, apply: bool = False, index_url: str | None = None
) -> dict[str, Any]:
    """Pull the latest commit, then refresh requirements."""
    base = nodes_dir(instance)
    exe = git_exe(instance)
    if base is None or exe is None:
        return {"ok": False, "error": "custom_nodes dir or git unavailable"}

    target = base / name
    if not (target / ".git").exists():
        return {"ok": False, "error": f"not a git checkout: {target}"}

    before = _commit(exe, target)
    steps: list[list[str]] = [[exe, "-C", str(target), "pull", "--ff-only"]]
    steps += _requirements_steps(instance, target, index_url)
    if not apply:
        return {
            "ok": True,
            "applied_now": False,
            "from": before,
            "commands": steps,
        }

    outcomes = [_run(step) for step in steps]
    failed = next((item for item in outcomes if not item["ok"]), None)
    return {
        "ok": failed is None,
        "applied_now": True,
        "from": before,
        "to": _commit(exe, target),
        "steps": outcomes,
        "error": failed["stderr"] if failed else None,
    }


def rollback(
    instance: Instance,
    name: str,
    *,
    to: str | None = None,
    apply: bool = False,
    index_url: str | None = None,
) -> dict[str, Any]:
    """Move a node back to an earlier commit (previous HEAD by default)."""
    base = nodes_dir(instance)
    exe = git_exe(instance)
    if base is None or exe is None:
        return {"ok": False, "error": "custom_nodes dir or git unavailable"}

    target = base / name
    if not (target / ".git").exists():
        return {"ok": False, "error": f"not a git checkout: {target}"}

    history = _history(exe, target)
    current = history[0] if history else None
    destination = to or (history[1] if len(history) > 1 else None)
    if destination is None:
        return {"ok": False, "error": "no earlier commit to roll back to"}

    steps: list[list[str]] = [[exe, "-C", str(target), "checkout", destination]]
    steps += _requirements_steps(instance, target, index_url)
    if not apply:
        return {
            "ok": True,
            "applied_now": False,
            "from": current,
            "to": destination,
            "commands": steps,
        }

    outcomes = [_run(step) for step in steps]
    failed = next((item for item in outcomes if not item["ok"]), None)
    return {
        "ok": failed is None,
        "applied_now": True,
        "from": current,
        "to": destination,
        "steps": outcomes,
        "error": failed["stderr"] if failed else None,
    }


def set_enabled(instance: Instance, name: str, enabled: bool, *, apply: bool = False) -> dict:
    """Enable/disable by renaming the directory, the way ComfyUI itself does it."""
    base = nodes_dir(instance)
    if base is None:
        return {"ok": False, "error": "instance has no ComfyUI dir"}
    current = base / name
    stripped = name[: -len(DISABLED_SUFFIX)] if name.endswith(DISABLED_SUFFIX) else name
    target = base / (stripped if enabled else stripped + DISABLED_SUFFIX)
    if not current.exists():
        return {"ok": False, "error": f"node not found: {current}"}
    if current == target:
        return {"ok": True, "applied_now": False, "path": str(target), "changed": False}
    if not apply:
        return {
            "ok": True,
            "applied_now": False,
            "path": str(target),
            "from": str(current),
            "changed": True,
        }
    current.rename(target)
    return {"ok": True, "applied_now": True, "path": str(target), "from": str(current)}


def _requirements_steps(
    instance: Instance, target: Path, index_url: str | None
) -> list[list[str]]:
    requirements = target / "requirements.txt"
    if not requirements.exists() or instance.python is None:
        return []
    uv = shutil.which("uv")
    if uv is None:
        return []
    command = [uv, "pip", "install", "--python", str(instance.python), "-r", str(requirements)]
    if index_url:
        command += ["--index-url", index_url]
    return [command]


def _commit(exe: str | None, directory: Path) -> str | None:
    if exe is None or not (directory / ".git").exists():
        return None
    outcome = _run([exe, "-C", str(directory), "rev-parse", "--short", "HEAD"])
    return outcome["stdout"] if outcome["ok"] else None


def _is_dirty(exe: str | None, directory: Path) -> bool | None:
    if exe is None or not (directory / ".git").exists():
        return None
    outcome = _run([exe, "-C", str(directory), "status", "--porcelain"])
    return bool(outcome["stdout"]) if outcome["ok"] else None


def _history(exe: str, directory: Path) -> list[str]:
    outcome = _run([exe, "-C", str(directory), "log", "--format=%h", "--max-count", "10"])
    if not outcome["ok"]:
        return []
    return [line for line in outcome["stdout"].splitlines() if line]


def _repo_name(source: str) -> str:
    cleaned = source.rstrip("/").rstrip("\\")
    name = cleaned.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if name.endswith(".git"):
        name = name[: -len(".git")]
    return name or "custom-node"


__all__ = ["git_exe", "install", "list_nodes", "nodes_dir", "rollback", "set_enabled", "update"]
