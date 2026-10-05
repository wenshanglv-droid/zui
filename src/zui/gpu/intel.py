"""Intel XPU provider (xpu-smi). TODO(M3)."""

from __future__ import annotations

from zui.gpu.base import GpuSnapshot


class IntelGpuProvider:
    def available(self) -> bool:
        return False

    def vendor(self) -> str:
        return "intel"

    def name(self) -> str:
        return "Intel"

    def caps(self) -> set[str]:
        return set()

    def snapshot(self) -> GpuSnapshot:
        raise RuntimeError("Intel provider not implemented yet")


__all__ = ["IntelGpuProvider"]
