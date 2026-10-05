"""Locating and analysing ComfyUI run logs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from zui.core import logparse

_CANDIDATES = ("comfyui.prev2.log", "comfyui.prev.log", "comfyui.log")


def locate_all(instance: Any) -> list[Path]:
    """Oldest first, so chronological order survives log rotation."""
    if instance.comfy_dir is None:
        return []
    user_dir = instance.comfy_dir / "user"
    found = [user_dir / name for name in _CANDIDATES if (user_dir / name).exists()]
    return found


def locate(instance: Any) -> Path | None:
    """Newest ComfyUI log."""
    files = locate_all(instance)
    return files[-1] if files else None


def last_run(instance: Any, *, max_runs: int = 3) -> dict[str, Any]:
    files = locate_all(instance)
    if not files:
        return {"found": False, "path": None, "files": [], "env": {}, "startup": [], "runs": []}

    runs: list[dict[str, Any]] = []
    env: dict[str, Any] = {}
    startup: list[dict[str, Any]] = []
    for path in files:
        data = logparse.analyze_file(path, max_runs=200)
        runs.extend(data.get("runs") or [])
        if data.get("env"):
            env = data["env"]
        if data.get("startup"):
            startup = data["startup"]

    return {
        "found": True,
        "path": str(files[-1]),
        "files": [str(item) for item in files],
        "env": env,
        "startup": startup,
        "runs": runs[-max_runs:],
    }


def suggestions(report: dict[str, Any]) -> list[str]:
    """Turn measured facts into a short, ordered to-do list."""
    tips: list[str] = []
    env = report.get("env") or {}

    if (env.get("attention") or "").startswith("pytorch"):
        tips.append("当前为 PyTorch 原生注意力；可安装 sage-attention 并加 --use-sage-attention")
    if env.get("fast_disk") is True:
        tips.append("启用了磁盘换页(fast_disk=True)；机械盘上会明显变慢，建议改回内存换页")

    for phase in report.get("startup") or []:
        if phase.get("id") == "manager_wait" and (phase.get("seconds") or 0) > 20:
            tips.append(
                f"ComfyUI-Manager 启动耗时 {phase['seconds']} 秒；断网环境建议关闭其网络抓取"
            )

    for run in report.get("runs") or []:
        loads = sorted(
            (item for item in run.get("loads") or [] if item.get("seconds")),
            key=lambda item: item["seconds"],
            reverse=True,
        )
        if loads and loads[0]["seconds"] > 30:
            tips.append(
                f"模型 {loads[0]['model']} 加载耗时 {loads[0]['seconds']} 秒，"
                "主要受磁盘顺序读速影响，建议迁移到 SSD/NVMe"
            )
        sampling = run.get("sampling") or {}
        if (sampling.get("rate_s_per_it") or 0) > 4:
            tips.append(
                f"采样 {sampling.get('rate_s_per_it')} s/it（{sampling.get('steps')} 步）；"
                "可尝试 flash/sage 注意力与 --fast fp16_accumulation autotune"
            )
    return _unique(tips)


def _unique(items: list[str]) -> list[str]:
    seen: list[str] = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return seen


__all__ = ["last_run", "locate", "locate_all", "suggestions"]
