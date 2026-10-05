"""All zui CLI commands.

Every command supports --json and honours docs/cli.md exit codes.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import typer

from zui.cli.output import Result, emit
from zui.core import (
    accelerate,
    asset,
    budget,
    comfy_args,
    diag_package,
    doctor,
    env_runtime,
    nodes,
    proc,
    profile,
    rules_sync,
    run_report,
    snapshot,
)
from zui.core import (
    instance as instance_mod,
)
from zui.core.launch_env import build_child_env, diff_env, resolve_pypi_index
from zui.store import repo

Json = typer.Option  # readability alias for the shared flag

app = typer.Typer(no_args_is_help=True, help="zui - ComfyUI environment operating system")
instances_app = typer.Typer(no_args_is_help=True, help="实例注册表")
env_app = typer.Typer(no_args_is_help=True, help="环境与自愈")
model_app = typer.Typer(no_args_is_help=True, help="模型资产与预算")
run_app = typer.Typer(no_args_is_help=True, help="运行性能报告")
optimize_app = typer.Typer(no_args_is_help=True, help="一键加速")
snapshots_app = typer.Typer(no_args_is_help=True, help="快照与回滚")
diag_app = typer.Typer(no_args_is_help=True, help="诊断包")
args_app = typer.Typer(no_args_is_help=True, help="启动参数表与档位")
node_app = typer.Typer(no_args_is_help=True, help="自定义节点管理")
rules_app = typer.Typer(no_args_is_help=True, help="外部化规则（可远端更新）")

app.add_typer(instances_app, name="instance")
app.add_typer(env_app, name="env")
app.add_typer(model_app, name="model")
app.add_typer(run_app, name="run")
app.add_typer(optimize_app, name="optimize")
app.add_typer(snapshots_app, name="snapshot")
app.add_typer(diag_app, name="diag")
app.add_typer(args_app, name="args")
app.add_typer(node_app, name="node")
app.add_typer(rules_app, name="rules")


def _emit(result: Result, as_json: bool) -> None:
    emit(result, as_json=as_json)
    raise typer.Exit(result.exit_code())


def _conn() -> Any:
    return repo.connect()


def _resolve(conn: Any, ref: str | None) -> Any:
    try:
        return instance_mod.require_instance(conn, ref)
    except KeyError as exc:
        _emit(Result.failure("TARGET_NOT_RUNNING", str(exc)), as_json=False)


# --------------------------------------------------------------------- instance


@instances_app.command("adopt")
def instance_adopt(
    path: Path = typer.Argument(..., help="包含 ComfyUI 的目录"),
    name: str | None = typer.Option(None, "--name", help="实例名称"),
    use: bool = typer.Option(True, "--use/--no-use", help="是否设为当前实例"),
    as_json: bool = Json(False, "--json"),
) -> None:
    """接管一个已存在的 ComfyUI 安装（只读）.zui 只写入自己的数据库。"""
    if not path.exists():
        _emit(Result.failure("USAGE_ERROR", f"路径不存在: {path}"), as_json)
    try:
        instance = instance_mod.adopt(path, name=name)
    except ValueError as exc:
        _emit(Result.failure("ENV_BROKEN", str(exc)), as_json)
    conn = _conn()
    if use:
        instance_mod.set_active(conn, instance.id)
        instance.active = True
    _emit(Result.success(**instance.to_dict()), as_json)


@instances_app.command("list")
def instance_list(as_json: bool = Json(False, "--json")) -> None:
    conn = _conn()
    rows = [item.to_dict() for item in instance_mod.list_instances(conn)]
    _emit(Result.success(instances=rows), as_json)


@instances_app.command("show")
def instance_show(
    ref: str | None = typer.Argument(None), as_json: bool = Json(False, "--json")
) -> None:
    conn = _conn()
    instance = _resolve(conn, ref)
    _emit(Result.success(**instance.to_dict()), as_json)


@instances_app.command("use")
def instance_use(
    ref: str = typer.Argument(..., help="实例 id 或名称"), as_json: bool = Json(False, "--json")
) -> None:
    conn = _conn()
    try:
        instance = instance_mod.set_active(conn, ref)
    except KeyError as exc:
        _emit(Result.failure("TARGET_NOT_RUNNING", str(exc)), as_json)
    _emit(Result.success(**instance.to_dict()), as_json)


@instances_app.command("remove")
def instance_remove(
    ref: str = typer.Argument(..., help="实例 id 或名称"),
    apply: bool = typer.Option(False, "--apply", help="真正注销，默认仅预览"),
    as_json: bool = Json(False, "--json"),
) -> None:
    conn = _conn()
    try:
        instance = instance_mod.get_instance(conn, ref)
    except KeyError as exc:
        _emit(Result.failure("TARGET_NOT_RUNNING", str(exc)), as_json)
    if instance is None:
        _emit(Result.failure("TARGET_NOT_RUNNING", f"unknown instance: {ref}"), as_json)
    assert instance is not None
    if not apply:
        _emit(
            Result.success(
                would_remove=instance.to_dict(),
                note="只注销注册表记录，不删除任何文件",
                applied_now=False,
            ),
            as_json,
        )
    try:
        removed = instance_mod.forget(conn, ref)
    except RuntimeError as exc:
        _emit(Result.failure("RUNTIME_ERROR", str(exc)), as_json)
    _emit(Result.success(removed=removed.to_dict(), applied_now=True), as_json)


# -------------------------------------------------------------------------- env


@env_app.command("inspect")
def env_inspect(
    ref: str | None = typer.Argument(None),
    no_bench: bool = typer.Option(False, "--no-bench", help="跳过磁盘测速"),
    as_json: bool = Json(False, "--json"),
) -> None:
    conn = _conn()
    instance = _resolve(conn, ref)
    facts = env_runtime.inspect(instance, conn=conn, bench=not no_bench)
    _emit(Result.success(**facts), as_json)


@env_app.command("doctor")
def env_doctor(
    ref: str | None = typer.Argument(None),
    no_bench: bool = typer.Option(False, "--no-bench"),
    scan_models: bool = typer.Option(False, "--scan-models", help="同时扫描模型资产"),
    as_json: bool = Json(False, "--json"),
) -> None:
    conn = _conn()
    instance = _resolve(conn, ref)
    report = doctor.report(
        instance, conn, include_disk_bench=not no_bench, include_model_scan=scan_models
    )
    _emit(Result.success(**report), as_json)


@env_app.command("repair")
def env_repair(
    ref: str | None = typer.Argument(None),
    apply: bool = typer.Option(False, "--apply", help="真正写入修改，默认仅预览"),
    stack: str | None = typer.Option(None, "--stack", help="重装 torch 时使用的栈 id"),
    pypi_mirror: str | None = typer.Option(None, "--pypi-mirror"),
    as_json: bool = Json(False, "--json"),
) -> None:
    conn = _conn()
    instance = _resolve(conn, ref)
    plan = env_runtime.plan_repair(instance)
    index_url = resolve_pypi_index(pypi_mirror) or os.environ.get("UV_INDEX_URL")
    applied: list[dict[str, Any]] = []
    if apply:
        snapshot.create(instance, conn, kind="pre-repair", note="自动快照")
        for change in plan.get("changes", []):
            if not change.get("possible"):
                continue
            outcome = env_runtime.apply_repair(
                instance,
                str(change["id"]),
                stack_id=stack,
                index_url=index_url,
            )
            applied.append({"id": change["id"], **outcome})
    else:
        for change in plan.get("changes", []):
            if change["id"] == "reinstall_torch_stack":
                preview = env_runtime.stack_plan(
                    instance, stack_id=stack, index_url=index_url
                )
                change["preview"] = preview
    _emit(Result.success(plan=plan, applied=applied, applied_now=apply), as_json)


@env_app.command("stacks")
def env_stacks(as_json: bool = Json(False, "--json")) -> None:
    from zui.core import torch_stacks

    _emit(
        Result.success(
            stacks=torch_stacks.stacks(),
            recommended=torch_stacks.recommend(),
            known_versions=torch_stacks.known_versions(),
        ),
        as_json,
    )


@env_app.command("create")
def env_create(
    name: str = typer.Argument(..., help="新环境目录名"),
    stack: str = typer.Option("nvidia-cu130", "--stack", help="torch 栈 id，见 `zui env stacks`"),
    parent: Path = typer.Option(Path.cwd(), "--parent", help="在哪个目录下创建"),
    pypi_mirror: str | None = typer.Option(None, "--pypi-mirror"),
    apply: bool = typer.Option(False, "--apply", help="真正创建，默认仅预览"),
    as_json: bool = Json(False, "--json"),
) -> None:
    from zui.core import torch_stacks

    if torch_stacks.get(stack) is None:
        _emit(Result.failure("USAGE_ERROR", f"未知栈 id: {stack}"), as_json)
    outcome = env_runtime.create_env(
        name,
        stack,
        parent=parent,
        index_url=resolve_pypi_index(pypi_mirror),
        apply=apply,
    )
    _emit(
        Result.success(**outcome)
        if outcome.get("ok")
        else Result.failure("RUNTIME_ERROR", str(outcome.get("error")), hint=outcome.get("stderr")),
        as_json,
    )


@env_app.command("args")
def env_args(
    ref: str | None = typer.Argument(None),
    capture: bool = typer.Option(False, "--capture", help="重新从 ComfyUI 提取参数表"),
    as_json: bool = Json(False, "--json"),
) -> None:
    conn = _conn()
    instance = _resolve(conn, ref)
    spec = None
    if not capture:
        spec = comfy_args.load(instance, conn)
    if spec is None:
        try:
            spec = comfy_args.capture(instance, conn)
        except ValueError as exc:
            _emit(Result.failure("ENV_BROKEN", str(exc)), as_json)
    _emit(Result.success(**comfy_args.summary(spec or {"groups": []})), as_json)


# ------------------------------------------------------------------- launch/process


@app.command("launch")
def launch(
    ref: str | None = typer.Argument(None),
    profile_name: str = typer.Option("default", "--profile"),
    port: int = typer.Option(8188, "--port"),
    extra: list[str] = typer.Option([], "--extra", help="追加到命令行的参数"),
    dry_run: bool = typer.Option(False, "--dry-run", help="只打印将要执行的命令与环境差异"),
    pypi_mirror: str | None = typer.Option(None, "--pypi-mirror"),
    hf_mirror: str | None = typer.Option(None, "--hf-mirror"),
    as_json: bool = Json(False, "--json"),
) -> None:
    conn = _conn()
    instance = _resolve(conn, ref)
    if instance.python is None:
        _emit(Result.failure("ENV_BROKEN", "实例没有可用的 Python 解释器"), as_json)

    port_note: str | None = None
    if not proc.port_available(port):
        allocated = proc.next_free_port(port + 1)
        if allocated is None:
            _emit(
                Result.failure("RUNTIME_ERROR", f"端口 {port} 及其后 100 个端口均被占用"),
                as_json,
            )
        assert allocated is not None
        port_note = f"端口 {port} 已被占用，改用 {allocated}"
        port = allocated

    settings = profile.get(conn, instance.id, profile_name)
    args = list(settings.get("args") or []) + list(extra)
    cwd = instance.comfy_dir or instance.root
    argv = [str(instance.python), "-X", "utf8", "main.py", "--port", str(port), *args]
    env = build_child_env(
        pypi_mirror=pypi_mirror,
        hf_mirror=hf_mirror,
        search_roots=[instance.root, instance.root.parent],
    )

    if dry_run:
        _emit(
            Result.success(
                cwd=str(cwd),
                argv=argv,
                env_diff=diff_env(env),
                port_note=port_note,
            ),
            as_json,
        )

    supervisor = proc.Supervisor()
    try:
        state = supervisor.start(
            conn,
            instance,
            argv,
            port=port,
            pypi_mirror=pypi_mirror,
            hf_mirror=hf_mirror,
        )
    except proc.Busy as exc:
        _emit(Result.failure("RUNTIME_ERROR", str(exc)), as_json)
    except Exception as exc:  # noqa: BLE001
        _emit(Result.failure("RUNTIME_ERROR", f"launch failed: {exc}"), as_json)

    deadline = time.monotonic() + proc.READY_TIMEOUT
    ready = False
    while time.monotonic() < deadline:
        if _http_ready(port):
            ready = True
            break
        if not proc.is_alive(state.pid):
            break
        time.sleep(1.0)
    _emit(
        Result.success(
            **state.to_dict(),
            ready=ready,
            url=f"http://127.0.0.1:{port}" if ready else None,
            port_note=port_note,
        ),
        as_json,
    )


@app.command("stop")
def stop(ref: str | None = typer.Argument(None), as_json: bool = Json(False, "--json")) -> None:
    conn = _conn()
    instance = _resolve(conn, ref)
    supervisor = proc.Supervisor()
    killed = supervisor.stop(conn, instance)
    _emit(Result.success(instance=instance.id, stopped=killed), as_json)


@app.command("status")
def status(ref: str | None = typer.Argument(None), as_json: bool = Json(False, "--json")) -> None:
    conn = _conn()
    instance = _resolve(conn, ref)
    info = proc.Supervisor().status(conn, instance)
    _emit(Result.success(**info), as_json)


@app.command("logs")
def logs(
    ref: str | None = typer.Argument(None),
    lines: int = typer.Option(80, "-n"),
    follow: bool = typer.Option(True, "--follow/--no-follow"),
    from_comfy: bool = typer.Option(False, "--comfy", help="读取 ComfyUI 自己的日志"),
) -> None:
    conn = _conn()
    instance = _resolve(conn, ref)
    path = run_report.locate(instance) if from_comfy else _stdout_log(instance)
    if path is None:
        typer.echo("没有可用的日志文件")
        raise typer.Exit(1)
    _tail(path, lines=lines, follow=follow)


def _stdout_log(instance: Any) -> Path | None:
    candidate = repo.log_dir() / f"{instance.id}.log"
    return candidate if candidate.exists() else None


def _tail(path: Path, *, lines: int, follow: bool) -> None:
    existing = path.read_text(errors="replace").splitlines()[-lines:]
    for line in existing:
        typer.echo(line)
    if not follow:
        return
    try:
        with open(path, "rb") as handle:
            handle.seek(0, 2)
            while True:
                chunk = handle.readline()
                if chunk:
                    typer.echo(chunk.decode("utf-8", "replace").rstrip())
                else:
                    time.sleep(0.3)
    except KeyboardInterrupt:
        return


def _http_ready(port: int) -> bool:
    import httpx

    try:
        httpx.get(f"http://127.0.0.1:{port}/system_stats", timeout=1.5).raise_for_status()
    except Exception:
        return False
    return True


# ------------------------------------------------------------------------- model


@model_app.command("scan")
def model_scan(
    ref: str | None = typer.Argument(None),
    with_hash: bool = typer.Option(False, "--hash", help="同时计算 blake3（较慢）"),
    as_json: bool = Json(False, "--json"),
) -> None:
    conn = _conn()
    instance = _resolve(conn, ref)
    summary = asset.scan(instance, conn, with_hash=with_hash)
    _emit(Result.success(**summary), as_json)


@model_app.command("report")
def model_report(
    ref: str | None = typer.Argument(None), as_json: bool = Json(False, "--json")
) -> None:
    conn = _conn()
    instance = _resolve(conn, ref)
    report = asset.report(conn, instance)
    _emit(Result.success(**report), as_json)


@model_app.command("workflows")
def model_workflows(
    ref: str | None = typer.Argument(None), as_json: bool = Json(False, "--json")
) -> None:
    conn = _conn()
    instance = _resolve(conn, ref)
    found = [str(path) for path in budget.workflows_for(instance)]
    _emit(Result.success(workflows=found), as_json)


@model_app.command("budget")
def model_budget(
    ref: str | None = typer.Argument(None),
    workflow: Path = typer.Argument(..., help="工作流 JSON 路径"),
    as_json: bool = Json(False, "--json"),
) -> None:
    conn = _conn()
    instance = _resolve(conn, ref)
    if not workflow.exists():
        _emit(Result.failure("USAGE_ERROR", f"找不到工作流: {workflow}"), as_json)
    result = budget.estimate(instance, conn, workflow)
    _emit(Result.success(**result), as_json)


@model_app.command("unused")
def model_unused(
    ref: str | None = typer.Argument(None), as_json: bool = Json(False, "--json")
) -> None:
    conn = _conn()
    instance = _resolve(conn, ref)
    _emit(Result.success(**asset.usage(conn, instance)), as_json)


# --------------------------------------------------------------------------- run


@run_app.command("report")
def run_report_cmd(
    ref: str | None = typer.Argument(None),
    max_runs: int = typer.Option(3, "--max-runs"),
    as_json: bool = Json(False, "--json"),
) -> None:
    conn = _conn()
    instance = _resolve(conn, ref)
    data = run_report.last_run(instance, max_runs=max_runs)
    if not data.get("found"):
        _emit(Result.failure("TARGET_NOT_RUNNING", "没有找到 ComfyUI 日志"), as_json)
    data["suggestions"] = run_report.suggestions(data)
    _emit(Result.success(**data), as_json)


# ---------------------------------------------------------------------- optimize


@optimize_app.command("analyze")
def optimize_analyze(
    ref: str | None = typer.Argument(None), as_json: bool = Json(False, "--json")
) -> None:
    conn = _conn()
    instance = _resolve(conn, ref)
    _emit(Result.success(**accelerate.analyze(instance, conn)), as_json)


@optimize_app.command("apply")
def optimize_apply(
    ref: str | None = typer.Argument(None),
    rule: str = typer.Option(..., "--rule", help="来自 optimize analyze 的规则 id"),
    profile_name: str = typer.Option("default", "--profile"),
    apply: bool = typer.Option(False, "--apply", help="真正安装，默认仅预览"),
    pypi_mirror: str | None = typer.Option(None, "--pypi-mirror"),
    as_json: bool = Json(False, "--json"),
) -> None:
    conn = _conn()
    instance = _resolve(conn, ref)
    index_url = (
        resolve_pypi_index(pypi_mirror)
        or os.environ.get("UV_INDEX_URL")
        or os.environ.get("PIP_INDEX_URL")
    )
    if not apply:
        preview = accelerate.preview_rule(
            instance, conn, rule, profile_name=profile_name, index_url=index_url
        )
        _emit(
            Result.success(**preview)
            if preview.get("ok")
            else Result.failure("TARGET_NOT_RUNNING", str(preview.get("error"))),
            as_json,
        )
    snapshot.create(instance, conn, kind="pre-optimize", note=rule)
    outcome = accelerate.apply_rule(
        instance, conn, rule, profile_name=profile_name, index_url=index_url
    )
    _emit(
        Result.success(**outcome)
        if outcome.get("ok")
        else Result.failure("RUNTIME_ERROR", str(outcome.get("error")), hint=outcome.get("stderr")),
        as_json,
    )


# ---------------------------------------------------------------------- snapshot


@snapshots_app.command("create")
def snapshot_create(
    ref: str | None = typer.Argument(None),
    note: str = typer.Option("", "--note"),
    as_json: bool = Json(False, "--json"),
) -> None:
    conn = _conn()
    instance = _resolve(conn, ref)
    _emit(Result.success(**snapshot.create(instance, conn, note=note)), as_json)


@snapshots_app.command("list")
def snapshot_list(
    ref: str | None = typer.Argument(None), as_json: bool = Json(False, "--json")
) -> None:
    conn = _conn()
    instance = _resolve(conn, ref)
    _emit(Result.success(snapshots=snapshot.list_snapshots(conn, instance.id)), as_json)


@snapshots_app.command("diff")
def snapshot_diff(
    left: str = typer.Argument(..., help="较早的快照 id"),
    right: str = typer.Argument(..., help="较新的快照 id"),
    as_json: bool = Json(False, "--json"),
) -> None:
    conn = _conn()
    try:
        changes = snapshot.diff(conn, left, right)
    except KeyError as exc:
        _emit(Result.failure("TARGET_NOT_RUNNING", str(exc)), as_json)
    _emit(Result.success(**changes), as_json)


@snapshots_app.command("restore")
def snapshot_restore(
    snapshot_id: str = typer.Argument(...),
    apply: bool = typer.Option(False, "--apply"),
    as_json: bool = Json(False, "--json"),
) -> None:
    conn = _conn()
    record = snapshot.get(conn, snapshot_id)
    if record is None:
        _emit(Result.failure("TARGET_NOT_RUNNING", f"unknown snapshot: {snapshot_id}"), as_json)
    assert record is not None
    owner_id = str(record.get("instance_id") or "")
    instance = _resolve(conn, owner_id)
    outcome = snapshot.restore(instance, conn, snapshot_id, apply=apply)
    _emit(Result.success(**outcome), as_json)


# -------------------------------------------------------------------------- node


def _node_index_url(pypi_mirror: str | None) -> str | None:
    return resolve_pypi_index(pypi_mirror) or os.environ.get("UV_INDEX_URL")


@node_app.command("list")
def node_list(
    ref: str | None = typer.Argument(None), as_json: bool = Json(False, "--json")
) -> None:
    conn = _conn()
    instance = _resolve(conn, ref)
    _emit(Result.success(**nodes.list_nodes(instance)), as_json)


@node_app.command("install")
def node_install(
    source: str = typer.Argument(..., help="git 仓库地址或本地路径"),
    ref: str | None = typer.Option(None, "--ref", "-r", help="实例 id"),
    apply: bool = typer.Option(False, "--apply", help="真正安装，默认仅预览"),
    pypi_mirror: str | None = typer.Option(None, "--pypi-mirror"),
    as_json: bool = Json(False, "--json"),
) -> None:
    conn = _conn()
    instance = _resolve(conn, ref)
    outcome = nodes.install(
        instance, source, apply=apply, index_url=_node_index_url(pypi_mirror)
    )
    _emit_node(outcome, as_json, apply)


@node_app.command("update")
def node_update(
    name: str = typer.Argument(..., help="节点目录名"),
    ref: str | None = typer.Option(None, "--ref", "-r", help="实例 id"),
    apply: bool = typer.Option(False, "--apply", help="真正更新，默认仅预览"),
    pypi_mirror: str | None = typer.Option(None, "--pypi-mirror"),
    as_json: bool = Json(False, "--json"),
) -> None:
    conn = _conn()
    instance = _resolve(conn, ref)
    if apply:
        snapshot.create(instance, conn, kind="pre-node-update", note=name)
    outcome = nodes.update(instance, name, apply=apply, index_url=_node_index_url(pypi_mirror))
    _emit_node(outcome, as_json, apply)


@node_app.command("rollback")
def node_rollback(
    name: str = typer.Argument(..., help="节点目录名"),
    to: str | None = typer.Option(None, "--to", help="目标 commit，默认上一个"),
    ref: str | None = typer.Option(None, "--ref", "-r", help="实例 id"),
    apply: bool = typer.Option(False, "--apply", help="真正回滚，默认仅预览"),
    pypi_mirror: str | None = typer.Option(None, "--pypi-mirror"),
    as_json: bool = Json(False, "--json"),
) -> None:
    conn = _conn()
    instance = _resolve(conn, ref)
    if apply:
        snapshot.create(instance, conn, kind="pre-node-rollback", note=name)
    outcome = nodes.rollback(
        instance, name, to=to, apply=apply, index_url=_node_index_url(pypi_mirror)
    )
    _emit_node(outcome, as_json, apply)


@node_app.command("disable")
def node_disable(
    name: str = typer.Argument(...),
    ref: str | None = typer.Option(None, "--ref", "-r", help="实例 id"),
    apply: bool = typer.Option(False, "--apply", help="真正改名，默认仅预览"),
    as_json: bool = Json(False, "--json"),
) -> None:
    conn = _conn()
    instance = _resolve(conn, ref)
    _emit_node(nodes.set_enabled(instance, name, False, apply=apply), as_json, apply)


@node_app.command("enable")
def node_enable(
    name: str = typer.Argument(...),
    ref: str | None = typer.Option(None, "--ref", "-r", help="实例 id"),
    apply: bool = typer.Option(False, "--apply", help="真正改名，默认仅预览"),
    as_json: bool = Json(False, "--json"),
) -> None:
    conn = _conn()
    instance = _resolve(conn, ref)
    _emit_node(nodes.set_enabled(instance, name, True, apply=apply), as_json, apply)


def _emit_node(outcome: dict[str, Any], as_json: bool, apply: bool) -> None:
    if outcome.get("ok"):
        _emit(Result.success(**outcome), as_json)
    _emit(
        Result.failure(
            "RUNTIME_ERROR", str(outcome.get("error")), hint=outcome.get("stderr")
        ),
        as_json,
    )


# ------------------------------------------------------------------------- rules


@rules_app.command("list")
def rules_list(as_json: bool = Json(False, "--json")) -> None:
    _emit(Result.success(**rules_sync.local_state()), as_json)


@rules_app.command("update")
def rules_update(
    base_url: str = typer.Option(..., "--url", help="规则目录的 HTTP(S) 基地址"),
    name: list[str] = typer.Option([], "--name", help="只更新指定文件，默认全部"),
    apply: bool = typer.Option(False, "--apply", help="真正写入，默认仅预览"),
    as_json: bool = Json(False, "--json"),
) -> None:
    outcome = rules_sync.update(base_url, list(name) or None, apply=apply)
    _emit(Result.success(**outcome), as_json)


# -------------------------------------------------------------------------- diag


@diag_app.command("export")
def diag_export(
    ref: str | None = typer.Argument(None),
    out: Path | None = typer.Option(None, "--out"),
    as_json: bool = Json(False, "--json"),
) -> None:
    conn = _conn()
    instance = _resolve(conn, ref)
    bundle = diag_package.export(instance, conn, out)
    size_mb = round(bundle.stat().st_size / 2**20, 2)
    _emit(Result.success(bundle=str(bundle), size_mb=size_mb), as_json)


# ------------------------------------------------------------------- args/profiles


@args_app.command("capture")
def args_capture(
    ref: str | None = typer.Argument(None),
    static: bool = typer.Option(
        False, "--static", help="强制静态解析 cli_args.py（venv 损坏时也能用）"
    ),
    as_json: bool = Json(False, "--json"),
) -> None:
    conn = _conn()
    instance = _resolve(conn, ref)
    try:
        spec = comfy_args.capture(instance, conn, force_static=static)
    except ValueError as exc:
        _emit(Result.failure("ENV_BROKEN", str(exc)), as_json)
    data = comfy_args.summary(spec)
    _emit(Result.success(**data), as_json)


@args_app.command("show")
def args_show(
    ref: str | None = typer.Argument(None),
    form: bool = typer.Option(False, "--form", help="输出 UI 表单 schema（按用途分组）"),
    as_json: bool = Json(False, "--json"),
) -> None:
    conn = _conn()
    instance = _resolve(conn, ref)
    spec = comfy_args.load(instance, conn)
    if spec is None:
        try:
            spec = comfy_args.capture(instance, conn)
        except ValueError as exc:
            _emit(Result.failure("ENV_BROKEN", str(exc)), as_json)
    assert spec is not None
    payload = comfy_args.form(spec) if form else comfy_args.summary(spec)
    if not form and not as_json:
        for group in payload.get("groups", []):
            typer.echo(f"\n## {group['title']}{' (互斥)' if group.get('exclusive') else ''}")
            for item in group.get("items", []):
                flags = ", ".join(item.get("flags") or [])
                label = item.get("label") or ""
                typer.echo(f"  {flags:<40} {label}")
        raise typer.Exit(0)
    _emit(Result.success(**payload), as_json)


@args_app.command("preview")
def args_preview(
    ref: str | None = typer.Argument(None),
    name: str = typer.Option("default", "--profile"),
    extra: list[str] = typer.Option([], "--extra"),
    as_json: bool = Json(False, "--json"),
) -> None:
    """打印某个档位最终会执行的完整命令行。"""
    conn = _conn()
    instance = _resolve(conn, ref)
    settings = profile.get(conn, instance.id, name)
    args = list(settings.get("args") or []) + list(extra)
    _emit(
        Result.success(
            profile=name,
            args=args,
            command=comfy_args.command_line(instance, args),
            env_diff=diff_env(build_child_env(search_roots=[instance.root])),
        ),
        as_json,
    )


@args_app.command("set")
def args_set(
    ref: str | None = typer.Argument(None),
    name: str = typer.Option("default", "--profile"),
    arg: list[str] = typer.Option([], "--arg", help="要写入档位的参数，可重复"),
    as_json: bool = Json(False, "--json"),
) -> None:
    conn = _conn()
    instance = _resolve(conn, ref)
    saved = profile.save(conn, instance.id, name, list(arg))
    _emit(
        Result.success(
            **saved, command=comfy_args.command_line(instance, list(saved.get("args") or []))
        ),
        as_json,
    )


@args_app.command("list")
def args_list(
    ref: str | None = typer.Argument(None), as_json: bool = Json(False, "--json")
) -> None:
    conn = _conn()
    instance = _resolve(conn, ref)
    profiles = profile.list_profiles(conn, instance.id)
    _emit(Result.success(profiles=profiles), as_json)


# ------------------------------------------------------------------------- misc


@app.command("version")
def version() -> None:
    from zui import __version__

    typer.echo(__version__)


@app.command("info")
def info(as_json: bool = Json(False, "--json")) -> None:
    import platform as _platform
    import sys

    from zui.gpu import resolve_provider
    from zui.platform import get_platform

    adapter = get_platform()
    provider = resolve_provider()
    _emit(
        Result.success(
            zui_version=_version_string(),
            python_version=sys.version.split()[0],
            platform_name=adapter.name,
            machine=str(_platform.machine()),
            app_data_dir=str(adapter.app_data_dir()),
            default_instance_root=str(adapter.default_instance_root()),
            gpu_vendor=provider.vendor(),
            gpu_name=provider.name(),
            gpu_caps=sorted(provider.caps()),
            database=str(repo.db_path()),
        ),
        as_json,
    )


def _version_string() -> str:
    from zui import __version__

    return __version__


@app.command("serve")
def serve(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(0, "--port", help="0 表示随机端口"),
) -> None:
    """启动本地 Web 工作台（可选，命令行能力不依赖它）。"""
    from zui.server import run_server

    run_server(host=host, port=port)


__all__ = ["app"]
