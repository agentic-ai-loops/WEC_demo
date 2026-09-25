"""GraphQL input types (docs/design/02-graphql-api.md, *Mutations* and *Update semantics*).

Inputs differ from the output types — references are ids, and there are no timestamps,
positions or computed fields — so they are declared here. Each `<Entity>Patch` is derived
from `<Entity>Input`: every field optional and defaulting to `UNSET`, so an omitted field
is left unchanged while an explicit `null` clears it.
"""

import dataclasses
import typing
from datetime import time
from enum import Enum
from typing import Any

import strawberry

from intelliw.businessdata import schema as m
from intelliw.graphql.types import Collection

ID = strawberry.ID


@strawberry.input
class AddressInput:
    street: str
    city: str
    region: str
    postal_code: str
    country: str = "CA"


@strawberry.input
class GeoPointInput:
    lat: float
    lng: float


@strawberry.input
class OpeningHoursInput:
    day: m.Weekday
    seq: int = 0
    open: time | None = None
    close: time | None = None
    closed: bool = False


@strawberry.input
class TimeRangeInput:
    open: time
    close: time


@strawberry.input
class EntityRefInput:
    collection: Collection  # type: ignore[valid-type]
    id: ID | None = None
    field: str | None = None


@strawberry.input
class ContactPointInput:
    kind: m.ContactKind
    value: str
    label: str = ""
    primary: bool = False
    hidden: bool = False
    id: ID | None = None


@strawberry.input
class LocationInput:
    name: str
    address: AddressInput
    geo: GeoPointInput | None = None
    map_url: str | None = None
    phone: ID | None = None
    hours: list[OpeningHoursInput] = strawberry.field(default_factory=list)
    hidden: bool = False
    id: ID | None = None


@strawberry.input
class ServiceCategoryInput:
    name: str
    hidden: bool = False
    id: ID | None = None


@strawberry.input
class ServiceInput:
    category: ID
    name: str
    summary: str = ""
    description: str = ""
    audience: str | None = None
    image: ID | None = None
    hidden: bool = False
    id: ID | None = None


@strawberry.input
class ProductCategoryInput:
    name: str
    description: str = ""
    image: ID | None = None
    hidden: bool = False
    id: ID | None = None


@strawberry.input
class StaffMemberInput:
    name: str
    role: str
    credentials: str = ""
    bio: str = ""
    languages: list[str] = strawberry.field(default_factory=list)
    photo: ID | None = None
    hidden: bool = False
    id: ID | None = None


@strawberry.input
class FaqInput:
    question: str
    answer: str
    services: list[ID] = strawberry.field(default_factory=list)
    hidden: bool = False
    id: ID | None = None


@strawberry.input
class SocialLinkInput:
    platform: m.SocialPlatform
    url: str
    label: str = ""
    hidden: bool = False
    id: ID | None = None


@strawberry.input
class AffiliationInput:
    name: str
    description: str = ""
    url: str | None = None
    logo: ID | None = None
    hidden: bool = False
    id: ID | None = None


@strawberry.input
class CustomerActionInput:
    type: m.ActionType
    label: str
    channel: ID
    services: list[ID] = strawberry.field(default_factory=list)
    hidden: bool = False
    id: ID | None = None


@strawberry.input
class AssetInput:
    type: m.AssetType
    path: str
    alt: str = ""
    id: ID | None = None


@strawberry.input
class ReviewInput:
    target: EntityRefInput
    note: str
    id: ID | None = None


@strawberry.input
class BusinessPatch:
    name: str | None = strawberry.UNSET
    legal_name: str | None = strawberry.UNSET
    tagline: str | None = strawberry.UNSET
    category: str | None = strawberry.UNSET
    description: str | None = strawberry.UNSET
    logo: ID | None = strawberry.UNSET
    favicon: ID | None = strawberry.UNSET
    brands: list[str] | None = strawberry.UNSET
    service_areas: list[str] | None = strawberry.UNSET
    serving_since: int | None = strawberry.UNSET
    payment_methods: list[m.PaymentMethod] | None = strawberry.UNSET


def _patch_of(input_cls: Any, name: str, exclude: frozenset[str] = frozenset({"id"})) -> Any:
    """`input_cls` with every field optional and defaulting to UNSET."""
    hints = typing.get_type_hints(input_cls)
    fields = [f.name for f in dataclasses.fields(input_cls) if f.name not in exclude]
    namespace: dict[str, Any] = {"__annotations__": {}}
    for f in fields:
        namespace["__annotations__"][f] = hints[f] | None
        namespace[f] = strawberry.UNSET
    return strawberry.input(type(name, (), namespace))


ContactPointPatch = _patch_of(ContactPointInput, "ContactPointPatch")
LocationPatch = _patch_of(LocationInput, "LocationPatch", frozenset({"id", "hours"}))
ServiceCategoryPatch = _patch_of(ServiceCategoryInput, "ServiceCategoryPatch")
ServicePatch = _patch_of(ServiceInput, "ServicePatch")
ProductCategoryPatch = _patch_of(ProductCategoryInput, "ProductCategoryPatch")
StaffMemberPatch = _patch_of(StaffMemberInput, "StaffMemberPatch")
FaqPatch = _patch_of(FaqInput, "FaqPatch")
SocialLinkPatch = _patch_of(SocialLinkInput, "SocialLinkPatch")
AffiliationPatch = _patch_of(AffiliationInput, "AffiliationPatch")
CustomerActionPatch = _patch_of(CustomerActionInput, "CustomerActionPatch")
AssetPatch = _patch_of(AssetInput, "AssetPatch")
ReviewPatch = _patch_of(ReviewInput, "ReviewPatch", frozenset({"id", "target"}))


def to_fields(obj: Any) -> dict[str, Any]:
    """An input object as plain data for the mutation layer; UNSET fields are left out."""

    def plain(value: Any) -> Any:
        if dataclasses.is_dataclass(value) and not isinstance(value, type):
            return {k: plain(v) for k, v in vars(value).items() if v is not strawberry.UNSET}
        if isinstance(value, list):
            return [plain(v) for v in value]
        if isinstance(value, Enum) and not isinstance(value, str):  # the Collection enum
            return value.value
        return value

    return {k: plain(v) for k, v in vars(obj).items() if v is not strawberry.UNSET}
