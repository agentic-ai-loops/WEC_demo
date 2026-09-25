"""Discover and load #design templates."""

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from intelliw.workspace import Workspace

PAGE_SUFFIX = ".html.j2"


def environment(ws: Workspace) -> Environment:
    return Environment(
        loader=FileSystemLoader(ws.templates_dir),
        autoescape=select_autoescape(["html", "j2"]),
        undefined=StrictUndefined,
    )


def page_templates(ws: Workspace) -> list[str]:
    """Template names (relative to templates/) that render to pages.

    Files whose name starts with `_` are partials/layouts and are skipped.
    """
    return sorted(
        p.relative_to(ws.templates_dir).as_posix()
        for p in ws.templates_dir.rglob(f"*{PAGE_SUFFIX}")
        if not p.name.startswith("_")
    )
