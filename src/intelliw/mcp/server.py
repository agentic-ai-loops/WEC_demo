"""MCP server: the #businessdata GraphQL API for #owner agents (docs/design/06-mcp-server.md).

A thin gateway rather than one tool per operation: agents read the schema and send
GraphQL documents. Reads and writes are separate tools so MCP clients can approve reads
freely and ask the owner before writes; the split is enforced here, not trusted.
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

# (document, variables) -> GraphQL response as JSON ({"data": ..., "errors": [...]})
Executor = Callable[[str, dict[str, Any] | None], Awaitable[dict[str, Any]]]

SCHEMA_URI = "graphql://schema"

INSTRUCTIONS = """\
Manage a small business's web presence. The business data is served by a GraphQL API:
read the schema (resource graphql://schema, or the graphql_schema tool), then use
graphql_query for reads and graphql_mutate for changes. Pass values in `variables`
rather than writing them into the document.

Conventions:
- There is one editable active version and read-only numbered snapshots. Queries read the
  active version unless given `version: <n>` or `snapshot: "<tag>"`. Mutations always
  change the active version.
- Before a batch of changes, `takeSnapshot(tag: ...)`; to undo, `activateVersion(...)`
  (unsaved changes are kept in an automatic snapshot).
- Updates are patches: an omitted field is unchanged; an explicit null clears it.
- Deleting marks an entity deleted; `trash` lists deleted entities and
  `restore<Entity>(id)` brings one back. `hidden: true` keeps an entity but leaves it off
  the site.
- Review items (`reviews`) are open questions for the owner; ask the owner, then
  `resolveReview` / `dismissReview`.
- Files (images) are uploaded by the owner in the web interface; `createAsset` only
  registers an uploaded file, then reference it (e.g. a staff member's `photo`).
- Errors carry `extensions.code`: NOT_FOUND, VALIDATION, IN_USE (with `usedBy`),
  CONFLICT, STALE_ORDER, READ_ONLY.
"""


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
    server = MCPServer(name="intelliw", instructions=INSTRUCTIONS)

    async def run(document: str, variables: dict[str, Any] | None) -> CallToolResult:
        result = await execute(document, variables)
        # The GraphQL response as JSON; an error result if it has errors (with their codes).
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(result))],
            is_error=bool(result.get("errors")),
        )

    @server.tool(
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

    return server


def uvicorn_server(settings: Settings) -> uvicorn.Server:
    app = create_server(settings).streamable_http_app(host=settings.mcp_host)
    config = uvicorn.Config(app, host=settings.mcp_host, port=settings.mcp_port, log_level="info")
    return uvicorn.Server(config)
