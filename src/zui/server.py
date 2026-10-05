"""Local web workbench: REST + WebSocket over the same CLI contracts."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

from zui.core import (
    accelerate,
    asset,
    comfy_args,
    doctor,
    env_runtime,
    proc,
    profile,
    run_report,
    snapshot,
)
from zui.core import (
    instance as instance_mod,
)
from zui.core.instance import Instance
from zui.core.launch_env import build_child_env, diff_env
from zui.gpu import resolve_provider
from zui.store import repo

_UI_DIR = Path(__file__).resolve().parents[2] / "ui"


def _conn() -> Any:
    return repo.connect()


def create_app() -> FastAPI:
    api = FastAPI(title="zui", version="0.1.0")

    @api.get("/api/instances")
    def list_instances() -> JSONResponse:
        conn = _conn()
        return JSONResponse({"instances": [i.to_dict() for i in instance_mod.list_instances(conn)]})

    @api.get("/api/instances/{ref}/status")
    def status(ref: str) -> JSONResponse:
        conn = _conn()
        instance = _instance(conn, ref)
        info = proc.Supervisor().status(conn, instance)
        return JSONResponse(info)

    @api.get("/api/health")
    def health() -> JSONResponse:
        return JSONResponse({"ok": True, "service": "zui"})

    @api.post("/api/instances/{ref}/launch")
    async def launch(ref: str, payload: dict[str, Any]) -> JSONResponse:
        conn = _conn()
        instance = _instance(conn, ref)
        try:
            return JSONResponse(_launch_payload(instance, conn, payload))
        except Exception as exc:  # noqa: BLE001 - never leak a 500 to the UI
            return JSONResponse(
                {"ok": False, "error": "LAUNCH_FAILED", "detail": str(exc)}, status_code=200
            )

    @api.post("/api/instances/{ref}/launch/dry-run")
    def launch_dry_run(ref: str, payload: dict[str, Any]) -> JSONResponse:
        conn = _conn()
        instance = _instance(conn, ref)
        return JSONResponse(_dry_run_payload(instance, conn, payload))

    @api.post("/api/instances/{ref}/stop")
    def stop(ref: str) -> JSONResponse:
        conn = _conn()
        instance = _instance(conn, ref)
        stopped = proc.Supervisor().stop(conn, instance)
        return JSONResponse({"stopped": stopped})

    @api.get("/api/instances/{ref}/doctor")
    def doctor_report(ref: str, scan_models: bool = False, bench: bool = False) -> JSONResponse:
        conn = _conn()
        instance = _instance(conn, ref)
        report = doctor.report(
            instance, conn, include_disk_bench=bench, include_model_scan=scan_models
        )
        return JSONResponse(report)

    @api.get("/api/instances/{ref}/env")
    def env(ref: str, bench: bool = False) -> JSONResponse:
        conn = _conn()
        instance = _instance(conn, ref)
        return JSONResponse(env_runtime.inspect(instance, conn=conn, bench=bench))

    @api.get("/api/instances/{ref}/run-report")
    def report(ref: str) -> JSONResponse:
        conn = _conn()
        instance = _instance(conn, ref)
        data = run_report.last_run(instance)
        data["suggestions"] = run_report.suggestions(data)
        return JSONResponse(data)

    @api.get("/api/instances/{ref}/assets")
    def assets(ref: str) -> JSONResponse:
        conn = _conn()
        instance = _instance(conn, ref)
        return JSONResponse(asset.report(conn, instance))

    @api.get("/api/instances/{ref}/optimize")
    def optimize(ref: str) -> JSONResponse:
        conn = _conn()
        instance = _instance(conn, ref)
        return JSONResponse(accelerate.analyze(instance, conn))

    @api.post("/api/instances/{ref}/optimize/apply")
    def optimize_apply(ref: str, payload: dict[str, Any]) -> JSONResponse:
        conn = _conn()
        instance = _instance(conn, ref)
        rule = str(payload.get("rule") or "")
        if not rule:
            return JSONResponse({"ok": False, "error": "missing rule"}, status_code=400)
        snapshot.create(instance, conn, kind="pre-optimize", note=rule)
        return JSONResponse(accelerate.apply_rule(instance, conn, rule))

    @api.post("/api/instances/{ref}/repair")
    def repair(ref: str, payload: dict[str, Any] | None = None) -> JSONResponse:
        conn = _conn()
        instance = _instance(conn, ref)
        plan = env_runtime.plan_repair(instance)
        applied = []
        if bool((payload or {}).get("apply")):
            snapshot.create(instance, conn, kind="pre-repair", note="web")
            for change in plan.get("changes", []):
                if change.get("possible"):
                    applied.append(
                        {
                            "id": change["id"],
                            **env_runtime.apply_repair(instance, str(change["id"])),
                        }
                    )
        return JSONResponse({"plan": plan, "applied": applied})

    @api.get("/api/gpu")
    def gpu() -> JSONResponse:
        provider = resolve_provider()
        snap = provider.snapshot()
        return JSONResponse(
            {"vendor": provider.vendor(), "caps": sorted(provider.caps()), "values": snap.values}
        )

    @api.get("/api/snapshots/{ref}")
    def snapshots(ref: str) -> JSONResponse:
        conn = _conn()
        instance = _instance(conn, ref)
        return JSONResponse({"snapshots": snapshot.list_snapshots(conn, instance.id)})

    @api.get("/api/instances/{ref}/args")
    def args_form(ref: str, refresh: bool = False, form: bool = True) -> JSONResponse:
        conn = _conn()
        instance = _instance(conn, ref)
        spec = None if refresh else comfy_args.load(instance, conn)
        if spec is None:
            try:
                spec = comfy_args.capture(instance, conn)
            except ValueError as exc:
                return JSONResponse({"ok": False, "error": "ARGS_UNAVAILABLE", "detail": str(exc)})
        payload = comfy_args.form(spec) if form else comfy_args.summary(spec)
        return JSONResponse({**payload, "ok": True})

    @api.get("/api/instances/{ref}/profiles")
    def list_profiles(ref: str) -> JSONResponse:
        conn = _conn()
        instance = _instance(conn, ref)
        return JSONResponse({"profiles": profile.list_profiles(conn, instance.id)})

    @api.put("/api/instances/{ref}/profiles/{name}")
    def save_profile(ref: str, name: str, payload: dict[str, Any]) -> JSONResponse:
        conn = _conn()
        instance = _instance(conn, ref)
        args = [str(item) for item in payload.get("args") or []]
        saved = profile.save(conn, instance.id, name, args)
        return JSONResponse(
            {"ok": True, **saved, "command": comfy_args.command_line(instance, args)}
        )

    @api.websocket("/ws/logs/{ref}")
    async def logs_socket(websocket: WebSocket, ref: str) -> None:
        await websocket.accept()
        conn = _conn()
        instance = _instance(conn, ref)
        path = repo.log_dir() / f"{instance.id}.log"
        if not path.exists():
            from zui.core.run_report import locate

            alternative = locate(instance)
            path = alternative if alternative else path
        try:
            # Send the tail immediately so a client always gets a first frame.
            if path.exists():
                size = path.stat().st_size
                with open(path, "rb") as handle:
                    handle.seek(max(0, size - 8192))
                    await websocket.send_text(handle.read().decode("utf-8", "replace"))
                position = size
            else:
                position = 0
            while True:
                if path.exists():
                    size = path.stat().st_size
                    if size < position:
                        position = 0
                    if size > position:
                        with open(path, "rb") as handle:
                            handle.seek(position)
                            data = handle.read(size - position)
                        position = size
                        await websocket.send_text(data.decode("utf-8", "replace"))
                await asyncio.sleep(0.5)
        except WebSocketDisconnect:
            return

    @api.websocket("/ws/metrics")
    async def metrics_socket(websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            while True:
                provider = resolve_provider()
                snap = provider.snapshot()
                await websocket.send_text(
                    json.dumps({"values": snap.values, "caps": sorted(provider.caps())})
                )
                await asyncio.sleep(1.0)
        except WebSocketDisconnect:
            return

    @api.get("/")
    def index() -> FileResponse:
        target = _UI_DIR / "index.html"
        if not target.exists():
            raise FileNotFoundError("ui/index.html is missing")  # pragma: no cover
        return FileResponse(target)

    @api.get("/ui/{asset_path:path}")
    def ui_assets(asset_path: str) -> FileResponse:
        target = _UI_DIR / asset_path
        if not target.exists():
            raise FileNotFoundError(asset_path)
        return FileResponse(target)

    return api


def _instance(conn: Any, ref: str) -> Instance:
    instance = instance_mod.get_instance(conn, ref) or instance_mod.active_instance(conn)
    if instance is None:
        raise LookupError(ref)
    return instance


def _argv_for(instance: Instance, conn: Any, payload: dict[str, Any]) -> tuple[list[str], int]:
    settings = profile.get(conn, instance.id, str(payload.get("profile") or "default"))
    args = list(settings.get("args") or []) + [str(item) for item in payload.get("extra") or []]
    port = int(payload.get("port") or 8188)
    return [str(instance.python), "-X", "utf8", "main.py", "--port", str(port), *args], port


def _dry_run_payload(instance: Instance, conn: Any, payload: dict[str, Any]) -> dict[str, Any]:
    argv, port = _argv_for(instance, conn, payload)
    env = build_child_env(
        pypi_mirror=payload.get("pypi_mirror"),
        hf_mirror=payload.get("hf_mirror"),
        search_roots=[instance.root],
    )
    return {
        "ok": True,
        "argv": argv,
        "cwd": str(instance.comfy_dir or instance.root),
        "port": port,
        "env_diff": diff_env(env),
    }


def _launch_payload(instance: Instance, conn: Any, payload: dict[str, Any]) -> dict[str, Any]:
    argv, port = _argv_for(instance, conn, payload)
    supervisor = proc.Supervisor()
    try:
        state = supervisor.start(conn, instance, argv, port=port)
    except proc.Busy:
        return {"ok": False, "error": "already running"}
    deadline = time.monotonic() + proc.READY_TIMEOUT
    ready = False
    while time.monotonic() < deadline:
        if proc.port_owner(port) is not None:
            ready = True
            break
        if not proc.is_alive(state.pid):
            break
        time.sleep(1.0)
    return {**state.to_dict(), "ready": ready, "url": f"http://127.0.0.1:{port}" if ready else None}


def run_server(host: str = "127.0.0.1", port: int = 0) -> None:
    import socket

    import uvicorn

    if port == 0:
        with socket.socket() as probe:
            probe.bind((host, 0))
            port = int(probe.getsockname()[1])
    app = create_app()
    print(f"zui workbench: http://{host}:{port}/")
    uvicorn.run(app, host=host, port=port, log_level="warning")


__all__ = ["create_app", "run_server"]
