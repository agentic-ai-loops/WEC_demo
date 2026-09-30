"""`jobs`: the re-render queue — run the worker, check the queue, queue a re-render."""

import logging
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from redis.exceptions import RedisError
from rich.console import Console
from rich.markup import escape
from rich.table import Table
from rq import Worker
from rq.registry import FailedJobRegistry, FinishedJobRegistry, StartedJobRegistry

from intelliw.config import Settings
from intelliw.jobs.models import MutationEvent, RenderAllResult
from intelliw.jobs.queue import connect, enqueue, render_queue
from intelliw.pidfile import PidFile, terminate

app = typer.Typer(
    no_args_is_help=True, help="The re-render job queue (rq + Redis; settings from .env)."
)
console = Console()
err = Console(stderr=True)

RunDirOpt = Annotated[
    Path | None,
    typer.Option(
        "--run-dir", help="Where worker.pid and the sites go (default: INTELLIW_RUN_DIR or ./run)."
    ),
]


def _settings(run_dir: Path | None) -> Settings:
    s = Settings.from_env()
    return replace(s, run_dir=run_dir) if run_dir is not None else s


def _redis_or_exit(s: Settings):
    connection = connect(s)
    try:
        connection.ping()
    except RedisError as exc:
        err.print(f"[red]✘ Redis is not reachable at {escape(s.redis_url)}:[/] {escape(str(exc))}")
        err.print("  Start it with `make redis-start`.")
        raise typer.Exit(2) from exc
    return connection


@app.command()
def worker(
    run_dir: RunDirOpt = None,
    burst: Annotated[bool, typer.Option("--burst", help="Process queued jobs, then exit.")] = False,
) -> None:
    """Run the worker in the foreground: re-render every design after each mutation."""
    s = _settings(run_dir)
    if s.workspace is None:
        err.print("[red]✘ No workspace:[/] set WORKSPACE in .env.")
        raise typer.Exit(2)
    pidfile = PidFile(s.run_dir, "worker")
    if pid := pidfile.read():
        err.print(f"[red]✘ A worker is already running (pid {pid}).[/] Stop it with `jobs stop`.")
        raise typer.Exit(1)
    connection = _redis_or_exit(s)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("strawberry.execution").setLevel(logging.CRITICAL)
    console.print(
        f"[cyan]Worker for[/] {s.workspace.name} [dim](queue {render_queue(connection).name}, "
        f"sites in {s.sites_dir})[/]"
    )
    pidfile.write()
    try:
        Worker([render_queue(connection)], connection=connection).work(burst=burst)
    finally:
        pidfile.remove_if_mine()


@app.command()
def stop(run_dir: RunDirOpt = None) -> None:
    """Stop the worker (it finishes the job in progress first)."""
    s = _settings(run_dir)
    pidfile = PidFile(s.run_dir, "worker")
    pid = pidfile.read()
    if pid is None:
        err.print(f"[yellow]No worker is running (no live pid in {pidfile.path}).[/]")
        raise typer.Exit(1)
    console.print(f"[cyan]Stopping the worker (pid {pid})...[/]")
    if not terminate(pid, timeout=60):
        err.print(f"[red]✘ pid {pid} did not exit.[/]")
        raise typer.Exit(1)
    pidfile.read()
    console.print("[green]✔ Worker stopped.[/]")


@app.command("enqueue")
def enqueue_cmd(
    mutation: Annotated[str, typer.Option(help="Recorded as the event's mutation.")] = "manual",
) -> None:
    """Queue a re-render of every design by hand."""
    s = _settings(None)
    queue = render_queue(_redis_or_exit(s))
    job = enqueue(queue, MutationEvent(mutation=mutation, updated_at=datetime.now(UTC)))
    console.print(f"[green]✔ Queued[/] job {job.id} [dim]({queue.count} waiting)[/]")


@app.command()
def status(run_dir: RunDirOpt = None) -> None:
    """Report Redis, the worker, the queue and the last finished job."""
    s = _settings(run_dir)
    connection = _redis_or_exit(s)
    queue = render_queue(connection)
    pid = PidFile(s.run_dir, "worker").read()
    workers = Worker.all(queue=queue)
    finished = FinishedJobRegistry(queue=queue)
    failed = FailedJobRegistry(queue=queue)

    table = Table(title=f"re-render queue {queue.name} · {s.redis_url}")
    table.add_column("check", style="bold")
    table.add_column("detail", overflow="fold")
    table.add_row("worker", f"pid {pid}" if pid else "[red]not running[/]")
    table.add_row("rq workers", ", ".join(f"{w.name} ({w.get_state()})" for w in workers) or "none")
    table.add_row("waiting", str(queue.count))
    table.add_row("running", str(StartedJobRegistry(queue=queue).count))
    table.add_row("finished / failed", f"{finished.count} / {failed.count}")
    last_ids = finished.get_job_ids()[-1:]
    if last_ids and (job := queue.fetch_job(last_ids[0])) is not None and job.return_value():
        result = RenderAllResult.model_validate(job.return_value())
        sites = ", ".join(
            f"{x.design} {'✔' if x.ok else '✘'}" + (f" ({x.pages} pages)" if x.ok else "")
            for x in result.sites
        )
        table.add_row(
            "last job",
            f"{result.event.mutation} at {result.event.updated_at:%Y-%m-%d %H:%M:%S} UTC: {sites}",
        )
    console.print(table)
    raise typer.Exit(0 if pid else 1)
