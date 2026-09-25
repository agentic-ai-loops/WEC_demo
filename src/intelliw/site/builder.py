"""Render #businessdata x #design into the workspace's _site directory."""

import shutil
from pathlib import Path

from intelliw import businessdata, design
from intelliw.design.loader import PAGE_SUFFIX
from intelliw.workspace import Workspace


def build(ws: Workspace) -> list[Path]:
    """Build the site from scratch. Returns the list of written files."""
    data = businessdata.load(ws)
    env = design.environment(ws)

    if ws.site_dir.exists():
        shutil.rmtree(ws.site_dir)
    ws.site_dir.mkdir(parents=True)

    written: list[Path] = []
    context = {"business": data.business, "data": data}
    for name in design.page_templates(ws):
        out = ws.site_dir / name.removesuffix(PAGE_SUFFIX)
        out = out.with_suffix(".html")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(env.get_template(name).render(context))
        written.append(out)

    for src, dest in [
        (ws.assets_dir, ws.site_dir / "assets"),
        (ws.resources_dir, ws.site_dir / "resources"),
    ]:
        if src.exists():
            shutil.copytree(src, dest)
            written.extend(p for p in dest.rglob("*") if p.is_file())

    return written
