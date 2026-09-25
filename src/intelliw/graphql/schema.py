"""The GraphQL schema (docs/design/02-graphql-api.md).

Queries read through `businessdata.queries` (batched by the DataLoaders in `context`);
mutations call `businessdata.mutations` / `businessdata.documents`, one transaction each.
Domain errors become GraphQL errors with a machine-readable `extensions.code`.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import strawberry
from graphql import GraphQLError
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from strawberry.extensions import SchemaExtension

from intelliw.businessdata import documents, mutations, queries
from intelliw.businessdata import schema as m
from intelliw.businessdata.tables import ACTIVE
from intelliw.graphql import inputs as i
from intelliw.graphql import types as t
from intelliw.graphql.context import Context

ID = strawberry.ID

# ---- errors ---------------------------------------------------------------------------


def _error(code: str, message: str, **extra: Any) -> GraphQLError:
    return GraphQLError(message, extensions={"code": code, **extra})


@contextmanager
def api_errors() -> Iterator[None]:
    """Translate domain errors into GraphQL errors (02-graphql-api, *Errors*)."""
    try:
        yield
    except queries.NotFound as exc:
        raise _error("NOT_FOUND", str(exc)) from exc
    except mutations.InUse as exc:
        used_by = [r.model_dump() for r in exc.used_by]
        raise _error(exc.code, str(exc), usedBy=used_by) from exc
    except mutations.MutationError as exc:
        raise _error(exc.code, str(exc)) from exc
    except documents.TagConflict as exc:
        raise _error("CONFLICT", str(exc)) from exc
    except (ValueError, ValidationError, IntegrityError) as exc:
        raise _error("VALIDATION", str(exc)) from exc


def _ctx(info: strawberry.Info) -> Context:
    return info.context


def _version(info: strawberry.Info, version: int | None, snapshot: str | None) -> int:
    with api_errors():
        return queries.resolve_version(_ctx(info).session, version=version, snapshot=snapshot)


def _mutate[R](
    info: strawberry.Info,
    change: Callable[[Any], R],
    version: int | None = None,
    snapshot: str | None = None,
) -> R:
    """Run one mutation in its own transaction, on the active version only."""
    ctx = _ctx(info)
    session = ctx.session
    with api_errors():
        if queries.resolve_version(session, version=version, snapshot=snapshot) != ACTIVE:
            raise _error(
                "READ_ONLY", "snapshots are read-only; mutations change the active version"
            )
        try:
            result = change(session)
            session.commit()
        except BaseException:
            session.rollback()
            raise
    ctx.reset_loaders()
    return result


def _wrap_all(models: list[Any], v: int) -> list[Any]:
    return [t.wrap(x, v) for x in models]


def _delete_result(r: mutations.DeleteResult) -> t.DeleteResult:
    return t.DeleteResult(
        id=ID(r.id),
        cleared_references=[t.EntityRef.of(x) for x in r.cleared_references],
        dismissed_reviews=[ID(x) for x in r.dismissed_reviews],
        orphaned_assets=[ID(x) for x in r.orphaned_assets],
    )


def _restore_result(r: mutations.RestoreResult) -> t.RestoreResult:
    return t.RestoreResult(
        id=ID(r.id),
        cleared_references=[t.EntityRef.of(x) for x in r.cleared_references],
        demoted_primary=r.demoted_primary,
    )


def _active_version(index: m.VersionIndex) -> t.ActiveVersion:
    based_on = next(s for s in index.snapshots if s.number == index.based_on)
    return t.ActiveVersion(based_on=t.Snapshot.from_pydantic(based_on), modified=index.modified)


# ---- collections ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Spec:
    collection: str  # in the model / database
    entity: str  # GraphQL entity name: create<entity>, ...
    plural: str  # GraphQL collection name: reorder<plural>, list query
    model: type[m.Entity]
    create_input: Any
    patch_input: Any


SPECS = [
    Spec(
        "contacts",
        "ContactPoint",
        "ContactPoints",
        m.ContactPoint,
        i.ContactPointInput,
        i.ContactPointPatch,
    ),
    Spec("locations", "Location", "Locations", m.Location, i.LocationInput, i.LocationPatch),
    Spec(
        "service_categories",
        "ServiceCategory",
        "ServiceCategories",
        m.ServiceCategory,
        i.ServiceCategoryInput,
        i.ServiceCategoryPatch,
    ),
    Spec("services", "Service", "Services", m.Service, i.ServiceInput, i.ServicePatch),
    Spec(
        "product_categories",
        "ProductCategory",
        "ProductCategories",
        m.ProductCategory,
        i.ProductCategoryInput,
        i.ProductCategoryPatch,
    ),
    Spec("staff", "StaffMember", "Staff", m.StaffMember, i.StaffMemberInput, i.StaffMemberPatch),
    Spec("faqs", "Faq", "Faqs", m.Faq, i.FaqInput, i.FaqPatch),
    Spec(
        "social_links",
        "SocialLink",
        "SocialLinks",
        m.SocialLink,
        i.SocialLinkInput,
        i.SocialLinkPatch,
    ),
    Spec(
        "affiliations",
        "Affiliation",
        "Affiliations",
        m.Affiliation,
        i.AffiliationInput,
        i.AffiliationPatch,
    ),
    Spec(
        "actions",
        "CustomerAction",
        "Actions",
        m.CustomerAction,
        i.CustomerActionInput,
        i.CustomerActionPatch,
    ),
    Spec("assets", "Asset", "Assets", m.Asset, i.AssetInput, i.AssetPatch),
    Spec("reviews", "Review", "Reviews", m.ReviewItem, i.ReviewInput, i.ReviewPatch),
]


def _typed(fn: Callable[..., Any], annotations: dict[str, Any]) -> Callable[..., Any]:
    fn.__annotations__ = annotations
    return fn


VersionArgs = {"version": int | None, "snapshot": str | None}


def _collection_mutations(spec: Spec) -> dict[str, Any]:
    out_type = t.TYPES[spec.model]
    c = spec.collection

    def create(info, input, before=None, version=None, snapshot=None):  # noqa: A002
        ctx = _ctx(info)
        return t.wrap(
            _mutate(
                info,
                lambda s: mutations.create(
                    s, c, i.to_fields(input), before=before, resources_dir=ctx.resources_dir
                ),
                version,
                snapshot,
            ),
            ACTIVE,
        )

    def update(info, id, patch, version=None, snapshot=None):  # noqa: A002
        ctx = _ctx(info)
        return t.wrap(
            _mutate(
                info,
                lambda s: mutations.update(
                    s, c, id, i.to_fields(patch), resources_dir=ctx.resources_dir
                ),
                version,
                snapshot,
            ),
            ACTIVE,
        )

    def delete(info, id, version=None, snapshot=None):  # noqa: A002
        return _delete_result(
            _mutate(info, lambda s: mutations.delete(s, c, id), version, snapshot)
        )

    def restore(info, id, version=None, snapshot=None):  # noqa: A002
        return _restore_result(
            _mutate(info, lambda s: mutations.restore(s, c, id), version, snapshot)
        )

    def move(info, id, before=None, version=None, snapshot=None):  # noqa: A002
        return _wrap_all(
            _mutate(info, lambda s: mutations.move(s, c, id, before), version, snapshot), ACTIVE
        )

    def reorder(info, ids, version=None, snapshot=None):
        return _wrap_all(
            _mutate(info, lambda s: mutations.reorder(s, c, list(ids)), version, snapshot), ACTIVE
        )

    info_t = {"info": strawberry.Info}
    fields = {
        f"update{spec.entity}": _typed(
            update,
            {**info_t, "id": ID, "patch": spec.patch_input, **VersionArgs, "return": out_type},
        ),
        f"delete{spec.entity}": _typed(
            delete, {**info_t, "id": ID, **VersionArgs, "return": t.DeleteResult}
        ),
        f"restore{spec.entity}": _typed(
            restore, {**info_t, "id": ID, **VersionArgs, "return": t.RestoreResult}
        ),
        f"move{spec.entity}": _typed(
            move, {**info_t, "id": ID, "before": ID | None, **VersionArgs, "return": list[out_type]}
        ),
        f"reorder{spec.plural}": _typed(
            reorder, {**info_t, "ids": list[ID], **VersionArgs, "return": list[out_type]}
        ),
    }
    if spec.collection != "reviews":  # createReview(target, note) is defined separately
        fields[f"create{spec.entity}"] = _typed(
            create,
            {
                **info_t,
                "input": spec.create_input,
                "before": ID | None,
                **VersionArgs,
                "return": out_type,
            },
        )
    return {name: strawberry.mutation(resolver=fn, name=name) for name, fn in fields.items()}


def _by_id_query(spec: Spec) -> Any:
    out_type = t.TYPES[spec.model]
    name = spec.entity[0].lower() + spec.entity[1:]
    if spec.collection == "reviews":
        name = "reviewItem"

    def resolve(info, id, version=None, snapshot=None):  # noqa: A002
        v = _version(info, version, snapshot)
        found = queries.get_entity(_ctx(info).session, spec.model, v, id)
        return t.wrap(found, v) if found is not None else None

    return strawberry.field(
        resolver=_typed(
            resolve, {"info": strawberry.Info, "id": ID, **VersionArgs, "return": out_type | None}
        ),
        name=name,
    )


# ---- queries --------------------------------------------------------------------------------


@strawberry.type
class Hello:
    message: str


def _list(info, model, version, snapshot, hidden=None, **equals) -> list[Any]:
    v = _version(info, version, snapshot)
    with api_errors():
        found = queries.list_entities(_ctx(info).session, model, v, hidden=hidden, **equals)
    return _wrap_all(found, v)


@strawberry.type
class BaseQuery:
    @strawberry.field
    def hello(self) -> Hello:
        return Hello(message="hello world")

    @strawberry.field
    def business(
        self, info: strawberry.Info, version: int | None = None, snapshot: str | None = None
    ) -> t.Business:
        v = _version(info, version, snapshot)
        with api_errors():
            return t.wrap(queries.get_business(_ctx(info).session, v), v)

    @strawberry.field
    def contact_points(
        self,
        info: strawberry.Info,
        kind: m.ContactKind | None = None,
        hidden: bool | None = None,
        version: int | None = None,
        snapshot: str | None = None,
    ) -> list[t.ContactPoint]:
        equals = {"kind": kind} if kind is not None else {}
        return _list(info, m.ContactPoint, version, snapshot, hidden, **equals)

    @strawberry.field
    def locations(
        self,
        info: strawberry.Info,
        hidden: bool | None = None,
        version: int | None = None,
        snapshot: str | None = None,
    ) -> list[t.Location]:
        return _list(info, m.Location, version, snapshot, hidden)

    @strawberry.field
    def service_categories(
        self,
        info: strawberry.Info,
        hidden: bool | None = None,
        version: int | None = None,
        snapshot: str | None = None,
    ) -> list[t.ServiceCategory]:
        return _list(info, m.ServiceCategory, version, snapshot, hidden)

    @strawberry.field
    def services(
        self,
        info: strawberry.Info,
        category: ID | None = None,
        hidden: bool | None = None,
        version: int | None = None,
        snapshot: str | None = None,
    ) -> list[t.Service]:
        equals = {"category": category} if category is not None else {}
        return _list(info, m.Service, version, snapshot, hidden, **equals)

    @strawberry.field
    def product_categories(
        self,
        info: strawberry.Info,
        hidden: bool | None = None,
        version: int | None = None,
        snapshot: str | None = None,
    ) -> list[t.ProductCategory]:
        return _list(info, m.ProductCategory, version, snapshot, hidden)

    @strawberry.field
    def staff(
        self,
        info: strawberry.Info,
        hidden: bool | None = None,
        version: int | None = None,
        snapshot: str | None = None,
    ) -> list[t.StaffMember]:
        return _list(info, m.StaffMember, version, snapshot, hidden)

    @strawberry.field
    def faqs(
        self,
        info: strawberry.Info,
        service: ID | None = None,
        hidden: bool | None = None,
        version: int | None = None,
        snapshot: str | None = None,
    ) -> list[t.Faq]:
        v = _version(info, version, snapshot)
        if service is None:
            return _list(info, m.Faq, version, snapshot, hidden)
        (found,) = queries.faqs_by_service(_ctx(info).session, v, [service])
        return _wrap_all([f for f in found if hidden is None or f.hidden == hidden], v)

    @strawberry.field
    def social_links(
        self,
        info: strawberry.Info,
        hidden: bool | None = None,
        version: int | None = None,
        snapshot: str | None = None,
    ) -> list[t.SocialLink]:
        return _list(info, m.SocialLink, version, snapshot, hidden)

    @strawberry.field
    def affiliations(
        self,
        info: strawberry.Info,
        hidden: bool | None = None,
        version: int | None = None,
        snapshot: str | None = None,
    ) -> list[t.Affiliation]:
        return _list(info, m.Affiliation, version, snapshot, hidden)

    @strawberry.field
    def actions(
        self,
        info: strawberry.Info,
        hidden: bool | None = None,
        version: int | None = None,
        snapshot: str | None = None,
    ) -> list[t.CustomerAction]:
        return _list(info, m.CustomerAction, version, snapshot, hidden)

    @strawberry.field
    def assets(
        self,
        info: strawberry.Info,
        type: m.AssetType | None = None,  # noqa: A002
        unused: bool | None = None,
        version: int | None = None,
        snapshot: str | None = None,
    ) -> list[t.Asset]:
        v = _version(info, version, snapshot)
        session = _ctx(info).session
        found = queries.list_entities(session, m.Asset, v, **({"type": type} if type else {}))
        if unused is not None:
            usage = queries.asset_usage(session, v, [a.id for a in found])
            found = [a for a, used in zip(found, usage, strict=True) if (not used) == unused]
        return _wrap_all(found, v)

    @strawberry.field(description="Paths under businessdata/resources/.")
    def resource_files(
        self,
        info: strawberry.Info,
        unregistered: bool | None = None,
        version: int | None = None,
        snapshot: str | None = None,
    ) -> list[str]:
        ctx = _ctx(info)
        v = _version(info, version, snapshot)
        root = ctx.resources_dir
        if root is None or not root.is_dir():
            return []
        files = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
        if unregistered is None:
            return files
        used = {a.path for a in queries.list_entities(ctx.session, m.Asset, v)}
        return [f for f in files if (f not in used) == unregistered]

    @strawberry.field
    def reviews(
        self,
        info: strawberry.Info,
        status: m.ReviewStatus | None = m.ReviewStatus.open,
        version: int | None = None,
        snapshot: str | None = None,
    ) -> list[t.ReviewItem]:
        equals = {"status": status} if status is not None else {}
        return _list(info, m.ReviewItem, version, snapshot, **equals)

    @strawberry.field(description="Deleted entities awaiting purge, newest first.")
    def trash(
        self,
        info: strawberry.Info,
        collection: t.Collection | None = None,  # type: ignore[valid-type]
        version: int | None = None,
        snapshot: str | None = None,
    ) -> list[t.TrashItem]:
        v = _version(info, version, snapshot)
        name = collection.value if collection is not None else None
        with api_errors():
            items = queries.trash(_ctx(info).session, v, name)
        return [
            t.TrashItem(ref=t.EntityRef.of(x.ref), label=x.label, deleted_at=x.deleted_at)
            for x in items
        ]

    @strawberry.field(description="Read-only snapshots, newest first.")
    def snapshots(self, info: strawberry.Info) -> list[t.Snapshot]:
        return [t.Snapshot.from_pydantic(s) for s in queries.list_snapshots(_ctx(info).session)]

    @strawberry.field
    def active_version(self, info: strawberry.Info) -> t.ActiveVersion:
        with api_errors():
            return _active_version(queries.get_version_index(_ctx(info).session))


Query = strawberry.type(
    type("Query", (BaseQuery,), {spec.entity: _by_id_query(spec) for spec in SPECS})
)


# ---- mutations ------------------------------------------------------------------------------


@strawberry.type
class BaseMutation:
    @strawberry.mutation
    def update_business(
        self,
        info: strawberry.Info,
        patch: i.BusinessPatch,
        version: int | None = None,
        snapshot: str | None = None,
    ) -> t.Business:
        business = _mutate(
            info, lambda s: mutations.update_business(s, i.to_fields(patch)), version, snapshot
        )
        return t.wrap(business, ACTIVE)

    @strawberry.mutation(description="Replace one day's hours; other days are untouched.")
    def set_opening_hours(
        self,
        info: strawberry.Info,
        location: ID,
        day: m.Weekday,
        intervals: list[i.TimeRangeInput],
        closed: bool | None = None,
        version: int | None = None,
        snapshot: str | None = None,
    ) -> t.Location:
        spans = [(r.open, r.close) for r in intervals]
        result = _mutate(
            info,
            lambda s: mutations.set_opening_hours(s, location, day, spans, closed=bool(closed)),
            version,
            snapshot,
        )
        return t.wrap(result, ACTIVE)

    @strawberry.mutation(description="Raise a question for the #owner.")
    def create_review(
        self,
        info: strawberry.Info,
        target: i.EntityRefInput,
        note: str,
        version: int | None = None,
        snapshot: str | None = None,
    ) -> t.ReviewItem:
        ref = m.EntityRef.model_validate(i.to_fields(target))
        return t.wrap(
            _mutate(info, lambda s: mutations.create_review(s, ref, note), version, snapshot),
            ACTIVE,
        )

    @strawberry.mutation
    def resolve_review(
        self,
        info: strawberry.Info,
        id: ID,  # noqa: A002
        resolution: str,
        version: int | None = None,
        snapshot: str | None = None,
    ) -> t.ReviewItem:
        return t.wrap(
            _mutate(info, lambda s: mutations.resolve_review(s, id, resolution), version, snapshot),
            ACTIVE,
        )

    @strawberry.mutation
    def dismiss_review(
        self,
        info: strawberry.Info,
        id: ID,  # noqa: A002
        reason: str,
        version: int | None = None,
        snapshot: str | None = None,
    ) -> t.ReviewItem:
        return t.wrap(
            _mutate(info, lambda s: mutations.dismiss_review(s, id, reason), version, snapshot),
            ACTIVE,
        )

    @strawberry.mutation
    def reopen_review(
        self,
        info: strawberry.Info,
        id: ID,  # noqa: A002
        version: int | None = None,
        snapshot: str | None = None,
    ) -> t.ReviewItem:
        return t.wrap(
            _mutate(info, lambda s: mutations.reopen_review(s, id), version, snapshot), ACTIVE
        )

    @strawberry.mutation(description="Freeze the active version into a new read-only snapshot.")
    def take_snapshot(self, info: strawberry.Info, tag: str | None = None) -> t.Snapshot:
        return t.Snapshot.from_pydantic(_mutate(info, lambda s: documents.take_snapshot(s, tag)))

    @strawberry.mutation(description="Roll the active version back to a snapshot.")
    def activate_version(
        self, info: strawberry.Info, version: int | None = None, snapshot: str | None = None
    ) -> t.ActivateResult:
        if (version is None) == (snapshot is None):
            raise _error("VALIDATION", "give exactly one of version or snapshot")
        number = _version(info, version, snapshot)
        index, auto = _mutate(info, lambda s: documents.activate_version(s, number))
        return t.ActivateResult(
            active_version=_active_version(index),
            auto_snapshot=t.Snapshot.from_pydantic(auto) if auto is not None else None,
        )


_generated: dict[str, Any] = {}
for _spec in SPECS:
    _generated.update(_collection_mutations(_spec))
Mutation = strawberry.type(type("Mutation", (BaseMutation,), _generated))


class CloseSession(SchemaExtension):
    """Close the request's database session when the operation ends."""

    def on_operation(self) -> Iterator[None]:
        yield
        context = self.execution_context.context
        if isinstance(context, Context):
            context.close()


schema = strawberry.Schema(query=Query, mutation=Mutation, extensions=[CloseSession])
