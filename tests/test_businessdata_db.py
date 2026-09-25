import json
from datetime import UTC, datetime

import pytest
from businessdata_sample import whitby
from conftest import QueryCounter
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, StatementError

from intelliw.businessdata import documents, queries
from intelliw.businessdata.schema import (
    Asset,
    BusinessData,
    ContactPoint,
    Faq,
    ReviewStatus,
    Service,
    StaffMember,
)
from intelliw.businessdata.tables import (
    ACTIVE,
    ContactPointRow,
    FaqRow,
    LocationRow,
    OpeningHoursRow,
    ServiceRow,
    StaffMemberRow,
)

# ---- documents -------------------------------------------------------------------


def test_round_trip_is_lossless(session):
    original = BusinessData.model_validate(whitby())
    assert documents.read_version(session, ACTIVE) == original


def test_initialize_creates_snapshot_1(session):
    index = queries.get_version_index(session)
    assert (index.based_on, index.modified) == (1, False)
    assert [s.number for s in index.snapshots] == [1]
    snapshot = documents.read_version(session, 1)
    assert snapshot.snapshot is not None and snapshot.snapshot.number == 1
    assert snapshot.model_dump(exclude={"snapshot"}) == documents.read_version(session).model_dump(
        exclude={"snapshot"}
    )


def test_timestamps_come_back_aware_utc(session):
    business = queries.get_business(session)
    assert business.created_at == datetime(2026, 9, 24, 14, 5, 12, tzinfo=UTC)


def test_write_into_non_empty_version_is_refused(session):
    with pytest.raises(ValueError):
        documents.write_version(session, BusinessData.model_validate(whitby()), ACTIVE)


# ---- queries ---------------------------------------------------------------------


def test_versions(session):
    assert queries.resolve_version(session) == ACTIVE
    assert queries.resolve_version(session, version=1) == 1
    with pytest.raises(queries.NotFound):
        queries.resolve_version(session, version=2)
    with pytest.raises(queries.NotFound):
        queries.resolve_version(session, snapshot="nope")
    with pytest.raises(ValueError):
        queries.resolve_version(session, version=1, snapshot="x")


def test_list_is_ordered_and_excludes_deleted(session):
    staff = queries.list_entities(session, StaffMember)
    assert [m.id for m in staff] == ["dr-raniero-fernando", "dr-andrea-chan"]
    assert staff[1].languages == ["en", "yue"]
    everyone = queries.list_entities(session, StaffMember, include_deleted=True)
    assert everyone[-1].id == "dr-peter-chan"


def test_list_filters(session):
    assert [c.id for c in queries.list_entities(session, ContactPoint, hidden=True)] == [
        "appointments-email"
    ]
    emails = queries.list_entities(session, ContactPoint, kind="email")
    assert [c.id for c in emails] == ["info-email", "appointments-email"]
    health = queries.list_entities(session, Service, category="eye-health")
    assert [s.id for s in health] == ["dry-eye-testing", "computer-related-eye-strain"]
    with pytest.raises(ValueError):
        queries.list_entities(session, Asset, hidden=False)


def test_get_entities_is_aligned_with_keys(session):
    result = queries.get_entities(
        session, StaffMember, ACTIVE, ["dr-andrea-chan", "nobody", "dr-peter-chan"]
    )
    assert [m.id if m else None for m in result] == ["dr-andrea-chan", None, None]


def test_reverse_relations(session):
    ids = ["eye-exams", "dry-eye-testing", "computer-related-eye-strain"]
    faqs = queries.faqs_by_service(session, ACTIVE, ids)
    assert [[f.id for f in group] for group in faqs] == [
        ["how-often-eye-exam", "reducing-computer-eye-strain"],
        [],
        ["reducing-computer-eye-strain"],
    ]
    actions = queries.actions_by_service(session, ACTIVE, ids)
    assert [[a.id for a in group] for group in actions] == [
        ["book-appointment"],
        ["book-appointment"],
        [],
    ]
    by_channel = queries.actions_by_channel(session, ACTIVE, ["main-phone", "info-email"])
    assert [[a.id for a in group] for group in by_channel] == [["call-clinic"], []]
    by_category = queries.services_by_category(session, ACTIVE, ["eye-health", "eye-exams"])
    assert [[s.id for s in group] for group in by_category] == [
        ["dry-eye-testing", "computer-related-eye-strain"],
        ["eye-exams"],
    ]


def test_reviews_by_target(session):
    groups = queries.reviews_by_target(
        session,
        ACTIVE,
        [("business", None), ("staff", "dr-andrea-chan"), ("contacts", "booking")],
    )
    assert [[r.id for r in g] for g in groups] == [
        ["legal-name"],
        ["dr-chan-photo"],
        ["booking-system"],
    ]
    open_only = queries.reviews_by_target(
        session, ACTIVE, [("contacts", "booking")], status=ReviewStatus.open
    )
    assert open_only == [[]]


def test_asset_usage(session):
    usage = queries.asset_usage(session, ACTIVE, ["logo", "dr-raniero-fernando", "unused-photo"])
    assert [[(u.collection, u.id, u.field) for u in g] for g in usage] == [
        [("business", None, "logo")],
        [("staff", "dr-raniero-fernando", "photo")],
        [],
    ]


def test_trash(session):
    items = queries.trash(session)
    assert [(t.ref.collection, t.ref.id, t.label) for t in items] == [
        ("staff", "dr-peter-chan", "Dr. Peter Chan")
    ]


def test_snapshot_is_read_in_its_own_version(session):
    session.execute(
        text("UPDATE staff SET name = 'Changed' WHERE version = 0 AND id = 'dr-andrea-chan'")
    )
    session.expire_all()  # raw SQL bypasses the ORM's identity map
    now = queries.get_entity(session, StaffMember, ACTIVE, "dr-andrea-chan")
    then = queries.get_entity(session, StaffMember, 1, "dr-andrea-chan")
    assert now is not None and now.name == "Changed"
    assert then is not None and then.name == "Dr. Andrea Chan"


def test_batched_loads_use_one_query(session, engine):
    ids = ["eye-exams", "dry-eye-testing", "computer-related-eye-strain"]
    counter = QueryCounter(engine)
    queries.get_entities(session, Service, ACTIVE, ids)
    assert counter.count == 1
    counter.count = 0
    queries.actions_by_service(session, ACTIVE, ids)
    # one query for the actions, one selectin load for their own service links
    assert counter.count == 2


# ---- constraints -------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


def test_foreign_keys_are_enforced(session):
    session.add(
        ServiceRow(
            version=ACTIVE,
            id="x",
            position=9,
            category="no-such-category",
            name="X",
            created_at=_now(),
            updated_at=_now(),
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_references_cannot_cross_versions(session):
    session.add(
        ServiceRow(
            version=7,
            id="x",
            position=0,
            category="eye-exams",
            name="X",
            created_at=_now(),
            updated_at=_now(),
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_one_primary_per_kind_is_enforced(session):
    session.add(
        ContactPointRow(
            version=ACTIVE,
            id="second-phone",
            position=5,
            kind="phone",
            value="1",
            primary=True,
            created_at=_now(),
            updated_at=_now(),
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_deleted_primary_does_not_count(session):
    session.add(
        ContactPointRow(
            version=ACTIVE,
            id="old-phone",
            position=5,
            kind="phone",
            value="1",
            primary=True,
            deleted=True,
            created_at=_now(),
            updated_at=_now(),
        )
    )
    session.flush()


def test_hours_check_constraint(session):
    session.add(
        OpeningHoursRow(
            version=ACTIVE,
            location_id="whitby",
            day="friday",
            seq=0,
            position=9,
            open=datetime(2026, 1, 1, 17).time(),
            close=datetime(2026, 1, 1, 9).time(),
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_enum_values_are_checked(session):
    # SQLAlchemy rejects unknown values before sending them ...
    session.add(
        ContactPointRow(
            version=ACTIVE,
            id="fax",
            position=5,
            kind="fax",
            value="1",
            created_at=_now(),
            updated_at=_now(),
        )
    )
    with pytest.raises(StatementError):
        session.flush()
    session.rollback()
    # ... and the database's CHECK constraint rejects them from any other writer.
    with pytest.raises(IntegrityError):
        session.execute(
            text(
                "INSERT INTO contacts (version, id, position, deleted, hidden, kind, label,"
                ' value, "primary", created_at, updated_at)'
                " VALUES (0, 'fax', 5, 0, 0, 'fax', '', '1', 0, :t, :t)"
            ),
            {"t": "2026-09-24 14:05:12"},
        )


def test_replacing_lists_rewrites_child_rows(session):
    faq = session.get(FaqRow, (ACTIVE, "reducing-computer-eye-strain"))
    assert faq is not None
    faq.services = ["eye-exams", "computer-related-eye-strain"]  # same ids, new order
    loc = session.get(LocationRow, (ACTIVE, "whitby"))
    assert loc is not None
    loc.hours = [{"day": "monday", "open": "10:00", "close": "18:00"}]
    session.flush()
    session.expire_all()
    faq_model = queries.get_entity(session, Faq, ACTIVE, "reducing-computer-eye-strain")
    assert faq_model is not None
    assert faq_model.services == ["eye-exams", "computer-related-eye-strain"]
    location = documents.read_version(session).locations[0]
    assert [(h.day, h.open.isoformat() if h.open else None) for h in location.hours] == [
        ("monday", "10:00:00")
    ]


# ---- snapshots and rollback ---------------------------------------------------------


def _content(session, version):
    return documents.read_version(session, version).model_dump(exclude={"snapshot"})


def _rename(session, name):
    row = session.get(StaffMemberRow, (ACTIVE, "dr-andrea-chan"))
    row.name = name
    documents.mark_modified(session)
    session.flush()


def test_take_snapshot_copies_active_version(session):
    snap = documents.take_snapshot(session, "reviewed")
    assert (snap.number, snap.tag, snap.parent) == (2, "reviewed", 1)
    assert _content(session, 2) == _content(session, ACTIVE)
    index = queries.get_version_index(session)
    assert (index.based_on, index.modified) == (2, False)
    assert queries.resolve_version(session, snapshot="reviewed") == 2


def test_take_snapshot_rejects_duplicate_and_invalid_tags(session):
    documents.take_snapshot(session, "reviewed")
    with pytest.raises(documents.TagConflict):
        documents.take_snapshot(session, "reviewed")
    with pytest.raises(ValueError):
        documents.take_snapshot(session, " padded ")


def test_mark_modified(session):
    documents.mark_modified(session)
    assert queries.get_version_index(session).modified is True


def test_activate_rolls_back_and_keeps_unsaved_changes(session):
    documents.take_snapshot(session, "reviewed")  # snapshot 2
    _rename(session, "Edited")
    index, auto = documents.activate_version(session, 2)

    assert auto is not None and auto.number == 3 and auto.tag is None
    assert (index.based_on, index.modified) == (2, False)
    assert _content(session, ACTIVE) == _content(session, 2)
    edited = queries.get_entity(session, StaffMember, 3, "dr-andrea-chan")
    assert edited is not None and edited.name == "Edited"

    # the rollback itself can be undone
    documents.activate_version(session, 3)
    now = queries.get_entity(session, StaffMember, ACTIVE, "dr-andrea-chan")
    assert now is not None and now.name == "Edited"


def test_activate_unmodified_takes_no_snapshot(session):
    index, auto = documents.activate_version(session, 1)
    assert auto is None
    assert [s.number for s in index.snapshots] == [1]


def test_activate_preserves_timestamps_and_positions(session):
    before = _content(session, ACTIVE)
    documents.activate_version(session, 1)
    assert _content(session, ACTIVE) == before


def test_snapshots_are_never_changed(session):
    frozen = _content(session, 1)
    documents.take_snapshot(session)
    _rename(session, "Edited")
    documents.activate_version(session, 2)
    documents.take_snapshot(session, "later")
    assert _content(session, 1) == frozen


def test_activate_unknown_snapshot(session):
    with pytest.raises(queries.NotFound):
        documents.activate_version(session, 42)


def test_staff_languages_is_a_json_array(session):
    stored = session.execute(
        text("SELECT languages FROM staff WHERE version = 0 AND id = 'dr-andrea-chan'")
    ).scalar()
    assert json.loads(stored) == ["en", "yue"]
    with pytest.raises(IntegrityError):
        session.execute(
            text(
                "UPDATE staff SET languages = '\"en\"' WHERE version = 0 AND id = 'dr-andrea-chan'"
            )
        )


def test_staff_languages_in_place_edit_is_saved(session):
    row = session.get(StaffMemberRow, (ACTIVE, "dr-andrea-chan"))
    assert row is not None
    row.languages.append("cmn")
    session.flush()
    session.expire_all()
    member = queries.get_entity(session, StaffMember, ACTIVE, "dr-andrea-chan")
    assert member is not None and member.languages == ["en", "yue", "cmn"]
