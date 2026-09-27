"""The sandboxed Jinja2 environment of one design (docs/design/04-design.md)."""

from pathlib import Path
from typing import Any
from urllib.parse import quote

from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateNotFound, pass_context
from jinja2.runtime import Context
from jinja2.sandbox import ImmutableSandboxedEnvironment

from intelliw.businessdata.resources import inside
from intelliw.render.layout import RESOURCES
from intelliw.render.links import relative_url


@pass_context
def _url(context: Context, path: str) -> str:
    """Relative URL from the current output file to a site path."""
    return relative_url(context["path"], path)


@pass_context
def _asset_url(context: Context, asset: Any) -> str:
    """Relative URL of a business image (an Asset selected with `path`)."""
    return relative_url(context["path"], f"{RESOURCES}/{quote(asset['path'])}")


class _DesignLoader(FileSystemLoader):
    """Loads templates from the design folder only; a symlink pointing out is refused."""

    def __init__(self, design_dir: Path):
        super().__init__(design_dir)
        self._root = design_dir

    def get_source(self, environment: Environment, template: str) -> Any:
        source, filename, uptodate = super().get_source(environment, template)
        if not inside(Path(filename), self._root):
            raise TemplateNotFound(template, f"{template} links outside the design folder")
        return source, filename, uptodate


def _autoescape(name: str | None) -> bool:
    return name is not None and name.endswith(".html.j2")


def environment(design_dir: Path) -> ImmutableSandboxedEnvironment:
    """Templates load only from `design_dir`; misspelled names are errors."""
    env = ImmutableSandboxedEnvironment(
        loader=_DesignLoader(design_dir),
        undefined=StrictUndefined,
        autoescape=_autoescape,
        keep_trailing_newline=True,
    )
    helpers: dict[str, Any] = {"url": _url, "asset_url": _asset_url}
    env.globals.update(helpers)
    return env
