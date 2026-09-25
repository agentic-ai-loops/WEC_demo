"""Changes to the active version of #businessdata (docs/design/02-graphql-api.md).

The write-side counterpart of `queries`: every function works on the active version,
validates the change against the rules of 01-businessdata and 02-graphql-api, keeps
`updated_at` and the version state (`documents.mark_modified`) current, and returns
Pydantic models from `schema`. Functions flush but never commit; the caller owns the
transaction, so a failed rule leaves nothing behind once it rolls back.

Failures raise `MutationError` subclasses (or `queries.NotFound`), each carrying the
error code the API reports.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError
from pydantic.alias_generators import to_camel
from sqlalchemy import select
from sqlalchemy.orm import Session

from intelliw.businessdata import documents, queries
from intelliw.businessdata.schema import (
    ACTION_CHANNEL_KINDS,
    COLLECTIONS,
    AssetType,
    Business,
    ContactKind,
    EntityRef,
    Location,
    OpeningHours,
    ReviewItem,
    ReviewStatus,
    Weekday,
    graphql_field_names,
)
from intelliw.businessdata.tables import (
    ACTIVE,
    COLLECTION_ROWS,
    ActionServiceRow,
    BusinessRow,
    FaqServiceRow,
    ReviewItemRow,
)

# ---- errors -----------------------------------------------------------------------


class MutationError(Exception):
    code = "VALIDATION"


class Invalid(MutationError):
    """A validation rule failed."""


class InUse(MutationError):
    """Deletion refused: other entities still need this one."""

    code = "IN_USE"

    def __init__(self, message: str, used_by: list[EntityRef]):
        super().__init__(message)
        self.used_by = used_by


class Conflict(MutationError):
    """An id or tag is already taken."""

    code = "CONFLICT"


class StaleOrder(MutationError):
    """A reorder list does not match the current ids."""

    code = "STALE_ORDER"


# ---- results ----------------------------------------------------------------------


@dataclass
class DeleteResult:
    id: str
    cleared_references: list[EntityRef] = field(default_factory=list)
    dismissed_reviews: list[str] = field(default_factory=list)
    orphaned_assets: list[str] = field(default_factory=list)


@dataclass
class RestoreResult:
    id: str
    cleared_references: list[EntityRef] = field(default_factory=list)
    demoted_primary: bool = False


# ---- references -------------------------------------------------------------------


@dataclass(frozen=True)
class Ref:
    """An entity field referring to an entity of another collection."""

    field: str
    target: str
    required: bool = False
    allowed: frozenset[str] | None = None  # allowed contact kinds / asset types


REFS: dict[str, list[Ref]] = {
    "business": [
        Ref("logo", "assets", allowed=frozenset({AssetType.logo})),
        Ref("favicon", "assets", allowed=frozenset({AssetType.favicon})),
    ],
    "locations": [Ref("phone", "contacts", allowed=frozenset({ContactKind.phone}))],
    "services": [Ref("category", "service_categories", required=True), Ref("image", "assets")],
    "product_categories": [Ref("image", "assets")],
    "staff": [Ref("photo", "assets")],
    "affiliations": [Ref("logo", "assets")],
    "actions": [Ref("channel", "contacts", required=True)],
}

# Many-to-many links: collection -> (list field, target collection, link row, owner column)
LINKS: dict[str, tuple[str, str, type[Any], str]] = {
    "faqs": ("services", "services", FaqServiceRow, "faq_id"),
    "actions": ("services", "services", ActionServiceRow, "action_id"),
}

# Field used to generate an id when a create gives none.
_ID_SOURCE = {
    "contacts": "label",
    "locations": "name",
    "service_categories": "name",
    "services": "name",
    "product_categories": "name",
    "staff": "name",
    "faqs": "question",
    "social_links": "platform",
    "affiliations": "name",
    "actions": "label",
    "assets": "path",
    "reviews": "note",
}

_SERVER_FIELDS = {"id", "position", "deleted", "created_at", "updated_at"}


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(UTC)


def _row_class(collection: str) -> Any:
    if collection not in COLLECTION_ROWS:
        raise Invalid(f"unknown collection '{collection}'")
    return COLLECTION_ROWS[collection]


def _rows(session: Session, collection: str) -> list[Any]:
    row = _row_class(collection)
    return list(session.scalars(select(row).where(row.version == ACTIVE).order_by(row.position)))


def _get_row(session: Session, collection: str, id: str, *, deleted: bool = False) -> Any:
    """The active version's row; `deleted` selects a deleted one instead of a live one."""
    row = session.get(_row_class(collection), (ACTIVE, id))
    if row is None or row.deleted != deleted:
        state = "deleted " if deleted else ""
        raise queries.NotFound(f"no {state}{collection} '{id}'")
    return row


def _live(session: Session, collection: str, id: str) -> Any | None:
    row = session.get(_row_class(collection), (ACTIVE, id))
    return row if row is not None and not row.deleted else None


def _model(collection: str, row: Any) -> Any:
    return COLLECTIONS[collection].model_validate(row)


def _validated[M: BaseModel](model: type[M], data: dict[str, Any]) -> M:
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(map(str, e['loc'])) or model.__name__}: {e['msg']}" for e in exc.errors()
        )
        raise Invalid(details) from exc


def _ref(collection: str, id: str | None, field: str | None = None) -> EntityRef:
    return EntityRef.model_validate(
        {"collection": collection, "id": id, "field": to_camel(field) if field else None}
    )


def _slug(text: str) -> str:
    words = re.sub(r"[^a-z0-9]+", " ", text.lower()).split()
    return "-".join(words[:8])[:60].strip("-") or "item"


# ---- validation -------------------------------------------------------------------


def _check_refs(session: Session, collection: str, entity: BaseModel) -> None:
    for ref in REFS.get(collection, []):
        value = getattr(entity, ref.field)
        if value is None:
            continue
        target = _live(session, ref.target, value)
        if target is None:
            raise Invalid(f"{ref.field}: no {ref.target} '{value}'")
        if ref.allowed is not None:
            kind = target.kind if ref.target == "contacts" else target.type
            if kind not in ref.allowed:
                raise Invalid(f"{ref.field}: '{value}' is a {kind}, not a {'/'.join(ref.allowed)}")
    if collection in LINKS:
        list_field, target_collection, *_ = LINKS[collection]
        for value in getattr(entity, list_field):
            if _live(session, target_collection, value) is None:
                raise Invalid(f"{list_field}: no {target_collection} '{value}'")
        if len(set(getattr(entity, list_field))) != len(getattr(entity, list_field)):
            raise Invalid(f"{list_field}: duplicate ids")
    get = entity.__getattribute__
    if collection == "actions":
        channel = _live(session, "contacts", get("channel"))
        if channel is not None and channel.kind not in ACTION_CHANNEL_KINDS[get("type")]:
            raise Invalid(f"a {get('type')} action cannot use a {channel.kind} channel")
    if collection == "contacts" and get("primary"):
        for other in _rows(session, "contacts"):
            if (
                not other.deleted
                and other.id != get("id")
                and other.primary
                and other.kind == get("kind")
            ):
                raise Invalid(f"'{other.id}' is already the primary {other.kind}")
    if collection == "reviews":
        _check_target(session, get("target"))


def _check_target(session: Session, target: EntityRef) -> None:
    if target.collection == "reviews":
        raise Invalid("a review item cannot target a review item")
    if target.collection == "business":
        model: type[BaseModel] = Business
    else:
        if target.id is None or _live(session, target.collection, target.id) is None:
            raise Invalid(f"target: no {target.collection} '{target.id}'")
        model = COLLECTIONS[target.collection]
    if target.field is not None and target.field not in graphql_field_names(model):
        raise Invalid(f"target: {target.collection} has no field '{target.field}'")


def _check_asset_file(path: str, resources_dir: Path | None) -> None:
    if resources_dir is not None and not (resources_dir / path).is_file():
        raise Invalid(f"path: no file '{path}' under businessdata/resources/")


def _check_channel_users(session: Session, contact_id: str, kind: ContactKind) -> None:
    """A contact point's kind may only change if every action using it still fits."""
    for action in _rows(session, "actions"):
        if not action.deleted and action.channel == contact_id:
            if kind not in ACTION_CHANNEL_KINDS[action.type]:
                raise Invalid(f"action '{action.id}' ({action.type}) cannot use a {kind} channel")


def _check_asset_type_users(session: Session, asset_id: str, type_: AssetType) -> None:
    business = session.get(BusinessRow, ACTIVE)
    for ref in REFS["business"]:
        if business is not None and getattr(business, ref.field) == asset_id:
            if ref.allowed is not None and type_ not in ref.allowed:
                raise Invalid(
                    f"asset '{asset_id}' is the business {ref.field}; it must stay a {ref.field}"
                )


# ---- ordering ---------------------------------------------------------------------


def _live_order(session: Session, collection: str) -> list[str]:
    return [r.id for r in _rows(session, collection) if not r.deleted]


def _renumber(session: Session, collection: str, live_order: Sequence[str]) -> None:
    """Live entities take positions 0..k-1 in `live_order`; deleted ones follow."""
    rows = _rows(session, collection)
    by_id = {r.id: r for r in rows}
    for position, id in enumerate(live_order):
        by_id[id].position = position
    for position, row in enumerate((r for r in rows if r.deleted), start=len(live_order)):
        row.position = position
    session.flush()


def _entities(session: Session, collection: str) -> list[Any]:
    return [_model(collection, r) for r in _rows(session, collection) if not r.deleted]


def _insert_before(order: list[str], id: str, before: str | None, collection: str) -> list[str]:
    order = [i for i in order if i != id]
    if before is None:
        return [*order, id]
    if before not in order:
        raise queries.NotFound(f"no {collection} '{before}'")
    index = order.index(before)
    return [*order[:index], id, *order[index:]]


# ---- generic entity mutations -------------------------------------------------------


def create(
    session: Session,
    collection: str,
    fields: dict[str, Any],
    *,
    before: str | None = None,
    resources_dir: Path | None = None,
    now: datetime | None = None,
) -> Any:
    """Add an entity at the end of its collection, or ahead of `before`."""
    row_cls = _row_class(collection)
    now = _now(now)
    taken = {r.id for r in _rows(session, collection)}  # deleted ids count (rule 12)
    id = fields.get("id")
    if id is not None:
        if id in taken:
            raise Conflict(f"{collection} id '{id}' is already taken")
    else:
        source = str(fields.get(_ID_SOURCE[collection]) or fields.get("value") or collection)
        base = _slug(Path(source).stem if collection == "assets" else source)
        id, n = base, 2
        while id in taken:
            id, n = f"{base}-{n}", n + 1

    data = {k: v for k, v in fields.items() if k not in _SERVER_FIELDS}
    entity: Any = _validated(
        COLLECTIONS[collection],
        {**data, "id": id, "position": len(taken), "created_at": now, "updated_at": now},
    )
    _check_refs(session, collection, entity)
    if collection == "assets":
        _check_asset_file(entity.path, resources_dir)

    order = _insert_before(_live_order(session, collection), id, before, collection)
    session.add(row_cls(version=ACTIVE, **entity.model_dump()))
    session.flush()
    _renumber(session, collection, order)
    documents.mark_modified(session)
    return _model(collection, _get_row(session, collection, id))


def update(
    session: Session,
    collection: str,
    id: str,
    patch: dict[str, Any],
    *,
    resources_dir: Path | None = None,
    now: datetime | None = None,
) -> Any:
    """Partial update: keys in `patch` are set (None clears); other fields are kept."""
    if forbidden := set(patch) & _SERVER_FIELDS:
        raise Invalid(f"cannot set {', '.join(sorted(forbidden))}")
    row = _get_row(session, collection, id)
    current = _model(collection, row)
    entity: Any = _validated(COLLECTIONS[collection], {**current.model_dump(), **patch})
    _check_refs(session, collection, entity)
    if collection == "assets":
        if "path" in patch:
            _check_asset_file(entity.path, resources_dir)
        if entity.type != current.type:
            _check_asset_type_users(session, id, entity.type)
    if collection == "contacts" and entity.kind != current.kind:
        _check_channel_users(session, id, entity.kind)

    new_values = entity.model_dump()
    if any(new_values[k] != current.model_dump()[k] for k in patch):
        for key in patch:
            setattr(row, key, new_values[key])
        row.updated_at = _now(now)
        session.flush()
    documents.mark_modified(session)
    return _model(collection, row)


def delete(
    session: Session, collection: str, id: str, *, now: datetime | None = None
) -> DeleteResult:
    """Mark an entity deleted, applying the delete rules of 02-graphql-api."""
    now = _now(now)
    row = _get_row(session, collection, id)
    result = DeleteResult(id=id)

    # Entities referring to this one: refuse (required reference) or clear (optional).
    users: list[EntityRef] = []
    to_clear: list[tuple[str, Any, Ref]] = []
    for owner, refs in REFS.items():
        for ref in (r for r in refs if r.target == collection):
            owners = (
                [session.get(BusinessRow, ACTIVE)] if owner == "business" else _rows(session, owner)
            )
            for other in owners:
                if other is None or getattr(other, "deleted", False):
                    continue
                if getattr(other, ref.field) == id:
                    other_id = None if owner == "business" else other.id
                    if ref.required:
                        users.append(_ref(owner, other_id))
                    else:
                        to_clear.append((owner, other, ref))
    if users:
        names = ", ".join(f"{u.collection} '{u.id}'" for u in users)
        raise InUse(f"{collection} '{id}' is used by {names}", users)

    for owner, other, ref in to_clear:
        setattr(other, ref.field, None)
        other.updated_at = now
        result.cleared_references.append(
            _ref(owner, None if owner == "business" else other.id, ref.field)
        )
    for owner, (list_field, target, *_rest) in LINKS.items():
        if target != collection:
            continue
        for other in _rows(session, owner):
            if not other.deleted and id in other.services:
                other.services = [s for s in other.services if s != id]
                other.updated_at = now
                result.cleared_references.append(_ref(owner, other.id, list_field))

    own_assets = [
        getattr(row, ref.field)
        for ref in REFS.get(collection, [])
        if ref.target == "assets" and getattr(row, ref.field)
    ]
    row.deleted, row.updated_at = True, now
    result.dismissed_reviews = _dismiss_reviews(session, collection, id, now)
    session.flush()
    usage = queries.asset_usage(session, ACTIVE, own_assets)
    result.orphaned_assets = [a for a, used in zip(own_assets, usage, strict=True) if not used]
    documents.mark_modified(session)
    return result


def _dismiss_reviews(session: Session, collection: str, id: str, now: datetime) -> list[str]:
    stmt = select(ReviewItemRow).where(
        ReviewItemRow.version == ACTIVE,
        ReviewItemRow.deleted.is_(False),
        ReviewItemRow.status == ReviewStatus.open,
        ReviewItemRow.target_collection == collection,
        ReviewItemRow.target_id == id,
    )
    dismissed = []
    for review in session.scalars(stmt):
        review.status, review.resolution, review.updated_at = (
            ReviewStatus.dismissed,
            "target deleted",
            now,
        )
        dismissed.append(review.id)
    return dismissed


def restore(
    session: Session, collection: str, id: str, *, now: datetime | None = None
) -> RestoreResult:
    """Undelete an entity; it goes to the end of its collection."""
    now = _now(now)
    row = _get_row(session, collection, id, deleted=True)
    result = RestoreResult(id=id)

    for ref in REFS.get(collection, []):
        value = getattr(row, ref.field)
        if value is None or _live(session, ref.target, value) is not None:
            continue
        if ref.required:
            raise Invalid(f"{ref.field}: {ref.target} '{value}' is deleted; restore it first")
        setattr(row, ref.field, None)
        result.cleared_references.append(_ref(collection, id, ref.field))
    if collection in LINKS:
        list_field, target, *_rest = LINKS[collection]
        kept = [v for v in row.services if _live(session, target, v) is not None]
        if kept != row.services:
            row.services = kept
            result.cleared_references.append(_ref(collection, id, list_field))
    if collection == "reviews":
        try:
            _check_target(session, _model(collection, row).target)
        except Invalid as exc:
            raise Invalid(f"{exc}; restore the target first") from exc
    if collection == "contacts" and row.primary:
        if any(
            not o.deleted and o.primary and o.kind == row.kind and o.id != id
            for o in _rows(session, "contacts")
        ):
            row.primary, result.demoted_primary = False, True

    order = [i for i in _live_order(session, collection) if i != id]
    row.deleted, row.updated_at = False, now
    session.flush()
    _renumber(session, collection, [*order, id])
    documents.mark_modified(session)
    return result


def move(session: Session, collection: str, id: str, before: str | None) -> list[Any]:
    """Move an entity ahead of `before` (None: to the end). Returns the collection."""
    _get_row(session, collection, id)
    if before == id:
        raise Invalid("an entity cannot be moved before itself")
    _renumber(
        session,
        collection,
        _insert_before(_live_order(session, collection), id, before, collection),
    )
    documents.mark_modified(session)
    return _entities(session, collection)


def reorder(session: Session, collection: str, ids: Sequence[str]) -> list[Any]:
    """Set the complete order; `ids` must be exactly the current live ids."""
    current = _live_order(session, collection)
    if len(ids) != len(set(ids)) or set(ids) != set(current):
        raise StaleOrder(f"the list must contain each current {collection} id exactly once")
    _renumber(session, collection, ids)
    documents.mark_modified(session)
    return _entities(session, collection)


# ---- special mutations ----------------------------------------------------------------


def update_business(
    session: Session, patch: dict[str, Any], *, now: datetime | None = None
) -> Business:
    if forbidden := set(patch) & {"created_at", "updated_at"}:
        raise Invalid(f"cannot set {', '.join(sorted(forbidden))}")
    row = session.get(BusinessRow, ACTIVE)
    if row is None:
        raise queries.NotFound("no business in the active version")
    current = Business.model_validate(row)
    business = _validated(Business, {**current.model_dump(), **patch})
    _check_refs(session, "business", business)
    new_values = business.model_dump()
    if any(new_values[k] != current.model_dump()[k] for k in patch):
        for key in patch:
            setattr(row, key, new_values[key])
        row.updated_at = _now(now)
        session.flush()
    documents.mark_modified(session)
    return Business.model_validate(row)


def set_opening_hours(
    session: Session,
    location_id: str,
    day: Weekday,
    intervals: Sequence[tuple[Any, Any]],
    *,
    closed: bool = False,
    now: datetime | None = None,
) -> Location:
    """Replace one day's hours; other days are untouched.

    `closed=True` marks the day closed; no intervals and not closed removes the day.
    """
    if closed and intervals:
        raise Invalid("a closed day cannot have intervals")
    row = _get_row(session, "locations", location_id)
    location: Location = _model("locations", row)
    others = [h for h in location.hours if h.day != day]
    if closed:
        today = [OpeningHours(day=day, closed=True)]
    else:
        today = [
            _validated(OpeningHours, {"day": day, "seq": i, "open": o, "close": c})
            for i, (o, c) in enumerate(intervals)
        ]
    order = list(Weekday)
    hours = sorted([*others, *today], key=lambda h: (order.index(h.day), h.seq))
    _validated(Location, {**location.model_dump(), "hours": [h.model_dump() for h in hours]})
    row.hours = hours
    row.updated_at = _now(now)
    session.flush()
    documents.mark_modified(session)
    return _model("locations", row)


def create_review(
    session: Session, target: EntityRef, note: str, *, now: datetime | None = None
) -> ReviewItem:
    return create(session, "reviews", {"target": target.model_dump(), "note": note}, now=now)


def _set_review_status(
    session: Session, id: str, status: ReviewStatus, resolution: str | None, now: datetime | None
) -> ReviewItem:
    row = _get_row(session, "reviews", id)
    if status is ReviewStatus.open:
        _check_target(session, _model("reviews", row).target)
    row.status, row.resolution, row.updated_at = status, resolution, _now(now)
    session.flush()
    documents.mark_modified(session)
    return _model("reviews", row)


def resolve_review(
    session: Session, id: str, resolution: str, *, now: datetime | None = None
) -> ReviewItem:
    return _set_review_status(session, id, ReviewStatus.resolved, resolution, now)


def dismiss_review(
    session: Session, id: str, reason: str, *, now: datetime | None = None
) -> ReviewItem:
    return _set_review_status(session, id, ReviewStatus.dismissed, reason, now)


def reopen_review(session: Session, id: str, *, now: datetime | None = None) -> ReviewItem:
    return _set_review_status(session, id, ReviewStatus.open, None, now)


__all__ = [
    "MutationError",
    "Invalid",
    "InUse",
    "Conflict",
    "StaleOrder",
    "DeleteResult",
    "RestoreResult",
    "REFS",
    "LINKS",
    "create",
    "update",
    "delete",
    "restore",
    "move",
    "reorder",
    "update_business",
    "set_opening_hours",
    "create_review",
    "resolve_review",
    "dismiss_review",
    "reopen_review",
]
