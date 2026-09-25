from datetime import UTC, datetime, time

import pytest

from intelliw.businessdata import documents, mutations, queries
from intelliw.businessdata.schema import (
    Business,
    ContactPoint,
    CustomerAction,
    EntityRef,
    Faq,
    Location,
    ReviewItem,
    Service,
    StaffMember,
    Weekday,
    integrity_errors,
)
from intelliw.businessdata.tables import ACTIVE

LATER = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def get(session, model, id):
    return queries.get_entity(session, model, ACTIVE, id)


def ids(session, model):
    return [e.id for e in queries.list_entities(session, model)]


def assert_valid(session):
    assert integrity_errors(documents.read_version(session, validate=False)) == []


# ---- create -----------------------------------------------------------------------


def test_create_appends_and_generates_id(session):
    staff = mutations.create(
        session, "staff", {"name": "Dr. Jane Doe", "role": "Optometrist"}, now=LATER
    )
    assert staff.id == "dr-jane-doe"
    assert ids(session, StaffMember)[-1] == "dr-jane-doe"
    assert staff.created_at == staff.updated_at == LATER
    assert queries.get_version_index(session).modified
    assert_valid(session)


def test_create_before(session):
    mutations.create(session, "staff", {"name": "New", "role": "Optician"}, before="dr-andrea-chan")
    assert ids(session, StaffMember) == ["dr-raniero-fernando", "new", "dr-andrea-chan"]
    assert_valid(session)


def test_generated_id_skips_deleted_ids(session):
    # dr-peter-chan exists as a deleted entity
    staff = mutations.create(session, "staff", {"name": "Dr. Peter Chan", "role": "Optometrist"})
    assert staff.id == "dr-peter-chan-2"


def test_explicit_taken_id_conflicts(session):
    with pytest.raises(mutations.Conflict):
        mutations.create(session, "staff", {"id": "dr-peter-chan", "name": "X", "role": "Y"})


def test_create_checks_references(session):
    with pytest.raises(mutations.Invalid, match="no service_categories 'nope'"):
        mutations.create(session, "services", {"name": "X", "category": "nope"})
    with pytest.raises(mutations.Invalid, match="cannot use a store channel"):
        mutations.create(session, "actions", {"type": "call", "label": "X", "channel": "store"})
    with pytest.raises(mutations.Invalid, match="already the primary phone"):
        mutations.create(session, "contacts", {"kind": "phone", "value": "1", "primary": True})


def test_create_asset_requires_file(session, tmp_path):
    with pytest.raises(mutations.Invalid, match="no file"):
        mutations.create(
            session, "assets", {"type": "photo", "path": "x.png"}, resources_dir=tmp_path
        )
    (tmp_path / "x.png").write_bytes(b"png")
    asset = mutations.create(
        session, "assets", {"type": "photo", "path": "x.png"}, resources_dir=tmp_path
    )
    assert asset.id == "x"


# ---- update -----------------------------------------------------------------------


def test_update_is_partial_and_advances_updated_at(session):
    before = get(session, StaffMember, "dr-andrea-chan")
    after = mutations.update(session, "staff", "dr-andrea-chan", {"bio": "New bio"}, now=LATER)
    assert after.bio == "New bio" and after.name == before.name
    assert after.updated_at == LATER and after.created_at == before.created_at


def test_update_null_clears_optional_and_rejects_required(session):
    staff = mutations.update(session, "staff", "dr-raniero-fernando", {"photo": None})
    assert staff.photo is None
    with pytest.raises(mutations.Invalid):
        mutations.update(session, "staff", "dr-andrea-chan", {"name": None})


def test_update_without_change_keeps_updated_at(session):
    before = get(session, StaffMember, "dr-andrea-chan")
    after = mutations.update(session, "staff", "dr-andrea-chan", {"name": before.name}, now=LATER)
    assert after.updated_at == before.updated_at


def test_update_rejects_server_fields(session):
    with pytest.raises(mutations.Invalid, match="cannot set"):
        mutations.update(session, "staff", "dr-andrea-chan", {"created_at": LATER})


def test_contact_kind_change_must_fit_actions(session):
    with pytest.raises(mutations.Invalid, match="call-clinic"):
        mutations.update(session, "contacts", "main-phone", {"kind": "email", "primary": False})


def test_logo_asset_type_cannot_change(session):
    with pytest.raises(mutations.Invalid, match="business logo"):
        mutations.update(session, "assets", "logo", {"type": "photo"})


def test_update_deleted_entity_is_not_found(session):
    with pytest.raises(queries.NotFound):
        mutations.update(session, "staff", "dr-peter-chan", {"bio": "x"})


def test_update_business(session):
    business = mutations.update_business(session, {"tagline": "New"}, now=LATER)
    assert business.tagline == "New" and business.updated_at == LATER
    with pytest.raises(mutations.Invalid, match="photo, not a logo"):
        mutations.update_business(session, {"logo": "unused-photo"})


# ---- delete / restore ---------------------------------------------------------------


def test_delete_refused_while_in_use(session):
    with pytest.raises(mutations.InUse) as exc:
        mutations.delete(session, "service_categories", "eye-health")
    assert {u.id for u in exc.value.used_by} == {"dry-eye-testing", "computer-related-eye-strain"}
    with pytest.raises(mutations.InUse):
        mutations.delete(session, "contacts", "booking")


def test_delete_asset_clears_references(session):
    result = mutations.delete(session, "assets", "dry-eye-device", now=LATER)
    assert [(r.collection, r.id, r.field) for r in result.cleared_references] == [
        ("services", "dry-eye-testing", "image")
    ]
    service = get(session, Service, "dry-eye-testing")
    assert service.image is None and service.updated_at == LATER
    assert get(session, type(service), "dry-eye-testing") is not None
    assert_valid(session)


def test_delete_service_removes_links_and_reports_them(session):
    result = mutations.delete(session, "services", "eye-exams")
    cleared = {(r.collection, r.id, r.field) for r in result.cleared_references}
    assert ("faqs", "how-often-eye-exam", "services") in cleared
    assert ("actions", "book-appointment", "services") in cleared
    assert get(session, Faq, "how-often-eye-exam").services == []
    assert get(session, CustomerAction, "book-appointment").services == ["dry-eye-testing"]
    assert_valid(session)


def test_delete_dismisses_reviews_and_reports_orphans(session):
    mutations.update(session, "staff", "dr-andrea-chan", {"photo": "unused-photo"})
    result = mutations.delete(session, "staff", "dr-andrea-chan")
    assert result.dismissed_reviews == ["dr-chan-photo"]
    assert result.orphaned_assets == ["unused-photo"]
    review = get(session, ReviewItem, "dr-chan-photo")
    assert review.status == "dismissed" and review.resolution == "target deleted"
    assert "dr-andrea-chan" not in ids(session, StaffMember)
    assert_valid(session)


def test_delete_location_phone_is_cleared(session):
    mutations.update(session, "actions", "call-clinic", {"channel": "booking", "type": "book"})
    result = mutations.delete(session, "contacts", "main-phone")
    assert ("locations", "whitby", "phone") in {
        (r.collection, r.id, r.field) for r in result.cleared_references
    }
    assert get(session, Location, "whitby").phone is None


def test_restore(session):
    mutations.delete(session, "staff", "dr-andrea-chan")
    result = mutations.restore(session, "staff", "dr-andrea-chan", now=LATER)
    assert result.cleared_references == []
    staff = get(session, StaffMember, "dr-andrea-chan")
    assert staff is not None and staff.updated_at == LATER
    assert ids(session, StaffMember)[-1] == "dr-andrea-chan"  # appended
    assert_valid(session)


def test_restore_requires_required_references(session):
    mutations.delete(session, "services", "eye-exams")
    mutations.delete(session, "service_categories", "eye-exams")
    with pytest.raises(mutations.Invalid, match="restore it first"):
        mutations.restore(session, "services", "eye-exams")


def test_restore_drops_optional_references_to_deleted(session):
    mutations.delete(session, "staff", "dr-raniero-fernando")
    mutations.delete(session, "assets", "dr-raniero-fernando")
    result = mutations.restore(session, "staff", "dr-raniero-fernando")
    assert [(r.collection, r.field) for r in result.cleared_references] == [("staff", "photo")]
    assert get(session, StaffMember, "dr-raniero-fernando").photo is None
    assert_valid(session)


def test_restore_demotes_primary(session):
    mutations.delete(session, "actions", "call-clinic")
    mutations.delete(session, "contacts", "main-phone")
    mutations.create(session, "contacts", {"kind": "phone", "value": "2", "primary": True})
    result = mutations.restore(session, "contacts", "main-phone")
    assert result.demoted_primary
    assert get(session, ContactPoint, "main-phone").primary is False
    assert_valid(session)


def test_restore_live_entity_is_not_found(session):
    with pytest.raises(queries.NotFound):
        mutations.restore(session, "staff", "dr-andrea-chan")


# ---- ordering -----------------------------------------------------------------------


def test_move_and_reorder(session):
    before = get(session, StaffMember, "dr-andrea-chan").updated_at
    mutations.move(session, "staff", "dr-andrea-chan", "dr-raniero-fernando")
    assert ids(session, StaffMember) == ["dr-andrea-chan", "dr-raniero-fernando"]
    assert get(session, StaffMember, "dr-andrea-chan").updated_at == before
    mutations.move(session, "staff", "dr-andrea-chan", None)
    assert ids(session, StaffMember) == ["dr-raniero-fernando", "dr-andrea-chan"]
    mutations.reorder(session, "staff", ["dr-andrea-chan", "dr-raniero-fernando"])
    assert ids(session, StaffMember) == ["dr-andrea-chan", "dr-raniero-fernando"]
    assert_valid(session)


def test_reorder_must_list_exactly_the_live_ids(session):
    with pytest.raises(mutations.StaleOrder):
        mutations.reorder(session, "staff", ["dr-andrea-chan"])
    with pytest.raises(mutations.StaleOrder):
        mutations.reorder(
            session, "staff", ["dr-andrea-chan", "dr-raniero-fernando", "dr-peter-chan"]
        )


# ---- hours and reviews ------------------------------------------------------------


def test_set_opening_hours_replaces_one_day(session):
    location = mutations.set_opening_hours(
        session, "whitby", Weekday.tuesday, [(time(9), time(19))], now=LATER
    )
    by_day = {(h.day, h.seq): (h.open, h.close) for h in location.hours}
    assert by_day[(Weekday.tuesday, 0)] == (time(9), time(19))
    assert (Weekday.tuesday, 1) not in by_day
    assert by_day[(Weekday.monday, 0)] == (time(9), time(19))
    assert location.updated_at == LATER
    closed = mutations.set_opening_hours(session, "whitby", Weekday.saturday, [], closed=True)
    assert any(h.day == Weekday.saturday and h.closed for h in closed.hours)
    with pytest.raises(mutations.Invalid):
        mutations.set_opening_hours(
            session, "whitby", Weekday.monday, [(time(9), time(12)), (time(11), time(13))]
        )


def test_review_workflow(session):
    target = EntityRef(collection="business", field="tagline")
    review = mutations.create_review(session, target, "Is the tagline current?")
    assert review.status == "open"
    assert mutations.resolve_review(session, review.id, "Yes").status == "resolved"
    assert mutations.reopen_review(session, review.id).status == "open"
    assert mutations.dismiss_review(session, review.id, "n/a").resolution == "n/a"
    with pytest.raises(mutations.Invalid, match="no field"):
        mutations.create_review(session, EntityRef(collection="business", field="nope"), "x")
    with pytest.raises(mutations.Invalid, match="no staff"):
        mutations.create_review(session, EntityRef(collection="staff", id="dr-peter-chan"), "x")


def test_every_mutation_marks_modified(session):
    documents.take_snapshot(session)
    assert not queries.get_version_index(session).modified
    mutations.move(session, "staff", "dr-andrea-chan", None)
    assert queries.get_version_index(session).modified


def test_business_model_unchanged_by_other_mutations(session):
    before = queries.get_business(session)
    mutations.create(session, "faqs", {"question": "Q?", "answer": "A."})
    assert queries.get_business(session) == before
    assert isinstance(before, Business)
