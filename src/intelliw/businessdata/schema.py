"""Schema of #businessdata (docs/design/01-businessdata.md).

These models are the single definition of every entity's fields. They are used for:

- the `business.json` document (`BusinessData`, one version of #businessdata);
- the results of database queries (`intelliw.businessdata.queries`), which the GraphQL
  resolvers return.

References between entities are ids (slugs); resolving them is up to the caller.
Entities carry no version: the version is context (a document, a query argument).
"""

from datetime import UTC, datetime, time
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    model_validator,
)
from pydantic.alias_generators import to_camel

Slug = Annotated[str, Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")]
Markdown = str  # CommonMark (rule 9)


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(UTC)


# A timezone-aware datetime, normalized to UTC (rule 11). A plain `datetime` underneath,
# so GraphQL maps it to its DateTime scalar.
UtcDatetime = Annotated[datetime, AfterValidator(_to_utc)]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class Timestamped(Model):
    created_at: UtcDatetime  # UTC; server-set (rule 11)
    updated_at: UtcDatetime  # UTC; server-set (rule 11)


class Entity(Timestamped):
    id: Slug  # immutable; unique per collection per version (rule 6)
    position: int = Field(ge=0)  # 0-based, contiguous per collection (rule 7)
    deleted: bool = False  # marked for purge (rule 12)


class Hideable(Entity):
    hidden: bool = False  # not rendered (rule 10)


# ---- enums -----------------------------------------------------------------


class ContactKind(StrEnum):
    phone = "phone"
    email = "email"
    booking = "booking"
    store = "store"
    website = "website"


class Weekday(StrEnum):
    monday = "monday"
    tuesday = "tuesday"
    wednesday = "wednesday"
    thursday = "thursday"
    friday = "friday"
    saturday = "saturday"
    sunday = "sunday"


class PaymentMethod(StrEnum):
    cash = "cash"
    credit = "credit"
    debit = "debit"
    cheque = "cheque"
    e_transfer = "e_transfer"
    direct_billing = "direct_billing"


class ActionType(StrEnum):
    book = "book"
    call = "call"
    email = "email"
    buy = "buy"
    visit = "visit"


class AssetType(StrEnum):
    logo = "logo"
    favicon = "favicon"
    photo = "photo"


class SocialPlatform(StrEnum):
    facebook = "facebook"
    instagram = "instagram"
    x = "x"
    pinterest = "pinterest"
    linkedin = "linkedin"
    youtube = "youtube"
    tiktok = "tiktok"


class ReviewStatus(StrEnum):
    open = "open"
    resolved = "resolved"
    dismissed = "dismissed"


# Channel kinds each action type may use (rule 2).
ACTION_CHANNEL_KINDS: dict[ActionType, frozenset[ContactKind]] = {
    ActionType.call: frozenset({ContactKind.phone}),
    ActionType.email: frozenset({ContactKind.email}),
    ActionType.book: frozenset({ContactKind.booking, ContactKind.phone}),
    ActionType.buy: frozenset({ContactKind.store}),
    ActionType.visit: frozenset({ContactKind.website}),
}


# ---- value objects (nested) ------------------------------------------------


class Address(Model):
    street: str
    city: str
    region: str  # ISO 3166-2 subdivision, e.g. "ON"
    postal_code: str
    country: str = "CA"  # ISO 3166-1 alpha-2


class GeoPoint(Model):
    lat: float
    lng: float


class OpeningHours(Model):
    day: Weekday
    seq: int = Field(default=0, ge=0)  # interval number within the day (split shifts)
    open: time | None = None
    close: time | None = None
    closed: bool = False

    @model_validator(mode="after")
    def _check_times(self) -> Self:
        if self.closed:
            if self.open is not None or self.close is not None:
                raise ValueError("closed hours cannot have open/close times")
        elif self.open is None or self.close is None:
            raise ValueError("open hours need both open and close times")
        elif self.open >= self.close:
            raise ValueError("open must be before close")
        return self


# ---- entities --------------------------------------------------------------


class Business(Timestamped):
    name: str
    legal_name: str | None = None
    tagline: str = ""
    category: str = ""
    description: Markdown = ""
    logo: Slug | None = None  # -> Asset.id (type logo)
    favicon: Slug | None = None  # -> Asset.id (type favicon)
    brands: list[str] = []
    service_areas: list[str] = []
    serving_since: int | None = None  # year
    payment_methods: list[PaymentMethod] = []


class ContactPoint(Hideable):
    kind: ContactKind
    label: str = ""
    value: str  # phone number, email address or URL
    primary: bool = False  # at most one primary non-deleted per kind

    @property
    def href(self) -> str:
        if self.kind is ContactKind.phone:
            return "tel:" + "".join(c for c in self.value if c.isdigit() or c == "+")
        if self.kind is ContactKind.email:
            return "mailto:" + self.value
        return self.value


class Location(Hideable):
    name: str
    address: Address
    geo: GeoPoint | None = None
    map_url: HttpUrl | None = None
    phone: Slug | None = None  # -> ContactPoint.id (kind phone)
    hours: list[OpeningHours] = []

    @model_validator(mode="after")
    def _check_hours(self) -> Self:
        by_day: dict[Weekday, list[OpeningHours]] = {}
        for h in self.hours:
            by_day.setdefault(h.day, []).append(h)
        for day, intervals in by_day.items():
            if len({h.seq for h in intervals}) != len(intervals):
                raise ValueError(f"duplicate (day, seq) on {day}")
            if len(intervals) > 1 and any(h.closed for h in intervals):
                raise ValueError(f"{day} is both closed and open")
            spans = sorted(
                (h.open, h.close) for h in intervals if h.open is not None and h.close is not None
            )
            for (_, end), (start, _) in zip(spans, spans[1:], strict=False):
                if start < end:
                    raise ValueError(f"overlapping intervals on {day}")
        return self


class ServiceCategory(Hideable):
    name: str


class Service(Hideable):
    category: Slug  # -> ServiceCategory.id
    name: str
    summary: str = ""
    description: Markdown = ""
    audience: str | None = None
    image: Slug | None = None  # -> Asset.id; a picture of the business


class ProductCategory(Hideable):
    name: str
    description: Markdown = ""
    image: Slug | None = None  # -> Asset.id; a picture of products carried


class StaffMember(Hideable):
    name: str
    role: str
    credentials: str = ""
    bio: Markdown = ""
    languages: list[str] = []  # BCP 47 tags
    photo: Slug | None = None  # -> Asset.id


class Faq(Hideable):
    question: str
    answer: Markdown
    services: list[Slug] = []  # -> Service.id


class SocialLink(Hideable):
    platform: SocialPlatform
    url: HttpUrl
    label: str = ""


class Affiliation(Hideable):
    name: str
    description: Markdown = ""
    url: HttpUrl | None = None
    logo: Slug | None = None  # -> Asset.id


class CustomerAction(Hideable):
    type: ActionType
    label: str  # wording only
    channel: Slug  # -> ContactPoint.id
    services: list[Slug] = []  # -> Service.id


class Asset(Entity):
    type: AssetType
    path: str  # relative to businessdata/resources/; file is immutable
    alt: str = ""


# ---- review -----------------------------------------------------------------

Collection = Literal[
    "business",
    "contacts",
    "locations",
    "service_categories",
    "services",
    "product_categories",
    "staff",
    "faqs",
    "social_links",
    "affiliations",
    "actions",
    "assets",
    "reviews",
]


class EntityRef(Model):
    """An entity, or one field of it: review targets, cleared references, asset usage."""

    collection: Collection
    id: Slug | None = None  # None only for collection == "business"
    field: str | None = None  # GraphQL (camelCase) field name; None = whole entity

    @model_validator(mode="after")
    def _check_id(self) -> Self:
        if (self.collection == "business") != (self.id is None):
            raise ValueError("entity id is required unless collection is 'business'")
        return self


ReviewTarget = EntityRef


class ReviewItem(Entity):
    target: ReviewTarget
    note: str
    status: ReviewStatus = ReviewStatus.open
    resolution: str | None = None


# ---- versions ---------------------------------------------------------------


class Version(Model):
    """A read-only snapshot."""

    number: int = Field(ge=1)
    tag: str | None = Field(default=None, min_length=1, max_length=100, pattern=r"^\S(.*\S)?$")
    parent: int | None = None  # snapshot the active version was based on; None for 1
    created_at: UtcDatetime


class VersionIndex(Model):
    """Workspace-level version state; not part of any version."""

    based_on: int  # -> Version.number
    modified: bool  # active version changed since `based_on`
    snapshots: list[Version]


# ---- query results without a stored counterpart ------------------------------------


class TrashItem(Model):
    """A deleted entity awaiting purge (02-graphql-api, *Restore*)."""

    ref: EntityRef
    label: str  # name / label / question
    deleted_at: UtcDatetime  # the entity's updated_at at deletion


# ---- root document ---------------------------------------------------------

# Collection name -> entity model, in document order.
COLLECTIONS: dict[str, type[Entity]] = {
    "contacts": ContactPoint,
    "locations": Location,
    "service_categories": ServiceCategory,
    "services": Service,
    "product_categories": ProductCategory,
    "staff": StaffMember,
    "faqs": Faq,
    "social_links": SocialLink,
    "affiliations": Affiliation,
    "actions": CustomerAction,
    "assets": Asset,
    "reviews": ReviewItem,
}


def graphql_field_names(model: type[BaseModel]) -> set[str]:
    """Field names as the GraphQL API exposes them (camelCase)."""
    return {to_camel(name) for name in model.model_fields}


class BusinessData(Model):
    """The content of one version of #businessdata (`business.json`)."""

    schema_version: str = "1"
    snapshot: Version | None = None  # the snapshot this document is; None = active version
    business: Business
    contacts: list[ContactPoint] = []
    locations: list[Location] = []
    service_categories: list[ServiceCategory] = []
    services: list[Service] = []
    product_categories: list[ProductCategory] = []
    staff: list[StaffMember] = []
    faqs: list[Faq] = []
    social_links: list[SocialLink] = []
    affiliations: list[Affiliation] = []
    actions: list[CustomerAction] = []
    assets: list[Asset] = []
    reviews: list[ReviewItem] = []

    @model_validator(mode="after")
    def _check_integrity(self) -> Self:
        errors = integrity_errors(self)
        if errors:
            raise ValueError("; ".join(errors))
        return self


def integrity_errors(data: BusinessData) -> list[str]:
    """Referential-integrity violations in a whole document (01-businessdata)."""
    errors: list[str] = []

    for name in COLLECTIONS:
        entities: list[Entity] = getattr(data, name)
        ids = [e.id for e in entities]
        if len(set(ids)) != len(ids):
            errors.append(f"{name}: duplicate ids")
        if [e.position for e in entities] != list(range(len(entities))):
            errors.append(f"{name}: positions must be 0..n-1 in list order")

    assets = {a.id: a for a in data.assets if not a.deleted}
    contacts = {c.id: c for c in data.contacts if not c.deleted}
    categories = {c.id: c for c in data.service_categories if not c.deleted}
    services = {s.id: s for s in data.services if not s.deleted}

    def ref[E: Entity](where: str, target: str | None, pool: dict[str, E], what: str) -> E | None:
        if target is None:
            return None
        found = pool.get(target)
        if found is None:
            errors.append(f"{where}: no {what} '{target}'")
        return found

    for field, asset_type in (("logo", AssetType.logo), ("favicon", AssetType.favicon)):
        asset = ref(f"business.{field}", getattr(data.business, field), assets, "asset")
        if asset is not None and asset.type is not asset_type:
            errors.append(f"business.{field}: asset '{asset.id}' is not of type {asset_type}")

    for loc in (x for x in data.locations if not x.deleted):
        phone = ref(f"locations[{loc.id}].phone", loc.phone, contacts, "contact point")
        if phone is not None and phone.kind is not ContactKind.phone:
            errors.append(f"locations[{loc.id}].phone: '{phone.id}' is not a phone")
    for s in services.values():
        ref(f"services[{s.id}].category", s.category, categories, "service category")
        ref(f"services[{s.id}].image", s.image, assets, "asset")
    for p in (x for x in data.product_categories if not x.deleted):
        ref(f"product_categories[{p.id}].image", p.image, assets, "asset")
    for m in (x for x in data.staff if not x.deleted):
        ref(f"staff[{m.id}].photo", m.photo, assets, "asset")
    for a in (x for x in data.affiliations if not x.deleted):
        ref(f"affiliations[{a.id}].logo", a.logo, assets, "asset")
    for f in (x for x in data.faqs if not x.deleted):
        for sid in f.services:
            ref(f"faqs[{f.id}].services", sid, services, "service")
    for act in (x for x in data.actions if not x.deleted):
        channel = ref(f"actions[{act.id}].channel", act.channel, contacts, "contact point")
        if channel is not None and channel.kind not in ACTION_CHANNEL_KINDS[act.type]:
            errors.append(f"actions[{act.id}]: type {act.type} cannot use a {channel.kind} channel")
        for sid in act.services:
            ref(f"actions[{act.id}].services", sid, services, "service")

    primaries = [c.kind for c in contacts.values() if c.primary]
    for kind in set(primaries):
        if primaries.count(kind) > 1:
            errors.append(f"contacts: more than one primary {kind}")

    for r in (x for x in data.reviews if not x.deleted):
        t = r.target
        where = f"reviews[{r.id}].target"
        if t.collection == "reviews":
            errors.append(f"{where}: a review item cannot target a review item")
            continue
        if t.collection == "business":
            model: type[BaseModel] = Business
        else:
            all_entities = {e.id: e for e in getattr(data, t.collection)}
            entity = all_entities.get(t.id or "")
            if entity is None:
                errors.append(f"{where}: no {t.collection} '{t.id}'")
                continue
            if entity.deleted and r.status is ReviewStatus.open:
                errors.append(f"{where}: open review targets deleted '{t.id}'")
            model = COLLECTIONS[t.collection]
        if t.field is not None and t.field not in graphql_field_names(model):
            errors.append(f"{where}: {t.collection} has no field '{t.field}'")

    return errors
