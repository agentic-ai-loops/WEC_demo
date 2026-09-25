import re

import pytest
from graphql import build_schema, parse, validate
from mcp import Client
from pydantic.alias_generators import to_camel

from intelliw.businessdata import schema as m
from intelliw.businessdata.database import session_factory
from intelliw.config import Settings
from intelliw.graphql import types as t
from intelliw.graphql.context import Context
from intelliw.graphql.schema import schema
from intelliw.mcp import create_server, prompts

GQL = build_schema(schema.as_str())
AREAS = ["", "staff", "services", "locations", "images", "Team and reputation", "nonsense"]
ARGS = {"find_information": {"question": "Who speaks Cantonese?"},
        "update_business": {"request": "Add French to Dr. Chan's languages"}}  # fmt: skip


def rendered_prompts() -> dict[str, str]:
    out = {"instructions": prompts.render("instructions")}
    for name in prompts.PROMPTS:
        if name == "explore_business":
            for area in AREAS:
                out[f"{name}[{area}]"] = prompts.render(
                    name, area=area, selected=prompts.select_area(area)
                )
        else:
            out[name] = prompts.render(name, **ARGS.get(name, {}))
    return out


def graphql_blocks(text: str) -> list[str]:
    return re.findall(r"```graphql\n(.*?)```", text, re.S)


# ---- listing and rendering over MCP -------------------------------------------------


async def test_prompts_are_listed_and_render():
    server = create_server(Settings())
    async with Client(server) as client:
        listed = {p.name: p for p in (await client.list_prompts()).prompts}
        assert set(listed) == set(prompts.PROMPTS)
        for name, (title, description) in prompts.PROMPTS.items():
            assert listed[name].description == description and listed[name].title == title
        required = {
            name: {a.name for a in (p.arguments or []) if a.required} for name, p in listed.items()
        }
        assert required["find_information"] == {"question"}
        assert required["update_business"] == {"request"}
        assert required["explore_business"] == set()
        for name in prompts.PROMPTS:
            result = await client.get_prompt(name, ARGS.get(name, {}))
            assert result.messages and "Next steps" in result.messages[0].content.text


# ---- the reference stays in sync with the code ---------------------------------------


def test_reference_covers_every_entity_and_field():
    text = prompts.render("business_schema")
    for cls in t.TYPES.values():
        name = cls.__name__
        assert f"### {name}" in text, name
        section = text.split(f"### {name}\n", 1)[1].split("\n### ", 1)[0]
        for field in GQL.type_map[name].fields:  # type: ignore[union-attr]
            assert f"| `{field}` |" in section, f"{name}.{field}"


def test_model_descriptions_flow_into_schema_and_reference():
    text = prompts.render("business_schema")
    sdl = schema.as_str()
    models = {model: cls.__name__ for model, cls in t.TYPES.items()}
    models.update({m.Address: "Address", m.GeoPoint: "GeoPoint", m.OpeningHours: "OpeningHours"})
    for model, type_name in models.items():
        assert model.__doc__ and model.__doc__.strip() in text, model.__name__
        exposed = GQL.type_map[type_name].fields  # type: ignore[union-attr]
        for name, info in model.model_fields.items():
            if info.description is None or to_camel(name) not in exposed:
                continue  # position and deleted are not part of the API
            assert info.description in sdl, f"{model.__name__}.{name} not in SDL"
            assert prompts._cell(info.description) in text, f"{model.__name__}.{name}"


def test_reference_lists_queries_mutations_and_enums():
    text = prompts.render("business_schema")
    for name in ("staff", "staffMember(id: ...)", "createStaffMember", "reorderStaff",
                 "setOpeningHours", "resolveReview", "updateBusiness"):  # fmt: skip
        assert f"`{name}`" in text
    for enum in (m.ContactKind, m.PaymentMethod, m.ReviewStatus):
        assert f"`{enum.__name__}`" in text and all(f"`{v.value}`" in text for v in enum)
    assert "`graphql_schema`" in text and "`graphql://schema`" in text


# ---- examples are valid ------------------------------------------------------------------


def test_every_graphql_example_is_valid():
    found = 0
    for name, text in rendered_prompts().items():
        for block in graphql_blocks(text):
            errors = validate(GQL, parse(block))
            assert errors == [], f"{name}: {errors}\n{block}"
            found += 1
    assert found >= 10


@pytest.mark.parametrize("name", ["business_overview", "review_concerns"])
async def test_dashboard_queries_run_on_the_sample(name, engine, session):
    for block in graphql_blocks(prompts.render(name)):
        ctx = Context(session_factory(engine))
        result = await schema.execute(block, context_value=ctx)
        assert result.errors is None, result.errors


# ---- shared guidance -----------------------------------------------------------------


def test_every_task_prompt_has_formatting_and_discovery():
    for name, text in rendered_prompts().items():
        assert "**Next steps**" in text and "**Recommended:**" in text, name
        assert "tables" in text, name
        assert "`graphql_query`" in text and "`graphql_schema`" in text, name


def test_arguments_are_rendered():
    assert "Who speaks Cantonese?" in prompts.render(
        "find_information", question="Who speaks Cantonese?"
    )
    staff = prompts.render("explore_business", area="staff", selected=prompts.select_area("staff"))
    assert "query ExploreStaffMember" in staff and "languages" in staff
    unknown = prompts.render("explore_business", area="nonsense", selected=[])
    assert 'no area called "nonsense"' in unknown


def test_error_codes_come_from_the_code():
    text = prompts.render("instructions")
    for code in ("NOT_FOUND", "VALIDATION", "IN_USE", "CONFLICT", "STALE_ORDER", "READ_ONLY"):
        assert f"`{code}`" in text
