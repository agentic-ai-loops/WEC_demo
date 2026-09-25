"""MCP server: the #businessdata GraphQL API for #owner agents (docs/design/06-mcp-server.md).

A thin gateway rather than one tool per operation: agents read the schema and send
GraphQL documents. Reads and writes are separate tools so MCP clients can approve reads
freely and ask the owner before writes; the split is enforced here, not trusted. Prompts
and the server instructions are rendered from templates (`intelliw.mcp.prompts`).
"""

import json
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import uvicorn
from graphql import GraphQLSyntaxError, OperationDefinitionNode, OperationType, parse
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent, ToolAnnotations

from intelliw.config import Settings
from intelliw.graphql.schema import schema
from intelliw.mcp import prompts
from intelliw.mcp.names import SCHEMA_URI, TOOL_MUTATE, TOOL_QUERY, TOOL_SCHEMA

# (document, variables) -> GraphQL response as JSON ({"data": ..., "errors": [...]})
Executor = Callable[[str, dict[str, Any] | None], Awaitable[dict[str, Any]]]


def http_executor(settings: Settings) -> Executor:
    """Execute GraphQL by POSTing to the configured GraphQL server."""

    async def execute(document: str, variables: dict[str, Any] | None) -> dict[str, Any]:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                settings.graphql_url, json={"query": document, "variables": variables}
            )
            resp.raise_for_status()
            return resp.json()

    return execute


def operation_types(document: str) -> set[OperationType]:
    """The operation types in a GraphQL document; ToolError if it does not parse."""
    try:
        ast = parse(document)
    except GraphQLSyntaxError as exc:
        raise ToolError(f"GraphQL syntax error: {exc.message}") from exc
    return {d.operation for d in ast.definitions if isinstance(d, OperationDefinitionNode)}


def schema_sdl() -> str:
    return schema.as_str()


def create_server(settings: Settings, execute: Executor | None = None) -> MCPServer:
    execute = execute or http_executor(settings)
    server = MCPServer(name="intelliw", instructions=prompts.render("instructions"))

    async def run(document: str, variables: dict[str, Any] | None) -> CallToolResult:
        result = await execute(document, variables)
        # The GraphQL response as JSON; an error result if it has errors (with their codes).
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(result))],
            is_error=bool(result.get("errors")),
        )

    @server.tool(
        name=TOOL_QUERY,
        title="Read business data (GraphQL query)",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )
    async def graphql_query(
        document: str, variables: dict[str, Any] | None = None
    ) -> CallToolResult:
        """Run a GraphQL query against the business data; returns the JSON response.

        Only queries are accepted; use graphql_mutate for changes.
        """
        if OperationType.MUTATION in operation_types(document):
            raise ToolError("graphql_query only runs queries; use graphql_mutate for mutations")
        return await run(document, variables)

    @server.tool(
        name=TOOL_MUTATE,
        title="Change business data (GraphQL mutation)",
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=True,
            idempotent_hint=False,
            open_world_hint=False,
        ),
    )
    async def graphql_mutate(
        document: str, variables: dict[str, Any] | None = None
    ) -> CallToolResult:
        """Run a GraphQL mutation against the business data; returns the JSON response.

        Changes the active version. Take a snapshot first for changes you may want to undo.
        """
        types = operation_types(document)
        if types != {OperationType.MUTATION}:
            raise ToolError("graphql_mutate only runs mutations; use graphql_query for queries")
        return await run(document, variables)

    @server.tool(
        name=TOOL_SCHEMA,
        title="GraphQL schema of the business data",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )
    def graphql_schema() -> str:
        """The GraphQL schema (SDL): every type, field, argument and enum of the API."""
        return schema_sdl()

    @server.resource(
        SCHEMA_URI,
        name="graphql-schema",
        title="GraphQL schema",
        description="SDL of the business data GraphQL API.",
        mime_type="text/graphql",
    )
    def graphql_schema_resource() -> str:
        return schema_sdl()

    _register_prompts(server)
    return server


def _register_prompts(server: MCPServer) -> None:
    """The prompts of `intelliw.mcp.prompts`, rendered from their templates on request."""

    def prompt(name: str) -> Any:
        title, description = prompts.PROMPTS[name]
        return server.prompt(name=name, title=title, description=description)

    @prompt("business_schema")
    def business_schema() -> str:
        return prompts.render("business_schema")

    @prompt("business_overview")
    def business_overview() -> str:
        return prompts.render("business_overview")

    @prompt("explore_business")
    def explore_business(area: str = "") -> str:
        return prompts.render("explore_business", area=area, selected=prompts.select_area(area))

    @prompt("find_information")
    def find_information(question: str) -> str:
        return prompts.render("find_information", question=question)

    @prompt("review_concerns")
    def review_concerns() -> str:
        return prompts.render("review_concerns")

    @prompt("update_business")
    def update_business(request: str) -> str:
        return prompts.render("update_business", request=request)


def uvicorn_server(settings: Settings) -> uvicorn.Server:
    app = create_server(settings).streamable_http_app(host=settings.mcp_host)
    config = uvicorn.Config(app, host=settings.mcp_host, port=settings.mcp_port, log_level="info")
    return uvicorn.Server(config)
