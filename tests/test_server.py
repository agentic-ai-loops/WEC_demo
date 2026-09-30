"""The single intelliw server: /mcp, /graphql and /health (docs/notes/2026-09-29.md)."""

import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
from mcp import Client
from starlette.testclient import TestClient

from intelliw.config import Settings
from intelliw.server import create_app
from intelliw.workspace import Workspace

REPO = Path(__file__).parent.parent


@pytest.fixture
def client(data_ws: Workspace):
    app = create_app(Settings(workspace=data_ws.root))
    with TestClient(app) as c:  # runs the lifespan (MCP's session manager)
        yield c


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok" and body["database"] == "ok"
    assert body["service"] == "intelliw" and body["workspace"] == "ws"
    assert body["routes"] == {"mcp": "/mcp", "graphql": "/graphql", "health": "/health"}


def test_health_without_a_database_is_degraded():
    with TestClient(create_app(Settings(workspace=None))) as c:
        resp = c.get("/health")
    assert resp.status_code == 503 and resp.json()["status"] == "degraded"


def test_graphql_on_the_same_app(client):
    resp = client.post("/graphql", json={"query": "{ business { name } }"})
    assert resp.status_code == 200
    assert resp.json() == {"data": {"business": {"name": "Whitby Eye Care"}}}


def test_mcp_on_the_same_app(client):
    """A JSON-RPC initialize on /mcp answers (the MCP session manager runs)."""
    resp = client.post(
        "/mcp",
        headers={
            "accept": "application/json, text/event-stream",
            "content-type": "application/json",
        },
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
        },
    )
    assert resp.status_code == 200
    assert '"serverInfo"' in resp.text and "intelliw" in resp.text


# ---- svr start / status / stop, as processes ------------------------------------------


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _svr(*args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", "from intelliw.cli.svr import app; app()", *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


def test_svr_start_status_stop(data_ws: Workspace, tmp_path: Path):
    port = _free_port()
    run_dir = tmp_path / "run"
    env = {**os.environ, "WORKSPACE": str(data_ws.root), "NO_COLOR": "1", "COLUMNS": "200"}
    log = (tmp_path / "svr.log").open("w")
    server = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "from intelliw.cli.svr import app; app()",
            "start",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--run-dir",
            str(run_dir),
        ],
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    # reap the child as soon as it exits (as a shell would), so `svr stop` doesn't see a
    # zombie as still running
    reaper = threading.Thread(target=server.wait, daemon=True)
    reaper.start()
    try:
        url = f"http://127.0.0.1:{port}"
        for _ in range(100):
            try:
                if httpx.get(f"{url}/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        assert (run_dir / "svr.pid").read_text().strip() == str(server.pid)
        assert json.loads((run_dir / "svr.json").read_text()) == {"host": "127.0.0.1", "port": port}

        # a second start is refused
        again = _svr(
            "start", "--host", "127.0.0.1", "--port", str(port), "--run-dir", str(run_dir), env=env
        )
        assert again.returncode == 1 and "already running" in again.stderr

        status = _svr("status", "--run-dir", str(run_dir), env=env)
        assert status.returncode == 0, status.stdout + status.stderr
        assert "graphql_query" in status.stdout and "database ok" in status.stdout

        stop = _svr("stop", "--run-dir", str(run_dir), env=env)
        assert stop.returncode == 0, stop.stderr
        reaper.join(timeout=15)
        assert server.returncode is not None
        assert not (run_dir / "svr.pid").exists() and not (run_dir / "svr.json").exists()

        after = _svr("status", "--run-dir", str(run_dir), env=env)
        assert after.returncode == 1 and "not running" in after.stderr
    finally:
        if server.returncode is None:
            server.kill()
        log.close()


def test_svr_start_refuses_a_busy_port(tmp_path: Path):
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        env = {**os.environ, "NO_COLOR": "1", "COLUMNS": "200"}
        result = _svr(
            "start", "--host", "127.0.0.1", "--port", str(port), "--run-dir", str(tmp_path), env=env
        )
    assert result.returncode == 1 and "in use by another program" in result.stderr
    assert not (tmp_path / "svr.pid").exists()


async def test_mcp_tools_run_graphql_in_process(data_ws: Workspace):
    """The MCP tools query the same database as /graphql, without an HTTP hop."""
    from intelliw.businessdata.database import create_db_engine, session_factory
    from intelliw.mcp import create_server
    from intelliw.mcp.server import schema_executor

    engine = create_db_engine(data_ws.database_file)
    try:
        server = create_server(schema_executor(session_factory(engine), data_ws.resources_dir))
        async with Client(server) as c:
            result = await c.call_tool("graphql_query", {"document": "{ business { name } }"})
        assert json.loads(result.content[0].text) == {
            "data": {"business": {"name": "Whitby Eye Care"}}
        }
    finally:
        engine.dispose()
