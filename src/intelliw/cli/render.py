"""`render`: render a workspace's #businessdata with a design (docs/design/04-design.md)."""

import logging
from dataclasses import replace
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from rich.console import Console
from rich.markup import escape

from intelliw.businessdata import queries
from intelliw.config import Settings
from intelliw.render import (
    CheckFailed,
    DesignNotFound,
    RenderError,
    RenderOptions,
    RenderResult,
    render,
)
from intelliw.workspace import Workspace

app = typer.Typer(
    add_completion=False,
    help="Render #businessdata with a design into {run_dir}/sites/{workspace}/{design}/.",
)
console = Console()
err = Console(stderr=True)


@app.command()
def main(
    design: Annotated[
        str, typer.Option("--design", "--template", help="Design name: design/<name>/.")
    ],
    workspace: Annotated[
        str | None,
        typer.Argument(help="Workspace directory (default: WORKSPACE in .env)."),
    ] = None,
    include_hidden: Annotated[
        bool, typer.Option("--include-hidden", help="Render hidden entities too.")
    ] = False,
    version: Annotated[
        int | None, typer.Option("--version", help="Render snapshot N (default: active).")
    ] = None,
    snapshot: Annotated[
        str | None, typer.Option("--snapshot", help="Render the snapshot with this tag.")
    ] = None,
    dryrun: Annotated[
        bool, typer.Option("--dryrun", help="Run every query and template; write nothing.")
    ] = False,
    run_dir: Annotated[
        Path | None,
        typer.Option(
            "--run-dir", help="Sites go to {run_dir}/sites/ (default: INTELLIW_RUN_DIR or ./run)."
        ),
    ] = None,
) -> None:
    """Check the design, render every page, and write the site (unless --dryrun)."""
    if version is not None and snapshot is not None:
        _fail("Give either --version or --snapshot, not both.", 2)
    settings = Settings.from_env()
    if run_dir is not None:
        settings = replace(settings, run_dir=run_dir)
    root = Path(workspace) if workspace is not None else settings.workspace
    if root is None:
        _fail("No workspace: set WORKSPACE in .env or pass the workspace directory.", 2)
    ws = Workspace(root.resolve())
    if not ws.database_file.is_file():
        _fail(f"No #businessdata database at {ws.database_file}", 2)
    # GraphQL errors become render errors; strawberry's own error log would repeat them
    logging.getLogger("strawberry.execution").setLevel(logging.CRITICAL)

    options = RenderOptions(version=version, snapshot=snapshot, include_hidden=include_hidden)
    try:
        result = render(ws, design, options, sites_dir=settings.sites_dir, dry_run=dryrun)
    except DesignNotFound as exc:
        _fail(str(exc), 2)
    except queries.NotFound as exc:
        _fail(str(exc), 2)
    except CheckFailed as exc:
        err.print(f"[red]✘ {exc.stage.capitalize()} failed[/] ({len(exc.problems)} problem(s)):")
        for problem in exc.problems:
            err.print(f"  {escape(str(problem))}")
        raise typer.Exit(1) from exc
    except RenderError as exc:
        err.print(f"[red]✘ Render failed:[/] {escape(str(exc.problem))}")
        raise typer.Exit(1) from exc
    _report(result)


def _report(r: RenderResult) -> None:
    data = r.info.businessdata
    what = "active version" if data.version is None else f"snapshot {data.version}"
    if data.snapshot:
        what += f" ({escape(data.snapshot)})"
    if r.info.include_hidden:
        what += ", hidden entities included"
    counts = f"{r.pages} pages, {len(r.files)} files, {r.images} business images"
    if r.written:
        console.print(f"[green]✔ Rendered[/] design [bold]{r.design}[/] with the {what}.")
        console.print(f"  {counts} → {escape(str(r.output_dir))}")
    else:
        console.print(f"[green]✔ Dry run[/] of design [bold]{r.design}[/] with the {what}.")
        console.print(f"  {counts}; nothing written (would write {escape(str(r.output_dir))})")


def _fail(message: str, code: int) -> NoReturn:
    err.print(f"[red]✘ {escape(message)}[/]")
    raise typer.Exit(code)
