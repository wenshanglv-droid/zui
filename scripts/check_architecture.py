"""Architecture gate. CI runs this; it must stay dependency-free (stdlib only).

Guards:
  1. OS/GPU specifics live only in platform/ and gpu/.
  2. subprocess calls never use shell=True.
  3. rules/ data files parse and carry schema_version, so they cannot silently drift.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

ALLOWED_DIRS = {"platform", "gpu"}
FORBIDDEN_TOKENS = {
    "sys.platform": "use zui.platform instead",
    "os.name": "use zui.platform instead",
    "win32api": "OS specifics belong in platform/win32.py",
    "ctypes.windll": "OS specifics belong in platform/win32.py (except platform/)",
    "shell=True": "never pass shell=True; pass argv lists",
}

RULE_FILES = {
    "rules/mirrors.json": "json",
    "rules/torch_matrix.json": "json",
    "rules/attention_wheels.json": "json",
    "rules/doctor_rules.yaml": "yaml",
    "rules/log_patterns.yaml": "yaml",
}


def _scan_sources() -> list[str]:
    problems: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        parts = set(path.relative_to(SRC).parts)
        text = path.read_text(encoding="utf-8")
        for token, advice in FORBIDDEN_TOKENS.items():
            if token not in text:
                continue
            if token == "shell=True":
                problems.append(f"{path.relative_to(ROOT)}: forbidden `{token}` ({advice})")
                continue
            if not (parts & ALLOWED_DIRS):
                problems.append(
                    f"{path.relative_to(ROOT)}: `{token}` outside platform/ gpu/ ({advice})"
                )
    return problems


def _scan_rules() -> list[str]:
    problems: list[str] = []
    for rel, kind in RULE_FILES.items():
        path = ROOT / rel
        if not path.exists():
            problems.append(f"{rel}: missing")
            continue
        text = path.read_text(encoding="utf-8")
        try:
            if kind == "json":
                data = json.loads(text)
            else:
                import yaml  # noqa: PLC0415 - dev dependency

                data = yaml.safe_load(text)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{rel}: parse error: {exc}")
            continue
        if not isinstance(data, dict) or "schema_version" not in data:
            problems.append(f"{rel}: missing schema_version")
    return problems


def _scan_regexes() -> list[str]:
    """Fail fast on regexes that would explode at runtime."""
    problems: list[str] = []
    patterns_file = ROOT / "rules" / "log_patterns.yaml"
    if not patterns_file.exists():
        return problems
    import yaml  # noqa: PLC0415

    data = yaml.safe_load(patterns_file.read_text(encoding="utf-8")) or {}
    for name, pattern in (data.get("lines") or {}).items():
        try:
            re.compile(str(pattern))
        except re.error as exc:
            problems.append(f"rules/log_patterns.yaml: invalid regex for `{name}`: {exc}")
    return problems


def main() -> int:
    problems = _scan_sources() + _scan_rules() + _scan_regexes()
    if problems:
        print("architecture gate FAILED:")
        for item in problems:
            print(f"  - {item}")
        return 1
    print("architecture gate OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
