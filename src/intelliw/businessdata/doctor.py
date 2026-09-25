"""Health check of a workspace's #businessdata (`business doctor`).

Checks one version (the active version unless a snapshot is named):

- statistics of the entities per collection;
- referential integrity: SQLite's own checks (foreign-key violations of the version's
  rows; database-wide integrity), the version state, and the document rules of
  01-businessdata (`schema.integrity_errors`);
- missing resources: the version's asset files that do not exist under
  `businessdata/resources/`. Files that no version at all refers to are reported too,
  but are not a problem.
"""

from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from intelliw.businessdata import documents, queries
from intelliw.businessdata.schema import integrity_errors
from intelliw.businessdata.tables import (
    ACTIVE,
    COLLECTION_ROWS,
    AssetRow,
    HideableRow,
    ReviewItemRow,
    SnapshotRow,
)


@dataclass(frozen=True)
class CollectionStats:
    collection: str
    live: int
    hidden: int | None  # None for collections without a hidden flag
    deleted: int


@dataclass(frozen=True)
class MissingResource:
    asset: str
    path: str


@dataclass
class Report:
    version: int  # the version checked
    business_name: str
    stats: list[CollectionStats]
    open_reviews: int
    snapshots: list[int]
    based_on: int | None
    modified: bool | None
    problems: list[str] = field(default_factory=list)  # integrity problems
    missing: list[MissingResource] = field(default_factory=list)
    unregistered: list[str] = field(default_factory=list)  # files no version refers to

    @property
    def ok(self) -> bool:
        return not self.problems and not self.missing


def version_label(version: int) -> str:
    return "active" if version == ACTIVE else f"snapshot {version}"


def collection_stats(session: Session, version: int = ACTIVE) -> list[CollectionStats]:
    stats = []
    for name, row in COLLECTION_ROWS.items():
        hideable = issubclass(row, HideableRow)
        live = func.count().filter(row.deleted.is_(False))
        hidden = (
            func.count().filter(row.deleted.is_(False), row.hidden.is_(True))  # type: ignore[attr-defined]
            if hideable
            else func.count()
        )
        deleted = func.count().filter(row.deleted.is_(True))
        n_live, n_hidden, n_deleted = session.execute(
            select(live, hidden, deleted).where(row.version == version)
        ).one()
        stats.append(CollectionStats(name, n_live, n_hidden if hideable else None, n_deleted))
    return stats


def _database_problems(session: Session, version: int) -> list[str]:
    """Database-wide integrity, and foreign-key violations of the version's rows."""
    problems = []
    result = session.execute(text("PRAGMA integrity_check")).scalar()
    if result != "ok":
        problems.append(f"database: integrity_check reports {result}")
    for table, rowid, parent, _fk in session.execute(text("PRAGMA foreign_key_check")).all():
        row_version = session.execute(
            text(f'SELECT version FROM "{table}" WHERE rowid = :rowid'), {"rowid": rowid}
        ).scalar()
        if row_version == version or table in ("snapshots", "version_state"):
            problems.append(f"database: {table} row {rowid} refers to a missing {parent} row")
    return problems


def check(session: Session, resources_dir: Path, version: int = ACTIVE) -> Report:
    """Check one version of an open workspace database (the active version by default)."""
    try:
        index = queries.get_version_index(session)
        based_on, modified = index.based_on, index.modified
    except queries.NotFound:
        based_on = modified = None
    snapshots = list(session.scalars(select(SnapshotRow.number).order_by(SnapshotRow.number)))
    try:
        business = queries.get_business(session, version)
    except queries.NotFound:
        business = None

    open_reviews = session.scalar(
        select(func.count()).where(
            ReviewItemRow.version == version,
            ReviewItemRow.deleted.is_(False),
            ReviewItemRow.status == "open",
        )
    )
    report = Report(
        version=version,
        business_name=business.name if business else "",
        stats=collection_stats(session, version),
        open_reviews=open_reviews or 0,
        snapshots=snapshots,
        based_on=based_on,
        modified=modified,
        problems=_database_problems(session, version),
    )

    label = version_label(version)
    if business is None:
        report.problems.append(f"{label}: no business record")
    if based_on is None:
        report.problems.append("versions: no version state (database not initialized)")
    elif based_on not in snapshots:
        report.problems.append(f"versions: active version is based on missing snapshot {based_on}")

    try:
        data = documents.read_version(session, version, validate=False)
    except (queries.NotFound, ValidationError) as exc:
        report.problems.append(f"{label}: cannot be read: {exc}")
    else:
        report.problems.extend(f"{label}: {e}" for e in integrity_errors(data))
        report.missing = [
            MissingResource(a.id, a.path)
            for a in data.assets
            if not a.deleted and not (resources_dir / a.path).is_file()
        ]

    # Files are shared by all versions: a file is unregistered only if no version uses it.
    referenced = set(session.scalars(select(AssetRow.path).distinct()))
    if resources_dir.is_dir():
        report.unregistered = sorted(
            p.relative_to(resources_dir).as_posix()
            for p in resources_dir.rglob("*")
            if p.is_file() and p.relative_to(resources_dir).as_posix() not in referenced
        )
    return report
