import json

import pytest
from mcp import Client

from intelliw.businessdata.database import session_factory
from intelliw.config import Settings
from intelliw.graphql.context import Context
from intelliw.graphql.schema import schema
from intelliw.mcp import create_server


@pytest.fixture
def server(engine, session):
    """The MCP server, executing GraphQL in-process against the sample database."""

    async def execute(document: str, variables: dict | None) -> dict:
        ctx = Context(session_factory(engine))
        result = await schema.execute(document, variable_values=variables, context_value=ctx)
        response: dict = {"data": result.data}
        if result.errors:
            response["errors"] = [e.formatted for e in result.errors]
        return response

    return create_server(Settings(), execute=execute)


async def call(server, tool: str, **args):
    async with Client(server) as client:
        return await client.call_tool(tool, args)


async def test_tools_and_annotations(server):
    async with Client(server) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    assert set(tools) == {"graphql_query", "graphql_mutate", "graphql_schema"}
    assert tools["graphql_query"].annotations.read_only_hint is True
    assert tools["graphql_schema"].annotations.read_only_hint is True
    assert tools["graphql_mutate"].annotations.destructive_hint is True


async def test_query(server):
    result = await call(server, "graphql_query", document="{ staff { name } }")
    assert not result.is_error
    names = [s["name"] for s in json.loads(result.content[0].text)["data"]["staff"]]
    assert names == ["Dr. Raniero Fernando", "Dr. Andrea Chan"]


async def test_variables_are_passed(server):
    result = await call(
        server,
        "graphql_query",
        document="query($id: ID!) { staffMember(id: $id) { name } }",
        variables={"id": "dr-andrea-chan"},
    )
    assert json.loads(result.content[0].text)["data"]["staffMember"]["name"] == "Dr. Andrea Chan"


async def test_query_tool_rejects_mutations(server):
    result = await call(server, "graphql_query", document="mutation { takeSnapshot { number } }")
    assert result.is_error and "graphql_mutate" in result.content[0].text
    # nothing happened
    check = await call(server, "graphql_query", document="{ snapshots { number } }")
    assert json.loads(check.content[0].text)["data"]["snapshots"] == [{"number": 1}]


async def test_mutate(server):
    result = await call(
        server,
        "graphql_mutate",
        document="mutation($tag: String) { takeSnapshot(tag: $tag) { number tag } }",
        variables={"tag": "before-edits"},
    )
    assert not result.is_error
    assert json.loads(result.content[0].text)["data"]["takeSnapshot"] == {
        "number": 2,
        "tag": "before-edits",
    }


async def test_mutate_tool_rejects_queries(server):
    result = await call(server, "graphql_mutate", document="{ staff { name } }")
    assert result.is_error and "graphql_query" in result.content[0].text


async def test_graphql_errors_are_error_results_with_codes(server):
    result = await call(
        server,
        "graphql_mutate",
        document='mutation { deleteServiceCategory(id: "eye-health") { id } }',
    )
    assert result.is_error
    errors = json.loads(result.content[0].text)["errors"]
    assert errors[0]["extensions"]["code"] == "IN_USE"


async def test_syntax_error(server):
    result = await call(server, "graphql_query", document="{ staff { name }")
    assert result.is_error and "syntax error" in result.content[0].text


async def test_schema_tool_and_resource(server):
    tool = await call(server, "graphql_schema")
    assert "type Query" in tool.content[0].text and "type Mutation" in tool.content[0].text
    async with Client(server) as client:
        resources = (await client.list_resources()).resources
        assert [str(r.uri) for r in resources] == ["graphql://schema"]
        content = (await client.read_resource("graphql://schema")).contents[0]
    assert content.text == tool.content[0].text
    assert content.mime_type == "text/graphql"


async def test_instructions_mention_conventions(server):
    for word in ("graphql_query", "graphql_mutate", "takeSnapshot", "READ_ONLY", "variables"):
        assert word in server.instructions
