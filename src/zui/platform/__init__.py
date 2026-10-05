"""Platform adapter factory."""

from __future__ import annotations

import sys

from zui.platform.base import PlatformAdapter

_adapter: PlatformAdapter | None = None


def get_platform() -> PlatformAdapter:
    """Return the adapter for the current OS (cached)."""
    global _adapter
    if _adapter is None:
        if sys.platform.startswith("win"):
            from zui.platform.win32 import WindowsAdapter

            _adapter = WindowsAdapter()
        elif sys.platform == "darwin":
            from zui.platform.darwin import DarwinAdapter

            _adapter = DarwinAdapter()
        elif sys.platform.startswith("linux"):
            from zui.platform.linux import LinuxAdapter

            _adapter = LinuxAdapter()
        else:
            raise RuntimeError(f"unsupported platform: {sys.platform}")
    return _adapter


def app_data_dir() -> object:
    return get_platform().app_data_dir()


__all__ = ["get_platform", "app_data_dir"]
