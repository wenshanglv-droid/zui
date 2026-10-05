"""Provider resolution: first available wins, otherwise capabilities are empty."""

from __future__ import annotations

from zui.gpu.amd import AmdGpuProvider
from zui.gpu.apple import AppleGpuProvider
from zui.gpu.base import GpuProvider, NullGpuProvider
from zui.gpu.intel import IntelGpuProvider
from zui.gpu.nvidia import NvidiaGpuProvider

_CANDIDATES: tuple[type[GpuProvider], ...] = (
    NvidiaGpuProvider,
    AmdGpuProvider,
    IntelGpuProvider,
    AppleGpuProvider,
)


def resolve_provider() -> GpuProvider:
    for candidate in _CANDIDATES:
        provider = candidate()
        try:
            if provider.available():
                return provider
        except Exception:
            continue
    return NullGpuProvider()


__all__ = ["resolve_provider", "GpuProvider", "NullGpuProvider"]
