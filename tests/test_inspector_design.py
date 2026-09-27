"""The generic `inspector` design shows the entire #businessdata (notes 2026-09-26, Task 2)."""

import asyncio
import json
import re
import shutil
from pathlib import Path

import pytest
from graphql import (
    FieldNode,
    FragmentDefinitionNode,
    FragmentSpreadNode,
    GraphQLObjectType,
    GraphQLUnionType,
    InlineFragmentNode,
    OperationDefinitionNode,
    SelectionSetNode,
    get_named_type,
    parse,
)

from intelliw.businessdata.database import create_db_engine, session_factory
from intelliw.graphql import types as t
from intelliw.graphql.context import View
from intelliw.graphql.schema import schema
from intelliw.render import RenderOptions, check_design, render
from intelliw.render.query import QueryRunner
from intelliw.workspace import Workspace

DESIGN = Path(__file__).parent.parent / "examples" / "whitby_eye_care" / "design" / "inspector"
ENTITY_TYPES = {cls.__name__ for cls in t.TYPES.values()}
# Query fields that are not #businessdata (or are covered by the list fields).
NOT_DUMPED = {"hello"}


def _fields(
    selection_set: SelectionSetNode,
    fragments: dict[str, FragmentDefinitionNode],
    on: str | None = None,
) -> dict[str, FieldNode]:
    """Selected fields by name, following fragments (inline ones only for type `on`)."""
    found: dict[str, FieldNode] = {}
    for sel in selection_set.selections:
        if isinstance(sel, FieldNode):
            found.setdefault(sel.name.value, sel)
        elif isinstance(sel, InlineFragmentNode):
            if sel.type_condition is None or sel.type_condition.name.value == on:
                found.update(_fields(sel.selection_set, fragments, on))
        elif isinstance(sel, FragmentSpreadNode):
            found.update(_fields(fragments[sel.name.value].selection_set, fragments, on))
    return found


def _missing(
    type_: GraphQLObjectType, selection_set: SelectionSetNode, fragments, path: str, top: bool
) -> list[str]:
    """Fields of `type_` not selected: all of them for records and value objects; for a
    nested reference to an entity, `__typename` and `id`."""
    selected = _fields(selection_set, fragments, type_.name)
    if not top and type_.name in ENTITY_TYPES:
        wanted = ["__typename"] + (["id"] if "id" in type_.fields else [])
        return [f"{path}.{f}" for f in wanted if f not in selected]
    missing = [f"{path}.{name}" for name in type_.fields if name not in selected]
    if top and type_.name in ENTITY_TYPES and "__typename" not in selected:
        missing.append(f"{path}.__typename")
    for name, node in selected.items():
        if name == "__typename" or node.selection_set is None:
            continue
        field_type = get_named_type(type_.fields[name].type)
        if isinstance(field_type, GraphQLUnionType):
            for member in field_type.types:
                members = _fields(node.selection_set, fragments, member.name)
                if "__typename" not in members or ("id" in member.fields and "id" not in members):
                    missing.append(f"{path}.{name} on {member.name}")
        elif isinstance(field_type, GraphQLObjectType):
            missing += _missing(field_type, node.selection_set, fragments, f"{path}.{name}", False)
    return missing


# entity page route -> (by-id query field, list field in the index page's dump)
ROUTES = {
    "contacts": ("contactPoint", "contactPoints"),
    "locations": ("location", "locations"),
    "service-categories": ("serviceCategory", "serviceCategories"),
    "services": ("service", "services"),
    "product-categories": ("productCategory", "productCategories"),
    "staff": ("staffMember", "staff"),
    "faqs": ("faq", "faqs"),
    "social-links": ("socialLink", "socialLinks"),
    "affiliations": ("affiliation", "affiliations"),
    "actions": ("customerAction", "actions"),
    "assets": ("asset", "assets"),
    "reviews": ("reviewItem", "reviews"),
}


def _root_missing(source: str) -> list[str]:
    """Fields of the root types a query does not select (see `_missing`)."""
    doc = parse(source)
    fragments = {d.name.value: d for d in doc.definitions if isinstance(d, FragmentDefinitionNode)}
    (op,) = [d for d in doc.definitions if isinstance(d, OperationDefinitionNode)]
    query_type = schema._schema.query_type
    assert query_type is not None
    missing: list[str] = []
    for node in op.selection_set.selections:
        assert isinstance(node, FieldNode)
        field_type = get_named_type(query_type.fields[node.name.value].type)
        if isinstance(field_type, GraphQLObjectType) and node.selection_set is not None:
            missing += _missing(field_type, node.selection_set, fragments, node.name.value, True)
    return missing


def test_every_collection_has_entity_pages_selecting_every_field():
    assert sorted(ROUTES) == sorted(
        p.parent.parent.name for p in DESIGN.glob("*/[[]id[]]/page.gql")
    ), "one [id] route per collection"
    for route, (by_id, _) in ROUTES.items():
        folder = DESIGN / route / "[id]"
        source = (folder / "page.gql").read_text()
        assert f"{by_id}(id: $id)" in source, route
        assert _root_missing(source) == [], route
        assert (folder / "page.html.j2").read_text().strip() == (
            '{% extends "_layouts/entity.html.j2" %}'
        )
    assert _root_missing((DESIGN / "business" / "page.gql").read_text()) == []


def test_the_dump_query_selects_every_field():
    doc = parse((DESIGN / "page.gql").read_text())
    (op,) = [d for d in doc.definitions if isinstance(d, OperationDefinitionNode)]
    roots = {n.name.value for n in op.selection_set.selections if isinstance(n, FieldNode)}
    query_type = schema._schema.query_type
    assert query_type is not None
    list_fields = {
        name
        for name, f in query_type.fields.items()
        if "id" not in f.args and name not in NOT_DUMPED
    }
    assert list_fields - roots == set(), "every collection and version field is dumped"

    assert _root_missing((DESIGN / "page.gql").read_text()) == []


def test_the_design_passes_the_check():
    assert check_design(DESIGN) == []


@pytest.fixture
def ws(data_ws: Workspace) -> Workspace:
    shutil.copytree(DESIGN, data_ws.design("inspector"))
    return data_ws


def test_everything_is_rendered(ws):
    result = render(ws, "inspector", RenderOptions(include_hidden=True))
    out = result.output_dir
    html = (out / "index.html").read_text()

    # every record of every collection has its card, with every field; hidden ones marked
    data = _dump(ws)
    for key, rows in data.items():
        if isinstance(rows, list) and rows and isinstance(rows[0], dict) and "id" in rows[0]:
            for row in rows:
                assert f'id="{row["__typename"]}-{row["id"]}"' in html, (key, row["id"])
            for field in rows[0]:
                assert f'data-field="{field}"' in html, (key, field)
    assert re.search(
        r'<article class="card is-hidden[^"]*" id="ContactPoint-appointments-email">', html
    )
    # every field of the business, the trash, the snapshots
    for field in data["business"]:
        assert f'data-field="{field}"' in html
    assert "dr-peter-chan" in html  # in the trash
    # every business image is published and shown
    assets = {a["path"] for a in data["assets"]}
    assert {p.name for p in (out / "resources").iterdir()} == assets
    for path in assets:
        assert f'src="resources/{path}"' in html
    assert json.loads((out / "render.json").read_text())["include_hidden"] is True

    # every entity has its page, linked from its card title on the index
    for route, (_, list_field) in ROUTES.items():
        for row in data[list_field]:
            page = out / route / row["id"] / "index.html"
            assert page.is_file(), (route, row["id"])
            assert f'class="title-link" href="{route}/{row["id"]}/"' in html, (route, row["id"])
            entity = page.read_text()
            for field in row:
                assert f'data-field="{field}"' in entity, (route, row["id"], field)
    assert (out / "business" / "index.html").is_file()
    assert 'href="business/"' in html


def test_without_hidden_records_the_page_says_so(ws):
    render(ws, "inspector")
    html = (ws.output_dir("active") / "index.html").read_text()
    assert "Hidden records are left out" in html
    assert 'id="ContactPoint-appointments-email"' not in html
    out = ws.output_dir("active")
    assert not (out / "contacts" / "appointments-email").exists()  # no page for hidden
    assert (out / "contacts" / "main-phone" / "index.html").is_file()


def test_entity_pages_link_to_related_entities_and_neighbours(ws):
    render(ws, "inspector", RenderOptions(include_hidden=True))
    page = (ws.output_dir("active") / "services" / "eye-exams" / "index.html").read_text()
    assert 'href="../../service-categories/eye-exams/"' in page  # its category
    assert 'href="../../faqs/how-often-eye-exam/"' in page  # a related FAQ
    assert 'href="../dry-eye-testing/"' in page  # the next service
    assert "1 / 3" in page
    assert '<a href="../../">inspector</a>' in page


def _dump(ws: Workspace) -> dict:
    """The design's own query, run as the renderer runs it (hidden included)."""
    engine = create_db_engine(ws.database_file)
    try:
        runner = QueryRunner(session_factory(engine), ws.resources_dir, View(0, True))
        return asyncio.run(runner.run("page.gql", (DESIGN / "page.gql").read_text(), {}))
    finally:
        engine.dispose()


def test_entity_pages_show_the_assets_they_reference(ws):
    render(ws, "inspector", RenderOptions(include_hidden=True))
    out = ws.output_dir("active")
    staff = (out / "staff" / "dr-raniero-fernando" / "index.html").read_text()
    assert "referenced assets" in staff
    assert 'data-asset-field="photo"' in staff
    assert '<img src="../../resources/Dr-Raniero-Fernando.jpg"' in staff
    assert 'href="../../assets/dr-raniero-fernando/"' in staff  # the asset's page
    business = (out / "business" / "index.html").read_text()
    assert 'data-asset-field="logo"' in business and 'data-asset-field="favicon"' in business
    no_photo = (out / "staff" / "dr-andrea-chan" / "index.html").read_text()
    assert "referenced assets" not in no_photo
