"""NVIDIA provider (NVML). Requires the `nvidia` extra: uv sync --extra nvidia."""

from __future__ import annotations

from contextlib import suppress

from zui.gpu.base import (
    CAP_ARCH,
    CAP_DRIVER,
    CAP_TEMP,
    CAP_UTIL,
    CAP_VRAM_TOTAL,
    CAP_VRAM_USED,
    GpuSnapshot,
)

_DRIVER_KEY = "driver"


class NvidiaGpuProvider:
    def __init__(self) -> None:
        self._pynvml: object | None = None

    def available(self) -> bool:
        try:
            import pynvml  # noqa: PLC0415 - optional dependency

            pynvml.nvmlInit()
        except Exception:
            return False
        self._pynvml = pynvml
        return True

    def vendor(self) -> str:
        return "nvidia"

    def name(self) -> str:
        return "NVIDIA"

    def caps(self) -> set[str]:
        return {CAP_ARCH, CAP_VRAM_TOTAL, CAP_VRAM_USED, CAP_UTIL, CAP_TEMP, CAP_DRIVER}

    def snapshot(self) -> GpuSnapshot:
        if self._pynvml is None and not self.available():
            raise RuntimeError("NVML unavailable")
        import pynvml  # noqa: PLC0415

        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        name = pynvml.nvmlDeviceGetName(handle)
        if isinstance(name, bytes):
            name = name.decode("utf-8", "replace")
        memory = pynvml.nvmlDeviceGetMemoryInfo(handle)
        util = pynvml.nvmlDeviceGetUtilizationRates(handle)
        values: dict[str, object] = {
            "name": str(name),
            CAP_VRAM_TOTAL: int(memory.total),
            CAP_VRAM_USED: int(memory.used),
            CAP_UTIL: int(util.gpu),
            CAP_DRIVER: str(pynvml.nvmlSystemGetDriverVersion()),
        }
        # Capabilities absent are fine; never fabricate placeholder values.
        with suppress(Exception):
            major, minor = pynvml.nvmlDeviceGetCudaComputeCapability(handle)
            values[CAP_ARCH] = f"sm_{major}{minor}"
        with suppress(Exception):
            values[CAP_TEMP] = int(
                pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
            )
        return GpuSnapshot(vendor="nvidia", name=str(name), values=values)


__all__ = ["NvidiaGpuProvider"]
