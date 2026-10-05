"""Child-process environment injection.

Everything that must reach ComfyUI as an environment variable is assembled here,
so launch behaviour stays inspectable and reproducible (`zui launch --dry-run`).
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from zui.core.paths import rules_dir

ALWAYS_SET: dict[str, str] = {
    "PYTHONUTF8": "1",
    "PYTHONUNBUFFERED": "1",
    "PYTHONIOENCODING": "utf-8",
}


def load_mirrors() -> dict[str, Any]:
    data: dict[str, Any] = json.loads((rules_dir() / "mirrors.json").read_text(encoding="utf-8"))
    return data


def detect_portable_git(*search_roots: Path) -> Path | None:
    """Pick up a bundled PortableGit before falling back to system git."""
    candidates: list[Path] = []
    for root in search_roots:
        candidates.extend(
            [
                root / "tools" / "PortableGit" / "bin" / "git.exe",
                root / "PortableGit" / "bin" / "git.exe",
                root / "tools" / "PortableGit" / "bin" / "git",
            ]
        )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    system = shutil.which("git")
    return Path(system) if system else None


def resolve_pypi_index(mirror: str | None) -> str | None:
    """Resolve a mirror id (from rules/mirrors.json) or a literal URL to an index URL."""
    if not mirror:
        return None
    if mirror.startswith(("http://", "https://")):
        return mirror
    for entry in load_mirrors().get("pypi", []):
        if entry["id"] == mirror:
            return str(entry["index_url"])
    return None


def resolve_hf_endpoint(mirror: str | None) -> str | None:
    """Resolve a mirror id (from rules/mirrors.json) or a literal URL to an endpoint."""
    if not mirror:
        return None
    if mirror.startswith(("http://", "https://")):
        return mirror
    for entry in load_mirrors().get("huggingface", []):
        if entry["id"] == mirror:
            return str(entry["endpoint"])
    return None


def build_child_env(
    *,
    base: dict[str, str] | None = None,
    unset: list[str] | None = None,
    pypi_mirror: str | None = None,
    hf_mirror: str | None = None,
    search_roots: list[Path] | None = None,
) -> dict[str, str]:
    """Assemble the environment for a ComfyUI child process."""
    env = dict(os.environ) if base is None else dict(base)
    env.update(ALWAYS_SET)

    index_url = resolve_pypi_index(pypi_mirror)
    if index_url:
        env["PIP_INDEX_URL"] = index_url
        env["UV_INDEX_URL"] = index_url
        env["UV_DEFAULT_INDEX"] = index_url
    endpoint = resolve_hf_endpoint(hf_mirror)
    if endpoint:
        env["HF_ENDPOINT"] = endpoint

    git = detect_portable_git(*(search_roots or []))
    if git is not None:
        env["GIT_PYTHON_GIT_EXECUTABLE"] = str(git)
        bin_dir = str(git.parent)
        existing = env.get("PATH", "")
        if bin_dir not in existing.split(os.pathsep):
            env["PATH"] = bin_dir + os.pathsep + existing

    for name in unset or []:
        env.pop(name, None)
    return env


def diff_env(child: dict[str, str]) -> dict[str, str]:
    """Variables a child environment changes relative to the current process."""
    changed: dict[str, str] = {}
    for key in sorted(set(child) | set(os.environ)):
        if child.get(key) != os.environ.get(key):
            changed[key] = str(child.get(key, "<unset>"))
    return changed


__all__ = [
    "ALWAYS_SET",
    "build_child_env",
    "detect_portable_git",
    "diff_env",
    "load_mirrors",
    "resolve_hf_endpoint",
    "resolve_pypi_index",
]
