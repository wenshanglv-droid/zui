"""ComfyUI log pipeline parser driven by rules/log_patterns.yaml."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from zui.core.paths import rules_dir


def load_patterns() -> dict[str, Any]:
    import yaml

    raw = (rules_dir() / "log_patterns.yaml").read_text(encoding="utf-8")
    data: dict[str, Any] = yaml.safe_load(raw) or {}
    return data


def analyze(text: str, *, max_runs: int = 5) -> dict[str, Any]:
    """Turn raw ComfyUI output into environment facts, startup phases and run reports."""
    patterns = load_patterns()
    ts_re = re.compile(patterns.get("timestamp", ""))
    line_res = {key: re.compile(value) for key, value in (patterns.get("lines") or {}).items()}

    events: list[dict[str, Any]] = []
    for raw in _frames(text):
        cleaned = _strip_ansi(raw)
        if not cleaned.strip():
            continue
        match = ts_re.match(cleaned)
        if match:
            stamp, body = _parse_ts(match.group("ts")), match.group("msg")
        else:
            stamp, body = None, cleaned
        kinds = [key for key, exp in line_res.items() if exp.search(body)]
        events.append({"ts": stamp, "text": body, "kinds": kinds})

    return {
        "env": _env_facts(events, line_res),
        "startup": _startup_phases(events, patterns),
        "runs": _runs(events, line_res, max_runs=max_runs),
    }


def analyze_file(path: Path, **kwargs: Any) -> dict[str, Any]:
    return analyze(path.read_text(encoding="utf-8", errors="replace"), **kwargs)


def _frames(text: str) -> list[str]:
    """tqdm writes progress with carriage returns; treat every frame as a line."""
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1B\[[0-?]*[ -/]*[@-~]", "", text)


def _parse_ts(value: str) -> float | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S.%f").timestamp()
    except ValueError:
        return None


def _parse_clock(value: str) -> float:
    """`01:32` or `1:02:03` -> seconds."""
    parts = [int(part) for part in value.split(":") if part]
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + part
    return float(seconds)


def _env_facts(events: list[dict[str, Any]], line_res: dict[str, Any]) -> dict[str, Any]:
    facts: dict[str, Any] = {}
    for event in events:
        text = event["text"]
        if "attention_backend" in event["kinds"]:
            facts["attention"] = line_res["attention_backend"].search(text).group("backend").strip()
        elif "torch_version" in event["kinds"]:
            facts["torch"] = line_res["torch_version"].search(text).group("version").strip()
        elif "vram_state" in event["kinds"]:
            facts["vram_state"] = line_res["vram_state"].search(text).group("state")
        elif "storage_policy" in event["kinds"]:
            facts["fast_disk"] = line_res["storage_policy"].search(text).group("flag") == "True"
        elif "total_vram" in event["kinds"]:
            match = line_res["total_vram"].search(text)
            facts["vram_total_mb"] = int(match.group("vram"))
            facts["ram_total_mb"] = int(match.group("ram"))
        elif "Device:" in text:
            facts["device"] = text.split("Device:", 1)[1].strip()
    return facts


def _startup_phases(events: list[dict[str, Any]], patterns: dict[str, Any]) -> list[dict[str, Any]]:
    phases: list[dict[str, Any]] = []
    for spec in patterns.get("phases", []):
        if spec.get("id") not in {"manager_wait", "init"}:
            continue
        start_re, end_re = re.compile(spec["start"]), re.compile(spec["end"])
        start = next((e["ts"] for e in events if start_re.search(e["text"])), None)
        end = next((e["ts"] for e in events if e["ts"] and end_re.search(e["text"])), None)
        if start is None or end is None or end < start:
            continue
        phases.append({"id": spec["id"], "seconds": round(end - start, 2)})
    return phases


def _runs(
    events: list[dict[str, Any]], line_res: dict[str, Any], *, max_runs: int
) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for event in events:
        text, kinds = event["text"], event["kinds"]
        if "got_prompt" in kinds:
            current = {
                "started_at": _iso(event["ts"]),
                "start_ts": event["ts"],
                "loads": [],
                "tqdm": [],
                "vaes": [],
                "total_seconds": None,
                "end_ts": None,
            }
            runs.append(current)
            continue
        if current is None:
            continue

        if "requested_load" in kinds:
            model = line_res["requested_load"].search(text).group("model").strip()
            current["loads"].append({"model": model, "ts": event["ts"]})
        elif "vae_load" in kinds:
            match = line_res["vae_load"].search(text)
            current["vaes"].append({"dtype": match.group("dtype"), "ts": event["ts"]})
        elif "tqdm" in kinds:
            match = line_res["tqdm"].match(text)
            if match:
                rate = float(match.group("rate"))
                unit = match.group("unit")
                current["tqdm"].append(
                    {
                        "steps": int(match.group("total")),
                        "elapsed_seconds": _parse_clock(match.group("elapsed")),
                        "rate": rate if unit == "s/it" else round(1 / rate, 4) if rate else None,
                    }
                )
        elif "prompt_executed" in kinds:
            current["total_seconds"] = float(
                line_res["prompt_executed"].search(text).group("seconds")
            )
            current["end_ts"] = event["ts"]
            current = None

    for run in runs:
        _finalize(run)
    return runs[-max_runs:]


def _finalize(run: dict[str, Any]) -> None:
    start_ts, end_ts = run.pop("start_ts", None), run.pop("end_ts", None)

    sampling = {"seconds": None, "steps": None, "rate_s_per_it": None}
    if run["tqdm"]:
        last = run["tqdm"][-1]
        sampling.update(
            {
                "seconds": round(last["elapsed_seconds"], 2),
                "steps": last["steps"],
                "rate_s_per_it": last["rate"],
            }
        )

    for index, load in enumerate(run["loads"]):
        nxt = run["loads"][index + 1]["ts"] if index + 1 < len(run["loads"]) else None
        boundary = nxt if nxt is not None else end_ts
        if load["ts"] is not None and boundary is not None:
            load["seconds"] = round(max(boundary - load["ts"], 0.0), 2)
        else:
            load["seconds"] = None

    # Sampling time is folded into whichever load gap immediately precedes VAE work.
    host = _sampling_host(run)
    if host is not None and host.get("seconds") and sampling["seconds"]:
        host["seconds"] = round(max(host["seconds"] - sampling["seconds"], 0.0), 2)

    for load in run["loads"]:
        del load["ts"]
    for vae in run["vaes"]:
        del vae["ts"]

    if run["total_seconds"] is None and start_ts and end_ts:
        run["total_seconds"] = round(max(end_ts - start_ts, 0.0), 2)

    load_total = round(sum(item.get("seconds") or 0 for item in run["loads"]), 2)
    run["load_seconds_total"] = load_total
    run["sampling"] = sampling
    run["other_seconds"] = (
        round(max(run["total_seconds"] - load_total - (sampling["seconds"] or 0), 0.0), 2)
        if run["total_seconds"] is not None
        else None
    )


def _sampling_host(run: dict[str, Any]) -> dict[str, Any] | None:
    loads: list[dict[str, Any]] = list(run.get("loads") or [])
    if not loads:
        return None
    vae_stamps = [float(vae["ts"]) for vae in (run.get("vaes") or []) if vae.get("ts") is not None]
    if vae_stamps:
        first_vae = min(vae_stamps)
        for index, load in enumerate(loads):
            stop = loads[index + 1].get("ts") if index + 1 < len(loads) else None
            start = load.get("ts")
            if start is not None and stop is not None and start < first_vae <= stop:
                return load
    return loads[-1]


def _iso(value: float | None) -> str | None:
    return datetime.fromtimestamp(value).isoformat(timespec="seconds") if value else None


__all__ = ["analyze", "analyze_file", "load_patterns"]
