"""Workflow resource budget: what a graph actually needs versus what the box has."""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

import psutil

from zui.core.instance import Instance
from zui.gpu import resolve_provider
from zui.store import repo

_DTYPE_BYTES = {
    "F8_E4M3": 1,
    "F8_E5M2": 1,
    "BF16": 2,
    "F16": 2,
    "F32": 4,
    "I8": 1,
}


def estimate(instance: Instance, conn: sqlite3.Connection, workflow: Path) -> dict[str, Any]:
    data = json.loads(workflow.read_text(encoding="utf-8"))
    referenced = referenced_strings(data)
    known = repo.fetch_all(conn, "SELECT * FROM assets WHERE instance_id = ?", (instance.id,))

    matched: list[dict[str, Any]] = []
    total = 0
    for row in known:
        name = Path(row["rel_path"]).name
        if name not in referenced:
            continue
        size = int(row["size"] or 0)
        total += size
        matched.append(
            {
                "name": name,
                "gb": round(size / 2**30, 2),
                "family": row["family"],
                "dtype": row["dtype"],
            }
        )
    unmatched = sorted(
        name
        for name in referenced
        if _is_modelish(name) and name not in {item["name"] for item in matched}
    )

    ram = psutil.virtual_memory().total
    provider = resolve_provider()
    vram = provider.snapshot().get("vram.total")
    vram_total = int(vram) if vram is not None else None

    verdict, advice = _verdict(total, ram, vram_total)
    return {
        "workflow": str(workflow),
        "nodes": _node_count(data),
        "referenced_models": sorted(referenced),
        "matched": matched,
        "missing_in_index": unmatched,
        "required_bytes": total,
        "required_gb": round(total / 2**30, 2),
        "ram_gb": round(ram / 2**30, 1),
        "vram_gb": round(vram_total / 2**30, 1) if vram_total else None,
        "verdict": verdict,
        "advice": advice,
    }


def _verdict(required: int, ram: int, vram: int | None) -> tuple[str, list[str]]:
    advice: list[str] = []
    if required <= 0:
        return "unknown", ["工作流未引用已索引的模型；先运行 `zui model scan`"]
    if required > ram:
        advice.append(
            f"权重合计 {required / 2**30:.1f} GB 超出物理内存 {ram / 2**30:.1f} GB，"
            "mmap 会反复回源读盘，优先换量化版本或减少同时加载的模型"
        )
        verdict = "will-thrash"
    elif required > ram * 0.8:
        advice.append("权重接近内存上限，建议一次只保留一个主模型")
        verdict = "tight"
    else:
        verdict = "fits"
    if vram and required > vram:
        advice.append(
            f"权重大于显存（{vram / 2**30:.1f} GB），需要 BlockSwap/分层卸载；"
            "块数越多速度越慢，建议先提高量化等级"
        )
    if required > ram or (vram and required > vram * 3):
        advice.append("考虑加内存到 64GB，这是此类工作负载性价比最高的升级")
    return verdict, advice


def referenced_strings(node: Any) -> set[str]:
    found: set[str] = set()
    stack = [node]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            for value in item.values():
                stack.append(value)
        elif isinstance(item, list):
            stack.extend(item)
        elif isinstance(item, str):
            found.add(item)
    return found


def _is_modelish(name: str) -> bool:
    lowered = name.lower()
    return any(token in lowered for token in (".safetensors", ".gguf", ".ckpt", ".pt", ".bin"))


def _node_count(data: dict[str, Any]) -> int:
    nodes = data.get("nodes")
    return len(nodes) if isinstance(nodes, list) else 0


def workflows_for(instance: Instance) -> list[Path]:
    if instance.comfy_dir is None:
        return []
    base = instance.comfy_dir / "user" / "default" / "workflows"
    if not base.is_dir():
        return []
    return sorted(base.glob("*.json"))


def family_totals(rows: list[sqlite3.Row]) -> dict[str, int]:
    totals: dict[str, int] = defaultdict(int)
    for row in rows:
        totals[str(row["family"])] += int(row["size"] or 0)
    return dict(totals)


__all__ = ["estimate", "family_totals", "referenced_strings", "workflows_for"]
