"""Environment health report driven by rules/doctor_rules.yaml."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
from typing import Any

import psutil

from zui.core import env_runtime
from zui.core.instance import Instance
from zui.core.launch_env import detect_portable_git
from zui.core.paths import rules_dir
from zui.platform import get_platform


def load_rules() -> list[dict[str, Any]]:
    import yaml

    data = yaml.safe_load((rules_dir() / "doctor_rules.yaml").read_text(encoding="utf-8")) or {}
    return list(data.get("checks", []))


def _rule(ident: str) -> dict[str, Any]:
    for rule in load_rules():
        if rule.get("id") == ident:
            return rule
    return {"id": ident, "severity": "info", "title": ident, "detail": "", "fix": None}


def _finding(ident: str, *, status: str, detail: str | None = None) -> dict[str, Any]:
    rule = _rule(ident)
    return {
        "id": ident,
        "severity": rule.get("severity", "info"),
        "title": rule.get("title", ident),
        "detail": detail or rule.get("detail", ""),
        "fix": rule.get("fix"),
        "status": status,
    }


Check = Callable[
    [Instance, sqlite3.Connection, dict[str, Any]], tuple[str, str | None] | None
]

_BACKENDS = ("sage-attention", "flash_attn", "xformers")


def _applies(rule: dict[str, Any], adapter: Any) -> bool:
    """Honour the `platforms` field of a rule; unknown platforms are skipped."""
    platforms = rule.get("platforms")
    if not platforms:
        return True
    return str(getattr(adapter, "name", "")) in list(platforms)


def _env_check(issue_id: str) -> Check:
    def _run(
        instance: Instance, conn: sqlite3.Connection, ctx: dict[str, Any]
    ) -> tuple[str, str | None]:
        del instance, conn
        issues = {issue["id"]: issue for issue in (ctx["facts"].get("issues") or [])}
        if issue_id in issues:
            return "failed", str(issues[issue_id]["detail"])
        return "passed", None

    return _run


def _env_ok_check(
    instance: Instance, conn: sqlite3.Connection, ctx: dict[str, Any]
) -> tuple[str, str | None] | None:
    del instance, conn
    if ctx["facts"].get("issues"):
        return None
    return "passed", None


def _git_check(
    instance: Instance, conn: sqlite3.Connection, ctx: dict[str, Any]
) -> tuple[str, str | None]:
    del conn, ctx
    git = detect_portable_git(instance.root, instance.root.parent)
    if git:
        return "passed", f"使用 Git: {git}"
    return "failed", "未找到 Git，节点管理将不可用"


def _port_check(
    instance: Instance, conn: sqlite3.Connection, ctx: dict[str, Any]
) -> tuple[str, str | None]:
    del instance, conn, ctx
    owner = _port_owner(8188)
    if owner:
        return "failed", f"端口 8188 已被占用: {owner}"
    return "passed", "端口 8188 空闲"


def _attention_check(
    instance: Instance, conn: sqlite3.Connection, ctx: dict[str, Any]
) -> tuple[str, str | None]:
    del conn
    packages = ctx["facts"].get("packages") or {}
    installed = [name for name in _BACKENDS if packages.get(name)]
    seen = _last_attention(instance)
    if seen:
        return "passed", f"当前日志显示为 {seen}"
    if installed:
        return "passed", f"已安装 {', '.join(installed)}"
    return "failed", "未检测到加速后端"


def _power_check(
    instance: Instance, conn: sqlite3.Connection, ctx: dict[str, Any]
) -> tuple[str, str | None]:
    del instance, conn
    warning = ctx["adapter"].power_plan_warning()
    return ("failed", warning) if warning else ("passed", "")


def _dependency_check(
    instance: Instance, conn: sqlite3.Connection, ctx: dict[str, Any]
) -> tuple[str, str | None]:
    """Compare pinned versions across every custom node's requirements.txt."""
    del conn, ctx
    from zui.core import nodes as nodes_mod

    base = nodes_mod.nodes_dir(instance)
    if base is None or not base.is_dir():
        return "passed", "没有 custom_nodes 目录"

    seen: dict[str, tuple[str, str]] = {}
    conflicts: list[str] = []
    for requirements in sorted(base.glob("*/requirements.txt")):
        owner = requirements.parent.name
        for raw in requirements.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith(("#", "-")) or "://" in line:
                continue
            match = re.match(r"^([A-Za-z0-9_.\-]+)\s*(.*)$", line)
            if not match:
                continue
            name = match.group(1).lower().replace("_", "-")
            spec = (match.group(2) or "").strip()
            previous = seen.get(name)
            if previous and previous[1] != spec:
                conflicts.append(
                    f"{name}: {previous[0]} 要求 {previous[1] or '任意'} "
                    f"vs {owner} 要求 {spec or '任意'}"
                )
            seen.setdefault(name, (owner, spec))

    if conflicts:
        return "failed", "; ".join(conflicts)
    if not seen:
        return "passed", "自定义节点没有声明依赖"
    return "passed", f"已比对 {len(seen)} 个依赖声明，未发现冲突"


def _pagefile_check(
    instance: Instance, conn: sqlite3.Connection, ctx: dict[str, Any]
) -> tuple[str, str | None]:
    del instance, conn
    adapter = ctx["adapter"]
    paths = list(adapter.pagefile_paths())
    if not paths:
        return "passed", "未检测到页面文件（该平台无此项或已禁用）"
    slow: list[str] = []
    for path in paths:
        try:
            kind = adapter.disk_kind(path.parent)
        except Exception:  # noqa: BLE001
            continue
        if kind == "hdd":
            slow.append(f"{path} ({kind})")
    if slow:
        return "failed", "页面文件位于低速磁盘: " + ", ".join(slow)
    return "passed", "页面文件所在磁盘速度可接受"


def _weights_check(
    instance: Instance, conn: sqlite3.Connection, ctx: dict[str, Any]
) -> tuple[str, str | None] | None:
    if not ctx.get("include_model_scan"):
        return None
    from zui.core import asset

    summary = asset.scan(instance, conn)
    ctx["model_summary"] = summary
    total = float(summary.get("total_bytes") or 0)
    ram_gb = psutil.virtual_memory().total / 2**30
    return (
        _memory_verdict(instance, total),
        f"模型合计 {summary.get('total_gb')} GB，物理内存 {ram_gb:.1f} GB",
    )


_EVALUATORS: dict[str, Check] = {
    "venv_home_missing": _env_check("venv_home_missing"),
    "venv_scripts_missing": _env_check("venv_scripts_missing"),
    "interpreter_unusable": _env_check("interpreter_unusable"),
    "torch_stack_mismatch": _env_check("torch_stack_mismatch"),
    "path_non_ascii": _env_check("path_non_ascii"),
    "models_on_slow_disk": _env_check("models_on_slow_disk"),
    "env_ok": _env_ok_check,
    "git_missing": _git_check,
    "port_conflict": _port_check,
    "attention_backend_default": _attention_check,
    "power_plan_balanced": _power_check,
    "dependency_conflict": _dependency_check,
    "pagefile_on_slow_disk": _pagefile_check,
    "weights_exceed_ram": _weights_check,
}


def report(
    instance: Instance,
    conn: sqlite3.Connection,
    *,
    include_disk_bench: bool = True,
    include_model_scan: bool = False,
) -> dict[str, Any]:
    """Run the checks listed in rules/doctor_rules.yaml, in that order.

    Adding, reordering or disabling a check is a rule-file edit; no code change.
    """
    adapter = get_platform()
    facts = env_runtime.inspect(instance, conn=conn, bench=include_disk_bench)
    context: dict[str, Any] = {
        "facts": facts,
        "adapter": adapter,
        "include_model_scan": include_model_scan,
    }

    findings: list[dict[str, Any]] = []
    model_summary: dict[str, Any] | None = None
    for rule in load_rules():
        ident = str(rule.get("id"))
        if not _applies(rule, adapter):
            continue
        evaluator = _EVALUATORS.get(ident)
        if evaluator is None:
            continue
        outcome = evaluator(instance, conn, context)
        if outcome is None:
            continue
        status, detail = outcome
        findings.append(_finding(ident, status=status, detail=detail))
        if ident == "weights_exceed_ram":
            model_summary = context.get("model_summary")

    packages = facts.get("packages") or {}
    backends = [name for name in _BACKENDS if packages.get(name)]
    attention = _last_attention(instance)
    effective = attention or ("已安装 " + ", ".join(backends) if backends else None)

    return {
        "instance": instance.id,
        "attr": facts.get("attr"),
        "env": {
            "python": facts.get("python_version"),
            "torch": facts.get("torch_version"),
            "cuda": facts.get("cuda"),
            "attention_in_use": effective,
            "disks": facts.get("disks"),
        },
        "model_summary": model_summary,
        "findings": findings,
    }


def _last_attention(instance: Instance) -> str | None:
    from zui.core import logparse, run_report

    log = run_report.locate(instance)
    if log is None:
        return None
    try:
        facts = logparse.analyze_file(log).get("env") or {}
    except Exception:
        return None
    return facts.get("attention")


def _memory_verdict(instance: Instance, total_bytes: float) -> str:
    del instance
    if total_bytes <= 0:
        return "unknown"
    ram = psutil.virtual_memory().total
    if total_bytes > ram:
        return "failed"
    if total_bytes > ram * 0.8:
        return "warning"
    return "passed"


def _port_owner(port: int) -> str | None:
    from zui.core.proc import port_owner

    return port_owner(port)


__all__ = ["load_rules", "report"]
