import json

from mcp import Client

from intelliw.config import Settings
from intelliw.graphql import schema
from intelliw.mcp import create_server


async def schema_executor(gql: str) -> dict:
    result = await schema.execute(gql)
    return {"data": result.data}


async def test_query_tool_forwards_graphql():
    server = create_server(Settings(), execute=schema_executor)
    async with Client(server) as client:
        result = await client.call_tool("query", {"gql": "{ hello { message } }"})
    assert json.loads(result.content[0].text) == {"data": {"hello": {"message": "hello world"}}}
