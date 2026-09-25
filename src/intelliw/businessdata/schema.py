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
from typing import Annotated, Any, Literal, Self

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


def _d(description: str, default: Any = ..., **kwargs: Any) -> Any:
    """A field with a description (the single source for GraphQL and MCP prompt docs)."""
    return Field(default, description=description, **kwargs)


class Timestamped(Model):
    created_at: UtcDatetime = _d("When this was created (UTC).")
    updated_at: UtcDatetime = _d("When this last changed (UTC).")


class Entity(Timestamped):
    id: Slug = _d("Permanent identifier (a slug such as `dr-jane-doe`); never changes.")
    position: int = _d("Place in the owner's display order (0 = first).", ge=0)
    deleted: bool = _d("Marked as deleted; restorable until purged.", False)


class Hideable(Entity):
    hidden: bool = _d("Kept on record but left off the website.", False)


# ---- enums -----------------------------------------------------------------


class ContactKind(StrEnum):
    """What kind of contact channel a contact point is."""

    phone = "phone"
    email = "email"
    booking = "booking"
    store = "store"
    website = "website"


class Weekday(StrEnum):
    """Day of the week."""

    monday = "monday"
    tuesday = "tuesday"
    wednesday = "wednesday"
    thursday = "thursday"
    friday = "friday"
    saturday = "saturday"
    sunday = "sunday"


class PaymentMethod(StrEnum):
    """A way customers can pay."""

    cash = "cash"
    credit = "credit"
    debit = "debit"
    cheque = "cheque"
    e_transfer = "e_transfer"
    direct_billing = "direct_billing"


class ActionType(StrEnum):
    """What a customer action lets a customer do."""

    book = "book"
    call = "call"
    email = "email"
    buy = "buy"
    visit = "visit"


class AssetType(StrEnum):
    """What an image is used as."""

    logo = "logo"
    favicon = "favicon"
    photo = "photo"


class SocialPlatform(StrEnum):
    """A social media platform."""

    facebook = "facebook"
    instagram = "instagram"
    x = "x"
    pinterest = "pinterest"
    linkedin = "linkedin"
    youtube = "youtube"
    tiktok = "tiktok"


class ReviewStatus(StrEnum):
    """Where a review item stands."""

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
    """A postal address."""

    street: str = _d("Street address, including unit.")
    city: str = _d("City or town.")
    region: str = _d("Province or state code, e.g. `ON`.")
    postal_code: str = _d("Postal or ZIP code.")
    country: str = _d("Two-letter country code, e.g. `CA`.", "CA")


class GeoPoint(Model):
    """A map position."""

    lat: float = _d("Latitude.")
    lng: float = _d("Longitude.")


class OpeningHours(Model):
    """Opening hours for one day; a day with a break has one entry per interval."""

    day: Weekday = _d("Day of the week.")
    seq: int = _d("Interval number within the day (0, 1, ... for split shifts).", 0, ge=0)
    open: time | None = _d("Opening time (empty when closed).", None)
    close: time | None = _d("Closing time (empty when closed).", None)
    closed: bool = _d("Closed all day.", False)

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
    """The business itself: its name, identity and the facts shown across the website."""

    name: str = _d("Name customers know the business by.")
    legal_name: str | None = _d("Official or registered name, if different.", None)
    tagline: str = _d("Short slogan.", "")
    category: str = _d("Kind of business, e.g. `Independent optometry clinic`.", "")
    description: Markdown = _d("About the business (Markdown).", "")
    logo: Slug | None = _d("The business logo (an asset of type logo).", None)
    favicon: Slug | None = _d("The small browser-tab icon (an asset of type favicon).", None)
    brands: list[str] = _d("Brands the business carries.", [])
    service_areas: list[str] = _d("Towns and regions the business serves.", [])
    serving_since: int | None = _d("Year the business started serving its area.", None)
    payment_methods: list[PaymentMethod] = _d("Payment methods accepted.", [])


class ContactPoint(Hideable):
    """A way to reach the business: a phone number, email address, booking or store link."""

    kind: ContactKind = _d("Phone, email, booking link, online store or website.")
    label: str = _d("Short label, e.g. `Main` or `Appointments`.", "")
    value: str = _d("The phone number, email address or URL.")
    primary: bool = _d("The main contact point of its kind (at most one per kind).", False)

    @property
    def href(self) -> str:
        if self.kind is ContactKind.phone:
            return "tel:" + "".join(c for c in self.value if c.isdigit() or c == "+")
        if self.kind is ContactKind.email:
            return "mailto:" + self.value
        return self.value


class Location(Hideable):
    """A place where the business serves customers, with its address and opening hours."""

    name: str = _d("Name of the location.")
    address: Address = _d("Postal address.")
    geo: GeoPoint | None = _d("Map position.", None)
    map_url: HttpUrl | None = _d("Link to the location on a map.", None)
    phone: Slug | None = _d("The location's phone number (a phone contact point).", None)
    hours: list[OpeningHours] = _d("Weekly opening hours.", [])

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
    """A group of related services, e.g. `Eye exams`."""

    name: str = _d("Name of the category.")


class Service(Hideable):
    """A service the business offers customers."""

    category: Slug = _d("The category this service belongs to.")
    name: str = _d("Name of the service.")
    summary: str = _d("One-sentence summary.", "")
    description: Markdown = _d("Full description (Markdown).", "")
    audience: str | None = _d("Who the service is for, e.g. `Children under 19`.", None)
    image: Slug | None = _d("A photo of this service at the business (an asset).", None)


class ProductCategory(Hideable):
    """A group of products the business sells, e.g. `Sunglasses`."""

    name: str = _d("Name of the product category.")
    description: Markdown = _d("Description (Markdown).", "")
    image: Slug | None = _d("A photo of the products the business carries (an asset).", None)


class StaffMember(Hideable):
    """A person on the team, e.g. an optometrist."""

    name: str = _d("Full name, e.g. `Dr. Jane Doe`.")
    role: str = _d("Job title, e.g. `Optometrist`.")
    credentials: str = _d("Degrees and certifications.", "")
    bio: Markdown = _d("Biography (Markdown).", "")
    languages: list[str] = _d("Languages spoken, as language codes such as `en`, `yue`.", [])
    photo: Slug | None = _d("Portrait of the staff member (an asset).", None)


class Faq(Hideable):
    """A frequently asked question and its answer."""

    question: str = _d("The question, as a customer would ask it.")
    answer: Markdown = _d("The answer (Markdown).")
    services: list[Slug] = _d("Services this question is about.", [])


class SocialLink(Hideable):
    """A link to the business on a social media platform."""

    platform: SocialPlatform = _d("The social media platform.")
    url: HttpUrl = _d("Link to the business's page.")
    label: str = _d("Link text.", "")


class Affiliation(Hideable):
    """An organisation the business belongs to or is certified by."""

    name: str = _d("Name of the organisation.")
    description: Markdown = _d("What the membership means for customers (Markdown).", "")
    url: HttpUrl | None = _d("The organisation's website.", None)
    logo: Slug | None = _d("The organisation's logo (an asset).", None)


class CustomerAction(Hideable):
    """Something a customer can do, like booking or calling, through a contact point."""

    type: ActionType = _d("What the customer does: book, call, email, buy or visit.")
    label: str = _d("Button wording, e.g. `Book Appointment`.")
    channel: Slug = _d("The contact point the action uses (booking link, phone, ...).")
    services: list[Slug] = _d("Services the action applies to.", [])


class Asset(Entity):
    """An image of the business (logo, favicon or photo), uploaded by the owner."""

    type: AssetType = _d("What the image is used as: logo, favicon or photo.")
    path: str = _d("File name of the uploaded image.")
    alt: str = _d("Text description of the image, for accessibility.", "")


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

    collection: Collection = _d("The kind of entity.")
    id: Slug | None = _d("The entity's id (empty for the business itself).", None)
    field: str | None = _d("One field of the entity (empty = the whole entity).", None)

    @model_validator(mode="after")
    def _check_id(self) -> Self:
        if (self.collection == "business") != (self.id is None):
            raise ValueError("entity id is required unless collection is 'business'")
        return self


ReviewTarget = EntityRef


class ReviewItem(Entity):
    """A question for the owner to check or decide, about one entity or field."""

    target: ReviewTarget = _d("What the question is about.")
    note: str = _d("The question, in plain language.")
    status: ReviewStatus = _d("Open, resolved or dismissed.", ReviewStatus.open)
    resolution: str | None = _d("The owner's answer or reason for dismissal.", None)


# ---- versions ---------------------------------------------------------------


class Version(Model):
    """A read-only snapshot of the business data, for rollback."""

    number: int = _d("Snapshot number (1, 2, ...).", ge=1)
    tag: str | None = _d(
        "Optional name of the snapshot.",
        None,
        min_length=1,
        max_length=100,
        pattern=r"^\S(.*\S)?$",
    )
    parent: int | None = _d("The snapshot the active version was based on when taken.", None)
    created_at: UtcDatetime = _d("When the snapshot was taken (UTC).")


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
