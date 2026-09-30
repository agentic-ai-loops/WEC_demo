"""Jobs run by the worker (`jobs worker`)."""

import logging
from pathlib import Path
from typing import Any

from intelliw.config import Settings
from intelliw.jobs.models import MutationEvent, RenderAllResult, SiteRender
from intelliw.render import RenderFailure, list_designs, render
from intelliw.workspace import Workspace

log = logging.getLogger(__name__)


def render_all(message: dict[str, Any]) -> dict[str, Any]:
    """The re-render job: every design of the workspace, from the active version.

    The workspace and the run directory come from the worker's settings (`.env`).
    Returns the `RenderAllResult` as JSON-ready data (stored as the job's result).
    """
    event = MutationEvent.model_validate(message)
    settings = Settings.from_env()
    if settings.workspace is None:
        raise RuntimeError("no workspace: set WORKSPACE in .env for the worker")
    result = render_designs(Workspace(settings.workspace), settings.sites_dir, event)
    for site in result.sites:
        if site.ok:
            log.info("rendered %s (%d pages) → %s", site.design, site.pages, site.output_dir)
        else:
            log.error("design %s failed: %s", site.design, site.error)
    return result.model_dump(mode="json")


def render_designs(ws: Workspace, sites_dir: Path, event: MutationEvent) -> RenderAllResult:
    """Render every design with the active version; one failing design doesn't stop the rest."""
    sites: list[SiteRender] = []
    for design in list_designs(ws):
        try:
            r = render(ws, design, sites_dir=sites_dir)
        except (RenderFailure, OSError) as exc:
            sites.append(SiteRender(design=design, ok=False, error=f"{type(exc).__name__}: {exc}"))
        else:
            sites.append(
                SiteRender(design=design, ok=True, output_dir=str(r.output_dir), pages=r.pages)
            )
    return RenderAllResult(event=event, workspace=ws.root.name, sites=sites)
