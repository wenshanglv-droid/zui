"""Resolved filesystem locations used across zui.

No OS branching here: platform specifics come from zui.platform.
"""

from __future__ import annotations

from pathlib import Path

_RULES_DIR = Path(__file__).resolve().parents[3] / "rules"
_BASE_DIRS = ("standalone-env", "base", ".python")


def rules_dir() -> Path:
    """Directory holding the data-driven rule files."""
    return _RULES_DIR


def python_in(directory: Path) -> Path | None:
    """Return a python executable inside `directory`, honouring the platform."""
    from zui.platform import get_platform

    adapter = get_platform()
    for name in adapter.python_exe_names():
        candidate = directory / adapter.venv_bindir() / name
        if candidate.exists():
            return candidate
        direct = directory / name
        if direct.exists():
            return direct
    return None


def base_python_for(root: Path) -> Path | None:
    """Locate the standalone base interpreter next to an install root."""
    for name in _BASE_DIRS:
        found = python_in(root / name)
        if found is not None:
            return found
    return None


def base_home_for(root: Path) -> Path | None:
    """Locate the *directory* holding a standalone base interpreter.

    pyvenv.cfg's ``home`` key (PEP 405) points at the directory that contains
    the base interpreter, not at the executable itself.
    """
    for name in _BASE_DIRS:
        directory = root / name
        if python_in(directory) is not None:
            return directory
    return None


def venv_python_for(root: Path) -> Path | None:
    """Return the interpreter of a venv root, honouring the platform."""
    from zui.platform import get_platform

    adapter = get_platform()
    for name in adapter.python_exe_names():
        candidate = root / adapter.venv_bindir() / name
        if candidate.exists():
            return candidate
    return None


def sibling_model_roots(root: Path) -> list[Path]:
    """Common locations where ComfyUI models may live next to an install."""
    candidates = [
        root / "models",
        root / "ComfyUI" / "models",
        root.parent / "ComfyUI-Shared" / "models",
        root.parent.parent / "ComfyUI-Shared" / "models",
    ]
    return [path for path in candidates if path.is_dir()]


__all__ = [
    "base_home_for",
    "base_python_for",
    "python_in",
    "rules_dir",
    "sibling_model_roots",
    "venv_python_for",
]
