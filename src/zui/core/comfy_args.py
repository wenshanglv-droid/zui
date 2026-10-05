"""ComfyUI CLI surface, captured from the target install.

Two capture strategies, both *without importing ComfyUI into zui*:

1. runtime — ask the instance's own interpreter to dump its argparse tree.
2. static  — parse `comfy/cli_args.py` with `ast` (never executes it).

The static path matters: it still produces a full parameter table when the
instance's venv is broken, which is exactly when the user needs the UI most.
"""

from __future__ import annotations

import ast
import json
import sqlite3
import subprocess
from typing import Any

from zui.core.instance import Instance
from zui.store import repo

_CAPTURE = r"""
import json
import argparse
import comfy.options as opts

opts.args_parsing = False
import comfy.cli_args as ca


def kind(action):
    if isinstance(action, argparse._StoreTrueAction):
        return "bool"
    if action.choices:
        return "choice"
    if action.type is int:
        return "int"
    if action.type is float:
        return "float"
    if isinstance(action, argparse._AppendAction):
        return "append"
    if action.nargs:
        return "list"
    return "string"


def describe(action):
    return {
        "flags": list(action.option_strings),
        "dest": action.dest,
        "help": (action.help or "").strip().replace("\n", " "),
        "kind": kind(action),
        "choices": list(action.choices) if action.choices else None,
        "default": None if action.default is None else str(action.default),
        "metavar": action.metavar,
    }


parser = ca.parser
groups = []
claimed = set()
for group in parser._mutually_exclusive_groups:
    items = [describe(a) for a in group._group_actions if a.option_strings]
    for action in group._group_actions:
        claimed.add(id(action))
    if items:
        groups.append({"title": group.title or "group", "exclusive": True, "items": items})

rest = []
for action in parser._actions:
    if not action.option_strings or action.dest == "help" or id(action) in claimed:
        continue
    rest.append(describe(action))
if rest:
    groups.append({"title": "其他选项", "exclusive": False, "items": rest})

print("__ZUI_ARGS__" + json.dumps({"groups": groups}))
"""

_LABELS: dict[str, tuple[str, str]] = {
    "--highvram": ("保持模型常驻显存", "显存充足时可省去反复加载"),
    "--gpu-only": ("全部载入显存", "显存不足时反而会失败"),
    "--lowvram": ("低显存模式", "显存不足时启用"),
    "--novram": ("极低显存模式", "最后手段，速度最慢"),
    "--reserve-vram": ("预留显存 (GB)", "给系统或其它程序留出空间"),
    "--vram-headroom": ("动态显存余量 (GB)", "动态显存保留的额外余量"),
    "--fp16-unet": ("扩散模型 fp16", "Ampere 及更早 GPU 常用"),
    "--bf16-unet": ("扩散模型 bf16", "Ada/Blackwell 推荐"),
    "--fp8_e4m3fn-unet": ("扩散模型 fp8 存储", "省显存；Ampere 无 FP8 计算单元"),
    "--fp16-vae": ("VAE fp16", "省显存，可能出现黑图"),
    "--bf16-vae": ("VAE bf16", "画质与速度折中"),
    "--cpu-vae": ("VAE 跑 CPU", "显存极紧时使用，最慢"),
    "--fp8_e4m3fn-text-enc": ("文本编码器 fp8", "大模型可显著省显存"),
    "--fp16-text-enc": ("文本编码器 fp16", "常见默认选择"),
    "--use-sage-attention": ("启用 SageAttention", "需先安装 sage-attention，视频模型提速明显"),
    "--use-flash-attention": ("启用 FlashAttention", "需先安装 flash-attn"),
    "--use-pytorch-cross-attention": ("PyTorch 原生注意力", "默认后端"),
    "--use-split-cross-attention": ("拆分交叉注意力", "老卡省显存"),
    "--use-quad-cross-attention": ("Quad 交叉注意力", "老卡省显存"),
    "--fast": ("实验性加速集合", "可指定 fp16_accumulation / autotune 等子项"),
    "--enable-triton-backend": ("启用 Triton 后端", "需已安装 triton"),
    "--cache-none": ("不缓存节点结果", "省内存，重复 run 不再跳过"),
    "--cache-lru": ("LRU 缓存条数", "限制缓存规模"),
    "--cache-ram": ("按内存压力缓存", "默认值依系统内存计算"),
    "--high-ram": ("激进使用内存", "内存充足时可减少换页"),
    "--disable-smart-memory": ("激进卸载到内存", "显存紧张时 fallback"),
    "--disable-pinned-memory": ("禁用锁页内存", "与其它程序争抢内存时可尝试"),
    "--preview-method": ("采样预览方式", "none 最快，latent2rgb 有预览但更慢"),
    "--fast-disk": ("磁盘换页优先", "机械盘上不要启用"),
    "--disable-mmap": ("不使用 mmap 加载", "内存不足时更稳，但加载更慢"),
    "--enable-manager": ("启用 ComfyUI-Manager", "会联网抓取，断网时拖慢启动"),
    "--disable-manager-ui": ("仅关闭 Manager 界面", "后台任务仍运行"),
    "--listen": ("监听地址", ""),
    "--port": ("端口", ""),
    "--disable-api-nodes": ("禁用 API 节点", "同时阻断前端联网请求"),
    "--cuda-malloc": ("启用 cudaMallocAsync", "torch 2.0+ 默认开启"),
    "--disable-cuda-malloc": ("禁用 cudaMallocAsync", "显存碎片异常时可尝试"),
    "--disable-xformers": ("禁用 xformers", "已装 xformers 但想回退时使用"),
    "--deterministic": ("确定性采样", "会略慢，但结果可复现"),
    "--windows-standalone-build": ("Windows 独立构建模式", "Desktop 环境常见"),
}

# UI sections: (title, keyword fragments matched against the flag)
_SECTIONS: list[tuple[str, tuple[str, ...]]] = [
    ("注意力与加速", ("attention", "fast", "triton", "xformers", "compile", "deterministic")),
    ("显存与卸载", ("vram", "ram", "memory", "gpu-only", "cpu", "malloc", "swap")),
    ("精度", ("fp16", "bf16", "fp8", "fp32", "fp64", "unet", "vae", "text-enc", "force-fp")),
    ("缓存", ("cache",)),
    (
        "目录与网络",
        ("directory", "listen", "port", "tls", "cors", "upload", "manager", "api-nodes"),
    ),
    ("调试与其它", ()),
]

_RECOMMENDED: dict[str, str] = {
    "--fast": "fp16_accumulation autotune",
    "--enable-triton-backend": "",
    "--preview-method": "none",
    "--disable-mmap": "",
}


def _labels() -> dict[str, tuple[str, str]]:
    return _LABELS


# ------------------------------------------------------------------ static parse


def _const_str(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _static_kind(keywords: dict[str, ast.expr]) -> tuple[str, list[str] | None]:
    action = keywords.get("action")
    action_name = _const_str(action) if action is not None else None
    if action_name in {"store_true", "store_false"}:
        return "bool", None
    if action_name == "append":
        return "append", None
    choices = keywords.get("choices")
    if isinstance(choices, (ast.List, ast.Tuple, ast.Set)):
        values = [_const_str(item) for item in choices.elts]
        if all(value is not None for value in values):
            return "choice", [str(value) for value in values if value is not None]
    type_node = keywords.get("type")
    if isinstance(type_node, ast.Name):
        if type_node.id == "int":
            return "int", None
        if type_node.id == "float":
            return "float", None
    nargs = keywords.get("nargs")
    if nargs is not None:
        return "list", None
    return "string", None


def _static_describe(call: ast.Call, receiver: str) -> dict[str, Any] | None:
    raw_flags = [_const_str(arg) for arg in call.args]
    flags = [flag for flag in raw_flags if flag is not None]
    if not flags:
        return None
    keywords = {kw.arg: kw.value for kw in call.keywords if kw.arg}
    kind, choices = _static_kind(keywords)
    default_node = keywords.get("default")
    default: str | None = None
    if isinstance(default_node, ast.Constant) and default_node.value is not None:
        default = str(default_node.value)
    help_node = keywords.get("help")
    help_text = ""
    if isinstance(help_node, ast.Constant) and isinstance(help_node.value, str):
        help_text = " ".join(help_node.value.split())
    elif isinstance(help_node, ast.JoinedStr):
        help_text = "".join(
            part.value
            for part in help_node.values
            if isinstance(part, ast.Constant) and isinstance(part.value, str)
        ).strip()
    metavar = _const_str(keywords["metavar"]) if "metavar" in keywords else None
    return {
        "flags": flags,
        "dest": flags[-1].lstrip("-").replace("-", "_"),
        "help": help_text,
        "kind": kind,
        "choices": choices,
        "default": default,
        "metavar": metavar,
        "group": receiver,
    }


def static_capture(instance: Instance) -> dict[str, Any]:
    """Parse `comfy/cli_args.py` without executing it."""
    base = instance.comfy_dir or instance.root
    source = base / "comfy" / "cli_args.py"
    if not source.exists():
        raise ValueError(f"找不到 ComfyUI 参数定义文件: {source}")
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))

    exclusive: dict[str, str] = {}
    titled: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        value = node.value
        if not isinstance(value, ast.Call):
            continue
        attr = getattr(value.func, "attr", "")
        title = _const_str(value.args[0]) if value.args else target.id
        if attr == "add_mutually_exclusive_group":
            exclusive[target.id] = title or target.id
        elif attr == "add_argument_group":
            titled[target.id] = title or target.id

    grouped: dict[str, dict[str, Any]] = {}

    def bucket(name: str, title: str, is_exclusive: bool) -> dict[str, Any]:
        key = f"{'x' if is_exclusive else 'g'}:{name}"
        if key not in grouped:
            grouped[key] = {
                "title": title,
                "exclusive": is_exclusive,
                "items": [],
            }
        return grouped[key]

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "add_argument":
            continue
        receiver = func.value.id if isinstance(func.value, ast.Name) else "parser"
        item = _static_describe(node, receiver)
        if item is None:
            continue
        if receiver in exclusive:
            bucket(receiver, exclusive[receiver], True)["items"].append(item)
        elif receiver in titled:
            bucket(receiver, titled[receiver], False)["items"].append(item)
        else:
            bucket("parser", "通用选项", False)["items"].append(item)

    groups = sorted(grouped.values(), key=lambda g: (not g["exclusive"], g["title"]))
    return {
        "groups": groups,
        "labels": _labels(),
        "source": "static",
        "file": str(source),
    }


# ------------------------------------------------------------------- persistence


def _key(instance: Instance, source: str) -> str:
    return f"{instance.comfy_version or 'unknown'}:{source}"


def capture(
    instance: Instance,
    conn: sqlite3.Connection,
    timeout: float = 120.0,
    *,
    force_static: bool = False,
) -> dict[str, Any]:
    """Dump the argparse tree; falls back to static parsing when the venv is broken."""
    if force_static:
        spec = static_capture(instance)
    else:
        python = instance.python
        if python is None:
            spec = static_capture(instance)
        else:
            try:
                spec = _runtime_capture(instance, python, timeout)
            except (ValueError, OSError, subprocess.SubprocessError):
                spec = static_capture(instance)
    _store(conn, instance, spec)
    return spec


def _runtime_capture(instance: Instance, python: Any, timeout: float) -> dict[str, Any]:
    cwd = instance.comfy_dir or instance.root
    try:
        result = subprocess.run(  # noqa: S603
            [str(python), "-c", _CAPTURE],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError("capturing ComfyUI arguments timed out") from exc

    for line in result.stdout.splitlines():
        if line.startswith("__ZUI_ARGS__"):
            spec: dict[str, Any] = json.loads(line[len("__ZUI_ARGS__") :])
            spec["labels"] = _labels()
            spec["source"] = "runtime"
            return spec
    raise ValueError(f"could not capture ComfyUI arguments: {(result.stderr or '').strip()[:300]}")


def _store(conn: sqlite3.Connection, instance: Instance, spec: dict[str, Any]) -> None:
    key = _key(instance, str(spec.get("source") or "static"))
    repo.execute(
        conn, "DELETE FROM argspecs WHERE instance_id = ? AND comfy_hash = ?", (instance.id, key)
    )
    repo.execute(
        conn,
        "INSERT INTO argspecs (instance_id, comfy_hash, spec_json, captured_at) VALUES (?,?,?,?)",
        (instance.id, key, json.dumps(spec, ensure_ascii=False), repo.now_iso()),
    )


def load(instance: Instance, conn: sqlite3.Connection) -> dict[str, Any] | None:
    """Prefer the runtime capture; fall back to the static one."""
    for source in ("runtime", "static"):
        row = repo.fetch_one(
            conn,
            "SELECT spec_json FROM argspecs WHERE instance_id = ? AND comfy_hash = ?",
            (instance.id, _key(instance, source)),
        )
        if row is not None:
            spec: dict[str, Any] = json.loads(row["spec_json"])
            return spec
    return None


# ------------------------------------------------------------------------- views


def _section_for(flag: str) -> str:
    for title, needles in _SECTIONS:
        for needle in needles:
            if needle in flag:
                return title
    return _SECTIONS[-1][0]


def summary(spec: dict[str, Any]) -> dict[str, Any]:
    labels: dict[str, tuple[str, str]] = spec.get("labels", {})
    groups: list[dict[str, Any]] = []
    for group in spec.get("groups", []):
        items = []
        for item in group.get("items", []):
            primary = (item.get("flags") or [None])[0]
            label, hint = labels.get(str(primary), ("", ""))
            items.append({**item, "label": label, "hint": hint})
        groups.append({**group, "items": items})
    count = sum(len(group.get("items", [])) for group in groups)
    return {
        "groups": groups,
        "option_count": count,
        "source": spec.get("source", "unknown"),
    }


def form(spec: dict[str, Any]) -> dict[str, Any]:
    """Flatten the capture into a UI form schema grouped by concern."""
    labels: dict[str, tuple[str, str]] = spec.get("labels", {})
    sections: dict[str, list[dict[str, Any]]] = {title: [] for title, _ in _SECTIONS}
    seen: set[str] = set()

    for group in spec.get("groups", []):
        for item in group.get("items", []):
            flags = [str(flag) for flag in item.get("flags") or []]
            if not flags:
                continue
            primary = flags[0]
            if primary in seen:
                continue
            seen.add(primary)
            label, hint = labels.get(primary, ("", ""))
            recommended = primary in _RECOMMENDED
            sections[_section_for(primary)].append(
                {
                    "flag": primary,
                    "aliases": flags[1:],
                    "label": label or primary,
                    "hint": hint,
                    "kind": item.get("kind", "string"),
                    "choices": item.get("choices"),
                    "default": item.get("default"),
                    "help": item.get("help", ""),
                    "exclusive_group": group.get("title") if group.get("exclusive") else None,
                    "recommended": recommended,
                    "recommended_value": _RECOMMENDED.get(primary, ""),
                }
            )

    ordered = [
        {"title": title, "fields": fields} for title, _ in _SECTIONS if (fields := sections[title])
    ]
    return {
        "sections": ordered,
        "field_count": sum(len(section["fields"]) for section in ordered),
        "source": spec.get("source", "unknown"),
    }


def command_line(instance: Instance, args: list[str]) -> str:
    """Render the exact command line a launch would use (for previews)."""
    python = instance.python
    head = str(python) if python is not None else "<python>"
    parts = [head, "-X", "utf8", "main.py", *args]
    return " ".join(parts)


__all__ = [
    "capture",
    "command_line",
    "form",
    "load",
    "static_capture",
    "summary",
]
