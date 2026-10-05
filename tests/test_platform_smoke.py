"""Platform and GPU adapters must resolve cleanly on every supported OS."""

from __future__ import annotations

from zui.gpu import resolve_provider
from zui.platform import get_platform


def test_platform_adapter_resolves() -> None:
    adapter = get_platform()
    assert adapter.name in {"windows", "darwin", "linux"}


def test_app_data_dir_is_absolute_and_named_zui() -> None:
    path = get_platform().app_data_dir()
    assert path.is_absolute()
    assert path.name == "zui"


def test_default_instance_root_is_absolute() -> None:
    assert get_platform().default_instance_root().is_absolute()


def test_gpu_provider_always_resolves() -> None:
    provider = resolve_provider()
    assert isinstance(provider.caps(), set)
    snapshot = provider.snapshot()
    assert snapshot.vendor
