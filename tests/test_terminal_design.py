"""The `terminal` design: a monochrome terminal view of #businessdata (notes 2026-09-30)."""

import re
import shutil
from pathlib import Path

import pytest
from conftest import site, sites_dir

from intelliw.render import RenderOptions, check_design, render
from intelliw.workspace import Workspace

WHITBY = Path(__file__).parent.parent / "workspaces" / "whitby_eye_care"
DESIGN = WHITBY / "design" / "terminal"
COLLECTIONS = [
    "contacts", "locations", "service_categories", "services", "product_categories",
    "staff", "faqs", "social_links", "affiliations", "actions", "assets", "reviews",
]  # fmt: skip


def test_the_design_passes_the_check():
    assert check_design(DESIGN) == []


def test_styling_is_monospace_and_grey_only():
    css = (DESIGN / "static" / "terminal.css").read_text() + (DESIGN / "404.html.j2").read_text()
    for family in re.findall(r"font(?:-family)?:\s*([^;]+);", css):
        assert "mono" in family.lower() or family == "inherit", family
    for colour in re.findall(r"#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b", css):
        rgb = [c * 2 for c in colour] if len(colour) == 3 else re.findall("..", colour)
        assert len(set(rgb)) == 1, f"#{colour} is not a grey"
    assert (DESIGN / "static" / "IBM-PLEX-MONO-LICENSE.txt").is_file()


@pytest.fixture
def ws(data_ws: Workspace) -> Workspace:
    shutil.copytree(DESIGN, data_ws.design("terminal"))
    return data_ws


def _entity_pages(out: Path) -> dict[str, set[str]]:
    return {
        c: {p.name for p in (out / c).iterdir() if (p / "index.html").is_file()}
        for c in COLLECTIONS
        if (out / c).is_dir()
    }


@pytest.mark.parametrize("include_hidden", [False, True])
def test_every_entity_is_reachable_from_the_front_page(ws, include_hidden):
    result = render(
        ws, "terminal", RenderOptions(include_hidden=include_hidden), sites_dir=sites_dir(ws)
    )
    out = result.output_dir
    front = (out / "index.html").read_text()
    pages = _entity_pages(out)
    assert sum(len(ids) for ids in pages.values()) > 20
    for collection, ids in pages.items():
        assert f'id="{collection}"' in front, collection
        for id_ in ids:
            assert f'href="{collection}/{id_}/"' in front, (collection, id_)
    assert (out / "business" / "index.html").is_file() and 'href="business/"' in front


def test_hidden_entities_only_with_include_hidden(ws):
    render(ws, "terminal", sites_dir=sites_dir(ws))
    out = site(ws, "terminal")
    assert not (out / "contacts" / "appointments-email").exists()
    assert "[!] preview" not in (out / "index.html").read_text()

    render(ws, "terminal", RenderOptions(include_hidden=True), sites_dir=sites_dir(ws))
    page = (out / "contacts" / "appointments-email" / "index.html").read_text()
    assert "hidden: not on the public site" in page
    assert "[!] preview" in page
    assert "dr-peter-chan" in (out / "index.html").read_text()  # in the trash


def test_entity_pages_link_references_and_neighbours(ws):
    render(ws, "terminal", RenderOptions(include_hidden=True), sites_dir=sites_dir(ws))
    page = (site(ws, "terminal") / "services" / "eye-exams" / "index.html").read_text()
    assert 'href="../../service_categories/eye-exams/"' in page  # its category
    assert 'href="../../faqs/how-often-eye-exam/"' in page  # a related FAQ
    assert 'rel="next"' in page and "[1/" in page
    assert '<a href="../../">home</a>' in page  # the breadcrumb leads home
    assert '<a href="../../#services">services</a>' in page  # and to the collection


def test_it_renders_the_whitby_data(tmp_path):
    ws = Workspace(tmp_path / "whitby_eye_care")
    shutil.copytree(WHITBY / "businessdata", ws.root / "businessdata")
    shutil.copytree(DESIGN, ws.design("terminal"))
    result = render(ws, "terminal", sites_dir=tmp_path / "sites")
    out = result.output_dir
    pages = _entity_pages(out)
    assert len(pages["services"]) == 12 and len(pages["reviews"]) == 10
    # a review of the business links to the business page; of an entity, to its page
    review = (out / "reviews" / "business-legal-name" / "index.html").read_text()
    assert 'href="../../business/"' in review
    review = (out / "reviews" / "staff-dr-andrea-chan" / "index.html").read_text()
    assert 'href="../../staff/dr-andrea-chan/"' in review
    # asset pages show the (published) image and what uses it
    asset = (out / "assets" / "dr-raniero-fernando" / "index.html").read_text()
    assert 'src="../../resources/dr-raniero-fernando.jpg"' in asset
    assert 'href="../../staff/dr-raniero-fernando/"' in asset
    # images are shown where they are used, and on the front page
    staff = (out / "staff" / "dr-raniero-fernando" / "index.html").read_text()
    assert 'src="../../resources/dr-raniero-fernando.jpg"' in staff
    front = (out / "index.html").read_text()
    assert 'src="resources/whitby-eye-care-logo.png"' in front
    for html in out.rglob("*.html"):
        assert "&lt;" not in html.read_text().replace("&lt; prev", ""), html
