"""Render #businessdata with a design into `{sites_dir}/{workspace}/{design}/`.

Follows docs/design/04-design.md, *Rendering*: the design check, `site.gql`, the walk
(dynamic segments expanded by the parent folder), business images, `render.json`, the
link check, then an atomic swap of the output folder. Everything is rendered in memory
first, so a dry run does all of it but write.
"""

import asyncio
import json
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jinja2 import TemplateError, TemplateNotFound, TemplateSyntaxError
from sqlalchemy.orm import Session, sessionmaker

from intelliw.businessdata import queries
from intelliw.businessdata import schema as m
from intelliw.businessdata.database import create_db_engine, session_factory
from intelliw.businessdata.resources import UnsafePath, inside, resource_file
from intelliw.graphql.context import View
from intelliw.render.check import check_design
from intelliw.render.config import DesignConfig, load_config
from intelliw.render.env import environment
from intelliw.render.errors import CheckFailed, DesignNotFound, RenderError
from intelliw.render.info import DesignInfo, RenderInfo, build_info
from intelliw.render.layout import (
    DESIGN_NAME,
    PAGE_QUERY,
    PAGE_TEMPLATE,
    PARAM_VALUE,
    PARAMS_QUERY,
    RENDER_JSON,
    RESOURCES,
    SITE_QUERY,
    TEMPLATE_SUFFIX,
    ignored,
    list_designs,
    output_name,
    relative,
    segment_name,
    site_dir,
)
from intelliw.render.links import check_links
from intelliw.render.query import QueryRunner
from intelliw.workspace import Workspace


@dataclass(frozen=True)
class RenderOptions:
    """What data is shown (docs/design/04-design.md, *Render options*)."""

    version: int | None = None
    snapshot: str | None = None
    include_hidden: bool = False


@dataclass
class RenderResult:
    design: str
    version_name: str
    output_dir: Path
    info: RenderInfo
    files: list[str]  # output paths, sorted
    pages: int
    images: int
    written: bool


def render(
    ws: Workspace,
    design: str,
    options: RenderOptions = RenderOptions(),  # noqa: B008 - frozen dataclass
    *,
    sites_dir: Path,
    dry_run: bool = False,
) -> RenderResult:
    """Render the workspace's #businessdata with `design`; raises a `RenderFailure`.

    The site goes to `site_dir(sites_dir, ws, design)` (`{sites_dir}/{workspace}/{design}`).
    """
    return asyncio.run(render_async(ws, design, options, sites_dir=sites_dir, dry_run=dry_run))


async def render_async(
    ws: Workspace,
    design: str,
    options: RenderOptions = RenderOptions(),  # noqa: B008
    *,
    sites_dir: Path,
    dry_run: bool = False,
) -> RenderResult:
    design_dir = ws.design(design)
    if not DESIGN_NAME.match(design) or not design_dir.is_dir():
        known = ", ".join(list_designs(ws)) or "none"
        raise DesignNotFound(f"no design {design!r} in {ws.design_dir} (designs: {known})")
    problems = check_design(design_dir)
    if problems:
        raise CheckFailed("design check", problems)
    if not ws.database_file.is_file():
        raise FileNotFoundError(f"no #businessdata database at {ws.database_file}")

    engine = create_db_engine(ws.database_file)
    try:
        sessions = session_factory(engine)
        build = _Build(ws, design, design_dir, options, sessions)
        await build.run()
    finally:
        engine.dispose()

    output_dir = site_dir(sites_dir, ws, design)
    if not dry_run:
        _write(output_dir.parent, output_dir, build.files)
    return RenderResult(
        design=design,
        version_name=build.version_name,
        output_dir=output_dir,
        info=build.info,
        files=sorted(build.files),
        pages=build.pages,
        images=build.images,
        written=not dry_run,
    )


@dataclass
class _Build:
    ws: Workspace
    design: str
    design_dir: Path
    options: RenderOptions
    sessions: sessionmaker[Session]
    files: dict[str, bytes | Path] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)  # output path -> design file
    asset_ids: set[str] = field(default_factory=set)
    pages: int = 0
    images: int = 0

    async def run(self) -> None:
        rendered_at = datetime.now(UTC)
        with self.sessions() as session:
            self.version = queries.resolve_version(
                session, version=self.options.version, snapshot=self.options.snapshot
            )
            self.config: DesignConfig = load_config(self.design_dir)
            self.info = build_info(
                session,
                version=self.version,
                design=DesignInfo(name=self.design, title=self.config.title),
                include_hidden=self.options.include_hidden,
                rendered_at=rendered_at,
            )
        self.version_name = self.info.businessdata.version_name
        self.runner = QueryRunner(
            self.sessions, self.ws.resources_dir, View(self.version, self.options.include_hidden)
        )
        self.env = environment(self.design_dir)
        self.render_meta = self.info.model_dump(mode="json")

        site_query = self.design_dir / SITE_QUERY
        self.site = await self._query(site_query, {}) if site_query.is_file() else {}
        await self._walk(self.design_dir, "", {})
        self._publish_images()
        self._add(RENDER_JSON, _json(self.render_meta), "render metadata")
        problems = check_links(self.files, self.sources)
        if problems:
            raise CheckFailed("link check", problems)

    # ---- walk ------------------------------------------------------------------------

    async def _walk(self, folder: Path, out: str, params: dict[str, str]) -> None:
        entries = sorted(p for p in folder.iterdir() if not ignored(p.name))
        for path in (p for p in entries if p.is_file()):
            name = path.name
            if name == PAGE_TEMPLATE:
                query = folder / PAGE_QUERY
                data = await self._query(query, params) if query.is_file() else {}
                self._render(path, out + "index.html", params, data=data)
                self.pages += 1
            elif name.endswith(".gql"):
                continue
            elif name.endswith(TEMPLATE_SUFFIX):
                self._render(path, out + output_name(name), params)
            else:
                rel = relative(self.design_dir, path)
                if not inside(path, self.design_dir):
                    raise RenderError(rel, "links to a file outside the design folder")
                self._add(out + name, path, rel)
        for sub in (p for p in entries if p.is_dir()):
            seg = segment_name(sub.name)
            if seg is None:
                await self._walk(sub, f"{out}{sub.name}/", params)
                continue
            for value in await self._params(sub / PARAMS_QUERY, seg, params):
                await self._walk(sub, f"{out}{value}/", {**params, seg: value})

    async def _params(self, query: Path, key: str, params: dict[str, str]) -> list[str]:
        file = relative(self.design_dir, query)
        data = await self._query(query, params)
        (items,) = data.values()
        if not isinstance(items, list):
            raise RenderError(file, "params.gql must return a list")
        values: list[str] = []
        for item in items:
            value = item.get(key) if isinstance(item, dict) else None
            if not isinstance(value, str) or not PARAM_VALUE.match(value):
                raise RenderError(
                    file, f"item {item!r}: {key!r} must be a path segment like 'eye-exams'"
                )
            if value in values:
                raise RenderError(file, f"duplicate {key} {value!r}")
            values.append(value)
        return values

    async def _query(self, path: Path, params: dict[str, str]) -> dict[str, Any]:
        data = await self.runner.run(
            relative(self.design_dir, path), path.read_text(encoding="utf-8"), params
        )
        _collect_assets(data, self.asset_ids)
        return data

    # ---- templates -------------------------------------------------------------------

    def _render(
        self, template: Path, out: str, params: dict[str, str], *, data: Any = None
    ) -> None:
        name = relative(self.design_dir, template)
        context: dict[str, Any] = {
            "config": self.config.model_dump(mode="json"),
            "site": self.site,
            "params": params,
            "path": out,
            "render": self.render_meta,
        }
        if template.name == PAGE_TEMPLATE:
            context["data"] = data
        try:
            text = self.env.get_template(name).render(context)
        except TemplateSyntaxError as exc:
            raise RenderError(exc.name or name, exc.message or "syntax error", exc.lineno) from exc
        except TemplateNotFound as exc:
            message = exc.message if exc.message != exc.name else f"template not found: {exc.name}"
            raise RenderError(name, message or "template not found") from exc
        except TemplateError as exc:
            file, line = _template_frame(exc, self.design_dir, name)
            raise RenderError(file, str(exc), line) from exc
        self._add(out, text.encode("utf-8"), name)

    # ---- output ----------------------------------------------------------------------

    def _add(self, out: str, content: bytes | Path, source: str) -> None:
        if out in self.files:
            raise RenderError(source, f"output {out} is also written by {self.sources[out]}")
        self.files[out] = content
        self.sources[out] = source

    def _publish_images(self) -> None:
        if not self.asset_ids:
            return
        with self.sessions() as session:
            ids = sorted(self.asset_ids)
            assets = queries.get_entities(session, m.Asset, self.version, ids)
        for asset in assets:
            if asset is None:
                continue
            try:
                src = resource_file(self.ws.resources_dir, asset.path)
            except UnsafePath as exc:
                raise RenderError(
                    f"{RESOURCES}/{asset.path}", f"asset {asset.id!r}: {exc}"
                ) from exc
            if not src.is_file():
                raise RenderError(
                    f"{RESOURCES}/{asset.path}",
                    f"file of asset {asset.id!r} is missing: businessdata/resources/{asset.path}",
                )
            self._add(f"{RESOURCES}/{asset.path}", src, f"asset {asset.id}")
            self.images += 1


def _collect_assets(value: Any, ids: set[str]) -> None:
    """Ids of every `{"__typename": "Asset", "id": ...}` in a query result."""
    if isinstance(value, dict):
        if value.get("__typename") == "Asset" and isinstance(value.get("id"), str):
            ids.add(value["id"])
        for v in value.values():
            _collect_assets(v, ids)
    elif isinstance(value, list):
        for v in value:
            _collect_assets(v, ids)


def _template_frame(exc: BaseException, design_dir: Path, default: str) -> tuple[str, int | None]:
    """The innermost design template and line in a template error's traceback."""
    found: tuple[str, int | None] = (default, None)
    tb = exc.__traceback__
    root = str(design_dir)
    while tb is not None:
        filename = tb.tb_frame.f_code.co_filename
        if filename.endswith(TEMPLATE_SUFFIX) and filename.startswith(root):
            found = (Path(filename).relative_to(design_dir).as_posix(), tb.tb_lineno)
        tb = tb.tb_next
    return found


def _json(value: Any) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _write(outputs_dir: Path, target: Path, files: dict[str, bytes | Path]) -> None:
    """Write into a temporary folder next to `target` (in `outputs_dir`), then swap it in."""
    outputs_dir.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=".tmp-", dir=outputs_dir))
    try:
        for name, content in files.items():
            dest = tmp / name
            if not inside(dest, tmp):
                raise RenderError(name, "output path leaves the output folder")
            dest.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, Path):
                shutil.copyfile(content, dest)
            else:
                dest.write_bytes(content)
        old = None
        if target.exists():
            old = outputs_dir / f".old-{uuid.uuid4().hex}"
            target.rename(old)
        tmp.rename(target)
        if old is not None:
            shutil.rmtree(old, ignore_errors=True)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
