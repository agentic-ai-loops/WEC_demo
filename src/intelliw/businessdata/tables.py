"""Relational storage of #businessdata (SQLAlchemy, SQLite).

Layout (see docs/design/02-graphql-api.md, *Storage requirements*):

- one table per collection; every row belongs to a version (`version` is part of every
  primary key; 0 is the active version, 1.. are snapshots);
- references are composite foreign keys `(version, <ref>) -> <table>(version, id)`, so a
  reference can never cross versions;
- nested lists (opening hours, brands, ...) and many-to-many links are child tables
  owned by their parent row; staff languages are a JSON array column.

ORM attributes are named exactly like the fields of the Pydantic models in
`intelliw.businessdata.schema`, so `Model.model_validate(row)` converts a row, and
`Row(version=v, **model.model_dump())` creates one. Flattened or child-table data is
exposed through properties and association proxies for that purpose.

Relationships are declared only for owned child rows. Other references are plain columns
guarded by foreign keys; queries resolve them by id.
"""

from datetime import UTC, datetime, time
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    TypeDecorator,
)
from sqlalchemy.ext.associationproxy import AssociationProxy, association_proxy
from sqlalchemy.ext.mutable import MutableList
from sqlalchemy.ext.orderinglist import ordering_list
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from intelliw.businessdata.schema import (
    ActionType,
    AssetType,
    ContactKind,
    OpeningHours,
    PaymentMethod,
    ReviewStatus,
    SocialPlatform,
    Weekday,
)

ACTIVE = 0  # version number of the active version; snapshots are 1, 2, ...


class UTCDateTime(TypeDecorator[datetime]):
    """Stores aware datetimes as naive UTC; returns them aware (UTC)."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime; timestamps must be timezone-aware")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        return None if value is None else value.replace(tzinfo=UTC)


class UrlString(TypeDecorator[str]):
    """Stores URL objects (e.g. pydantic `HttpUrl`) as strings."""

    impl = String
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> str | None:
        return None if value is None else str(value)


def _enum(enum: type[StrEnum]) -> Enum:
    return Enum(
        enum,
        native_enum=False,
        create_constraint=True,
        values_callable=lambda e: [m.value for m in e],
        validate_strings=True,
        length=32,
    )


class Base(DeclarativeBase):
    # `time` uses SQLAlchemy's Time, stored by SQLite as fixed-format ISO strings, so the
    # `open < close` CHECK constraint compares correctly.
    type_annotation_map = {datetime: UTCDateTime}


def _ref(column: str, table: str) -> ForeignKeyConstraint:
    """Composite foreign key from (version, column) to table(version, id)."""
    return ForeignKeyConstraint(["version", column], [f"{table}.version", f"{table}.id"])


def _owner(column: str, table: str) -> ForeignKeyConstraint:
    """Composite foreign key from a child row to the row that owns it."""
    return ForeignKeyConstraint(
        ["version", column], [f"{table}.version", f"{table}.id"], ondelete="CASCADE"
    )


class TimestampedRow:
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]


class EntityRow(TimestampedRow):
    version: Mapped[int] = mapped_column(primary_key=True)
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    position: Mapped[int]
    deleted: Mapped[bool] = mapped_column(default=False)


class HideableRow(EntityRow):
    hidden: Mapped[bool] = mapped_column(default=False)


# ---- business ----------------------------------------------------------------


class BusinessBrandRow(Base):
    __tablename__ = "business_brands"
    version: Mapped[int] = mapped_column(
        ForeignKey("business.version", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int] = mapped_column(primary_key=True)
    value: Mapped[str]


class BusinessServiceAreaRow(Base):
    __tablename__ = "business_service_areas"
    version: Mapped[int] = mapped_column(
        ForeignKey("business.version", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int] = mapped_column(primary_key=True)
    value: Mapped[str]


class BusinessPaymentMethodRow(Base):
    __tablename__ = "business_payment_methods"
    version: Mapped[int] = mapped_column(
        ForeignKey("business.version", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int] = mapped_column(primary_key=True)
    value: Mapped[PaymentMethod] = mapped_column(_enum(PaymentMethod))


class BusinessRow(TimestampedRow, Base):
    __tablename__ = "business"
    __table_args__ = (_ref("logo", "assets"), _ref("favicon", "assets"))

    version: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    legal_name: Mapped[str | None]
    tagline: Mapped[str] = mapped_column(default="")
    category: Mapped[str] = mapped_column(default="")
    description: Mapped[str] = mapped_column(default="")
    logo: Mapped[str | None]
    favicon: Mapped[str | None]
    serving_since: Mapped[int | None]

    _brand_rows: Mapped[list[BusinessBrandRow]] = relationship(
        order_by=BusinessBrandRow.position,
        collection_class=ordering_list("position"),
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    _area_rows: Mapped[list[BusinessServiceAreaRow]] = relationship(
        order_by=BusinessServiceAreaRow.position,
        collection_class=ordering_list("position"),
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    _payment_rows: Mapped[list[BusinessPaymentMethodRow]] = relationship(
        order_by=BusinessPaymentMethodRow.position,
        collection_class=ordering_list("position"),
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    _brands: AssociationProxy[list[str]] = association_proxy(
        "_brand_rows", "value", creator=lambda v: BusinessBrandRow(value=v)
    )
    _service_areas: AssociationProxy[list[str]] = association_proxy(
        "_area_rows", "value", creator=lambda v: BusinessServiceAreaRow(value=v)
    )
    _payment_methods: AssociationProxy[list[PaymentMethod]] = association_proxy(
        "_payment_rows", "value", creator=lambda v: BusinessPaymentMethodRow(value=v)
    )

    @property
    def brands(self) -> list[str]:
        return list(self._brands)

    @brands.setter
    def brands(self, values: list[str]) -> None:
        self._brands = list(values)

    @property
    def service_areas(self) -> list[str]:
        return list(self._service_areas)

    @service_areas.setter
    def service_areas(self, values: list[str]) -> None:
        self._service_areas = list(values)

    @property
    def payment_methods(self) -> list[PaymentMethod]:
        return list(self._payment_methods)

    @payment_methods.setter
    def payment_methods(self, values: list[PaymentMethod]) -> None:
        self._payment_methods = list(values)


# ---- contact points and locations -----------------------------------------------


class ContactPointRow(HideableRow, Base):
    __tablename__ = "contacts"

    kind: Mapped[ContactKind] = mapped_column(_enum(ContactKind))
    label: Mapped[str] = mapped_column(default="")
    value: Mapped[str]
    primary: Mapped[bool] = mapped_column(default=False)


Index(
    "uq_contacts_one_primary_per_kind",
    ContactPointRow.version,
    ContactPointRow.kind,
    unique=True,
    sqlite_where=ContactPointRow.primary.is_(True) & ContactPointRow.deleted.is_(False),
)


class OpeningHoursRow(Base):
    __tablename__ = "opening_hours"
    __table_args__ = (
        _owner("location_id", "locations"),
        CheckConstraint(
            "(closed = 1 AND open IS NULL AND close IS NULL)"
            " OR (closed = 0 AND open IS NOT NULL AND close IS NOT NULL AND open < close)",
            name="ck_opening_hours_times",
        ),
    )

    version: Mapped[int] = mapped_column(primary_key=True)
    location_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    day: Mapped[Weekday] = mapped_column(_enum(Weekday), primary_key=True)
    seq: Mapped[int] = mapped_column(primary_key=True, default=0)
    position: Mapped[int]  # list order within the location
    open: Mapped[time | None]
    close: Mapped[time | None]
    closed: Mapped[bool] = mapped_column(default=False)


class LocationRow(HideableRow, Base):
    __tablename__ = "locations"
    __table_args__ = (_ref("phone", "contacts"),)

    name: Mapped[str]
    street: Mapped[str]
    city: Mapped[str]
    region: Mapped[str]
    postal_code: Mapped[str]
    country: Mapped[str]
    lat: Mapped[float | None]
    lng: Mapped[float | None]
    map_url: Mapped[str | None] = mapped_column(UrlString)
    phone: Mapped[str | None]

    _hour_rows: Mapped[list[OpeningHoursRow]] = relationship(
        order_by=OpeningHoursRow.position,
        collection_class=ordering_list("position"),
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    @property
    def address(self) -> dict[str, str]:
        return {
            "street": self.street,
            "city": self.city,
            "region": self.region,
            "postal_code": self.postal_code,
            "country": self.country,
        }

    @address.setter
    def address(self, value: Any) -> None:
        value = _as_dict(value)
        for key in ("street", "city", "region", "postal_code", "country"):
            setattr(self, key, value[key])

    @property
    def geo(self) -> dict[str, float] | None:
        if self.lat is None or self.lng is None:
            return None
        return {"lat": self.lat, "lng": self.lng}

    @geo.setter
    def geo(self, value: Any) -> None:
        value = _as_dict(value) if value is not None else {"lat": None, "lng": None}
        self.lat, self.lng = value["lat"], value["lng"]

    @property
    def hours(self) -> list[OpeningHoursRow]:
        return list(self._hour_rows)

    @hours.setter
    def hours(self, values: list[Any]) -> None:
        # Validate through the schema model so strings are coerced and rules apply.
        self._hour_rows = [
            OpeningHoursRow(**OpeningHours.model_validate(v).model_dump()) for v in values
        ]


# ---- services, products, staff -------------------------------------------------


class ServiceCategoryRow(HideableRow, Base):
    __tablename__ = "service_categories"

    name: Mapped[str]


class ServiceRow(HideableRow, Base):
    __tablename__ = "services"
    __table_args__ = (
        _ref("category", "service_categories"),
        _ref("image", "assets"),
        Index("ix_services_category", "version", "category"),
    )

    category: Mapped[str]
    name: Mapped[str]
    summary: Mapped[str] = mapped_column(default="")
    description: Mapped[str] = mapped_column(default="")
    audience: Mapped[str | None]
    image: Mapped[str | None]


class ProductCategoryRow(HideableRow, Base):
    __tablename__ = "product_categories"
    __table_args__ = (_ref("image", "assets"),)

    name: Mapped[str]
    description: Mapped[str] = mapped_column(default="")
    image: Mapped[str | None]


class StaffMemberRow(HideableRow, Base):
    __tablename__ = "staff"
    __table_args__ = (
        _ref("photo", "assets"),
        CheckConstraint("json_type(languages) = 'array'", name="ck_staff_languages_array"),
    )

    name: Mapped[str]
    role: Mapped[str]
    credentials: Mapped[str] = mapped_column(default="")
    bio: Mapped[str] = mapped_column(default="")
    photo: Mapped[str | None]
    languages: Mapped[list[str]] = mapped_column(
        MutableList.as_mutable(JSON()), default=list
    )  # BCP 47 tags, e.g. ["en", "yue"]


# ---- FAQs, social links, affiliations, customer actions ---------------------------


class FaqServiceRow(Base):
    __tablename__ = "faq_services"
    __table_args__ = (
        _owner("faq_id", "faqs"),
        _ref("service_id", "services"),
        Index("ix_faq_services_service", "version", "service_id"),
    )

    version: Mapped[int] = mapped_column(primary_key=True)
    faq_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    service_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    position: Mapped[int]


class FaqRow(HideableRow, Base):
    __tablename__ = "faqs"

    question: Mapped[str]
    answer: Mapped[str]

    _service_rows: Mapped[list[FaqServiceRow]] = relationship(
        order_by=FaqServiceRow.position,
        collection_class=ordering_list("position"),
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    _services: AssociationProxy[list[str]] = association_proxy(
        "_service_rows", "service_id", creator=lambda sid: FaqServiceRow(service_id=sid)
    )

    @property
    def services(self) -> list[str]:
        return list(self._services)

    @services.setter
    def services(self, values: list[str]) -> None:
        self._services = list(values)


class SocialLinkRow(HideableRow, Base):
    __tablename__ = "social_links"

    platform: Mapped[SocialPlatform] = mapped_column(_enum(SocialPlatform))
    url: Mapped[str] = mapped_column(UrlString)
    label: Mapped[str] = mapped_column(default="")


class AffiliationRow(HideableRow, Base):
    __tablename__ = "affiliations"
    __table_args__ = (_ref("logo", "assets"),)

    name: Mapped[str]
    description: Mapped[str] = mapped_column(default="")
    url: Mapped[str | None] = mapped_column(UrlString)
    logo: Mapped[str | None]


class ActionServiceRow(Base):
    __tablename__ = "action_services"
    __table_args__ = (
        _owner("action_id", "actions"),
        _ref("service_id", "services"),
        Index("ix_action_services_service", "version", "service_id"),
    )

    version: Mapped[int] = mapped_column(primary_key=True)
    action_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    service_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    position: Mapped[int]


class CustomerActionRow(HideableRow, Base):
    __tablename__ = "actions"
    __table_args__ = (
        _ref("channel", "contacts"),
        Index("ix_actions_channel", "version", "channel"),
    )

    type: Mapped[ActionType] = mapped_column(_enum(ActionType))
    label: Mapped[str]
    channel: Mapped[str]

    _service_rows: Mapped[list[ActionServiceRow]] = relationship(
        order_by=ActionServiceRow.position,
        collection_class=ordering_list("position"),
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    _services: AssociationProxy[list[str]] = association_proxy(
        "_service_rows", "service_id", creator=lambda sid: ActionServiceRow(service_id=sid)
    )

    @property
    def services(self) -> list[str]:
        return list(self._services)

    @services.setter
    def services(self, values: list[str]) -> None:
        self._services = list(values)


# ---- assets and reviews ----------------------------------------------------------


class AssetRow(EntityRow, Base):
    __tablename__ = "assets"

    type: Mapped[AssetType] = mapped_column(_enum(AssetType))
    path: Mapped[str]
    alt: Mapped[str] = mapped_column(default="")


class ReviewItemRow(EntityRow, Base):
    __tablename__ = "reviews"
    __table_args__ = (
        CheckConstraint(
            "(target_collection = 'business') = (target_id IS NULL)",
            name="ck_reviews_target_id",
        ),
        Index("ix_reviews_target", "version", "target_collection", "target_id"),
    )

    target_collection: Mapped[str] = mapped_column(String(32))
    target_id: Mapped[str | None] = mapped_column(String(100))
    target_field: Mapped[str | None] = mapped_column(String(64))
    note: Mapped[str]
    status: Mapped[ReviewStatus] = mapped_column(_enum(ReviewStatus), default=ReviewStatus.open)
    resolution: Mapped[str | None]

    @property
    def target(self) -> dict[str, str | None]:
        return {
            "collection": self.target_collection,
            "id": self.target_id,
            "field": self.target_field,
        }

    @target.setter
    def target(self, value: Any) -> None:
        value = _as_dict(value)
        self.target_collection = value["collection"]
        self.target_id = value.get("id")
        self.target_field = value.get("field")


# ---- versions --------------------------------------------------------------------


class SnapshotRow(Base):
    __tablename__ = "snapshots"

    number: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    tag: Mapped[str | None] = mapped_column(String(100), unique=True)
    parent: Mapped[int | None] = mapped_column(ForeignKey("snapshots.number"))
    created_at: Mapped[datetime]


class VersionStateRow(Base):
    """Single row: which snapshot the active version is based on, and whether it changed."""

    __tablename__ = "version_state"
    __table_args__ = (CheckConstraint("id = 1", name="ck_version_state_single_row"),)

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    based_on: Mapped[int] = mapped_column(ForeignKey("snapshots.number"))
    modified: Mapped[bool] = mapped_column(Boolean, default=False)


# Collection name (as in `BusinessData`) -> table row class.
COLLECTION_ROWS: dict[str, type[EntityRow]] = {
    "contacts": ContactPointRow,
    "locations": LocationRow,
    "service_categories": ServiceCategoryRow,
    "services": ServiceRow,
    "product_categories": ProductCategoryRow,
    "staff": StaffMemberRow,
    "faqs": FaqRow,
    "social_links": SocialLinkRow,
    "affiliations": AffiliationRow,
    "actions": CustomerActionRow,
    "assets": AssetRow,
    "reviews": ReviewItemRow,
}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else value.model_dump()
