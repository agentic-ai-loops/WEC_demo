"""`svr`: run the intelliw server — `/mcp`, `/graphql` and `/health` on one port."""

import asyncio
import json
import os
import socket
from dataclasses import replace
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from intelliw.checks import check_graphql, check_health, check_mcp
from intelliw.config import Settings
from intelliw.pidfile import PidFile, terminate

app = typer.Typer(no_args_is_help=True, help="Run the intelliw server (defaults from .env).")
console = Console()
err = Console(stderr=True)

RunDirOpt = Annotated[
    Path | None,
    typer.Option("--run-dir", help="Where svr.pid is kept (default: INTELLIW_RUN_DIR or ./run)."),
]


def _pidfile(run_dir: Path) -> PidFile:
    return PidFile(run_dir, "svr")


def _state_file(run_dir: Path) -> Path:
    """Where `start` records its host and port, for `status`."""
    return run_dir / "svr.json"


def _settings(run_dir: Path | None, **overrides: Any) -> Settings:
    s = Settings.from_env()
    changes = {k: v for k, v in overrides.items() if v is not None}
    if run_dir is not None:
        changes["run_dir"] = run_dir
    return replace(s, **changes)


def _recorded(s: Settings) -> Settings:
    """The settings the running server was started with (host and port), if recorded."""
    try:
        state = json.loads(_state_file(s.run_dir).read_text())
        return replace(s, host=state["host"], port=int(state["port"]))
    except (FileNotFoundError, ValueError, KeyError):
        return s


def _port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


@app.command()
def start(
    host: Annotated[
        str | None, typer.Option(help="Bind address (default: SVR_HOST or 0.0.0.0).")
    ] = None,
    port: Annotated[int | None, typer.Option(help="Port (default: SVR_PORT or 8000).")] = None,
    run_dir: RunDirOpt = None,
) -> None:
    """Start the server in the foreground: /mcp, /graphql and /health."""
    s = _settings(run_dir, host=host, port=port)
    pidfile = _pidfile(s.run_dir)
    if pid := pidfile.read():
        err.print(f"[red]✘ The server is already running (pid {pid}).[/] Stop it with `svr stop`.")
        raise typer.Exit(1)
    ok, _ = check_health(s)
    if ok:
        err.print(f"[red]✘ Another intelliw server already answers at {escape(s.base_url)}.[/]")
        raise typer.Exit(1)
    if not _port_free(s.host, s.port):
        err.print(
            f"[red]✘ Port {s.port} on {s.host} is in use by another program.[/] "
            "Choose another with --port or SVR_PORT."
        )
        raise typer.Exit(1)

    from intelliw.server import ROUTES, uvicorn_server

    server = uvicorn_server(s)
    pidfile.write()
    state = _state_file(s.run_dir)
    state.write_text(json.dumps({"host": s.host, "port": s.port}) + "\n")
    routes = ", ".join(ROUTES.values())
    console.print(f"[cyan]Starting intelliw at[/] {s.base_url} [dim]({routes})[/]")
    try:
        server.run()
    finally:
        if pidfile.read() == os.getpid():  # only this process's files
            pidfile.remove_if_mine()
            state.unlink(missing_ok=True)


@app.command()
def stop(run_dir: RunDirOpt = None) -> None:
    """Shut down the server started by `svr start`."""
    s = _settings(run_dir)
    pidfile = _pidfile(s.run_dir)
    pid = pidfile.read()
    if pid is None:
        err.print(f"[yellow]The server is not running (no live pid in {pidfile.path}).[/]")
        raise typer.Exit(1)
    console.print(f"[cyan]Stopping the server (pid {pid})...[/]")
    if not terminate(pid):
        err.print(f"[red]✘ pid {pid} did not exit.[/]")
        raise typer.Exit(1)
    pidfile.read()  # clears the file if the process was killed before cleaning up
    _state_file(s.run_dir).unlink(missing_ok=True)
    console.print("[green]✔ Server stopped.[/]")


@app.command()
def status(run_dir: RunDirOpt = None) -> None:
    """Report whether the server runs, and the health of /mcp, /graphql and /health."""
    s = _recorded(_settings(run_dir))
    pidfile = _pidfile(s.run_dir)
    pid = pidfile.read()
    if pid is None:
        err.print(f"[yellow]The server is not running (no live pid in {pidfile.path}).[/]")
        raise typer.Exit(1)
    health_ok, health = check_health(s)
    graphql_ok, graphql_detail = check_graphql(s)
    mcp_ok, mcp_detail = asyncio.run(check_mcp(s))

    table = Table(title=f"intelliw server {s.base_url}")
    table.add_column("check", style="bold")
    table.add_column("status")
    table.add_column("detail", overflow="fold")

    def row(name: str, ok: bool, detail: str) -> None:
        table.add_row(name, "[green]up[/]" if ok else "[red]down[/]", escape(detail))

    row("process", True, f"pid {pid}")
    if isinstance(health, dict):
        detail = " · ".join(
            f"{k} {health.get(k)}" for k in ("status", "database", "workspace", "version")
        )
    else:
        detail = health
    row("/health", health_ok, detail)
    row("/graphql", graphql_ok, graphql_detail)
    row("/mcp", mcp_ok, mcp_detail)
    console.print(table)
    raise typer.Exit(0 if pid and health_ok and graphql_ok and mcp_ok else 1)
