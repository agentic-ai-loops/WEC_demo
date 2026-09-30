"""The intelliw server: one ASGI app, one port (`svr start`).

- `/mcp`     the MCP server for #owner agents (streamable HTTP; docs/design/06-mcp-server.md)
- `/graphql` the #businessdata GraphQL API (docs/design/02-graphql-api.md)
- `/health`  a JSON health report

The MCP tools execute GraphQL in-process, against the same database connections as the
`/graphql` route.
"""

import os
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from typing import Any

import uvicorn
from redis.exceptions import RedisError
from sqlalchemy import text
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from intelliw.config import Settings
from intelliw.graphql.server import BusinessDataGraphQL
from intelliw.jobs.queue import connect, queue_publisher
from intelliw.mcp.server import create_server, schema_executor
from intelliw.workspace import Workspace

ROUTES = {"mcp": "/mcp", "graphql": "/graphql", "health": "/health"}


def _version() -> str:
    try:
        return package_version("intelliw")
    except PackageNotFoundError:  # pragma: no cover - running from a source tree
        return "unknown"


def create_app(settings: Settings) -> Starlette:
    workspace = Workspace(settings.workspace) if settings.workspace is not None else None
    # every committed mutation, from /graphql or from MCP, queues a re-render job
    publish = queue_publisher(settings)
    graphql = BusinessDataGraphQL(workspace, publish)
    resources = workspace.resources_dir if workspace is not None else None
    mcp = create_server(schema_executor(graphql.sessions, resources, publish))

    async def health(request: Request) -> JSONResponse:
        database = "no workspace database (set WORKSPACE in .env)"
        if graphql.sessions is not None:
            try:
                with graphql.sessions() as session:
                    session.execute(text("SELECT 1"))
                database = "ok"
            except Exception as exc:  # report, don't fail the health endpoint
                database = f"error: {type(exc).__name__}: {exc}"
        try:
            jobs = "ok" if connect(settings).ping() else "unavailable"
        except RedisError as exc:
            jobs = f"unavailable: {type(exc).__name__}"
        ok = database == "ok"
        body: dict[str, Any] = {
            "status": "ok" if ok else "degraded",
            "service": "intelliw",
            "version": _version(),
            "pid": os.getpid(),
            "routes": ROUTES,
            "workspace": workspace.root.name if workspace is not None else None,
            "database": database,
            "jobs": jobs,  # the re-render queue; mutations still work without it
        }
        return JSONResponse(body, status_code=200 if ok else 503)

    # MCP's app serves /mcp and runs its session manager in its lifespan, so the other
    # routes are added to it rather than mounting it inside another app.
    app = mcp.streamable_http_app(streamable_http_path=ROUTES["mcp"], host=settings.host)
    app.router.routes.extend(
        [
            Route(ROUTES["graphql"], graphql, methods=["GET", "POST"]),
            Route(ROUTES["health"], health, methods=["GET"]),
        ]
    )
    return app


def uvicorn_server(settings: Settings) -> uvicorn.Server:
    config = uvicorn.Config(
        create_app(settings), host=settings.host, port=settings.port, log_level="info"
    )
    return uvicorn.Server(config)
