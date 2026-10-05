"""Apple Silicon provider. Unified memory: VRAM concepts do not apply. TODO(M3)."""

from __future__ import annotations

from zui.gpu.base import GpuSnapshot


class AppleGpuProvider:
    def available(self) -> bool:
        return False

    def vendor(self) -> str:
        return "apple"

    def name(self) -> str:
        return "Apple"

    def caps(self) -> set[str]:
        # Deliberately empty: expose nothing rather than mislabelling RAM as VRAM.
        return set()

    def snapshot(self) -> GpuSnapshot:
        raise RuntimeError("Apple provider not implemented yet")


__all__ = ["AppleGpuProvider"]
