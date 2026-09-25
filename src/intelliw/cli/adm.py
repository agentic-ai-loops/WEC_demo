"""`adm`: workspace/site tools."""

from pathlib import Path

import typer
from rich.console import Console

from intelliw.workspace import Workspace

app = typer.Typer(no_args_is_help=True, help="Workspace and site tools.")
console = Console()


@app.callback()
def _root() -> None:
    pass


@app.command()
def build(workspace: Path) -> None:
    """Render the site of a workspace into <workspace>/_site."""
    from intelliw.site import build

    ws = Workspace(workspace.resolve())
    files = build(ws)
    console.print(f"[green]Wrote {len(files)} files to[/] {ws.site_dir}")
