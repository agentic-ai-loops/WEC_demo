"""#design rendering: design bundles, the design check, and the renderer.

See docs/design/04-design.md. Modules:

- `layout`   — design names, file kinds, output folder names
- `config`   — `DesignConfig` (`_config.json`)
- `check`    — the design check
- `env`      — the sandboxed Jinja2 environment, `url()` / `asset_url()`
- `query`    — design queries, run in-process in the render's view
- `links`    — relative links and the link check
- `info`     — `RenderInfo` (`render.json`)
- `renderer` — `render()`
"""

from intelliw.render.check import check_design, check_workspace
from intelliw.render.config import DesignConfig
from intelliw.render.errors import (
    CheckFailed,
    DesignNotFound,
    Problem,
    RenderError,
    RenderFailure,
)
from intelliw.render.info import RenderInfo
from intelliw.render.layout import list_designs, version_name
from intelliw.render.renderer import RenderOptions, RenderResult, render, render_async

__all__ = [
    "CheckFailed",
    "DesignConfig",
    "DesignNotFound",
    "Problem",
    "RenderError",
    "RenderFailure",
    "RenderInfo",
    "RenderOptions",
    "RenderResult",
    "check_design",
    "check_workspace",
    "list_designs",
    "render",
    "render_async",
    "version_name",
]
