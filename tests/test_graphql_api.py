from pathlib import Path
from typing import Any

import pytest
from conftest import QueryCounter

from intelliw.businessdata import documents
from intelliw.businessdata.database import session_factory
from intelliw.graphql.context import Context, View
from intelliw.graphql.schema import schema


@pytest.fixture
def resources(tmp_path: Path) -> Path:
    for name in (
        "logo.png",
        "fav_icon.png",
        "Dr-Raniero-Fernando.jpg",
        "unnamed.png",
        "unused.png",
    ):
        (tmp_path / name).write_bytes(b"png")
    (tmp_path / "stray.png").write_bytes(b"png")
    return tmp_path


@pytest.fixture
def api(engine, session, resources):
    """Run a GraphQL document against the sample database; returns (data, errors)."""

    async def run(document: str, **variables: Any) -> tuple[Any, list[dict]]:
        ctx = Context(session_factory(engine), resources)
        result = await schema.execute(document, variable_values=variables, context_value=ctx)
        errors = [{"message": e.message, **(e.extensions or {})} for e in (result.errors or [])]
        return result.data, errors

    return run


def codes(errors: list[dict]) -> list[str]:
    return [e.get("code") for e in errors]


# ---- queries ------------------------------------------------------------------------


async def test_business_and_references(api):
    data, errors = await api("""{ business { name logo { path } favicon { type }
        paymentMethods reviews { id } version } }""")
    assert errors == []
    b = data["business"]
    assert b["name"] == "Whitby Eye Care" and b["logo"]["path"] == "logo.png"
    assert b["favicon"]["type"] == "favicon"
    assert b["paymentMethods"] == ["cash", "credit", "debit"]
    assert [r["id"] for r in b["reviews"]] == ["legal-name"]
    assert b["version"] is None


async def test_nested_relations(api):
    data, errors = await api("""{ services { id category { name } image { path }
        faqs { id } actions { id href channel { kind } } } }""")
    assert errors == []
    by_id = {s["id"]: s for s in data["services"]}
    assert by_id["dry-eye-testing"]["image"]["path"] == "unnamed.png"
    assert [f["id"] for f in by_id["eye-exams"]["faqs"]] == [
        "how-often-eye-exam",
        "reducing-computer-eye-strain",
    ]
    action = by_id["eye-exams"]["actions"][0]
    assert action["href"] == "https://atlas.opto.com/book"
    assert action["channel"]["kind"] == "booking"


async def test_nested_query_cost_does_not_grow_with_data(api, engine):
    document = """{ services { category { name } image { path } faqs { id } actions { id } } }"""
    counter = QueryCounter(engine)
    await api(document)
    small = counter.count
    for n in range(5):
        _, errors = await api(
            "mutation($n: String!) {"
            ' createService(input: {name: $n, category: "eye-exams"}) { id } }',
            n=f"Extra {n}",
        )
        assert errors == []
    counter.count = 0
    data, _ = await api(document)
    assert len(data["services"]) == 8
    assert counter.count == small


async def test_contact_point_used_by_and_href(api):
    data, _ = await api('{ contactPoint(id: "main-phone") { href usedBy { id } } }')
    assert data["contactPoint"] == {"href": "tel:9056556236", "usedBy": [{"id": "call-clinic"}]}


async def test_hidden_excluded_by_default_and_deleted_always(api):
    data, _ = await api("""{ contactPoints { id }
        all: contactPoints(includeHidden: true) { id hidden }
        staff { id }
        contactPoint(id: "appointments-email") { id }
        shown: contactPoint(id: "appointments-email", includeHidden: true) { hidden } }""")
    assert "appointments-email" not in [c["id"] for c in data["contactPoints"]]
    assert {"id": "appointments-email", "hidden": True} in data["all"]
    assert [s["id"] for s in data["staff"]] == ["dr-raniero-fernando", "dr-andrea-chan"]
    assert data["contactPoint"] is None
    assert data["shown"] == {"hidden": True}
    data, _ = await api('{ staffMember(id: "dr-peter-chan", includeHidden: true) { id } }')
    assert data["staffMember"] is None


async def test_hidden_entities_are_left_out_of_nested_fields(api):
    data, errors = await api("""mutation {
        a: updateService(id: "dry-eye-testing", patch: {hidden: true}) { id hidden }
        b: updateStaffMember(id: "dr-raniero-fernando", patch: {hidden: true}) { hidden }
        c: updateServiceCategory(id: "eye-health", patch: {hidden: true}) { hidden } }""")
    assert errors == []
    assert data["a"] == {"id": "dry-eye-testing", "hidden": True}  # mutations show hidden
    query = """query($h: Boolean!) {
        serviceCategories(includeHidden: $h) { id services { id } }
        customerAction(id: "book-appointment", includeHidden: $h) { services { id } }
        service(id: "computer-related-eye-strain", includeHidden: $h) { category { id hidden } }
        asset(id: "dr-raniero-fernando", includeHidden: $h) { usedBy { id } }
        assets(unused: true, includeHidden: $h) { id } }"""
    data, errors = await api(query, h=False)
    assert errors == []
    assert data["serviceCategories"] == [{"id": "eye-exams", "services": [{"id": "eye-exams"}]}]
    assert data["customerAction"]["services"] == [{"id": "eye-exams"}]
    # a required reference resolves even to a hidden entity
    assert data["service"]["category"] == {"id": "eye-health", "hidden": True}
    assert data["asset"]["usedBy"] == []
    assert {"dr-raniero-fernando", "dry-eye-device"} <= {a["id"] for a in data["assets"]}
    data, errors = await api(query, h=True)
    assert errors == []
    assert [c["id"] for c in data["serviceCategories"]] == ["eye-exams", "eye-health"]
    assert data["serviceCategories"][1]["services"] == [
        {"id": "dry-eye-testing"},
        {"id": "computer-related-eye-strain"},
    ]
    assert data["customerAction"]["services"] == [{"id": "eye-exams"}, {"id": "dry-eye-testing"}]
    assert data["asset"]["usedBy"] == [{"id": "dr-raniero-fernando"}]
    assert [a["id"] for a in data["assets"]] == ["unused-photo"]


async def test_review_targets_follow_include_hidden(api):
    await api('mutation { updateContactPoint(id: "booking", patch: {hidden: true}) { id } }')
    query = """query($h: Boolean!) {
        reviews(status: null, includeHidden: $h) { id target { entity { __typename } } } }"""
    data, _ = await api(query, h=False)
    targets = {r["id"]: r["target"]["entity"] for r in data["reviews"]}
    assert targets["booking-system"] is None
    assert targets["legal-name"] == {"__typename": "Business"}
    data, _ = await api(query, h=True)
    targets = {r["id"]: r["target"]["entity"] for r in data["reviews"]}
    assert targets["booking-system"] == {"__typename": "ContactPoint"}


async def test_filters(api):
    data, _ = await api("""{ services(category: "eye-health") { id }
        faqs(service: "computer-related-eye-strain") { id }
        contactPoints(kind: email, includeHidden: true) { id } }""")
    assert [s["id"] for s in data["services"]] == ["dry-eye-testing", "computer-related-eye-strain"]
    assert data["faqs"] == [{"id": "reducing-computer-eye-strain"}]
    assert [c["id"] for c in data["contactPoints"]] == ["info-email", "appointments-email"]


async def test_assets_and_resource_files(api):
    data, _ = await api("""{ assets(unused: true) { id usedBy { collection } }
        asset(id: "logo") { usedBy { collection id field } }
        resourceFiles(unregistered: true) }""")
    assert data["assets"] == [{"id": "unused-photo", "usedBy": []}]
    assert data["asset"]["usedBy"] == [{"collection": "business", "id": None, "field": "logo"}]
    assert data["resourceFiles"] == ["stray.png"]


async def test_reviews_and_target_union(api):
    data, _ = await api("""{ reviews { id target { collection id field
        entity { __typename ... on StaffMember { name } ... on Business { name } } } } }""")
    reviews = {r["id"]: r["target"] for r in data["reviews"]}
    assert set(reviews) == {"legal-name", "dr-chan-photo"}  # open only, by default
    assert reviews["dr-chan-photo"]["entity"] == {
        "__typename": "StaffMember",
        "name": "Dr. Andrea Chan",
    }
    assert reviews["legal-name"]["entity"]["__typename"] == "Business"
    data, _ = await api("{ reviews(status: null) { id } }")
    assert len(data["reviews"]) == 4


async def test_trash(api):
    data, _ = await api("{ trash { ref { collection id } label deletedAt } }")
    assert data["trash"][0]["ref"] == {"collection": "staff", "id": "dr-peter-chan"}
    assert data["trash"][0]["label"] == "Dr. Peter Chan"


# ---- versions ---------------------------------------------------------------------------


async def test_version_selection(api):
    data, errors = await api("""mutation { takeSnapshot(tag: "before") { number tag parent } }""")
    assert errors == [] and data["takeSnapshot"] == {"number": 2, "tag": "before", "parent": 1}
    await api(
        'mutation { updateStaffMember(id: "dr-andrea-chan", patch: {name: "Changed"}) { id } }'
    )
    data, errors = await api("""{
        now: staffMember(id: "dr-andrea-chan") { name version }
        then: staffMember(id: "dr-andrea-chan", snapshot: "before") { name version }
        cat: service(id: "eye-exams", version: 2) { version category { version } }
        activeVersion { basedOn { number } modified } }""")
    assert errors == []
    assert data["now"] == {"name": "Changed", "version": None}
    assert data["then"] == {"name": "Dr. Andrea Chan", "version": 2}
    assert data["cat"] == {"version": 2, "category": {"version": 2}}
    assert data["activeVersion"] == {"basedOn": {"number": 2}, "modified": True}


async def test_version_argument_errors(api):
    _, errors = await api('{ staff(version: 1, snapshot: "x") { id } }')
    assert codes(errors) == ["VALIDATION"]
    _, errors = await api("{ staff(version: 99) { id } }")
    assert codes(errors) == ["NOT_FOUND"]


async def test_snapshots_are_read_only(api):
    _, errors = await api(
        'mutation { updateStaffMember(id: "dr-andrea-chan", version: 1, patch: {bio: "x"}) { id } }'
    )
    assert codes(errors) == ["READ_ONLY"]


async def test_activate_version_keeps_unsaved_changes(api):
    await api(
        'mutation { updateStaffMember(id: "dr-andrea-chan", patch: {name: "Edited"}) { id } }'
    )
    data, errors = await api("""mutation { activateVersion(version: 1) {
        activeVersion { basedOn { number } modified } autoSnapshot { number tag } } }""")
    assert errors == []
    assert data["activateVersion"] == {
        "activeVersion": {"basedOn": {"number": 1}, "modified": False},
        "autoSnapshot": {"number": 2, "tag": None},
    }
    data, _ = await api('{ staffMember(id: "dr-andrea-chan") { name } }')
    assert data["staffMember"]["name"] == "Dr. Andrea Chan"
    _, errors = await api("mutation { activateVersion { activeVersion { modified } } }")
    assert codes(errors) == ["VALIDATION"]


async def test_duplicate_snapshot_tag(api):
    await api('mutation { takeSnapshot(tag: "t") { number } }')
    _, errors = await api('mutation { takeSnapshot(tag: "t") { number } }')
    assert codes(errors) == ["CONFLICT"]


# ---- mutations --------------------------------------------------------------------------


async def test_create_update_move_reorder(api):
    data, errors = await api("""mutation { createStaffMember(input: {name: "Dr. Jane Doe",
        role: "Optometrist", languages: ["en", "fr"]}, before: "dr-andrea-chan") {
        id languages version } }""")
    assert errors == []
    assert data["createStaffMember"] == {
        "id": "dr-jane-doe",
        "languages": ["en", "fr"],
        "version": None,
    }
    data, _ = await api("{ staff { id } }")
    assert [s["id"] for s in data["staff"]] == [
        "dr-raniero-fernando",
        "dr-jane-doe",
        "dr-andrea-chan",
    ]

    data, _ = await api('mutation { moveStaffMember(id: "dr-jane-doe") { id } }')
    assert [s["id"] for s in data["moveStaffMember"]][-1] == "dr-jane-doe"
    data, errors = await api("""mutation { reorderStaff(ids: ["dr-jane-doe", "dr-andrea-chan",
        "dr-raniero-fernando"]) { id } }""")
    assert errors == [] and data["reorderStaff"][0]["id"] == "dr-jane-doe"
    _, errors = await api('mutation { reorderStaff(ids: ["dr-jane-doe"]) { id } }')
    assert codes(errors) == ["STALE_ORDER"]


async def test_patch_semantics(api):
    data, errors = await api("""mutation { updateStaffMember(id: "dr-raniero-fernando",
        patch: {photo: null}) { name photo { id } } }""")
    assert errors == []
    assert data["updateStaffMember"] == {"name": "Dr. Raniero Fernando", "photo": None}
    _, errors = await api(
        'mutation { updateStaffMember(id: "dr-andrea-chan", patch: {name: null}) { id } }'
    )
    assert codes(errors) == ["VALIDATION"]


async def test_error_codes(api):
    _, errors = await api(
        'mutation { createStaffMember(input: {id: "dr-peter-chan", name: "X", role: "Y"}) { id } }'
    )
    assert codes(errors) == ["CONFLICT"]
    _, errors = await api('mutation { deleteServiceCategory(id: "eye-health") { id } }')
    assert codes(errors) == ["IN_USE"]
    assert {u["id"] for u in errors[0]["usedBy"]} == {
        "dry-eye-testing",
        "computer-related-eye-strain",
    }
    _, errors = await api('mutation { updateStaffMember(id: "nobody", patch: {bio: "x"}) { id } }')
    assert codes(errors) == ["NOT_FOUND"]
    _, errors = await api(
        'mutation { updateContactPoint(id: "main-phone",'
        " patch: {kind: email, primary: false}) { id } }"
    )
    assert codes(errors) == ["VALIDATION"]


async def test_failed_mutation_leaves_nothing(api):
    _, errors = await api(
        """mutation { createService(input: {name: "X", category: "nope"}) { id } }"""
    )
    assert codes(errors) == ["VALIDATION"]
    data, _ = await api("{ services { id } activeVersion { modified } }")
    assert "x" not in [s["id"] for s in data["services"]]
    assert data["activeVersion"]["modified"] is False


async def test_delete_and_restore(api):
    data, errors = await api("""mutation { deleteAsset(id: "dry-eye-device") {
        id clearedReferences { collection id field } dismissedReviews orphanedAssets } }""")
    assert errors == []
    assert data["deleteAsset"]["clearedReferences"] == [
        {"collection": "services", "id": "dry-eye-testing", "field": "image"}
    ]
    data, _ = await api(
        """mutation { deleteStaffMember(id: "dr-andrea-chan") { dismissedReviews } }"""
    )
    assert data["deleteStaffMember"]["dismissedReviews"] == ["dr-chan-photo"]
    _, errors = await api(
        'mutation { updateStaffMember(id: "dr-andrea-chan", patch: {bio: "x"}) { id } }'
    )
    assert codes(errors) == ["NOT_FOUND"]
    data, errors = await api(
        'mutation { restoreStaffMember(id: "dr-andrea-chan") { id demotedPrimary } }'
    )
    assert errors == [] and data["restoreStaffMember"]["id"] == "dr-andrea-chan"
    data, _ = await api("{ staff { id } }")
    assert data["staff"][-1]["id"] == "dr-andrea-chan"


async def test_set_opening_hours(api):
    data, errors = await api("""mutation { setOpeningHours(location: "whitby", day: tuesday,
        intervals: [{open: "09:00", close: "19:00"}]) { hours { day seq open close closed } } }""")
    assert errors == []
    tuesday = [h for h in data["setOpeningHours"]["hours"] if h["day"] == "tuesday"]
    assert tuesday == [
        {"day": "tuesday", "seq": 0, "open": "09:00:00", "close": "19:00:00", "closed": False}
    ]


async def test_update_business(api):
    data, errors = await api("""mutation { updateBusiness(patch: {tagline: "New",
        paymentMethods: [cash, e_transfer]}) { tagline paymentMethods name } }""")
    assert errors == []
    assert data["updateBusiness"] == {
        "tagline": "New",
        "paymentMethods": ["cash", "e_transfer"],
        "name": "Whitby Eye Care",
    }


async def test_review_workflow(api):
    data, errors = await api("""mutation { createReview(target: {collection: services,
        id: "eye-exams", field: "summary"}, note: "Is this summary accurate?") {
        id status target { entity { ... on Service { name } } } } }""")
    assert errors == []
    review = data["createReview"]
    assert review["status"] == "open" and review["target"]["entity"] == {"name": "Eye Exams"}
    data, _ = await api(
        'mutation($id: ID!) { resolveReview(id: $id, resolution: "Yes") { status resolution } }',
        id=review["id"],
    )
    assert data["resolveReview"] == {"status": "resolved", "resolution": "Yes"}


async def test_create_asset_requires_file(api):
    _, errors = await api(
        'mutation { createAsset(input: {type: photo, path: "missing.png"}) { id } }'
    )
    assert codes(errors) == ["VALIDATION"]
    data, errors = await api(
        'mutation { createAsset(input: {type: photo, path: "stray.png"}) { id } }'
    )
    assert errors == [] and data["createAsset"]["id"] == "stray"


async def test_hello_still_works():
    result = await schema.execute("{ hello { message } }")
    assert result.data == {"hello": {"message": "hello world"}}


async def test_update_asset_path_keeps_references(api):
    data, errors = await api(
        'mutation { updateAsset(id: "logo", patch: {path: "stray.png"}) { path usedBy { field } } }'
    )
    assert errors == []
    assert data["updateAsset"] == {"path": "stray.png", "usedBy": [{"field": "logo"}]}
    data, _ = await api("{ business { logo { id path } } }")
    assert data["business"]["logo"] == {"id": "logo", "path": "stray.png"}


async def test_hiding_a_referenced_entity_keeps_references(api):
    data, errors = await api(
        'mutation { updateContactPoint(id: "main-phone", patch: {hidden: true}) { hidden } }'
    )
    assert errors == [] and data["updateContactPoint"] == {"hidden": True}
    data, _ = await api('{ location(id: "whitby") { phone { id } } }')
    assert data["location"]["phone"] is None  # hidden without includeHidden
    data, _ = await api('{ location(id: "whitby", includeHidden: true) { phone { id hidden } } }')
    assert data["location"]["phone"] == {"id": "main-phone", "hidden": True}


async def test_position_is_exposed_and_follows_the_order(api):
    data, errors = await api("{ staff { id position } services { position } }")
    assert errors == []
    assert data["staff"] == [
        {"id": "dr-raniero-fernando", "position": 0},
        {"id": "dr-andrea-chan", "position": 1},
    ]
    assert [s["position"] for s in data["services"]] == [0, 1, 2]
    data, _ = await api(
        'mutation { moveStaffMember(id: "dr-andrea-chan", before: "dr-raniero-fernando") '
        "{ id position } }"
    )
    assert data["moveStaffMember"] == [
        {"id": "dr-andrea-chan", "position": 0},
        {"id": "dr-raniero-fernando", "position": 1},
    ]
    data, _ = await api('mutation { restoreStaffMember(id: "dr-peter-chan") { id } }')
    data, _ = await api('{ staffMember(id: "dr-peter-chan") { position } }')
    assert data["staffMember"]["position"] == 2  # restored to the end


async def test_context_default_view_applies_to_omitted_arguments(engine, session):
    """The renderer's view: a snapshot and hidden entities, unless a field says otherwise."""
    documents.take_snapshot(session, "two")  # snapshot 2, with appointments-email hidden
    session.commit()
    query = "{ business { version } contactPoints { id } one: business(version: 1) { version } }"
    ctx = Context(session_factory(engine), default_view=View(2, include_hidden=True))
    result = await schema.execute(query, context_value=ctx)
    assert result.errors is None, result.errors
    assert result.data["business"] == {"version": 2}
    assert "appointments-email" in [c["id"] for c in result.data["contactPoints"]]
    assert result.data["one"] == {"version": 1}  # an explicit argument wins


@pytest.mark.parametrize("path", ["/etc/passwd", "../outside.png", "a/../../x.png", ".env"])
async def test_asset_paths_must_stay_in_resources(api, path):
    create = """mutation($path: String!) {
        createAsset(input: {id: "x", type: photo, path: $path}) { id } }"""
    data, errors = await api(create, path=path)
    assert codes(errors) == ["VALIDATION"], errors
    update = 'mutation($path: String!) { updateAsset(id: "logo", patch: {path: $path}) { id } }'
    data, errors = await api(update, path=path)
    assert codes(errors) == ["VALIDATION"], errors


async def test_asset_symlink_out_of_resources_is_refused(api, resources, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside") / "secret.png"
    outside.write_bytes(b"secret")
    (resources / "link.png").symlink_to(outside)
    data, errors = await api(
        'mutation { createAsset(input: {id: "x", type: photo, path: "link.png"}) { id } }'
    )
    assert codes(errors) == ["VALIDATION"] and "outside" in errors[0]["message"]
