"""Read access to #businessdata; every result is a Pydantic model from `schema`.

Designed for the GraphQL resolvers (docs/design/02-graphql-api.md):

- every function takes the version to read (`ACTIVE` or a snapshot number; see
  `resolve_version`), so nested resolvers stay in their parent's version;
- batch functions take a sequence of keys and return one result per key, in key order —
  the contract of a DataLoader load function — using one query per call;
- deleted entities are excluded unless `include_deleted=True`; hidden entities are
  included unless filtered with `hidden=`.
"""

from collections import defaultdict
from collections.abc import Hashable, Iterable, Sequence
from typing import Any

from sqlalchemy import Select, select, tuple_
from sqlalchemy.orm import Session

from intelliw.businessdata.schema import (
    COLLECTIONS,
    Business,
    CustomerAction,
    Entity,
    EntityRef,
    Faq,
    ReviewItem,
    ReviewStatus,
    Service,
    TrashItem,
    Version,
    VersionIndex,
)
from intelliw.businessdata.tables import (
    ACTIVE,
    COLLECTION_ROWS,
    ActionServiceRow,
    AffiliationRow,
    BusinessRow,
    CustomerActionRow,
    EntityRow,
    FaqRow,
    FaqServiceRow,
    HideableRow,
    ProductCategoryRow,
    ReviewItemRow,
    ServiceRow,
    SnapshotRow,
    StaffMemberRow,
    VersionStateRow,
)

__all__ = [
    "ACTIVE",
    "NotFound",
    "resolve_version",
    "get_version_index",
    "list_snapshots",
    "get_business",
    "list_entities",
    "get_entity",
    "get_entities",
    "services_by_category",
    "faqs_by_service",
    "actions_by_service",
    "actions_by_channel",
    "reviews_by_target",
    "asset_usage",
    "trash",
]


class NotFound(LookupError):
    """A version, snapshot tag or entity does not exist."""


# Entity model -> (collection name, row class)
_MODEL_TABLES: dict[type[Entity], tuple[str, type[EntityRow]]] = {
    model: (name, COLLECTION_ROWS[name]) for name, model in COLLECTIONS.items()
}


def _row_class(model: type[Entity]) -> type[EntityRow]:
    return _MODEL_TABLES[model][1]


# ---- versions -----------------------------------------------------------------------


def resolve_version(
    session: Session, *, version: int | None = None, snapshot: str | None = None
) -> int:
    """The version number selected by the API's `version` / `snapshot` arguments.

    Neither selects the active version (`ACTIVE`). Raises `ValueError` if both are given
    and `NotFound` if the snapshot does not exist.
    """
    if version is not None and snapshot is not None:
        raise ValueError("give either version or snapshot, not both")
    if version is None and snapshot is None:
        return ACTIVE
    stmt = select(SnapshotRow.number).where(
        SnapshotRow.number == version if version is not None else SnapshotRow.tag == snapshot
    )
    number = session.scalar(stmt)
    if number is None:
        raise NotFound(f"no snapshot {version if version is not None else repr(snapshot)}")
    return number


def list_snapshots(session: Session) -> list[Version]:
    """All snapshots, newest first."""
    rows = session.scalars(select(SnapshotRow).order_by(SnapshotRow.number.desc()))
    return [Version.model_validate(r) for r in rows]


def get_version_index(session: Session) -> VersionIndex:
    state = session.get(VersionStateRow, 1)
    if state is None:
        raise NotFound("the workspace has no versions (not initialized)")
    return VersionIndex(
        based_on=state.based_on, modified=state.modified, snapshots=list_snapshots(session)
    )


# ---- entities -----------------------------------------------------------------------


def get_business(session: Session, version: int = ACTIVE) -> Business:
    row = session.get(BusinessRow, version)
    if row is None:
        raise NotFound(f"no business in version {version}")
    return Business.model_validate(row)


def _entity_select[R: EntityRow](row: type[R], version: int, include_deleted: bool) -> Select[R]:
    stmt = select(row).where(row.version == version)
    if not include_deleted:
        stmt = stmt.where(row.deleted.is_(False))
    return stmt


def list_entities[E: Entity](
    session: Session,
    model: type[E],
    version: int = ACTIVE,
    *,
    hidden: bool | None = None,
    include_deleted: bool = False,
    **equals: Any,
) -> list[E]:
    """All entities of one collection, in position order.

    `hidden` filters content entities by their hidden flag; `equals` filters on columns,
    e.g. `list_entities(s, Service, category="eye-exams")`.
    """
    row = _row_class(model)
    stmt = _entity_select(row, version, include_deleted)
    if hidden is not None:
        if not issubclass(row, HideableRow):
            raise ValueError(f"{model.__name__} cannot be hidden")
        stmt = stmt.where(row.hidden.is_(hidden))
    for column, value in equals.items():
        stmt = stmt.where(getattr(row, column) == value)
    return [model.model_validate(r) for r in session.scalars(stmt.order_by(row.position))]


def get_entities[E: Entity](
    session: Session,
    model: type[E],
    version: int,
    ids: Sequence[str],
    *,
    include_deleted: bool = False,
) -> list[E | None]:
    """Entities by id, one per id in order; `None` for missing (or deleted) ids."""
    row = _row_class(model)
    stmt = _entity_select(row, version, include_deleted).where(row.id.in_(set(ids)))
    found = {r.id: model.model_validate(r) for r in session.scalars(stmt)}
    return [found.get(i) for i in ids]


def get_entity[E: Entity](
    session: Session, model: type[E], version: int, id: str, *, include_deleted: bool = False
) -> E | None:
    return get_entities(session, model, version, [id], include_deleted=include_deleted)[0]


# ---- reverse relations (batched) ---------------------------------------------------


def _grouped[K: Hashable, V](pairs: Iterable[tuple[K, V]], keys: Sequence[K]) -> list[list[V]]:
    groups: dict[K, list[V]] = defaultdict(list)
    for key, value in pairs:
        groups[key].append(value)
    return [groups.get(k, []) for k in keys]


def services_by_category(
    session: Session, version: int, category_ids: Sequence[str]
) -> list[list[Service]]:
    """`ServiceCategory.services`, per category id."""
    stmt = (
        _entity_select(ServiceRow, version, False)
        .where(ServiceRow.category.in_(set(category_ids)))
        .order_by(ServiceRow.position)
    )
    rows = session.scalars(stmt)
    return _grouped(((r.category, Service.model_validate(r)) for r in rows), category_ids)


def faqs_by_service(session: Session, version: int, service_ids: Sequence[str]) -> list[list[Faq]]:
    """`Service.faqs`, per service id, in FAQ position order."""
    stmt = (
        select(FaqServiceRow.service_id, FaqRow)
        .join(
            FaqRow,
            (FaqRow.version == FaqServiceRow.version) & (FaqRow.id == FaqServiceRow.faq_id),
        )
        .where(
            FaqServiceRow.version == version,
            FaqServiceRow.service_id.in_(set(service_ids)),
            FaqRow.deleted.is_(False),
        )
        .order_by(FaqRow.position)
    )
    rows = session.execute(stmt)
    return _grouped(((sid, Faq.model_validate(r)) for sid, r in rows), service_ids)


def actions_by_service(
    session: Session, version: int, service_ids: Sequence[str]
) -> list[list[CustomerAction]]:
    """`Service.actions`, per service id, in action position order."""
    stmt = (
        select(ActionServiceRow.service_id, CustomerActionRow)
        .join(
            CustomerActionRow,
            (CustomerActionRow.version == ActionServiceRow.version)
            & (CustomerActionRow.id == ActionServiceRow.action_id),
        )
        .where(
            ActionServiceRow.version == version,
            ActionServiceRow.service_id.in_(set(service_ids)),
            CustomerActionRow.deleted.is_(False),
        )
        .order_by(CustomerActionRow.position)
    )
    rows = session.execute(stmt)
    return _grouped(((sid, CustomerAction.model_validate(r)) for sid, r in rows), service_ids)


def actions_by_channel(
    session: Session, version: int, contact_ids: Sequence[str]
) -> list[list[CustomerAction]]:
    """`ContactPoint.usedBy`, per contact point id."""
    stmt = (
        _entity_select(CustomerActionRow, version, False)
        .where(CustomerActionRow.channel.in_(set(contact_ids)))
        .order_by(CustomerActionRow.position)
    )
    rows = session.scalars(stmt)
    return _grouped(((r.channel, CustomerAction.model_validate(r)) for r in rows), contact_ids)


def reviews_by_target(
    session: Session,
    version: int,
    targets: Sequence[tuple[str, str | None]],
    *,
    status: ReviewStatus | None = None,
) -> list[list[ReviewItem]]:
    """`<Entity>.reviews`, per (collection, id) target; id is None for the business."""
    stmt = _entity_select(ReviewItemRow, version, False).order_by(ReviewItemRow.position)
    ids = [t for t in targets if t[1] is not None]
    clauses = []
    if ids:
        clauses.append(tuple_(ReviewItemRow.target_collection, ReviewItemRow.target_id).in_(ids))
    if any(t[1] is None for t in targets):
        clauses.append(ReviewItemRow.target_collection == "business")
    if not clauses:
        return []
    stmt = stmt.where(clauses[0] if len(clauses) == 1 else clauses[0] | clauses[1])
    if status is not None:
        stmt = stmt.where(ReviewItemRow.status == status)
    rows = session.scalars(stmt)
    return _grouped(
        (((r.target_collection, r.target_id), ReviewItem.model_validate(r)) for r in rows),
        list(targets),
    )


# (collection, row class, column, GraphQL field) for every reference to an asset.
_ASSET_REFERENCES: list[tuple[str, type[Any], str, str]] = [
    ("business", BusinessRow, "logo", "logo"),
    ("business", BusinessRow, "favicon", "favicon"),
    ("services", ServiceRow, "image", "image"),
    ("product_categories", ProductCategoryRow, "image", "image"),
    ("staff", StaffMemberRow, "photo", "photo"),
    ("affiliations", AffiliationRow, "logo", "logo"),
]


def asset_usage(session: Session, version: int, asset_ids: Sequence[str]) -> list[list[EntityRef]]:
    """`Asset.usedBy`, per asset id: the non-deleted entities referencing it.

    An asset with no usage is unused (`assets(unused: true)`).
    """
    wanted = set(asset_ids)
    pairs: list[tuple[str, EntityRef]] = []
    for collection, row, column, field in _ASSET_REFERENCES:
        col = getattr(row, column)
        stmt: Select[Any] = select(row).where(row.version == version, col.in_(wanted))
        if row is not BusinessRow:
            stmt = stmt.where(row.deleted.is_(False))
        for r in session.scalars(stmt):
            entity_id = None if row is BusinessRow else r.id
            ref = EntityRef.model_validate(
                {"collection": collection, "id": entity_id, "field": field}
            )
            pairs.append((getattr(r, column), ref))
    return _grouped(pairs, asset_ids)


# ---- trash --------------------------------------------------------------------------

_LABEL_FIELDS = ("name", "label", "question", "path")


def _label(row: Any) -> str:
    return next((getattr(row, f) for f in _LABEL_FIELDS if hasattr(row, f)), row.id)


def trash(
    session: Session, version: int = ACTIVE, collection: str | None = None
) -> list[TrashItem]:
    """Deleted entities, newest first, optionally of one collection."""
    names = [collection] if collection is not None else list(COLLECTION_ROWS)
    items: list[TrashItem] = []
    for name in names:
        row = COLLECTION_ROWS[name]
        stmt = select(row).where(row.version == version, row.deleted.is_(True))
        for r in session.scalars(stmt):
            items.append(
                TrashItem(
                    ref=EntityRef.model_validate({"collection": name, "id": r.id}),
                    label=_label(r),
                    deleted_at=r.updated_at,
                )
            )
    return sorted(items, key=lambda item: item.deleted_at, reverse=True)
