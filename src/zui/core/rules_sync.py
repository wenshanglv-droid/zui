"""Rule set synchronisation.

Rules live as plain files in `rules/` so they can be vendored, edited by hand or
refreshed from a URL without rebuilding zui. Nothing here is applied silently:
`update` is dry-run until `apply=True`, and every replaced file is backed up.
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from zui.core.paths import rules_dir

_KNOWN = (
    "doctor_rules.yaml",
    "torch_matrix.json",
    "attention_wheels.json",
    "log_patterns.yaml",
    "mirrors.json",
)
_TIMEOUT = 20.0


def known_files() -> list[str]:
    return list(_KNOWN)


def local_state() -> dict[str, Any]:
    """Name -> size/digest/index for every rule file we know about."""
    base = rules_dir()
    files: list[dict[str, Any]] = []
    for name in _KNOWN:
        path = base / name
        files.append(
            {
                "name": name,
                "exists": path.exists(),
                "size": path.stat().st_size if path.exists() else 0,
                "sha256": _digest(path) if path.exists() else None,
            }
        )
    return {"rules_dir": str(base), "files": files}


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _valid(name: str, payload: bytes) -> tuple[bool, str | None]:
    """Reject a download that is not the shape the rest of zui expects."""
    try:
        if name.endswith(".json"):
            data: Any = json.loads(payload.decode("utf-8"))
            if not isinstance(data, dict):
                return False, "top level JSON must be an object"
        else:
            import yaml

            data = yaml.safe_load(payload.decode("utf-8"))
            if not isinstance(data, dict):
                return False, "top level YAML must be a mapping"
    except Exception as exc:  # noqa: BLE001
        return False, f"unparseable: {exc}"
    return True, None


def update(
    base_url: str,
    names: list[str] | None = None,
    *,
    apply: bool = False,
    timeout: float = _TIMEOUT,
) -> dict[str, Any]:
    """Download rule files and (with `apply`) swap them in, keeping backups."""
    targets = names or _KNOWN
    base = rules_dir()
    planned: list[dict[str, Any]] = []

    for name in targets:
        url = f"{base_url.rstrip('/')}/{name}"
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
                payload = response.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            planned.append(
                {"name": name, "url": url, "ok": False, "error": f"download failed: {exc}"}
            )
            continue

        ok, error = _valid(name, payload)
        if not ok:
            planned.append({"name": name, "url": url, "ok": False, "error": error})
            continue

        current = base / name
        changed = not current.exists() or current.read_bytes() != payload
        entry = {
            "name": name,
            "url": url,
            "ok": True,
            "bytes": len(payload),
            "changed": changed,
        }
        if apply and changed:
            if current.exists():
                current.replace(current.with_suffix(current.suffix + ".bak"))
            current.write_bytes(payload)
            entry["written"] = str(current)
        planned.append(entry)

    return {
        "ok": all(item["ok"] for item in planned),
        "applied_now": apply,
        "rules_dir": str(base),
        "files": planned,
        "changed": [item["name"] for item in planned if item.get("changed")],
    }


__all__ = ["known_files", "local_state", "update"]
