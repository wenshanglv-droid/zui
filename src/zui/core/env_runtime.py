"""Environment inspection and self-repair.

Nothing here imports ComfyUI: facts come from the filesystem and from asking the
target interpreter to report its own metadata.
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

from zui.core.instance import Instance
from zui.platform import get_platform

_PROBE = r"""
import json, sys
import importlib.metadata as md

info = {"python": "{}.{}.{}".format(*sys.version_info[:3]), "executable": sys.executable}
packages = ["torch", "torchvision", "torchaudio", "safetensors", "xformers",
            "sage-attention", "flash_attn", "triton-windows", "triton", "comfy-kitchen"]
for name in packages:
    try:
        info[name] = md.version(name)
    except Exception:
        info[name] = None
print("__ZUI_PROBE__" + json.dumps(info))
"""


def read_pyvenv_home(venv_dir: Path) -> tuple[Path | None, str | None]:
    """Return (absolute home path, raw value) declared by pyvenv.cfg."""
    cfg = venv_dir / "pyvenv.cfg"
    if not cfg.exists():
        return None, None
    for line in cfg.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.strip().lower().startswith("home"):
            raw = line.split("=", 1)[1].strip()
            return Path(raw), raw
    return None, None


def probe_python(python: Path, cwd: Path, timeout: float = 60.0) -> dict[str, Any]:
    try:
        result = subprocess.run(  # noqa: S603
            [str(python), "-c", _PROBE],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "reason": "timeout"}
    except OSError as exc:
        return {"ok": False, "reason": f"cannot execute: {exc}"}
    for line in result.stdout.splitlines():
        if line.startswith("__ZUI_PROBE__"):
            data: dict[str, Any] = json.loads(line[len("__ZUI_PROBE__") :])
            data["ok"] = True
            return data
    return {"ok": False, "reason": (result.stderr.strip() or "interpreter produced no probe")}


def inspect(
    instance: Instance, *, conn: sqlite3.Connection | None = None, bench: bool = True
) -> dict[str, Any]:
    """Collect environment facts and detected issues."""
    issues: list[dict[str, Any]] = []
    venv_dir = instance.venv_dir
    python = instance.python
    probe: dict[str, Any] = {"ok": False, "reason": "no interpreter found"}
    if python is not None:
        probe = probe_python(python, instance.comfy_dir or instance.root)

    declared_home = None
    if venv_dir is not None:
        declared_home, raw_home = read_pyvenv_home(venv_dir)
        if raw_home is not None and not Path(raw_home).exists():
            issues.append(
                _issue(
                    "venv_home_missing",
                    detail=f"{venv_dir / 'pyvenv.cfg'} 的 home 指向不存在的路径: {raw_home}",
                    fix_inputs={"venv_dir": str(venv_dir), "expected_home": raw_home},
                )
            )
        scripts_ok = venv_dir.joinpath("Scripts").is_dir() or venv_dir.joinpath("bin").is_dir()
        if not scripts_ok:
            issues.append(
                _issue(
                    "venv_scripts_missing",
                    detail=f"{venv_dir} 缺少 Scripts/bin 目录",
                    fix_inputs={"venv_dir": str(venv_dir)},
                )
            )

    if not probe.get("ok"):
        issues.append(
            _issue(
                "interpreter_unusable",
                severity="critical",
                detail=str(probe.get("reason")),
                fix_inputs={"python": str(python) if python else None},
            )
        )

    if not _ascii_only(instance.root):
        issues.append(
            _issue(
                "path_non_ascii",
                detail=f"安装路径包含非 ASCII 字符: {instance.root}",
                fix_inputs={},
            )
        )

    torch_ver = probe.get("torch")
    cuda_ver = _cuda_from(torch_ver)
    if torch_ver and not _stack_known(torch_ver):
        issues.append(
            _issue(
                "torch_stack_mismatch",
                detail=f"torch {torch_ver} 不在已知版本矩阵中",
                fix_inputs={"torch": torch_ver},
            )
        )

    model_roots = [path for path in instance.model_roots if path.is_dir()]
    disk_report: list[dict[str, Any]] = []
    for root in model_roots:
        kind, speed = _classify_disk(root) if bench else ("unknown", None)
        disk_report.append({"path": str(root), "kind": kind, "mb_per_s": speed})
        if kind == "hdd":
            issues.append(
                _issue(
                    "models_on_slow_disk",
                    detail=f"模型目录位于低速磁盘 ({speed} MB/s): {root}",
                    fix_inputs={"path": str(root)},
                )
            )

    attr = (
        "broken"
        if any(item["severity"] == "critical" for item in issues)
        else ("ok" if not issues else "degraded")
    )
    backends = [b for b in ("sage-attention", "flash_attn", "xformers") if probe.get(b)]
    attention_label = "已安装 " + ", ".join(backends) if backends else None
    facts = {
        "instance": instance.id,
        "root": str(instance.root),
        "comfy_dir": str(instance.comfy_dir) if instance.comfy_dir else None,
        "venv_dir": str(venv_dir) if venv_dir else None,
        "python": str(python) if python else None,
        "python_version": probe.get("python"),
        "torch_version": torch_ver,
        "cuda": cuda_ver,
        "attention_in_use": attention_label,
        "packages": {key: probe.get(key) for key in _PACKAGES},
        "declared_home": str(declared_home) if declared_home else None,
        "disks": disk_report,
        "attr": attr,
        "issues": issues,
    }
    if conn is not None:
        _persist(conn, instance, facts)
    return facts


_PACKAGES = (
    "torch",
    "torchvision",
    "torchaudio",
    "safetensors",
    "xformers",
    "sage-attention",
    "flash_attn",
    "triton-windows",
    "comfy-kitchen",
)


def plan_repair(instance: Instance) -> dict[str, Any]:
    """Compute, but do not apply, the actions needed to make the env usable."""
    facts = inspect(instance)
    changes: list[dict[str, Any]] = []
    for issue in facts["issues"]:
        if issue["id"] == "venv_home_missing" and instance.venv_dir is not None:
            candidate = _find_base_home(instance)
            changes.append(
                {
                    "id": "repair_pyvenv_home",
                    "target": str(instance.venv_dir / "pyvenv.cfg"),
                    "action": "rewrite home",
                    "from": facts.get("declared_home"),
                    "to": str(candidate) if candidate else None,
                    "possible": candidate is not None,
                }
            )
        elif issue["id"] == "torch_stack_mismatch":
            plan = stack_plan(instance)
            changes.append(
                {
                    "id": "reinstall_torch_stack",
                    "target": str(instance.venv_dir) if instance.venv_dir else None,
                    "action": "uv pip install torch stack",
                    "from": facts.get("torch_version"),
                    "to": (plan.get("stack") or {}).get("id"),
                    "possible": bool(plan.get("ok")) and instance.venv_dir is not None,
                    "stack": plan.get("stack"),
                    "command": plan.get("command"),
                    "note": "会先记录当前版本，装完若 torch 不可导入则回滚重装",
                }
            )
    return {"instance": instance.id, "issues": facts["issues"], "changes": changes}


def apply_repair(
    instance: Instance,
    change_id: str,
    *,
    stack_id: str | None = None,
    index_url: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Apply one planned change.

    File-level edits happen here directly; package installs delegate to
    :func:`install_stack` and roll back to the previous versions on failure.
    """
    if change_id == "reinstall_torch_stack":
        return install_stack(
            instance, stack_id=stack_id, index_url=index_url, dry_run=dry_run
        )
    if change_id != "repair_pyvenv_home":
        return {"ok": False, "error": f"unsupported change: {change_id}"}
    if instance.venv_dir is None:
        return {"ok": False, "error": "instance has no venv"}
    home_dir = _find_base_home(instance)
    if home_dir is None:
        return {"ok": False, "error": "no usable base interpreter found"}
    fallback_python = _find_base_python(instance)

    cfg = instance.venv_dir / "pyvenv.cfg"
    original = cfg.read_text(encoding="utf-8")
    backup = cfg.with_suffix(".cfg.zui-bak")
    backup.write_text(original, encoding="utf-8")
    updated = "\n".join(
        f"home = {home_dir}" if line.strip().lower().startswith("home") else line
        for line in original.splitlines()
    )
    cfg.write_text(updated, encoding="utf-8")

    verify = probe_python(
        instance.python or fallback_python or home_dir,
        instance.comfy_dir or instance.root,
    )
    if not verify.get("ok"):
        cfg.write_text(original, encoding="utf-8")
        return {
            "ok": False,
            "error": "verification failed; pyvenv.cfg restored",
            "backup": str(backup),
        }
    return {
        "ok": True,
        "changed": str(cfg),
        "from": _home_of(original),
        "to": str(home_dir),
        "backup": str(backup),
        "python": verify.get("python"),
        "torch": verify.get("torch"),
    }


def stack_plan(
    instance: Instance | None = None,
    *,
    stack_id: str | None = None,
    index_url: str | None = None,
) -> dict[str, Any]:
    """The exact torch-stack install command for this box, without running it."""
    from zui.core import torch_stacks
    from zui.gpu import resolve_provider

    stack = (
        torch_stacks.get(stack_id)
        if stack_id
        else torch_stacks.recommend(resolve_provider().vendor())
    )
    if stack is None:
        return {"ok": False, "error": f"unknown stack: {stack_id or '<auto>'}"}

    packages = stack.get("packages") or {}
    requirements = [f"{name}=={version}" for name, version in packages.items() if version]
    url = index_url or stack.get("index_url")
    uv = shutil.which("uv") or "uv"

    command = [uv, "pip", "install"]
    if instance is not None and instance.python is not None:
        command += ["--python", str(instance.python)]
    if url:
        command += ["--index-url", str(url)]
    command += requirements
    return {
        "ok": True,
        "stack": stack,
        "requirements": requirements,
        "index_url": str(url) if url else None,
        "command": command,
    }


def install_stack(
    instance: Instance,
    *,
    stack_id: str | None = None,
    index_url: str | None = None,
    dry_run: bool = False,
    timeout: float = 1800.0,
) -> dict[str, Any]:
    """Install (or preview) a torch stack into the instance's venv.

    Verifies afterwards; if torch stops importing, reinstalls the versions that
    were there before so the environment never ends up worse than it started.
    """
    plan = stack_plan(instance, stack_id=stack_id, index_url=index_url)
    if not plan.get("ok"):
        return plan
    python = instance.python
    if python is None:
        return {
            "ok": False,
            "applied_now": False,
            "error": "instance has no interpreter",
            "stack": plan.get("stack"),
            "command": plan.get("command"),
        }
    if dry_run:
        return {**plan, "applied_now": False, "instance": instance.id}

    previous = _installed_versions(instance)
    result = subprocess.run(  # noqa: S603
        plan["command"],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        return {
            "ok": False,
            "error": "install failed",
            "command": plan["command"],
            "stderr": result.stderr[-2000:],
        }

    verify = probe_python(python, instance.comfy_dir or instance.root)
    if not verify.get("ok") or not verify.get("torch"):
        rollback = _restore_versions(instance, previous, index_url)
        return {
            "ok": False,
            "error": "torch not importable after install; previous versions reinstalled",
            "rolled_back": rollback,
        }
    return {
        "ok": True,
        "applied_now": True,
        "instance": instance.id,
        "stack": plan["stack"]["id"],
        "previous": previous,
        "torch": verify.get("torch"),
        "command": plan["command"],
    }


def create_env(
    name: str,
    stack_id: str,
    *,
    parent: Path | None = None,
    index_url: str | None = None,
    apply: bool = False,
) -> dict[str, Any]:
    """Create a fresh venv with a known torch stack. Dry-run unless `apply`."""
    plan = stack_plan(None, stack_id=stack_id, index_url=index_url)
    if not plan.get("ok"):
        return plan

    uv = shutil.which("uv")
    if uv is None:
        return {"ok": False, "error": "uv not found on PATH"}

    root = (parent or Path.cwd()) / name
    venv = root / ".venv"
    adapter = get_platform()
    venv_python = venv / adapter.venv_bindir() / adapter.python_exe_names()[0]

    install = [uv, "pip", "install", "--python", str(venv_python)]
    if plan["index_url"]:
        install += ["--index-url", str(plan["index_url"])]
    install += plan["requirements"]

    python_spec = str(plan["stack"].get("python") or "3.12")
    steps: list[dict[str, Any]] = [
        {"kind": "venv", "command": [uv, "venv", "--python", python_spec, str(venv)]},
        {"kind": "torch", "command": install},
    ]
    outcome: dict[str, Any] = {
        "ok": True,
        "applied_now": False,
        "root": str(root),
        "venv": str(venv),
        "stack": plan["stack"]["id"],
        "steps": steps,
    }
    if not apply:
        outcome["next"] = f"确认后加 --apply；完成后用 `zui instance adopt {root}` 接管"
        return outcome

    done: list[dict[str, Any]] = []
    for step in steps:
        result = subprocess.run(  # noqa: S603
            step["command"], capture_output=True, text=True, timeout=1800.0, check=False
        )
        if result.returncode != 0:
            return {
                "ok": False,
                "error": f"{step['kind']} step failed",
                "command": step["command"],
                "stderr": result.stderr[-2000:],
                "completed": done,
            }
        done.append({"kind": step["kind"], "ok": True})

    probe = probe_python(venv_python, root)
    outcome |= {"applied_now": True, "completed": done, "torch": probe.get("torch")}
    outcome["next"] = f"`zui instance adopt {root}`"
    return outcome


def _installed_versions(instance: Instance) -> dict[str, str]:
    python = instance.python
    if python is None:
        return {}
    probe = probe_python(python, instance.comfy_dir or instance.root)
    return {
        name: str(probe[name])
        for name in ("torch", "torchvision", "torchaudio")
        if probe.get(name)
    }


def _restore_versions(
    instance: Instance, previous: dict[str, str], index_url: str | None
) -> dict[str, Any]:
    """Best-effort rollback of a failed stack install."""
    if not previous:
        return {"ok": False, "error": "no previous versions recorded"}
    uv = shutil.which("uv")
    if uv is None or instance.python is None:
        return {"ok": False, "error": "uv or interpreter missing"}
    command = [uv, "pip", "install", "--python", str(instance.python)]
    if index_url:
        command += ["--index-url", index_url]
    command += [f"{name}=={version}" for name, version in previous.items()]
    result = subprocess.run(  # noqa: S603
        command, capture_output=True, text=True, timeout=1800.0, check=False
    )
    return {"ok": result.returncode == 0, "command": command}


def _home_of(text: str) -> str | None:
    for line in text.splitlines():
        if line.strip().lower().startswith("home"):
            return line.split("=", 1)[1].strip()
    return None


def _repair_roots(instance: Instance) -> list[Path]:
    """Candidate roots to search for a standalone base interpreter."""
    roots: list[Path] = [instance.root, instance.root.parent]
    if instance.comfy_dir is not None:
        roots += [instance.comfy_dir.parent, instance.comfy_dir]
    seen: set[Path] = set()
    ordered: list[Path] = []
    for root in roots:
        if root not in seen:
            seen.add(root)
            ordered.append(root)
    return ordered


def _find_base_python(instance: Instance) -> Path | None:
    from zui.core.paths import base_python_for

    for root in _repair_roots(instance):
        found = base_python_for(root)
        if found is not None and found.exists():
            return found
    return None


def _find_base_home(instance: Instance) -> Path | None:
    """Directory to write into pyvenv.cfg's ``home`` key."""
    from zui.core.paths import base_home_for

    for root in _repair_roots(instance):
        found = base_home_for(root)
        if found is not None and found.is_dir():
            return found
    return None


def _issue(
    ident: str, *, detail: str, severity: str = "critical", fix_inputs: dict[str, Any]
) -> dict[str, Any]:
    del severity  # severity lives in rules; issues carry it via detect consistently
    sev = (
        "critical"
        if ident
        in {
            "venv_home_missing",
            "venv_scripts_missing",
            "interpreter_unusable",
            "torch_stack_mismatch",
        }
        else "warning"
    )
    return {"id": ident, "severity": sev, "detail": detail, "inputs": fix_inputs}


def _ascii_only(path: Path) -> bool:
    try:
        str(path).encode("ascii")
    except UnicodeEncodeError:
        return False
    return True


def _cuda_from(torch_version: str | None) -> str | None:
    if not torch_version:
        return None
    match = re.search(r"\+(cu\d+|cpu|rocm[\d.]+)", torch_version)
    return match.group(1) if match else None


def _stack_known(torch_version: str) -> bool:
    from zui.core.torch_stacks import is_known

    return is_known(torch_version)


def _classify_disk(path: Path) -> tuple[str, float | None]:
    adapter = get_platform()
    try:
        speed = adapter.disk_bench(path)
    except Exception:
        return "unknown", None
    kind = adapter.disk_kind(path)
    return kind, speed


def _persist(conn: sqlite3.Connection, instance: Instance, facts: dict[str, Any]) -> None:
    from zui.store import repo

    repo.execute(
        conn,
        """
        INSERT INTO instance_env (instance_id, python_ver, torch_ver, cuda_ver, attr,
                                  issues_json, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(instance_id) DO UPDATE SET
            python_ver=excluded.python_ver, torch_ver=excluded.torch_ver,
            cuda_ver=excluded.cuda_ver, attr=excluded.attr,
            issues_json=excluded.issues_json, updated_at=excluded.updated_at
        """,
        (
            instance.id,
            facts.get("python_version"),
            facts.get("torch_version"),
            facts.get("cuda"),
            facts.get("attr"),
            json.dumps(facts.get("issues", []), ensure_ascii=False),
            repo.now_iso(),
        ),
    )


__all__ = [
    "apply_repair",
    "create_env",
    "install_stack",
    "inspect",
    "plan_repair",
    "probe_python",
    "read_pyvenv_home",
    "stack_plan",
]
