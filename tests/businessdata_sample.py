"""A small but complete #businessdata document (Whitby Eye Care), as plain JSON-like data."""

from typing import Any

T0 = "2026-09-24T14:05:12Z"


def _e(id: str, position: int, **fields: Any) -> dict[str, Any]:
    return {"id": id, "position": position, "created_at": T0, "updated_at": T0, **fields}


def whitby() -> dict[str, Any]:
    return {
        "business": {
            "name": "Whitby Eye Care",
            "legal_name": "Whitby Eye Care - Dr. R. Fernando & Associates",
            "tagline": "Your eyes deserve an optometrist!",
            "category": "Independent optometry clinic",
            "description": "Complete **eye health** examinations.",
            "logo": "logo",
            "favicon": "favicon",
            "brands": ["Oakley", "Guess"],
            "service_areas": ["Whitby", "Oshawa", "Ajax"],
            "serving_since": 2002,
            "payment_methods": ["cash", "credit", "debit"],
            "created_at": T0,
            "updated_at": T0,
        },
        "contacts": [
            _e("main-phone", 0, kind="phone", label="Main", value="905-655-6236", primary=True),
            _e("info-email", 1, kind="email", value="info@whitbyeyecare.com", primary=True),
            _e("appointments-email", 2, kind="email", value="dr.r.fernando@gmail.com", hidden=True),
            _e("booking", 3, kind="booking", value="https://atlas.opto.com/book"),
            _e("store", 4, kind="store", value="https://whitbyeyecare.ottooptics.io/"),
        ],
        "locations": [
            _e(
                "whitby",
                0,
                name="Whitby Eye Care",
                address={
                    "street": "4091 Thickson Road North, Unit 2",
                    "city": "Whitby",
                    "region": "ON",
                    "postal_code": "L1R 2X3",
                    "country": "CA",
                },
                geo={"lat": 43.9234837, "lng": -78.9259917},
                map_url="https://maps.example.com/whitby",
                phone="main-phone",
                hours=[
                    {"day": "monday", "open": "09:00", "close": "19:00"},
                    {"day": "tuesday", "seq": 0, "open": "09:00", "close": "12:00"},
                    {"day": "tuesday", "seq": 1, "open": "13:00", "close": "17:00"},
                    {"day": "sunday", "closed": True},
                ],
            )
        ],
        "service_categories": [
            _e("eye-exams", 0, name="Eye exams"),
            _e("eye-health", 1, name="Eye health"),
        ],
        "services": [
            _e(
                "eye-exams",
                0,
                category="eye-exams",
                name="Eye Exams",
                summary="A full eye examination.",
            ),
            _e(
                "dry-eye-testing",
                1,
                category="eye-health",
                name="Dry Eye Testing",
                image="dry-eye-device",
            ),
            _e(
                "computer-related-eye-strain",
                2,
                category="eye-health",
                name="Computer Related Eye Strain",
            ),
        ],
        "product_categories": [
            _e("sunglasses", 0, name="Sunglasses", description="Top brands."),
        ],
        "staff": [
            _e(
                "dr-raniero-fernando",
                0,
                name="Dr. Raniero Fernando",
                role="Optometrist",
                photo="dr-raniero-fernando",
                languages=["en"],
            ),
            _e(
                "dr-andrea-chan",
                1,
                name="Dr. Andrea Chan",
                role="Optometrist",
                languages=["en", "yue"],
            ),
            _e("dr-peter-chan", 2, name="Dr. Peter Chan", role="Optometrist", deleted=True),
        ],
        "faqs": [
            _e(
                "how-often-eye-exam",
                0,
                question="How often should I have an eye exam?",
                answer="Every 1 to 2 years.",
                services=["eye-exams"],
            ),
            _e(
                "reducing-computer-eye-strain",
                1,
                question="How can I reduce eye strain?",
                answer="Follow the 20-20-20 rule.",
                services=["computer-related-eye-strain", "eye-exams"],
            ),
        ],
        "social_links": [
            _e(
                "facebook",
                0,
                platform="facebook",
                url="https://www.facebook.com/wecare4yourvision/",
            ),
        ],
        "affiliations": [
            _e(
                "optometric-services-inc",
                0,
                name="Optometric Services Inc.",
                url="https://example.com/osi",
            ),
        ],
        "actions": [
            _e(
                "book-appointment",
                0,
                type="book",
                label="Book Appointment",
                channel="booking",
                services=["eye-exams", "dry-eye-testing"],
            ),
            _e("call-clinic", 1, type="call", label="Call Now", channel="main-phone"),
            _e(
                "reorder-contact-lenses",
                2,
                type="buy",
                label="Reorder Contact Lenses",
                channel="store",
            ),
        ],
        "assets": [
            _e("logo", 0, type="logo", path="logo.png", alt="Whitby Eye Care logo"),
            _e("favicon", 1, type="favicon", path="fav_icon.png"),
            _e("dr-raniero-fernando", 2, type="photo", path="Dr-Raniero-Fernando.jpg"),
            _e("dry-eye-device", 3, type="photo", path="unnamed.png"),
            _e("unused-photo", 4, type="photo", path="unused.png"),
        ],
        "reviews": [
            _e(
                "legal-name",
                0,
                target={"collection": "business", "field": "legalName"},
                note="Which name is official?",
            ),
            _e(
                "booking-system",
                1,
                target={"collection": "contacts", "id": "booking", "field": "value"},
                note="Atlas or JotForm?",
                status="resolved",
                resolution="Keep Atlas.",
            ),
            _e(
                "dr-chan-photo",
                2,
                target={"collection": "staff", "id": "dr-andrea-chan", "field": "photo"},
                note="Please send a portrait of Dr. Chan.",
            ),
            _e(
                "dr-peter-chan",
                3,
                target={"collection": "staff", "id": "dr-peter-chan"},
                note="Who is Dr. Peter Chan?",
                status="dismissed",
            ),
        ],
    }
