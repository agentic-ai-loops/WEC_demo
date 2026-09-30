"""The `clinic-pro` example design (docs/notes/2026-09-27.md)."""

import shutil
from pathlib import Path

import pytest
from conftest import sites_dir

from intelliw.businessdata import mutations
from intelliw.businessdata.database import create_db_engine, session_factory
from intelliw.render import check_design, render
from intelliw.workspace import Workspace

DESIGN = Path(__file__).parent.parent / "workspaces" / "whitby_eye_care" / "design" / "clinic-pro"
ACTIONS = ("book-appointment", "call-clinic", "reorder-contact-lenses")


@pytest.fixture
def out(data_ws: Workspace) -> Path:
    shutil.copytree(DESIGN, data_ws.design("clinic-pro"))
    return render(data_ws, "clinic-pro", sites_dir=sites_dir(data_ws)).output_dir


def pages(out: Path) -> dict[str, str]:
    return {p.relative_to(out).as_posix(): p.read_text() for p in out.rglob("index.html")}


def test_the_design_passes_the_check():
    assert check_design(DESIGN) == []


def test_every_page_of_the_brief_is_rendered(out):
    rendered = set(pages(out))
    assert {"index.html", "booking/index.html", "faq/index.html"} <= rendered
    # one page per (visible) service and staff member of the sample
    assert "services/dry-eye-testing/index.html" in rendered
    assert "services/eye-exams/index.html" in rendered
    assert "staff/dr-raniero-fernando/index.html" in rendered
    assert "staff/dr-andrea-chan/index.html" in rendered
    assert not (out / "staff" / "dr-peter-chan").exists()  # deleted in the sample
    assert (out / "404.html").is_file()


def test_vendored_libraries_and_images_are_published(out):
    for f in (
        "assets/bootstrap.min.css",
        "assets/bootstrap.bundle.min.js",
        "assets/bootstrap-icons.min.css",
        "assets/fonts/bootstrap-icons.woff2",
        "assets/hero.jpg",
    ):
        assert (out / f).is_file(), f
    home = (out / "index.html").read_text()
    assert 'href="assets/bootstrap.min.css"' in home
    assert 'src="resources/logo.png"' in home  # a business image, through asset_url


def test_actions_are_on_every_page(out):
    for name, html in pages(out).items():
        for action in ACTIONS:
            assert f'data-action="{action}"' in html, (name, action)
        assert 'class="action-bar d-xl-none"' in html, name


def test_home_has_the_sections_of_the_brief(out):
    home = (out / "index.html").read_text()
    for section in ("about", "location", "doctors", "services", "products", "online"):
        assert f'id="{section}"' in home, section
    assert "https://maps.google.com/maps?q=" in home and "output=embed" in home
    assert 'href="staff/dr-raniero-fernando/"' in home
    assert 'href="services/dry-eye-testing/"' in home
    assert 'data-contact="store"' in home
    assert 'class="bi bi-facebook"' in home


def test_records_named_by_id_may_be_missing(out):
    """The sample has no `general-enquiries-email`: the design leaves it out, no error."""
    booking = (out / "booking" / "index.html").read_text()
    assert 'data-contact="general-enquiries-email"' not in booking
    assert 'data-contact="main-phone"' in booking
    assert 'data-contact="booking"' in booking


def test_links_are_relative_at_depth(out):
    page = (out / "services" / "dry-eye-testing" / "index.html").read_text()
    assert 'href="../../assets/site.css"' in page
    assert 'href="../../booking/"' in page
    assert 'href="../eye-exams/"' in page or 'href="../../services/eye-exams/"' in page


def test_no_escaped_markup_in_the_output(data_ws):
    """Tags written by the design (e.g. <wbr> in contact values) must not come out escaped."""
    engine = create_db_engine(data_ws.database_file)
    with session_factory(engine)() as s:
        mutations.create(
            s,
            "contacts",
            {
                "id": "general-enquiries-email",
                "kind": "email",
                "label": "General",
                "value": "hello@clinic.example.com",
            },
        )
        s.commit()
    engine.dispose()
    shutil.copytree(DESIGN, data_ws.design("clinic-pro"))
    out = render(data_ws, "clinic-pro", sites_dir=sites_dir(data_ws)).output_dir
    for name, html in pages(out).items():
        assert "&lt;" not in html, name
    booking = (out / "booking" / "index.html").read_text()
    assert "hello@<wbr>clinic.<wbr>example.<wbr>com" in booking


def test_light_and_dark_themes_with_a_toggle(out):
    home = (out / "index.html").read_text()
    assert '<html lang="en" class="no-js" data-bs-theme="light">' in home  # light by default
    assert ':root, [data-bs-theme="light"] {' in home and '[data-bs-theme="dark"] {' in home
    assert "--c-primary: #a95a2e;" in home and "--c-primary: #e39a6e;" in home  # from _config.json
    assert "--c-button: #0d0c0c;" in home and "--c-button: #f0dcc4;" in home
    assert "data-theme-toggle" in home
    assert 'localStorage.getItem("clinic-theme")' in home  # applied before paint
    js = (out / "assets" / "site.js").read_text()
    assert 'localStorage.setItem("clinic-theme", next)' in js


def test_fonts_are_vendored_with_their_licences(out):
    """Editorial display/heading/UI faces; body text stays highly readable."""
    css = (out / "assets" / "site.css").read_text()
    fonts = out / "assets" / "fonts"
    for family, files, licence in [
        ("Atkinson Hyperlegible", ["atkinson-hyperlegible-latin-400-normal"], "ATKINSON"),
        ("Fraunces", ["fraunces-latin-opsz-normal"], "FRAUNCES"),
        ("Cormorant Garamond", ["cormorant-garamond-latin-600-normal"], "CORMORANT-GARAMOND"),
        ("Jost", ["jost-latin-500-normal"], "JOST"),
    ]:
        assert f'font-family: "{family}"' in css, family
        for f in files:
            assert (fonts / f"{f}.woff2").is_file(), f
        assert (out / "assets" / f"{licence}-LICENSE.txt").is_file(), licence
    assert '--bs-body-font-family: "Atkinson Hyperlegible"' in css


def test_the_site_never_says_closed(out):
    """Hours without opening times show a dash; the script says when the clinic opens next."""
    for name, html in pages(out).items():
        assert "Closed" not in html, name
    js = (out / "assets" / "site.js").read_text()
    assert "Closed" not in js and "Opens ${when} at" in js


def test_hero_actions_only_where_the_action_bar_is_hidden(out):
    """Phones get the actions in the bottom bar (d-xl-none), so the hero hides them there."""
    home = (out / "index.html").read_text()
    assert '<div class="hero-actions d-none d-xl-flex' in home
    assert '<div class="action-bar d-xl-none"' in home


def test_get_in_touch_card_can_pop(out):
    """The card's hover/press transform must not be overridden by the scroll fade-in."""
    home = (out / "index.html").read_text()
    assert '<div class="card contact-panel">' in home
    assert "contact-panel reveal" not in home
    css = (out / "assets" / "site.css").read_text()
    assert ".contact-panel:hover" in css and ".contact-panel:active" in css


def test_larger_type_scale_for_readability(out):
    css = (out / "assets" / "site.css").read_text()
    assert "html { font-size: 112.5%; }" in css  # 18px root: every rem size grows
    home = (out / "index.html").read_text()
    assert "navbar-expand-xl" in home  # the full navbar only where the larger text fits
