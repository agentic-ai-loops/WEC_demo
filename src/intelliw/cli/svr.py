"""`svr`: manage the GraphQL and MCP servers."""

import asyncio
from dataclasses import replace
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from intelliw.checks import check_graphql, check_mcp
from intelliw.config import Settings
from intelliw.pidfile import PidFile, terminate

app = typer.Typer(no_args_is_help=True, help="Manage intelliw servers (settings from .env).")
console = Console()
err = Console(stderr=True)

CheckOpt = Annotated[bool, typer.Option("--check", help="Check a running server.")]
StopOpt = Annotated[bool, typer.Option("--stop", help="Stop a server started by svr.")]


def _report(name: str, ok: bool, detail: str) -> None:
    mark = "[green]✔ up[/]" if ok else "[red]✘ down[/]"
    console.print(f"{mark} [bold]{name}[/] {detail}")


def _pidfiles(s: Settings) -> dict[str, PidFile]:
    return {name: PidFile(s.run_dir, name) for name in ("graphql", "mcp")}


def _ensure_not_running(s: Settings, names: list[str]) -> None:
    """Bail out if any of the named servers is already running."""
    pidfiles = _pidfiles(s)
    checks = {
        "graphql": lambda: check_graphql(s),
        "mcp": lambda: asyncio.run(check_mcp(s)),
    }
    for name in names:
        if pid := pidfiles[name].read():
            err.print(f"[red]✘ {name} is already running (pid {pid}).[/] Stop it with --stop.")
            raise typer.Exit(1)
        ok, detail = checks[name]()
        if ok:
            err.print(f"[red]✘ {name} is already being served by another process:[/] {detail}")
            raise typer.Exit(1)


def _stop(s: Settings, name: str) -> None:
    pidfiles = _pidfiles(s)
    pid = pidfiles[name].read()
    if pid is None:
        err.print(f"[yellow]{name} is not running (no live pid in {pidfiles[name].path}).[/]")
        raise typer.Exit(1)
    if name == "graphql" and pidfiles["mcp"].read() == pid:
        err.print(
            f"[red]✘ graphql runs inside the mcp server process (pid {pid}).[/] "
            "Use `svr mcp --stop`."
        )
        raise typer.Exit(1)
    console.print(f"[cyan]Stopping {name} (pid {pid})...[/]")
    if not terminate(pid):
        err.print(f"[red]✘ pid {pid} did not exit.[/]")
        raise typer.Exit(1)
    for pf in pidfiles.values():
        pf.read()  # clears files left behind if the process was killed
    console.print(f"[green]✔ {name} stopped.[/]")


def _serve(s: Settings, servers: dict) -> None:
    """Run uvicorn servers in this process, recording its PID for each."""
    pidfiles = _pidfiles(s)
    for name in servers:
        pidfiles[name].write()
    try:
        asyncio.run(_serve_all(list(servers.values())))
    finally:
        for name in servers:
            pidfiles[name].remove_if_mine()


async def _serve_all(servers: list) -> None:
    await asyncio.gather(*(srv.serve() for srv in servers))


def _exclusive(check: bool, stop: bool) -> None:
    if check and stop:
        raise typer.BadParameter("--check and --stop are mutually exclusive")


@app.command()
def graphql(
    check: CheckOpt = False,
    stop: StopOpt = False,
    host: Annotated[str | None, typer.Option(help="Override GRAPHQL_HOST.")] = None,
    port: Annotated[int | None, typer.Option(help="Override GRAPHQL_PORT.")] = None,
) -> None:
    """Start the GraphQL server (or --check / --stop it)."""
    _exclusive(check, stop)
    s = Settings.from_env()
    s = replace(s, graphql_host=host or s.graphql_host, graphql_port=port or s.graphql_port)
    if check:
        ok, detail = check_graphql(s)
        _report("graphql", ok, detail)
        raise typer.Exit(0 if ok else 1)
    if stop:
        _stop(s, "graphql")
        return

    _ensure_not_running(s, ["graphql"])
    from intelliw.graphql.server import uvicorn_server

    console.print(f"[cyan]Starting GraphQL at[/] http://{s.graphql_host}:{s.graphql_port}/graphql")
    _serve(s, {"graphql": uvicorn_server(s)})


@app.command()
def mcp(
    check: CheckOpt = False,
    stop: StopOpt = False,
    start_graphql: Annotated[
        bool, typer.Option("--start-graphql", help="Also start the GraphQL server.")
    ] = False,
    host: Annotated[str | None, typer.Option(help="Override MCP_HOST.")] = None,
    port: Annotated[int | None, typer.Option(help="Override MCP_PORT.")] = None,
) -> None:
    """Start the MCP server over streamable HTTP (or --check / --stop it)."""
    _exclusive(check, stop)
    s = Settings.from_env()
    s = replace(s, mcp_host=host or s.mcp_host, mcp_port=port or s.mcp_port)
    if check:
        ok, detail = asyncio.run(check_mcp(s))
        _report("mcp", ok, detail)
        raise typer.Exit(0 if ok else 1)
    if stop:
        _stop(s, "mcp")
        return

    _ensure_not_running(s, ["mcp", "graphql"] if start_graphql else ["mcp"])
    from intelliw.mcp.server import uvicorn_server as mcp_server

    servers = {"mcp": mcp_server(s)}
    console.print(f"[cyan]Starting MCP at[/] http://{s.mcp_host}:{s.mcp_port}/mcp")
    if start_graphql:
        from intelliw.graphql.server import uvicorn_server as graphql_server

        servers["graphql"] = graphql_server(s)
        console.print(
            f"[cyan]Starting GraphQL at[/] http://{s.graphql_host}:{s.graphql_port}/graphql"
        )
    _serve(s, servers)


@app.command()
def status() -> None:
    """Report the status of all servers."""
    s = Settings.from_env()
    pidfiles = _pidfiles(s)
    results = [("graphql", *check_graphql(s)), ("mcp", *asyncio.run(check_mcp(s)))]
    table = Table(title="intelliw servers")
    table.add_column("server", style="bold")
    table.add_column("status")
    table.add_column("pid")
    table.add_column("detail", overflow="fold")
    for name, ok, detail in results:
        pid = pidfiles[name].read()
        table.add_row(
            name, "[green]up[/]" if ok else "[red]down[/]", str(pid) if pid else "-", detail
        )
    console.print(table)
    raise typer.Exit(0 if all(ok for _, ok, _ in results) else 1)
