"""GraphQL output types (docs/design/02-graphql-api.md, *Types*).

Field types come from the Pydantic models in `intelliw.businessdata.schema` via
`strawberry.experimental.pydantic` (`strawberry.auto`); only reference fields are written
here, as resolvers that load the referenced entity. Every object carries its Pydantic
model and the version it was read from (both private), so nested fields resolve in their
parent's version.
"""

from datetime import datetime
from enum import Enum
from typing import Annotated, Any, get_args

import strawberry
from pydantic import BaseModel
from strawberry.experimental.pydantic import type as pydantic_type

from intelliw.businessdata import queries
from intelliw.businessdata import schema as m
from intelliw.businessdata.tables import ACTIVE

# Enums become GraphQL enums with the model's values.
for _enum in (
    m.ContactKind,
    m.Weekday,
    m.PaymentMethod,
    m.ActionType,
    m.AssetType,
    m.SocialPlatform,
    m.ReviewStatus,
):
    strawberry.enum(_enum, description=_enum.__doc__)

Collection = strawberry.enum(
    Enum("Collection", {name: name for name in get_args(m.Collection)}),  # type: ignore[misc]
    name="Collection",
    description="A kind of entity, as named in review targets and references.",
)


def _doc(model: type[BaseModel], field: str) -> str | None:
    """The description of a model field: reference fields reuse it for the resolved entity."""
    return model.model_fields[field].description


def _context(info: strawberry.Info) -> Any:
    return info.context


def _lazy(name: str) -> Any:
    """A reference to a type defined later in this module (the types are mutually recursive)."""
    return Annotated[name, strawberry.lazy("intelliw.graphql.types")]


def _version_field(v: int) -> int | None:
    return None if v == ACTIVE else v


# ---- nested values -------------------------------------------------------------------


@pydantic_type(model=m.Address, all_fields=True, description=m.Address.__doc__)
class Address:
    pass


@pydantic_type(model=m.GeoPoint, all_fields=True, description=m.GeoPoint.__doc__)
class GeoPoint:
    pass


@pydantic_type(model=m.OpeningHours, all_fields=True, description=m.OpeningHours.__doc__)
class OpeningHours:
    pass


@strawberry.type
class EntityRef:
    collection: Collection  # type: ignore[valid-type]
    id: strawberry.ID | None
    field: str | None

    @classmethod
    def of(cls, ref: m.EntityRef) -> "EntityRef":
        return cls(
            collection=Collection[ref.collection],  # type: ignore[index]
            id=strawberry.ID(ref.id) if ref.id is not None else None,
            field=ref.field,
        )


# ---- entities ------------------------------------------------------------------------


@strawberry.type
class _Wrapped:
    """Private state and the `version` field shared by entity types."""

    model: strawberry.Private[Any] = None
    v: strawberry.Private[int] = ACTIVE

    @strawberry.field(description="Snapshot the entity was read from; null = active version.")
    def version(self) -> int | None:
        return _version_field(self.v)


async def _load(info: strawberry.Info, model: type[m.Entity], v: int, id: str | None) -> Any:
    if id is None:
        return None
    found = await _context(info).loaders.entity(model).load((v, id))
    return wrap(found, v) if found is not None else None


async def _reviews(info: strawberry.Info, v: int, collection: str, id: str | None) -> list[Any]:
    found = await _context(info).loaders.reviews_by_target.load((v, (collection, id)))
    return [wrap(r, v) for r in found]


@pydantic_type(model=m.Asset, description=m.Asset.__doc__)
class Asset(_Wrapped):
    id: strawberry.ID
    type: strawberry.auto
    path: strawberry.auto
    alt: strawberry.auto
    created_at: strawberry.auto
    updated_at: strawberry.auto

    @strawberry.field(description="Non-deleted entities referring to this asset.")
    async def used_by(self, info: strawberry.Info) -> list[EntityRef]:
        found = await _context(info).loaders.asset_usage.load((self.v, self.model.id))
        return [EntityRef.of(r) for r in found]


@pydantic_type(model=m.Business, description=m.Business.__doc__)
class Business(_Wrapped):
    name: strawberry.auto
    legal_name: strawberry.auto
    tagline: strawberry.auto
    category: strawberry.auto
    description: strawberry.auto
    brands: strawberry.auto
    service_areas: strawberry.auto
    serving_since: strawberry.auto
    payment_methods: strawberry.auto
    created_at: strawberry.auto
    updated_at: strawberry.auto

    @strawberry.field(description=_doc(m.Business, "logo"))
    async def logo(self, info: strawberry.Info) -> Asset | None:
        return await _load(info, m.Asset, self.v, self.model.logo)

    @strawberry.field(description=_doc(m.Business, "favicon"))
    async def favicon(self, info: strawberry.Info) -> Asset | None:
        return await _load(info, m.Asset, self.v, self.model.favicon)

    @strawberry.field(description="Review items (questions for the owner) about this.")
    async def reviews(self, info: strawberry.Info) -> list[_lazy("ReviewItem")]:
        return await _reviews(info, self.v, "business", None)


@pydantic_type(model=m.ContactPoint, description=m.ContactPoint.__doc__)
class ContactPoint(_Wrapped):
    id: strawberry.ID
    kind: strawberry.auto
    label: strawberry.auto
    value: strawberry.auto
    primary: strawberry.auto
    hidden: strawberry.auto
    created_at: strawberry.auto
    updated_at: strawberry.auto

    @strawberry.field(description="Link for the contact point: tel:, mailto: or its URL.")
    def href(self) -> str:
        return self.model.href

    @strawberry.field(description="Customer actions using this contact point as their channel.")
    async def used_by(self, info: strawberry.Info) -> list[_lazy("CustomerAction")]:
        found = await _context(info).loaders.actions_by_channel.load((self.v, self.model.id))
        return [wrap(a, self.v) for a in found]

    @strawberry.field(description="Review items (questions for the owner) about this.")
    async def reviews(self, info: strawberry.Info) -> list[_lazy("ReviewItem")]:
        return await _reviews(info, self.v, "contacts", self.model.id)


@pydantic_type(model=m.Location, description=m.Location.__doc__)
class Location(_Wrapped):
    id: strawberry.ID
    name: strawberry.auto
    address: strawberry.auto
    geo: strawberry.auto
    map_url: strawberry.auto
    hours: strawberry.auto
    hidden: strawberry.auto
    created_at: strawberry.auto
    updated_at: strawberry.auto

    @strawberry.field(description=_doc(m.Location, "phone"))
    async def phone(self, info: strawberry.Info) -> ContactPoint | None:
        return await _load(info, m.ContactPoint, self.v, self.model.phone)

    @strawberry.field(description="Review items (questions for the owner) about this.")
    async def reviews(self, info: strawberry.Info) -> list[_lazy("ReviewItem")]:
        return await _reviews(info, self.v, "locations", self.model.id)


@pydantic_type(model=m.ServiceCategory, description=m.ServiceCategory.__doc__)
class ServiceCategory(_Wrapped):
    id: strawberry.ID
    name: strawberry.auto
    hidden: strawberry.auto
    created_at: strawberry.auto
    updated_at: strawberry.auto

    @strawberry.field(description="Services in this category.")
    async def services(self, info: strawberry.Info) -> list[_lazy("Service")]:
        found = await _context(info).loaders.services_by_category.load((self.v, self.model.id))
        return [wrap(s, self.v) for s in found]


@pydantic_type(model=m.Service, description=m.Service.__doc__)
class Service(_Wrapped):
    id: strawberry.ID
    name: strawberry.auto
    summary: strawberry.auto
    description: strawberry.auto
    audience: strawberry.auto
    hidden: strawberry.auto
    created_at: strawberry.auto
    updated_at: strawberry.auto

    @strawberry.field(description=_doc(m.Service, "category"))
    async def category(self, info: strawberry.Info) -> ServiceCategory:
        return await _load(info, m.ServiceCategory, self.v, self.model.category)

    @strawberry.field(description=_doc(m.Service, "image"))
    async def image(self, info: strawberry.Info) -> Asset | None:
        return await _load(info, m.Asset, self.v, self.model.image)

    @strawberry.field(description="FAQs about this service.")
    async def faqs(self, info: strawberry.Info) -> list[_lazy("Faq")]:
        found = await _context(info).loaders.faqs_by_service.load((self.v, self.model.id))
        return [wrap(f, self.v) for f in found]

    @strawberry.field(description="Customer actions that apply to this service.")
    async def actions(self, info: strawberry.Info) -> list[_lazy("CustomerAction")]:
        found = await _context(info).loaders.actions_by_service.load((self.v, self.model.id))
        return [wrap(a, self.v) for a in found]

    @strawberry.field(description="Review items (questions for the owner) about this.")
    async def reviews(self, info: strawberry.Info) -> list[_lazy("ReviewItem")]:
        return await _reviews(info, self.v, "services", self.model.id)


@pydantic_type(model=m.ProductCategory, description=m.ProductCategory.__doc__)
class ProductCategory(_Wrapped):
    id: strawberry.ID
    name: strawberry.auto
    description: strawberry.auto
    hidden: strawberry.auto
    created_at: strawberry.auto
    updated_at: strawberry.auto

    @strawberry.field(description=_doc(m.ProductCategory, "image"))
    async def image(self, info: strawberry.Info) -> Asset | None:
        return await _load(info, m.Asset, self.v, self.model.image)

    @strawberry.field(description="Review items (questions for the owner) about this.")
    async def reviews(self, info: strawberry.Info) -> list[_lazy("ReviewItem")]:
        return await _reviews(info, self.v, "product_categories", self.model.id)


@pydantic_type(model=m.StaffMember, description=m.StaffMember.__doc__)
class StaffMember(_Wrapped):
    id: strawberry.ID
    name: strawberry.auto
    role: strawberry.auto
    credentials: strawberry.auto
    bio: strawberry.auto
    languages: strawberry.auto
    hidden: strawberry.auto
    created_at: strawberry.auto
    updated_at: strawberry.auto

    @strawberry.field(description=_doc(m.StaffMember, "photo"))
    async def photo(self, info: strawberry.Info) -> Asset | None:
        return await _load(info, m.Asset, self.v, self.model.photo)

    @strawberry.field(description="Review items (questions for the owner) about this.")
    async def reviews(self, info: strawberry.Info) -> list[_lazy("ReviewItem")]:
        return await _reviews(info, self.v, "staff", self.model.id)


@pydantic_type(model=m.Faq, description=m.Faq.__doc__)
class Faq(_Wrapped):
    id: strawberry.ID
    question: strawberry.auto
    answer: strawberry.auto
    hidden: strawberry.auto
    created_at: strawberry.auto
    updated_at: strawberry.auto

    @strawberry.field(description=_doc(m.Faq, "services"))
    async def services(self, info: strawberry.Info) -> list[Service]:
        return [await _load(info, m.Service, self.v, sid) for sid in self.model.services]


@pydantic_type(model=m.SocialLink, description=m.SocialLink.__doc__)
class SocialLink(_Wrapped):
    id: strawberry.ID
    platform: strawberry.auto
    url: strawberry.auto
    label: strawberry.auto
    hidden: strawberry.auto
    created_at: strawberry.auto
    updated_at: strawberry.auto


@pydantic_type(model=m.Affiliation, description=m.Affiliation.__doc__)
class Affiliation(_Wrapped):
    id: strawberry.ID
    name: strawberry.auto
    description: strawberry.auto
    url: strawberry.auto
    hidden: strawberry.auto
    created_at: strawberry.auto
    updated_at: strawberry.auto

    @strawberry.field(description=_doc(m.Affiliation, "logo"))
    async def logo(self, info: strawberry.Info) -> Asset | None:
        return await _load(info, m.Asset, self.v, self.model.logo)


@pydantic_type(model=m.CustomerAction, description=m.CustomerAction.__doc__)
class CustomerAction(_Wrapped):
    id: strawberry.ID
    type: strawberry.auto
    label: strawberry.auto
    hidden: strawberry.auto
    created_at: strawberry.auto
    updated_at: strawberry.auto

    @strawberry.field(description=_doc(m.CustomerAction, "channel"))
    async def channel(self, info: strawberry.Info) -> ContactPoint:
        return await _load(info, m.ContactPoint, self.v, self.model.channel)

    @strawberry.field(description="The channel's link: tel:, mailto: or URL.")
    async def href(self, info: strawberry.Info) -> str:
        channel = (
            await _context(info).loaders.entity(m.ContactPoint).load((self.v, self.model.channel))
        )
        return channel.href if channel is not None else ""

    @strawberry.field(description=_doc(m.CustomerAction, "services"))
    async def services(self, info: strawberry.Info) -> list[Service]:
        return [await _load(info, m.Service, self.v, sid) for sid in self.model.services]


ReviewTargetEntity = Annotated[
    Business
    | ContactPoint
    | Location
    | ServiceCategory
    | Service
    | ProductCategory
    | StaffMember
    | Faq
    | SocialLink
    | Affiliation
    | CustomerAction
    | Asset,
    strawberry.union("ReviewTargetEntity"),
]


@strawberry.type
class ReviewTarget:
    collection: Collection  # type: ignore[valid-type]
    id: strawberry.ID | None
    field: str | None
    v: strawberry.Private[int] = ACTIVE

    @strawberry.field(description="The target entity; null if it no longer exists.")
    async def entity(self, info: strawberry.Info) -> ReviewTargetEntity | None:
        collection = self.collection.value
        if collection == "business":
            try:
                return wrap(queries.get_business(_context(info).session, self.v), self.v)
            except queries.NotFound:
                return None
        return await _load(info, m.COLLECTIONS[collection], self.v, self.id)


@pydantic_type(model=m.ReviewItem, description=m.ReviewItem.__doc__)
class ReviewItem(_Wrapped):
    id: strawberry.ID
    note: strawberry.auto
    status: strawberry.auto
    resolution: strawberry.auto
    created_at: strawberry.auto
    updated_at: strawberry.auto

    @strawberry.field(description=_doc(m.ReviewItem, "target"))
    def target(self) -> ReviewTarget:
        t = self.model.target
        return ReviewTarget(
            collection=Collection[t.collection],  # type: ignore[index]
            id=strawberry.ID(t.id) if t.id is not None else None,
            field=t.field,
            v=self.v,
        )


# ---- versions and results --------------------------------------------------------------


@pydantic_type(model=m.Version, all_fields=True, name="Snapshot", description=m.Version.__doc__)
class Snapshot:
    pass


@strawberry.type
class ActiveVersion:
    based_on: Snapshot
    modified: bool


@strawberry.type
class ActivateResult:
    active_version: ActiveVersion
    auto_snapshot: Snapshot | None


@strawberry.type
class TrashItem:
    ref: EntityRef
    label: str
    deleted_at: datetime


@strawberry.type
class DeleteResult:
    id: strawberry.ID
    cleared_references: list[EntityRef]
    dismissed_reviews: list[strawberry.ID]
    orphaned_assets: list[strawberry.ID]


@strawberry.type
class RestoreResult:
    id: strawberry.ID
    cleared_references: list[EntityRef]
    demoted_primary: bool


# Pydantic model -> GraphQL type
TYPES: dict[type, Any] = {
    m.Business: Business,
    m.ContactPoint: ContactPoint,
    m.Location: Location,
    m.ServiceCategory: ServiceCategory,
    m.Service: Service,
    m.ProductCategory: ProductCategory,
    m.StaffMember: StaffMember,
    m.Faq: Faq,
    m.SocialLink: SocialLink,
    m.Affiliation: Affiliation,
    m.CustomerAction: CustomerAction,
    m.Asset: Asset,
    m.ReviewItem: ReviewItem,
}


def wrap(model: Any, v: int) -> Any:
    """The GraphQL object for a schema model read from version `v`."""
    return TYPES[type(model)].from_pydantic(model, extra={"model": model, "v": v})
