import pytest
from businessdata_sample import whitby
from pydantic import ValidationError

from intelliw.businessdata.schema import BusinessData, ContactPoint, Location


def test_sample_is_valid():
    data = BusinessData.model_validate(whitby())
    assert data.business.name == "Whitby Eye Care"
    assert data.locations[0].hours[2].seq == 1


def invalid(mutate) -> str:
    doc = whitby()
    mutate(doc)
    with pytest.raises(ValidationError) as exc:
        BusinessData.model_validate(doc)
    return str(exc.value)


def test_duplicate_ids():
    assert "duplicate ids" in invalid(lambda d: d["faqs"][1].update(id="how-often-eye-exam"))


def test_positions_must_follow_list_order():
    assert "positions" in invalid(lambda d: d["faqs"][0].update(position=5))


def test_dangling_reference():
    assert "no service category 'nope'" in invalid(
        lambda d: d["services"][0].update(category="nope")
    )


def test_reference_to_deleted_entity():
    def mutate(d):
        d["assets"][3]["deleted"] = True  # dry-eye-device, used by a service

    assert "no asset 'dry-eye-device'" in invalid(mutate)


def test_logo_must_be_a_logo_asset():
    assert "not of type logo" in invalid(lambda d: d["business"].update(logo="unused-photo"))


def test_action_type_must_fit_channel():
    assert "cannot use a booking channel" in invalid(
        lambda d: d["actions"][1].update(channel="booking")
    )


def test_one_primary_per_kind():
    assert "more than one primary phone" in invalid(
        lambda d: d["contacts"].append({**d["contacts"][0], "id": "second-phone", "position": 5})
    )


def test_review_target_field_uses_graphql_names():
    assert "no field 'legal_name'" in invalid(
        lambda d: d["reviews"][0]["target"].update(field="legal_name")
    )


def test_open_review_cannot_target_deleted_entity():
    assert "open review targets deleted" in invalid(lambda d: d["reviews"][3].update(status="open"))


@pytest.mark.parametrize(
    "hours",
    [
        [{"day": "monday", "open": "10:00", "close": "09:00"}],
        [{"day": "monday", "closed": True, "open": "09:00", "close": "10:00"}],
        [
            {"day": "monday", "seq": 0, "open": "09:00", "close": "12:00"},
            {"day": "monday", "seq": 1, "open": "11:00", "close": "13:00"},
        ],
        [
            {"day": "monday", "seq": 0, "open": "09:00", "close": "12:00"},
            {"day": "monday", "seq": 0, "open": "13:00", "close": "14:00"},
        ],
    ],
)
def test_invalid_hours(hours):
    loc = whitby()["locations"][0]
    with pytest.raises(ValidationError):
        Location.model_validate({**loc, "hours": hours})


def test_naive_timestamps_are_rejected():
    c = whitby()["contacts"][0]
    with pytest.raises(ValidationError):
        ContactPoint.model_validate({**c, "created_at": "2026-09-24T14:05:12"})


def test_contact_href():
    contacts = BusinessData.model_validate(whitby()).contacts
    assert contacts[0].href == "tel:9056556236"
    assert contacts[1].href == "mailto:info@whitbyeyecare.com"
