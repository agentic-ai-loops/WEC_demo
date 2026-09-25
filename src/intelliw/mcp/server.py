"""MCP server. Tool surface to be specified in docs/design/06-mcp-server.md."""

import json
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import uvicorn
from mcp.server import MCPServer

from intelliw.config import Settings

Executor = Callable[[str], Awaitable[dict[str, Any]]]


def http_executor(settings: Settings) -> Executor:
    """Execute GraphQL by POSTing to the configured GraphQL server."""

    async def execute(gql: str) -> dict[str, Any]:
        async with httpx.AsyncClient() as client:
            resp = await client.post(settings.graphql_url, json={"query": gql})
            resp.raise_for_status()
            return resp.json()

    return execute


def create_server(settings: Settings, execute: Executor | None = None) -> MCPServer:
    execute = execute or http_executor(settings)
    server = MCPServer(
        name="intelliw",
        instructions="Manage a small business's web presence: business data, design, and site.",
    )

    @server.tool()
    async def query(gql: str) -> str:
        """Run a GraphQL query/mutation against the business data API; returns the JSON result."""
        return json.dumps(await execute(gql))

    return server


def uvicorn_server(settings: Settings) -> uvicorn.Server:
    app = create_server(settings).streamable_http_app(host=settings.mcp_host)
    config = uvicorn.Config(app, host=settings.mcp_host, port=settings.mcp_port, log_level="info")
    return uvicorn.Server(config)
