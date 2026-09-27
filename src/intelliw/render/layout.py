"""Names and file kinds of a design bundle (docs/design/04-design.md)."""

import re
from pathlib import Path

from intelliw.workspace import Workspace

DESIGN_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")
SEGMENT = re.compile(r"^\[([A-Za-z_][A-Za-z0-9_]*)\]$")
PARAM_VALUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

CONFIG_FILE = "_config.json"
SITE_QUERY = "site.gql"
PAGE_TEMPLATE = "page.html.j2"
PAGE_QUERY = "page.gql"
PARAMS_QUERY = "params.gql"
QUERY_FILES = (SITE_QUERY, PAGE_QUERY, PARAMS_QUERY)
TEMPLATE_SUFFIX = ".j2"

RESOURCES = "resources"  # business images in the output
RENDER_JSON = "render.json"  # render metadata in the output
RESERVED = (RESOURCES, RENDER_JSON, RENDER_JSON + TEMPLATE_SUFFIX)  # top-level names

ACTIVE_NAME = "active"


def ignored(name: str) -> bool:
    """`_*` entries are not rendered or copied; dotfiles are ignored."""
    return name.startswith(("_", "."))


def segment_name(folder: str) -> str | None:
    """`slug` for a dynamic segment folder `[slug]`; None for other folders."""
    m = SEGMENT.match(folder)
    return m.group(1) if m else None


def output_name(template: str) -> str:
    """`style.css.j2` -> `style.css`."""
    return template.removesuffix(TEMPLATE_SUFFIX)


def list_designs(ws: Workspace) -> list[str]:
    """Names of the designs in `<workspace>/design/`."""
    if not ws.design_dir.is_dir():
        return []
    return sorted(
        p.name
        for p in ws.design_dir.iterdir()
        if p.is_dir() and not ignored(p.name) and DESIGN_NAME.match(p.name)
    )


def version_name(number: int | None, tag: str | None) -> str:
    """The output folder name of a #businessdata version: `active`, `3`, `4_Spring-2026`."""
    if number is None:
        return ACTIVE_NAME
    if tag is None:
        return str(number)
    return f"{number}_{re.sub(r'[^A-Za-z0-9._-]+', '-', tag)}"


def relative(design_dir: Path, path: Path) -> str:
    """A design file's name as shown in messages: its path in the design folder."""
    return path.relative_to(design_dir).as_posix()
