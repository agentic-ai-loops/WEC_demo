"""ASGI app and runner for the GraphQL server (workspace from WORKSPACE in .env)."""

import logging

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route
from starlette.websockets import WebSocket
from strawberry.asgi import GraphQL

from intelliw.businessdata.database import create_db_engine, session_factory
from intelliw.config import Settings
from intelliw.graphql.context import Context
from intelliw.graphql.schema import schema
from intelliw.workspace import Workspace

log = logging.getLogger(__name__)


class BusinessDataGraphQL(GraphQL[Context, None]):
    """Strawberry's ASGI app with a `Context` (session + loaders) per request."""

    def __init__(self, workspace: Workspace | None):
        super().__init__(schema)
        self.workspace = workspace
        self.sessions = None
        if workspace is not None and workspace.database_file.is_file():
            self.sessions = session_factory(create_db_engine(workspace.database_file))
        else:
            log.warning("no workspace database (set WORKSPACE in .env); data queries will fail")

    async def get_context(
        self, request: Request | WebSocket, response: Response | WebSocket
    ) -> Context:
        resources = self.workspace.resources_dir if self.workspace is not None else None
        return Context(self.sessions, resources)


def create_app(settings: Settings | None = None) -> Starlette:
    settings = settings or Settings.from_env()
    workspace = Workspace(settings.workspace) if settings.workspace is not None else None
    graphql = BusinessDataGraphQL(workspace)
    return Starlette(routes=[Route("/graphql", graphql, methods=["GET", "POST"])])


def uvicorn_server(settings: Settings) -> uvicorn.Server:
    config = uvicorn.Config(
        create_app(settings),
        host=settings.graphql_host,
        port=settings.graphql_port,
        log_level="info",
    )
    return uvicorn.Server(config)
