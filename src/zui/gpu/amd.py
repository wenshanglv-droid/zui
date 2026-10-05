"""AMD ROCm provider (rocm-smi). TODO(M3)."""

from __future__ import annotations

from zui.gpu.base import GpuSnapshot


class AmdGpuProvider:
    def available(self) -> bool:
        return False

    def vendor(self) -> str:
        return "amd"

    def name(self) -> str:
        return "AMD"

    def caps(self) -> set[str]:
        return set()

    def snapshot(self) -> GpuSnapshot:
        raise RuntimeError("AMD provider not implemented yet")


__all__ = ["AmdGpuProvider"]
