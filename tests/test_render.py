"""The renderer and the design check (docs/design/04-design.md)."""

import json
import shutil
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from textwrap import dedent

import pytest
from conftest import site, sites_dir
from sqlalchemy.orm import Session

from intelliw.businessdata import documents, mutations, queries
from intelliw.businessdata.database import create_db_engine, session_factory
from intelliw.render import (
    CheckFailed,
    DesignNotFound,
    RenderError,
    RenderOptions,
    check_design,
    check_workspace,
    list_designs,
    version_name,
)
from intelliw.render import render as _render
from intelliw.render.links import relative_url
from intelliw.workspace import Workspace

EXAMPLE_DESIGN = Path(__file__).parent.parent / "workspaces" / "whitby_eye_care" / "design"

Design = Callable[..., Path]


@pytest.fixture
def ws(data_ws: Workspace) -> Workspace:
    return data_ws


@pytest.fixture
def design(ws: Workspace) -> Design:
    """`design({path: text}, name="d")` writes a design bundle into the workspace."""

    def write(files: dict[str, str], name: str = "d") -> Path:
        root = ws.design(name)
        for rel, text in files.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(dedent(text))
        return root

    return write


@contextmanager
def db(ws: Workspace) -> Iterator[Session]:
    engine = create_db_engine(ws.database_file)
    try:
        with session_factory(engine)() as s:
            yield s
            s.commit()
    finally:
        engine.dispose()


def render(ws: Workspace, design: str, options: RenderOptions = RenderOptions(), **kw):  # noqa: B008
    """Render into the test workspace's run dir."""
    return _render(ws, design, options, sites_dir=sites_dir(ws), **kw)


def out(ws: Workspace, design: str = "d") -> Path:
    return site(ws, design)


def text(ws: Workspace, rel: str, design: str = "d") -> str:
    return (out(ws, design) / rel).read_text()


SERVICE_PAGES = {
    "services/[slug]/params.gql": "query { services { slug: id } }",
    "services/[slug]/page.gql": """
        query($slug: ID!) { service(id: $slug) { name category { name } } }
    """,
    "services/[slug]/page.html.j2": (
        "{{ data.service.name }} in {{ data.service.category.name }} ({{ params.slug }})"
    ),
}


# ---- pages and files ---------------------------------------------------------------------


def test_static_and_dynamic_pages(ws, design):
    design(
        {
            "page.gql": "{ business { name } }",
            "page.html.j2": "Home of {{ data.business.name }}",
            "about/page.html.j2": "About ({{ data }})",
            **SERVICE_PAGES,
        }
    )
    result = render(ws, "d")
    assert result.written and result.version_name == "active"
    assert text(ws, "index.html") == "Home of Whitby Eye Care"
    assert text(ws, "about/index.html") == "About ({})"
    assert text(ws, "services/dry-eye-testing/index.html") == (
        "Dry Eye Testing in Eye health (dry-eye-testing)"
    )
    assert result.pages == 2 + 3  # home, about, three services
    assert not (out(ws) / "services" / "[slug]").exists()


def test_nested_segments_receive_enclosing_values(ws, design):
    design(
        {
            "c/[category]/params.gql": "query { serviceCategories { category: id } }",
            "c/[category]/[slug]/params.gql": """
                query($category: ID!) { services(category: $category) { slug: id } }
            """,
            "c/[category]/[slug]/page.gql": "query($slug: ID!) { service(id: $slug) { name } }",
            "c/[category]/[slug]/page.html.j2": "{{ params.category }}/{{ params.slug }}",
        }
    )
    render(ws, "d")
    assert text(ws, "c/eye-health/dry-eye-testing/index.html") == "eye-health/dry-eye-testing"
    assert text(ws, "c/eye-exams/eye-exams/index.html") == "eye-exams/eye-exams"
    assert not (out(ws) / "c" / "eye-exams" / "dry-eye-testing").exists()


def test_site_query_templates_copies_and_exclusions(ws, design):
    design(
        {
            "site.gql": "{ business { name } }",
            "_layouts/base.html.j2": "[{% block main %}{% endblock %}]",
            "_partials/brand.html.j2": "<b>{{ site.business.name }}</b>",
            "page.html.j2": """{% extends "_layouts/base.html.j2" %}{% block main %}"""
            """{% include "_partials/brand.html.j2" %}{% endblock %}""",
            "style.css.j2": "/* {{ site.business.name }} */",
            "sub/deep/note.txt.j2": "{{ site.business.name }} at {{ path }}",
            "hero.png": "png",
            "_notes.md": "not published",
            ".hidden": "ignored",
        }
    )
    render(ws, "d")
    files = sorted(p.relative_to(out(ws)).as_posix() for p in out(ws).rglob("*") if p.is_file())
    assert files == [
        "hero.png",
        "index.html",
        "render.json",
        "style.css",
        "sub/deep/note.txt",
    ]
    assert text(ws, "index.html") == "[<b>Whitby Eye Care</b>]"
    assert text(ws, "style.css") == "/* Whitby Eye Care */"
    assert text(ws, "sub/deep/note.txt") == "Whitby Eye Care at sub/deep/note.txt"


def test_html_is_escaped_other_templates_are_not(ws, design):
    with db(ws) as s:
        mutations.update_business(s, {"tagline": "Eyes & <glasses>"})
    design(
        {
            "site.gql": "{ business { tagline } }",
            "page.html.j2": "{{ site.business.tagline }}",
            "x.txt.j2": "{{ site.business.tagline }}",
        }
    )
    render(ws, "d")
    assert text(ws, "index.html") == "Eyes &amp; &lt;glasses&gt;"
    assert text(ws, "x.txt") == "Eyes & <glasses>"


# ---- render options ------------------------------------------------------------------------


def test_hidden_entities_are_left_out_unless_included(ws, design):
    with db(ws) as s:
        mutations.update(s, "services", "dry-eye-testing", {"hidden": True})
    design(
        {
            "page.gql": "{ services { id } }",
            "page.html.j2": "{{ data.services | map(attribute='id') | join(',') }}",
            **SERVICE_PAGES,
        }
    )
    render(ws, "d")
    assert text(ws, "index.html") == "eye-exams,computer-related-eye-strain"
    assert not (out(ws) / "services" / "dry-eye-testing").exists()

    render(ws, "d", RenderOptions(include_hidden=True))
    assert text(ws, "index.html") == "eye-exams,dry-eye-testing,computer-related-eye-strain"
    assert (out(ws) / "services" / "dry-eye-testing" / "index.html").is_file()
    info = json.loads(text(ws, "render.json"))
    assert info["include_hidden"] is True


def test_snapshot_render_goes_to_the_design_folder(ws, design):
    with db(ws) as s:
        documents.take_snapshot(s, "Spring 2026")  # snapshot 2
        mutations.update_business(s, {"name": "Renamed"})
    design(
        {
            "site.gql": "{ business { name } }",
            "page.html.j2": "{{ site.business.name }} {{ render.businessdata.version_name }}",
        }
    )
    render(ws, "d")
    assert text(ws, "index.html") == "Renamed active"
    by_tag = render(ws, "d", RenderOptions(snapshot="Spring 2026"))
    assert by_tag.version_name == "2_Spring-2026"
    # every render of a design goes to its one folder; render.json says which data
    assert by_tag.output_dir == out(ws)
    assert text(ws, "index.html") == "Whitby Eye Care 2_Spring-2026"
    assert json.loads(text(ws, "render.json"))["businessdata"]["snapshot"] == "Spring 2026"
    by_number = render(ws, "d", RenderOptions(version=2), dry_run=True)
    assert by_number.version_name == "2_Spring-2026"
    assert sorted(p.name for p in (sites_dir(ws) / ws.root.name).iterdir()) == ["d"]


def test_version_names():
    assert version_name(None, None) == "active"
    assert version_name(3, None) == "3"
    assert version_name(4, "Spring 2026") == "4_Spring-2026"
    assert version_name(5, "a/b  c?") == "5_a-b-c-"


def test_render_metadata_is_published_and_in_templates(ws, design):
    with db(ws) as s:
        mutations.update_business(s, {"tagline": "New"})
    design(
        {
            "_config.json": '{"title": "Test design", "options": {"accent": "red"}}',
            "page.html.j2": "{{ render.design.title }}|{{ config.options.accent }}|"
            "{{ render.businessdata.modified }}",
        }
    )
    render(ws, "d")
    info = json.loads(text(ws, "render.json"))
    assert info["design"] == {"name": "d", "title": "Test design"}
    data = info["businessdata"]
    assert data["version_name"] == "active" and data["version"] is None
    assert data["based_on"]["number"] == 1 and data["modified"] is True
    assert data["updated_at"] > "2026-09-24T14:05:12"  # the tagline change
    assert info["generator"].startswith("intelliw")
    assert set(info) == {"rendered_at", "generator", "design", "businessdata", "include_hidden"}
    assert text(ws, "index.html") == "Test design|red|True"


# ---- images --------------------------------------------------------------------------------


def test_only_selected_business_images_are_published(ws, design):
    design(
        {
            "site.gql": "{ business { logo { __typename id path alt } } }",
            "page.html.j2": '<img src="{{ asset_url(site.business.logo) }}">',
            **SERVICE_PAGES,
            "services/[slug]/page.gql": """query($slug: ID!) {
                service(id: $slug) { name image { __typename id path } category { name } } }""",
            "services/[slug]/page.html.j2": "{% if data.service.image %}"
            '<img src="{{ asset_url(data.service.image) }}">{% endif %}',
        }
    )
    result = render(ws, "d")
    resources = sorted(p.name for p in (out(ws) / "resources").iterdir())
    assert resources == ["logo.png", "unnamed.png"]  # logo + the dry-eye device
    assert result.images == 2
    assert text(ws, "index.html") == '<img src="resources/logo.png">'
    assert text(ws, "services/dry-eye-testing/index.html") == (
        '<img src="../../resources/unnamed.png">'
    )


# ---- links ---------------------------------------------------------------------------------


def test_relative_url():
    page = "services/eye-exams/index.html"
    assert relative_url(page, "") == "../../"
    assert relative_url(page, "services/") == "../"
    assert relative_url(page, "style.css") == "../../style.css"
    assert relative_url(page, "services/eye-exams/") == "./"
    assert relative_url("index.html", "") == "./"
    assert relative_url("index.html", "services/") == "services/"
    assert relative_url("style.css", "hero.png") == "hero.png"
    with pytest.raises(ValueError):
        relative_url("index.html", "/abs")


def test_links_work_at_every_depth(ws, design):
    links = (
        "<a href=\"{{ url('') }}\">home</a><a href=\"{{ url('services/eye-exams/') }}\">s</a>"
        '<link href="{{ url(\'style.css\') }}"><a href="https://example.com/x">ext</a>'
        '<a href="tel:123">t</a><a href="#top">top</a>'
    )
    design(
        {
            "page.html.j2": links,
            "style.css": "body { background: url(hero.png) }",
            "hero.png": "png",
            **SERVICE_PAGES,
            "services/[slug]/page.html.j2": links,
        }
    )
    render(ws, "d")
    assert 'href="../../"' in text(ws, "services/dry-eye-testing/index.html")
    assert 'href="services/eye-exams/"' in text(ws, "index.html")


@pytest.mark.parametrize(
    ("page", "message"),
    [
        ('<a href="/about/">x</a>', "root-relative link '/about/'"),
        ('<img src="missing.png">', "missing.png is not in the site"),
        ("<a href=\"{{ url('nope/') }}\">x</a>", "nope/index.html is not in the site"),
        ("<style>b { background: url('/x.png') }</style>", "root-relative link '/x.png'"),
        ('<a href="../outside.html">x</a>', "points outside the site"),
    ],
)
def test_link_check_fails_the_render(ws, design, page, message):
    design({"page.html.j2": page})
    with pytest.raises(CheckFailed) as exc:
        render(ws, "d")
    assert exc.value.stage == "link check"
    assert any(message in p.message for p in exc.value.problems), exc.value.problems
    assert not sites_dir(ws).exists() or not any(sites_dir(ws).iterdir())


# ---- failures -----------------------------------------------------------------------------


GOOD = {
    "page.html.j2": "good",
    **SERVICE_PAGES,
}

FAILURES: dict[str, tuple[dict[str, str], str]] = {
    "template error": ({"page.html.j2": "{{ data.nope.deeper }}"}, "page.html.j2"),
    "sandbox": ({"page.html.j2": "{{ ''.__class__.__mro__ }}"}, "page.html.j2"),
    "collision": ({"services/eye-exams/page.html.j2": "static"}, "services/eye-exams"),
    "bad params": (
        {"services/[slug]/params.gql": "query { services { slug: name } }"},
        "services/[slug]/params.gql",
    ),
    "template not found": ({"page.html.j2": '{% include "_nope.html.j2" %}'}, "page.html.j2"),
}


@pytest.mark.parametrize("case", sorted(FAILURES))
def test_a_failed_render_leaves_the_previous_output(ws, design, case):
    design(GOOD)
    render(ws, "d")
    before = text(ws, "index.html")
    files, where = FAILURES[case]
    design(files)
    with pytest.raises(RenderError) as exc:
        render(ws, "d")
    assert where in str(exc.value), str(exc.value)
    assert text(ws, "index.html") == before
    assert sorted(p.name for p in (sites_dir(ws) / ws.root.name).iterdir()) == ["d"]  # no temp


def test_a_graphql_error_fails_the_render(ws, design, monkeypatch):
    design(GOOD)
    render(ws, "d")

    def broken(*_args, **_kwargs):
        raise queries.NotFound("no business")

    monkeypatch.setattr(queries, "get_business", broken)
    design({"page.gql": "{ business { name } }"})
    with pytest.raises(RenderError) as exc:
        render(ws, "d")
    assert exc.value.problem.file == "page.gql"
    assert "no business" in exc.value.problem.message
    assert text(ws, "index.html") == "good"


def test_missing_asset_file_fails_the_render(ws, design):
    (ws.resources_dir / "logo.png").unlink()
    design({"site.gql": "{ business { logo { __typename id path } } }", "page.html.j2": "x"})
    with pytest.raises(RenderError, match="businessdata/resources/logo.png"):
        render(ws, "d")


def test_an_unsafe_asset_path_in_the_data_fails_the_render(ws, design):
    import sqlite3

    db = sqlite3.connect(ws.database_file)
    db.execute("UPDATE assets SET path = '../../../etc/passwd' WHERE version = 0 AND id = 'logo'")
    db.commit()
    db.close()
    design({"site.gql": "{ business { logo { __typename id path } } }", "page.html.j2": "x"})
    with pytest.raises(RenderError, match="string_pattern_mismatch"):  # refused on reading
        render(ws, "d")
    assert not sites_dir(ws).exists()


def test_design_symlinks_out_of_the_design_are_refused(ws, design, tmp_path_factory):
    secret = tmp_path_factory.mktemp("outside") / "secret.txt"
    secret.write_text("secret")
    root = design({"page.html.j2": "x"})
    (root / "leak.txt").symlink_to(secret)
    with pytest.raises(RenderError, match="outside the design folder"):
        render(ws, "d")
    (root / "leak.txt").unlink()
    (root / "_partials").mkdir()
    (root / "_partials" / "leak.html.j2").symlink_to(secret)
    (root / "page.html.j2").write_text('{% include "_partials/leak.html.j2" %}')
    with pytest.raises(RenderError, match="links outside the design folder"):
        render(ws, "d")
    assert not sites_dir(ws).exists()


def test_template_errors_name_the_line(ws, design):
    design(
        {
            "_layouts/b.html.j2": "ok\n{{ nope }}",
            "page.html.j2": '{% extends "_layouts/b.html.j2" %}',
        }
    )
    with pytest.raises(RenderError) as exc:
        render(ws, "d")
    assert exc.value.problem.file == "_layouts/b.html.j2"
    assert exc.value.problem.line == 2


def test_dry_run_writes_nothing_but_still_fails(ws, design):
    design(GOOD)
    result = render(ws, "d", dry_run=True)
    assert not result.written and not sites_dir(ws).exists()
    assert "services/eye-exams/index.html" in result.files
    design({"page.html.j2": "{{ nope }}"})
    with pytest.raises(RenderError):
        render(ws, "d", dry_run=True)


# ---- designs -----------------------------------------------------------------------------


def test_designs_are_separate(ws, design):
    design({"page.html.j2": "A"}, name="a")
    design({"page.html.j2": '{% include "../a/page.html.j2" %}'}, name="b")
    design({"page.html.j2": "C"}, name="_shared")
    assert list_designs(ws) == ["a", "b"]
    render(ws, "a")
    assert text(ws, "index.html", "a") == "A"
    with pytest.raises(RenderError):  # templates load only from their own design
        render(ws, "b")
    with pytest.raises(DesignNotFound):
        render(ws, "_shared")
    with pytest.raises(DesignNotFound):
        render(ws, "missing")


def test_the_example_design_renders(ws):
    shutil.copytree(EXAMPLE_DESIGN / "clinic", ws.design("clinic"))
    result = render(ws, "clinic")
    assert result.pages == 1 + 3
    assert result.output_dir == site(ws, "clinic")
    home = text(ws, "index.html", "clinic")
    assert "Whitby Eye Care" in home and 'href="services/dry-eye-testing/"' in home
    assert (out(ws, "clinic") / "resources" / "logo.png").is_file()


# ---- design check --------------------------------------------------------------------------


def problems(design_dir: Path) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for p in check_design(design_dir):
        found.setdefault(p.file, []).append(p.message)
    return found


def test_design_check_structure(design):
    root = design(
        {
            "_config.json": '{"title": 1, "extra": true}',
            "broken.html.j2": "{% if %}",
            "resources/x.png": "png",
            "render.json.j2": "{}",
            "about/page.gql": "{ business { name } }",
            "about/params.gql": "{ services { id } }",
            "about/site.gql": "{ business { name } }",
            "about/other.gql": "{ business { name } }",
            "[bad-name]/params.gql": "{ services { id } }",
            "[slug]/page.html.j2": "x",
            "[a]/params.gql": "{ services { a: id } }",
            "[a]/[a]/params.gql": "{ services { a: id } }",
        }
    )
    found = problems(root)
    assert found["_config.json"][0].startswith("invalid")
    assert "broken.html.j2" in found
    assert "reserved" in found["resources"][0]
    assert "reserved" in found["render.json.j2"][0]
    assert "without a sibling page.html.j2" in found["about/page.gql"][0]
    assert "outside a [name] folder" in found["about/params.gql"][0]
    assert "only allowed at the top level" in found["about/site.gql"][0]
    assert "unknown query file" in found["about/other.gql"][0]
    assert "bad segment name" in found["[bad-name]"][0]
    assert "without params.gql" in found["[slug]"][0]
    assert "repeats an enclosing segment" in found["[a]/[a]"][0]


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("{ business { ", "syntax error"),
        ('mutation { takeSnapshot(tag: "x") { number } }', "exactly one query operation"),
        ("query A { hello { message } } query B { hello { message } }", "exactly one query"),
        ("{ business { nope } }", "Cannot query field 'nope'"),
        ("query($other: ID!) { service(id: $other) { name } }", "variable $other"),
        ("{ staff(includeHidden: true) { id } }", "argument includeHidden"),
        ('{ business(snapshot: "x") { name } }', "argument snapshot"),
        ("{ business { logo { id path } } }", "missing: __typename"),
        ("{ assets { __typename id } }", "missing: path"),
        ("{ business { logo { ...L } } } fragment L on Asset { __typename id }", "missing: path"),
        ("{ business { logo { __typename id path: alt } } }", "missing: path"),
        (
            "{ reviews { target { entity { __typename ... on Asset { id } } } } }",
            "... on Asset must select",
        ),
    ],
)
def test_design_check_queries(design, source, message):
    root = design({"page.html.j2": "x", "page.gql": source})
    found = problems(root).get("page.gql", [])
    assert any(message in m for m in found), found


def test_design_check_accepts_fragments_with_the_asset_fields(design):
    root = design(
        {
            "page.html.j2": "x",
            "page.gql": "{ business { logo { ...L } } } fragment L on Asset { __typename id path }",
            "[slug]/params.gql": "query { services { slug: id } }",
            "[slug]/page.gql": "query($slug: ID!) { service(id: $slug) { name } }",
            "[slug]/page.html.j2": "x",
        }
    )
    assert check_design(root) == []


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("{ business { name } }", "is not a list"),
        ("{ services { id } }", "must have the key 'slug'"),
        ("{ services { slug: id } staff { slug: id } }", "exactly one root field"),
    ],
)
def test_design_check_params_shape(design, source, message):
    root = design({"[slug]/params.gql": source})
    found = problems(root).get("[slug]/params.gql", [])
    assert any(message in m for m in found), found


def test_check_every_design_in_a_workspace(ws, design):
    design({"page.html.j2": "ok"}, name="good")
    design({"page.html.j2": "{% if %}"}, name="bad")
    found = check_workspace(ws)
    assert sorted(found) == ["bad", "good"]
    assert found["good"] == [] and found["bad"][0].file == "page.html.j2"


def test_render_runs_the_check_first(ws, design):
    design({"page.html.j2": "x", "page.gql": "{ staff(includeHidden: true) { id } }"})
    with pytest.raises(CheckFailed) as exc:
        render(ws, "d")
    assert exc.value.stage == "design check"
    assert not sites_dir(ws).exists()
