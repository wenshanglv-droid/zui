"""Torch stack catalogue driven by rules/torch_matrix.json."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _rules_file() -> Path:
    from zui.core.paths import rules_dir

    return rules_dir() / "torch_matrix.json"


def matrix() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(_rules_file().read_text(encoding="utf-8"))
    return data


def stacks() -> list[dict[str, Any]]:
    return list(matrix().get("stacks", []))


def get(stack_id: str) -> dict[str, Any] | None:
    for entry in stacks():
        if entry.get("id") == stack_id:
            return entry
    return None


def known_versions() -> list[str]:
    versions: list[str] = []
    for entry in stacks():
        for name, version in (entry.get("packages") or {}).items():
            if version and name == "torch":
                versions.append(str(version))
    return versions


def is_known(torch_version: str) -> bool:
    return torch_version in known_versions()


def recommend(vendor: str | None = None) -> dict[str, Any] | None:
    """Prefer verified stacks for the current GPU vendor."""
    candidates = [
        entry
        for entry in stacks()
        if entry.get("verified")
        and any(entry["packages"].get(name) for name in ("torch",) if entry["packages"].get(name))
    ]
    if vendor:
        for entry in candidates:
            if entry.get("vendor") == vendor:
                return entry
    return candidates[0] if candidates else None


__all__ = ["get", "is_known", "known_versions", "matrix", "recommend", "stacks"]
