"""`render.json`: the published render metadata (docs/design/04-design.md)."""

from datetime import datetime
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version

from pydantic import BaseModel
from sqlalchemy.orm import Session

from intelliw.businessdata import queries
from intelliw.businessdata.schema import UtcDatetime, Version
from intelliw.businessdata.tables import ACTIVE
from intelliw.render.layout import version_name


class DesignInfo(BaseModel):
    name: str
    title: str


class SnapshotInfo(BaseModel):
    number: int
    tag: str | None
    created_at: UtcDatetime


class BusinessDataInfo(BaseModel):
    version_name: str
    version: int | None  # snapshot number; None = active
    snapshot: str | None  # snapshot tag
    based_on: SnapshotInfo | None
    modified: bool
    updated_at: UtcDatetime | None


class RenderInfo(BaseModel):
    """Where a rendered site came from. No business values, paths or user names."""

    rendered_at: UtcDatetime
    generator: str
    design: DesignInfo
    businessdata: BusinessDataInfo
    include_hidden: bool


def generator() -> str:
    try:
        return f"intelliw {package_version('intelliw')}"
    except PackageNotFoundError:  # pragma: no cover - running from a source tree
        return "intelliw"


def _snapshot(v: Version) -> SnapshotInfo:
    return SnapshotInfo(number=v.number, tag=v.tag, created_at=v.created_at)


def build_info(
    session: Session,
    *,
    version: int,
    design: DesignInfo,
    include_hidden: bool,
    rendered_at: datetime,
) -> RenderInfo:
    index = queries.get_version_index(session)
    snapshots = {s.number: s for s in index.snapshots}
    if version == ACTIVE:
        base = snapshots.get(index.based_on) if index.based_on is not None else None
        data = BusinessDataInfo(
            version_name=version_name(None, None),
            version=None,
            snapshot=None,
            based_on=_snapshot(base) if base is not None else None,
            modified=index.modified,
            updated_at=queries.latest_update(session, version),
        )
    else:
        snap = snapshots[version]
        data = BusinessDataInfo(
            version_name=version_name(snap.number, snap.tag),
            version=snap.number,
            snapshot=snap.tag,
            based_on=_snapshot(snap),
            modified=False,
            updated_at=queries.latest_update(session, version),
        )
    return RenderInfo(
        rendered_at=rendered_at,
        generator=generator(),
        design=design,
        businessdata=data,
        include_hidden=include_hidden,
    )
