"""Whole-version operations: document transfer, initialization, snapshots and rollback.

A `BusinessData` document is one version of #businessdata. These functions write a
document into a version, read a version back as a document (import/export), initialize a
workspace, take snapshots and activate them (docs/design/03-storage.md).
"""

from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import delete, exists, func, select
from sqlalchemy.orm import Session

from intelliw.businessdata import queries
from intelliw.businessdata.schema import COLLECTIONS, BusinessData, Version, VersionIndex
from intelliw.businessdata.tables import (
    ACTIVE,
    COLLECTION_ROWS,
    BusinessRow,
    SnapshotRow,
    VersionStateRow,
)

# Insert order that satisfies every foreign key (SQLite checks them per statement).
_INSERT_ORDER = [
    "assets",
    "contacts",
    "business",
    "locations",
    "service_categories",
    "services",
    "product_categories",
    "staff",
    "faqs",
    "social_links",
    "affiliations",
    "actions",
    "reviews",
]


def write_version(session: Session, data: BusinessData, version: int) -> None:
    """Insert every entity of `data` into `version`, which must be empty."""
    if session.scalar(select(exists().where(BusinessRow.version == version))):
        raise ValueError(f"version {version} already has data")
    for name in _INSERT_ORDER:
        if name == "business":
            session.add(BusinessRow(version=version, **data.business.model_dump()))
        else:
            row: Any = COLLECTION_ROWS[name]  # declarative constructor accepts column kwargs
            session.add_all(row(version=version, **e.model_dump()) for e in getattr(data, name))
        session.flush()


def read_version(session: Session, version: int = ACTIVE, *, validate: bool = True) -> BusinessData:
    """One version as a document, including hidden and deleted entities.

    With `validate=False` the document-level integrity check is skipped (each entity is
    still validated), so a damaged version can be inspected with `integrity_errors`.
    """
    snapshot = None
    if version != ACTIVE:
        row = session.get(SnapshotRow, version)
        if row is None:
            raise queries.NotFound(f"no snapshot {version}")
        snapshot = Version.model_validate(row)
    collections = {
        name: queries.list_entities(session, model, version, include_deleted=True)
        for name, model in COLLECTIONS.items()
    }
    fields = {
        "snapshot": snapshot,
        "business": queries.get_business(session, version),
        **collections,
    }
    if not validate:
        return BusinessData.model_construct(None, **fields)
    return BusinessData.model_validate(fields)


def initialize(session: Session, data: BusinessData, *, now: datetime | None = None) -> None:
    """Load the first document into an empty workspace database.

    Creates snapshot 1 and an active version based on it (01-businessdata, *Snapshot
    versioning*).
    """
    if session.get(VersionStateRow, 1) is not None:
        raise ValueError("the workspace database is already initialized")
    now = now or datetime.now(UTC)
    session.add(SnapshotRow(number=1, tag=None, parent=None, created_at=now))
    session.flush()
    write_version(session, data, ACTIVE)
    write_version(session, data, 1)
    session.add(VersionStateRow(id=1, based_on=1, modified=False))
    session.flush()


# ---- snapshots and rollback ------------------------------------------------------


class TagConflict(ValueError):
    """A snapshot tag is already in use."""


def _state(session: Session) -> VersionStateRow:
    state = session.get(VersionStateRow, 1)
    if state is None:
        raise queries.NotFound("the workspace has no versions (not initialized)")
    return state


def mark_modified(session: Session) -> None:
    """Record that the active version has changed since the snapshot it is based on."""
    _state(session).modified = True


def take_snapshot(
    session: Session, tag: str | None = None, *, now: datetime | None = None
) -> Version:
    """Freeze the active version into a new read-only snapshot.

    Raises `ValueError` for an invalid tag and `TagConflict` for a tag already in use.
    """
    state = _state(session)
    now = now or datetime.now(UTC)
    number = (session.scalar(select(func.max(SnapshotRow.number))) or 0) + 1
    try:
        snapshot = Version(number=number, tag=tag, parent=state.based_on, created_at=now)
    except ValidationError as exc:
        raise ValueError(f"invalid tag {tag!r}: tags are 1-100 characters, trimmed") from exc
    if tag is not None and session.scalar(select(exists().where(SnapshotRow.tag == tag))):
        raise TagConflict(f"tag {tag!r} is already in use")

    session.add(SnapshotRow(**snapshot.model_dump()))
    session.flush()
    write_version(session, read_version(session, ACTIVE), number)
    state.based_on, state.modified = number, False
    session.flush()
    return snapshot


def activate_version(
    session: Session, number: int, *, now: datetime | None = None
) -> tuple[VersionIndex, Version | None]:
    """Roll back: make the active version an exact copy of snapshot `number`.

    If the active version is modified, it is first saved as an untagged snapshot, which
    is returned (otherwise `None`). Snapshots themselves are never changed.
    """
    if session.get(SnapshotRow, number) is None:
        raise queries.NotFound(f"no snapshot {number}")
    auto = take_snapshot(session, now=now) if _state(session).modified else None
    data = read_version(session, number)

    # Replace the active version. Child and link rows go with their owners (ON DELETE
    # CASCADE); owners are deleted in reverse insert order so no reference dangles.
    for name in reversed(_INSERT_ORDER):
        row: Any = BusinessRow if name == "business" else COLLECTION_ROWS[name]
        session.execute(
            delete(row).where(row.version == ACTIVE).execution_options(synchronize_session=False)
        )
    session.expunge_all()  # drop stale objects of the deleted rows from the identity map
    write_version(session, data, ACTIVE)

    state = _state(session)
    state.based_on, state.modified = number, False
    session.flush()
    return queries.get_version_index(session), auto
