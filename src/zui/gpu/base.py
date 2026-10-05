"""GPU capability abstraction. Never assume a vendor; query capabilities."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

CAP_ARCH = "arch"
CAP_VRAM_TOTAL = "vram.total"
CAP_VRAM_USED = "vram.used"
CAP_UTIL = "util"
CAP_TEMP = "temp"
CAP_POWER = "power"
CAP_DRIVER = "driver"


@dataclass(slots=True)
class GpuSnapshot:
    vendor: str
    name: str
    values: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str) -> Any | None:
        return self.values.get(key)


@runtime_checkable
class GpuProvider(Protocol):
    """Uniform, optional GPU facts. Missing capabilities are simply absent."""

    def available(self) -> bool: ...

    def vendor(self) -> str: ...

    def name(self) -> str: ...

    def caps(self) -> set[str]: ...

    def snapshot(self) -> GpuSnapshot: ...


class NullGpuProvider:
    """Always available, exposes no capabilities (CPU-only / unknown)."""

    def available(self) -> bool:
        return True

    def vendor(self) -> str:
        return "none"

    def name(self) -> str:
        return "cpu-only"

    def caps(self) -> set[str]:
        return set()

    def snapshot(self) -> GpuSnapshot:
        return GpuSnapshot(vendor="none", name="cpu-only")


__all__ = [
    "CAP_ARCH",
    "CAP_VRAM_TOTAL",
    "CAP_VRAM_USED",
    "CAP_UTIL",
    "CAP_TEMP",
    "CAP_POWER",
    "CAP_DRIVER",
    "GpuSnapshot",
    "GpuProvider",
    "NullGpuProvider",
]
