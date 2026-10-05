"""Web workbench connectivity: REST endpoints and the two WebSocket streams.

Runs against a throw-away data dir (`ZUI_DATA_DIR`) and a synthetic ComfyUI
install, so it is hermetic on all three CI platforms.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from zui.core import comfy_args, profile  # noqa: E402
from zui.core import instance as instance_mod
from zui.core.instance import Instance  # noqa: E402
from zui.server import create_app  # noqa: E402
from zui.store import repo  # noqa: E402

Fixture = tuple[sqlite3.Connection, Instance]

_CLI_ARGS_SOURCE = """
import argparse

parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, default=8188, help="Set the listen port.")
parser.add_argument("--listen", type=str, default="127.0.0.1", help="IP to listen on.")
parser.add_argument("--lowvram", action="store_true", help="Split the unet.")
parser.add_argument("--highvram", action="store_true", help="Keep models in VRAM.")
parser.add_argument("--use-sage-attention", action="store_true", help="Use SageAttention.")
group = parser.add_mutually_exclusive_group()
group.add_argument("--fp16-unet", action="store_true", help="Run unet in fp16.")
group.add_argument("--bf16-unet", action="store_true", help="Run unet in bf16.")
parser.add_argument("--preview-method", type=str, choices=["none", "auto", "latent2rgb"],
                    default="none", help="Preview method.")
parser.add_argument("--cache-lru", type=int, default=0, help="LRU cache size.")
"""


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Fixture:
    comfy = tmp_path / "ComfyUI"
    (comfy / "comfy").mkdir(parents=True)
    (comfy / "main.py").write_text("# synthetic", encoding="utf-8")
    (comfy / "nodes.py").write_text("# synthetic", encoding="utf-8")
    (comfy / "comfy" / "cli_args.py").write_text(_CLI_ARGS_SOURCE, encoding="utf-8")
    (comfy / "comfyui_version.py").write_text('__version__ = "0.0.0-test"\n', encoding="utf-8")
    (comfy / "user").mkdir(parents=True)
    (comfy / "user" / "comfyui.log").write_text("[2026-01-01 00:00:00.000] got prompt\n", "utf-8")

    data = tmp_path / "zui-data"
    monkeypatch.setenv("ZUI_DATA_DIR", str(data))
    conn = repo.connect()
    inst = instance_mod.adopt(comfy, name="test-instance", conn=conn)
    return conn, inst


def test_health(env: Fixture) -> None:
    client = TestClient(create_app())
    assert client.get("/api/health").json()["ok"] is True


def test_instances_are_listed(env: Fixture) -> None:
    client = TestClient(create_app())
    payload = client.get("/api/instances").json()
    assert any(item["id"] == "test-instance" for item in payload["instances"])


def test_gpu_endpoint_always_answers(env: Fixture) -> None:
    payload = TestClient(create_app()).get("/api/gpu").json()
    assert "caps" in payload and "vendor" in payload


def test_dry_run_returns_full_command(env: Fixture) -> None:
    conn, inst = env
    profile.save(conn, inst.id, "default", ["--lowvram"])
    client = TestClient(create_app())
    payload = client.post("/api/instances/test-instance/launch/dry-run", json={"port": 8188}).json()
    assert payload["ok"] is True
    assert "main.py" in payload["argv"]
    assert "--lowvram" in payload["argv"]
    assert payload["argv"][payload["argv"].index("--port") + 1] == "8188"
    assert payload["env_diff"]["PYTHONUTF8"] == "1"


def test_args_form_is_built_from_cli_args(env: Fixture) -> None:
    client = TestClient(create_app())
    payload = client.get("/api/instances/test-instance/args").json()
    assert payload["ok"] is True
    assert payload["source"] == "static"
    flags = {field["flag"] for section in payload["sections"] for field in section["fields"]}
    assert {"--lowvram", "--use-sage-attention", "--fp16-unet", "--preview-method"} <= flags
    attention = next(
        section for section in payload["sections"] if section["title"] == "注意力与加速"
    )
    assert any(field["flag"] == "--use-sage-attention" for field in attention["fields"])
    preview = next(
        field
        for section in payload["sections"]
        for field in section["fields"]
        if field["flag"] == "--preview-method"
    )
    assert preview["kind"] == "choice"
    assert preview["choices"] == ["none", "auto", "latent2rgb"]


def test_profile_round_trip_via_rest(env: Fixture) -> None:
    client = TestClient(create_app())
    saved = client.put(
        "/api/instances/test-instance/profiles/fast",
        json={"args": ["--use-sage-attention", "--preview-method", "none"]},
    ).json()
    assert saved["ok"] is True
    assert saved["args"] == ["--use-sage-attention", "--preview-method", "none"]
    assert "main.py" in saved["command"]
    listed = client.get("/api/instances/test-instance/profiles").json()
    assert "fast" in {item["name"] for item in listed["profiles"]}


def test_logs_socket_streams_backlog(env: Fixture) -> None:
    client = TestClient(create_app())
    with client.websocket_connect("/ws/logs/test-instance") as socket:
        first = socket.receive_text()
    assert "got prompt" in first


def test_metrics_socket_streams_json(env: Fixture) -> None:
    client = TestClient(create_app())
    with client.websocket_connect("/ws/metrics") as socket:
        frame = json.loads(socket.receive_text())
    assert "values" in frame and "caps" in frame


def test_static_capture_records_exclusive_groups(env: Fixture) -> None:
    conn, inst = env
    spec = comfy_args.capture(inst, conn)
    exclusive = [group for group in spec["groups"] if group["exclusive"]]
    assert exclusive, "mutually exclusive group was not detected"
    flags = {flag for group in exclusive for item in group["items"] for flag in item["flags"]}
    assert {"--fp16-unet", "--bf16-unet"} <= flags
