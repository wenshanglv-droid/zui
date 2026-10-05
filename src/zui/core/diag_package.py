"""Support bundle: everything needed to diagnose someone else's machine."""

from __future__ import annotations

import json
import sqlite3
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from zui.core import asset, doctor, env_runtime, run_report, snapshot
from zui.core.instance import Instance
from zui.gpu import resolve_provider
from zui.platform import get_platform
from zui.store import repo

_LOG_TAIL = 500


def export(instance: Instance, conn: sqlite3.Connection, out_dir: Path | None = None) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target_dir = out_dir or repo.snapshot_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    bundle = target_dir / f"zui-diag-{instance.id}-{stamp}.zip"

    report: dict[str, Any] = {
        "zui_version": _version(),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "platform": {
            "name": get_platform().name,
            "app_data_dir": str(get_platform().app_data_dir()),
        },
        "gpu": {
            "vendor": resolve_provider().vendor(),
            "caps": sorted(resolve_provider().caps()),
            "values": dict(resolve_provider().snapshot().values),
        },
        "instance": instance.to_dict(),
        "env": env_runtime.inspect(instance, conn=None, bench=False),
        "doctor": doctor.report(instance, conn, include_disk_bench=False),
        "run_report": run_report.last_run(instance),
        "assets": asset.report(conn, instance),
        "snapshots": snapshot.list_snapshots(conn, instance.id)[:10],
    }

    log_path = run_report.locate(instance)
    tail = ""
    if log_path is not None:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-_LOG_TAIL:]
        tail = _sanitize("\n".join(lines))

    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("zui-report.json", json.dumps(report, ensure_ascii=False, indent=2))
        archive.writestr("comfyui-log-tail.txt", tail or "(no log available)")
    return bundle


def _sanitize(text: str) -> str:
    for token in (str(Path.home()),):
        text = text.replace(token, "<HOME>")
    return text


def _version() -> str:
    from zui import __version__

    return __version__


__all__ = ["export"]
