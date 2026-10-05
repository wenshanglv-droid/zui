"""Single contract surface for dependency injection and typing.

Everything the Service layer depends on is (re)exported here, so no module
needs to import an implementation directly.
"""

from __future__ import annotations

from zui.gpu.base import GpuProvider, GpuSnapshot
from zui.platform.base import PlatformAdapter, PlatformError, PtyLike

__all__ = [
    "PlatformAdapter",
    "PlatformError",
    "PtyLike",
    "GpuProvider",
    "GpuSnapshot",
]
