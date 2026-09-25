"""`business`: inspect and version a workspace's #businessdata (WORKSPACE in .env)."""

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table
from sqlalchemy.orm import Session

from intelliw.businessdata import documents, queries
from intelliw.businessdata.database import create_db_engine, session_factory
from intelliw.businessdata.doctor import Report, check, version_label
from intelliw.businessdata.schema import Version
from intelliw.config import Settings
from intelliw.workspace import Workspace

app = typer.Typer(
    no_args_is_help=True,
    help="Inspect and version #businessdata (workspace from WORKSPACE in .env).",
)
console = Console()
err = Console(stderr=True)

WorkspaceOpt = Annotated[
    Path | None,
    typer.Option("--workspace", "-w", help="Workspace directory (default: WORKSPACE in .env)."),
]


VersionOpt = Annotated[int | None, typer.Option("--version", help="Snapshot number.")]
TagOpt = Annotated[str | None, typer.Option("--tag", help="Snapshot tag.")]


def _resolve(session: Session, version: int | None, tag: str | None) -> int:
    try:
        return queries.resolve_version(session, version=version, snapshot=tag)
    except ValueError as exc:  # both given
        err.print("[red]✘ Give either --version or --tag, not both.[/]")
        raise typer.Exit(2) from exc
    except queries.NotFound as exc:
        err.print(f"[red]✘ {escape(str(exc))}[/]")
        raise typer.Exit(2) from exc


def _workspace(path: Path | None) -> Workspace:
    root = path or Settings.from_env().workspace
    if root is None:
        err.print("[red]✘ No workspace:[/] set WORKSPACE in .env or pass --workspace.")
        raise typer.Exit(2)
    ws = Workspace(root.resolve())
    if not ws.database_file.is_file():
        err.print(f"[red]✘ No database at[/] {escape(str(ws.database_file))}")
        raise typer.Exit(1)
    return ws


@contextmanager
def _session(ws: Workspace) -> Iterator[Session]:
    engine = create_db_engine(ws.database_file)
    try:
        with session_factory(engine)() as session:
            yield session
    finally:
        engine.dispose()


def _print_report(ws: Workspace, report: Report) -> None:
    name = escape(report.business_name) or "(no business)"
    console.print(f"[bold]{name}[/]  [dim]{escape(str(ws.root))}[/]")
    snapshots = ", ".join(map(str, report.snapshots)) or "none"
    state = (
        f"based on snapshot {report.based_on}, {'modified' if report.modified else 'unmodified'}"
        if report.based_on is not None
        else "no version state"
    )
    console.print(f"Versions: active ({state}); snapshots: {snapshots}")
    label = version_label(report.version)
    console.print(f"Checking: [bold]{label}[/]\n")

    table = Table(title=f"Entities ({label})", title_justify="left")
    table.add_column("collection")
    for col in ("live", "hidden", "deleted"):
        table.add_column(col, justify="right")
    for s in report.stats:
        hidden = "—" if s.hidden is None else str(s.hidden or "")
        table.add_row(s.collection, str(s.live), hidden, str(s.deleted or ""))
    console.print(table)
    console.print(f"Open review items: [bold]{report.open_reviews}[/]\n")

    if report.problems:
        console.print(f"[red]✘ Referential integrity: {len(report.problems)} problem(s)[/]")
        for p in report.problems:
            console.print(f"  [red]•[/] {escape(p)}")
    else:
        console.print("[green]✔ Referential integrity[/]")

    if report.missing:
        console.print(f"[red]✘ Missing resources: {len(report.missing)}[/]")
        for m in report.missing:
            console.print(f"  [red]•[/] asset [bold]{escape(m.asset)}[/] → {escape(m.path)}")
    else:
        console.print("[green]✔ Resources: every asset file exists[/]")
    if report.unregistered:
        console.print(
            f"[yellow]! Files no asset in any version refers to: {len(report.unregistered)}[/]"
        )
        for f in report.unregistered:
            console.print(f"  [yellow]•[/] {escape(f)}")


@app.command()
def doctor(workspace: WorkspaceOpt = None, version: VersionOpt = None, tag: TagOpt = None) -> None:
    """Print entity statistics; check referential integrity and missing resources.

    Checks the active version unless a snapshot is named with --version or --tag.
    """
    ws = _workspace(workspace)
    with _session(ws) as session:
        report = check(session, ws.resources_dir, _resolve(session, version, tag))
    _print_report(ws, report)
    if not report.ok:
        raise typer.Exit(1)


def _snapshot_label(v: Version) -> str:
    tag = f" [cyan]{escape(v.tag)}[/]" if v.tag else ""
    return f"[bold]snapshot {v.number}[/]{tag}"


@app.command()
def dump(workspace: WorkspaceOpt = None, version: VersionOpt = None, tag: TagOpt = None) -> None:
    """Dump #businessdata as indented JSON (the active version unless a snapshot is named)."""
    ws = _workspace(workspace)
    with _session(ws) as session:
        number = _resolve(session, version, tag)
        data = documents.read_version(session, number, validate=False)
    sys.stdout.write(data.model_dump_json(indent=2) + "\n")


@app.command()
def versions(workspace: WorkspaceOpt = None) -> None:
    """List the snapshots and their tags, and what the active version is based on."""
    ws = _workspace(workspace)
    with _session(ws) as session:
        try:
            index = queries.get_version_index(session)
        except queries.NotFound as exc:
            err.print(f"[red]✘ {escape(str(exc))}[/]")
            raise typer.Exit(1) from exc

    table = Table(title="Snapshots", title_justify="left")
    table.add_column("version", justify="right")
    table.add_column("tag")
    table.add_column("parent", justify="right")
    table.add_column("created (UTC)", no_wrap=True)
    table.add_column("", no_wrap=True)
    for v in index.snapshots:
        marker = "[green]◀ active[/]" if v.number == index.based_on else ""
        table.add_row(
            str(v.number),
            escape(v.tag or ""),
            "" if v.parent is None else str(v.parent),
            v.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            marker,
        )
    console.print(table)
    state = "[yellow]modified[/]" if index.modified else "[green]unmodified[/]"
    console.print(f"Active version: based on snapshot {index.based_on}, {state}")


@app.command()
def snapshot(workspace: WorkspaceOpt = None, tag: TagOpt = None) -> None:
    """Freeze the active version into a new read-only snapshot."""
    ws = _workspace(workspace)
    with _session(ws) as session:
        try:
            snap = documents.take_snapshot(session, tag)
        except documents.TagConflict as exc:
            err.print(f"[red]✘ {escape(str(exc))}[/]")
            raise typer.Exit(1) from exc
        except (ValueError, queries.NotFound) as exc:
            err.print(f"[red]✘ {escape(str(exc))}[/]")
            raise typer.Exit(2) from exc
        session.commit()
    console.print(f"[green]✔ Created[/] {_snapshot_label(snap)}")


@app.command()
def activate(
    workspace: WorkspaceOpt = None, version: VersionOpt = None, tag: TagOpt = None
) -> None:
    """Roll the active version back to a snapshot (by --version or --tag).

    If the active version has unsnapshotted changes, they are first saved as a new
    untagged snapshot.
    """
    if version is None and tag is None:
        err.print("[red]✘ Name the snapshot with --version or --tag.[/]")
        raise typer.Exit(2)
    ws = _workspace(workspace)
    with _session(ws) as session:
        number = _resolve(session, version, tag)
        index, auto = documents.activate_version(session, number)
        session.commit()
    if auto is not None:
        console.print(f"[yellow]! Unsaved changes were kept in[/] {_snapshot_label(auto)}")
    target = next(v for v in index.snapshots if v.number == number)
    console.print(f"[green]✔ Active version is now a copy of[/] {_snapshot_label(target)}")
