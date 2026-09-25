"""One-off: build the `whitby_eye_care` workspace from `digest.xml`.

    uv run python examples/import_whitby_digest.py [--force]

Follows the digest mapping in docs/design/01-businessdata.md. The digest's image files are
not available, so placeholder PNGs are generated, named after the entity referring to each
image (e.g. `dr-raniero-fernando.png` for a staff photo).
"""

import hashlib
import json
import re
import shutil
import struct
import sys
import zlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from intelliw.businessdata import documents
from intelliw.businessdata.database import create_db_engine, init_db, session_factory
from intelliw.businessdata.schema import BusinessData
from intelliw.workspace import Workspace

ROOT = Path(__file__).resolve().parent.parent
DIGEST = ROOT / "digest.xml"
WORKSPACE = Workspace(ROOT / "examples" / "whitby_eye_care")

# Image classification (01-businessdata, *Image ownership*): pictures of the business.
BUSINESS_IMAGES = {
    "logo",
    "favicon",
    "dr-raniero-fernando",
    "dr-sumeya-mao",
    "dry-eye-device",
    "blephex-device",
    "about-clinic",
}

LANGUAGES = {
    "English": "en",
    "French": "fr",
    "Cantonese": "yue",
    "Mandarin": "cmn",
    "Urdu": "ur",
    "Hindi": "hi",
    "Punjabi": "pa",
    "Tamil": "ta",
    "Spanish": "es",
}

ACTION_TYPES = {
    "phone": "call",
    "email": "email",
    "booking": "book",
    "store": "buy",
    "website": "visit",
}


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def text(el: ET.Element | None) -> str:
    return (el.text or "").strip() if el is not None else ""


def need(el: ET.Element, path: str) -> ET.Element:
    found = el.find(path)
    if found is None:
        raise ValueError(f"digest has no <{path}>")
    return found


def split_list(value: str) -> list[str]:
    """'A, B, C and D.' -> ['A', 'B', 'C', 'D']"""
    value = value.strip().rstrip(".")
    return [p.strip() for p in re.split(r",\s*|\s+and\s+", value) if p.strip()]


# ---- placeholder images ------------------------------------------------------------


def placeholder_png(path: Path, name: str, size: tuple[int, int] = (320, 200)) -> None:
    """A valid solid-colour PNG, colour derived from `name`."""
    w, h = size
    r, g, b = hashlib.sha256(name.encode()).digest()[:3]
    raw = b"".join(b"\x00" + bytes((r, g, b)) * w for _ in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)


# ---- digest -> document ------------------------------------------------------------


class Importer:
    def __init__(self, root: ET.Element, now: str):
        self.root = root
        self.now = now
        self.reviews: list[dict[str, Any]] = []
        self.contacts: list[dict[str, Any]] = []

    def entity(self, id: str, position: int, **fields: Any) -> dict[str, Any]:
        return {
            "id": id,
            "position": position,
            "created_at": self.now,
            "updated_at": self.now,
            **fields,
        }

    def review(
        self,
        el: ET.Element,
        review_id: str,
        collection: str,
        id: str | None,
        field: str | None = None,
        fallback_note: str | None = None,
    ) -> None:
        """Review item for a needs-review or low/medium-confidence element."""
        flagged = el.get("needs-review") == "true"
        uncertain = el.get("confidence") in ("low", "medium")
        if not (flagged or uncertain):
            return
        note = (
            el.get("note")
            or fallback_note
            or (
                "We were less sure about this than the rest of your site."
                " Please check it is described correctly."
            )
        )
        target = {"collection": collection, "id": id, "field": field}
        self.reviews.append(self.entity(review_id, len(self.reviews), target=target, note=note))

    # -- sections --

    def build_contacts(self) -> None:
        contact = self.root.find("contact")
        assert contact is not None
        primary_seen: set[str] = set()

        def add(el: ET.Element, kind: str, id: str, label: str = "") -> None:
            self.contacts.append(
                self.entity(
                    id,
                    len(self.contacts),
                    kind=kind,
                    label=label,
                    value=text(el),
                    primary=kind not in primary_seen,
                )
            )
            primary_seen.add(kind)

        for el in contact.findall("phone"):
            add(el, "phone", f"{slug(el.get('label', 'main'))}-phone", el.get("label", ""))
        for el in contact.findall("email"):
            add(el, "email", f"{slug(el.get('label', 'general'))}-email", el.get("label", ""))
        booking = contact.find("booking-url")
        if booking is not None:
            add(booking, "booking", "booking")
        store = self.root.find("products/store-url")
        if store is not None:
            add(store, "store", "store")

        for el in contact.findall("email"):
            self.review(
                el,
                f"contact-{slug(el.get('label', ''))}-email",
                "contacts",
                f"{slug(el.get('label', ''))}-email",
                "value",
            )
        if booking is not None:
            self.review(booking, "contact-booking", "contacts", "booking", "value")

    def contact_by_value(self, value: str, kind: str | None = None) -> str | None:
        digits = re.sub(r"\D", "", value)
        for c in self.contacts:
            if kind and c["kind"] != kind:
                continue
            if c["value"] == value or (
                c["kind"] == "phone" and digits and re.sub(r"\D", "", c["value"]) == digits
            ):
                return c["id"]
        return None

    def build(self) -> tuple[dict[str, Any], dict[str, Any]]:
        r = self.root
        biz_el = r.find("business")
        assert biz_el is not None
        business_name = text(biz_el.find("name"))
        self.review_business_fields(biz_el)
        self.build_contacts()

        # trust items -> business facts / affiliations
        trust = {i.get("id"): i for i in r.findall("trust/item")}
        brands_detail = next(
            (i.find("detail") for i in r.findall("trust/item") if i.get("kind") == "brands"), None
        )
        area_item = next(
            (i for i in r.findall("trust/item") if i.get("kind") == "service-area"), None
        )
        milestone = next((i for i in r.findall("trust/item") if i.get("kind") == "milestone"), None)
        payment = next((i for i in r.findall("trust/item") if i.get("kind") == "payment"), None)
        affiliations = []
        for item in r.findall("trust/item"):
            if item.get("kind") == "affiliation":
                name = re.sub(r"^Member of\s+", "", text(item.find("label")))
                affiliations.append(
                    self.entity(
                        item.get("id", slug(name)),
                        len(affiliations),
                        name=name,
                        description=text(item.find("detail")),
                    )
                )
        if brands_detail is not None:
            self.review(brands_detail, "business-brands", "business", None, "brands")
        del trust

        service_areas = (
            split_list(re.sub(r"^Serving\s+", "", text(area_item.find("label"))))
            if area_item is not None
            else []
        )
        year = (
            re.search(r"\b(19|20)\d{2}\b", text(milestone.find("label")))
            if milestone is not None
            else None
        )
        payment_text = text(payment.find("label")).lower() if payment is not None else ""
        payment_methods = [m for m in ("cash", "credit", "debit", "cheque") if m in payment_text]

        # assets and who refers to them
        asset_els = {a.get("id"): a for a in r.findall("assets/asset")}
        referrers: dict[str, str] = {}  # asset id -> file stem named after the referrer

        def use(asset_id: str | None, stem: str) -> str | None:
            if asset_id:
                referrers.setdefault(asset_id, stem)
            return asset_id

        logo = use(need(r, "brand/logo").get("asset"), f"{slug(business_name)}-logo")
        favicon = use(need(r, "brand/favicon").get("asset"), f"{slug(business_name)}-favicon")

        # location + hours
        loc_el = r.find("locations/location")
        assert loc_el is not None
        addr = need(loc_el, "address")
        hours_el = r.find("hours")
        hours = []
        for day in hours_el.findall("day") if hours_el is not None else []:
            if day.get("closed") == "true":
                hours.append({"day": day.get("name", "").lower(), "closed": True})
            else:
                hours.append(
                    {
                        "day": day.get("name", "").lower(),
                        "open": day.get("open"),
                        "close": day.get("close"),
                    }
                )
        loc_id = loc_el.get("id", "main")
        location = self.entity(
            loc_id,
            0,
            name=text(loc_el.find("name")),
            address={
                "street": text(addr.find("street")),
                "city": text(addr.find("city")),
                "region": text(addr.find("region")),
                "postal_code": text(addr.find("postal-code")),
                "country": text(addr.find("country")) or "CA",
            },
            map_url=text(loc_el.find("map-url")) or None,
            phone=self.contact_by_value(text(loc_el.find("phone")), "phone"),
            hours=hours,
        )
        map_el = loc_el.find("map-url")
        if map_el is not None:
            self.review(map_el, f"location-{loc_id}-map-url", "locations", loc_id, "mapUrl")
        if hours_el is not None:
            self.review(hours_el, f"location-{loc_id}-hours", "locations", loc_id, "hours")

        # services and categories
        categories: dict[str, dict[str, Any]] = {}
        services = []
        design_bindings: dict[str, dict[str, str]] = {"services": {}, "product_categories": {}}
        for el in r.findall("services/service"):
            cat_name = el.get("category", "Other")
            cat_id = slug(cat_name)
            categories.setdefault(cat_id, self.entity(cat_id, len(categories), name=cat_name))
            sid = el.get("id", "")
            img = el.find("image")
            image = img.get("asset") if img is not None else None
            if image:
                use(image, sid)
                if image not in BUSINESS_IMAGES:
                    design_bindings["services"][sid] = image
            services.append(
                self.entity(
                    sid,
                    len(services),
                    category=cat_id,
                    name=text(el.find("name")),
                    summary=text(el.find("summary")),
                    description=text(el.find("description")),
                    audience=text(el.find("audience")) or None,
                    image=image if image in BUSINESS_IMAGES else None,
                )
            )
            self.review(el, f"service-{sid}", "services", sid)

        product_categories = []
        for el in r.findall("products/category"):
            pid = el.get("id", "")
            img = el.find("image")
            image = img.get("asset") if img is not None else None
            if image:
                use(image, pid)
                if image not in BUSINESS_IMAGES:
                    design_bindings["product_categories"][pid] = image
            product_categories.append(
                self.entity(
                    pid,
                    len(product_categories),
                    name=text(el.find("name")),
                    description=text(el.find("description")),
                    image=image if image in BUSINESS_IMAGES else None,
                )
            )

        staff = []
        for el in r.findall("team/member"):
            mid = el.get("id", "")
            bio = text(el.find("bio"))
            photo_el = el.find("photo")
            photo = use(photo_el.get("asset"), mid) if photo_el is not None else None
            staff.append(
                self.entity(
                    mid,
                    len(staff),
                    name=text(el.find("name")),
                    role=text(el.find("role")),
                    credentials=text(el.find("credentials")),
                    bio=bio,
                    languages=[tag for name, tag in LANGUAGES.items() if name in bio],
                    photo=photo,
                )
            )
            self.review(el, f"staff-{mid}", "staff", mid)

        faqs = [
            self.entity(
                el.get("id", ""),
                i,
                question=text(el.find("question")),
                answer=text(el.find("answer")),
            )
            for i, el in enumerate(r.findall("faqs/faq"))
        ]
        social_links = [
            self.entity(
                el.get("platform", ""),
                i,
                platform=el.get("platform"),
                url=el.get("href"),
                label=text(el),
            )
            for i, el in enumerate(r.findall("social/link"))
        ]

        actions = []
        for el in r.findall("ctas/cta"):
            href = el.get("href", "")
            channel = self.contact_by_value(href.removeprefix("tel:").removeprefix("mailto:"))
            assert channel is not None, f"no contact point for {href}"
            kind = next(c["kind"] for c in self.contacts if c["id"] == channel)
            label = text(el)
            if kind == "phone":  # the number comes from the channel, not the label
                label = re.sub(r"\s*\+?[\d][\d\s().-]{6,}$", "", label)
            actions.append(
                self.entity(
                    el.get("id", ""),
                    len(actions),
                    type=ACTION_TYPES[kind],
                    label=label,
                    channel=channel,
                )
            )

        # business images -> assets; others -> #design
        assets, design_images = [], {}
        for aid, el in asset_els.items():
            assert aid is not None
            stem = referrers.get(aid) or ("hero" if el.get("type") == "hero" else aid)
            alt = text(el.find("alt"))
            if aid in BUSINESS_IMAGES:
                atype = el.get("type") if el.get("type") in ("logo", "favicon") else "photo"
                assets.append(
                    self.entity(
                        aid,
                        len(assets),
                        type=atype,
                        path=f"{stem}.png",
                        alt=alt or f"{business_name} {atype}",
                    )
                )
                if aid not in referrers:  # a business photo nothing refers to: ask
                    self.reviews.append(
                        self.entity(
                            f"asset-{aid}",
                            len(self.reviews),
                            target={"collection": "assets", "id": aid},
                            note="Is this a photo of your actual practice? If it is a stock image,"
                            " we will move it to the site design; if you have a real photo of"
                            " your clinic, please send it.",
                        )
                    )
            else:
                design_images[aid] = {
                    "file": f"assets/{stem}.png",
                    "alt": alt or aid.replace("-", " ").capitalize(),
                }

        business = {
            "name": business_name,
            "legal_name": text(biz_el.find("legal-name")) or None,
            "tagline": text(biz_el.find("tagline")),
            "category": text(biz_el.find("category")),
            "description": text(biz_el.find("description")),
            "logo": logo,
            "favicon": favicon,
            "brands": split_list(text(brands_detail)) if brands_detail is not None else [],
            "service_areas": service_areas,
            "serving_since": int(year.group(0)) if year else None,
            "payment_methods": payment_methods,
            "created_at": self.now,
            "updated_at": self.now,
        }
        document = {
            "business": business,
            "contacts": self.contacts,
            "locations": [location],
            "service_categories": list(categories.values()),
            "services": services,
            "product_categories": product_categories,
            "staff": staff,
            "faqs": faqs,
            "social_links": social_links,
            "affiliations": affiliations,
            "actions": actions,
            "assets": assets,
            "reviews": self.reviews,
        }
        hero = next((a for a, e in asset_els.items() if e.get("type") == "hero"), None)
        design = {
            "slots": {
                **(
                    {
                        "hero": {
                            "image": design_images[hero]["file"],
                            "alt": design_images[hero]["alt"],
                        }
                    }
                    if hero
                    else {}
                ),
                "about": {"image": "businessdata:about-clinic"},
                "service-illustration": {
                    "per": "services",
                    "default": None,
                    "bindings": {
                        sid: {"image": design_images[a]["file"], "alt": design_images[a]["alt"]}
                        for sid, a in design_bindings["services"].items()
                    },
                },
                "product-category-illustration": {
                    "per": "product_categories",
                    "default": None,
                    "bindings": {
                        pid: {"image": design_images[a]["file"], "alt": design_images[a]["alt"]}
                        for pid, a in design_bindings["product_categories"].items()
                    },
                },
            }
        }
        return document, design

    def review_business_fields(self, biz_el: ET.Element) -> None:
        for child in biz_el:
            field = re.sub(r"-(\w)", lambda m: m.group(1).upper(), child.tag)
            self.review(child, f"business-{child.tag}", "business", None, field)


def main() -> None:
    force = "--force" in sys.argv
    ws = WORKSPACE
    if ws.root.exists():
        if not force:
            sys.exit(f"{ws.root} exists; rerun with --force to rebuild it")
        shutil.rmtree(ws.root)
    for d in (ws.resources_dir, ws.assets_dir, ws.templates_dir, ws.site_dir):
        d.mkdir(parents=True, exist_ok=True)

    now = datetime.now(UTC).isoformat()
    document, design = Importer(ET.parse(DIGEST).getroot(), now).build()
    data = BusinessData.model_validate(document)  # full integrity check

    for asset in data.assets:
        placeholder_png(ws.resources_dir / asset.path, asset.id)
    design_files = {
        b["image"]
        for s in design["slots"].values()
        for b in ([s] if "image" in s else s.get("bindings", {}).values())
        if not b["image"].startswith("businessdata:")
    }
    for f in design_files:
        placeholder_png(ws.design_dir / f, f)
    (ws.design_dir / "design.json").write_text(json.dumps(design, indent=2) + "\n")

    engine = create_db_engine(ws.database_file)
    init_db(engine)
    with session_factory(engine)() as session:
        documents.initialize(session, data)
        session.commit()

    print(f"workspace:  {ws.root.relative_to(ROOT)}")
    print(f"database:   {ws.database_file.relative_to(ROOT)}")
    print(f"resources:  {len(data.assets)} placeholder images")
    print(f"design:     {len(design_files)} placeholder images, design.json")
    print(f"reviews:    {len(data.reviews)} open review items")


if __name__ == "__main__":
    main()
