"""The GraphQL ASGI app, served on `/graphql` by the intelliw server (`intelliw.server`)."""

import logging

from starlette.requests import Request
from starlette.responses import Response
from starlette.websockets import WebSocket
from strawberry.asgi import GraphQL

from intelliw.businessdata.database import create_db_engine, session_factory
from intelliw.graphql.context import Context, Publisher
from intelliw.graphql.schema import schema
from intelliw.workspace import Workspace

log = logging.getLogger(__name__)


class BusinessDataGraphQL(GraphQL[Context, None]):
    """Strawberry's ASGI app with a `Context` (session + loaders) per request."""

    def __init__(self, workspace: Workspace | None, publish: Publisher | None = None):
        super().__init__(schema)
        self.workspace = workspace
        self.publish = publish
        self.sessions = None
        if workspace is not None and workspace.database_file.is_file():
            self.sessions = session_factory(create_db_engine(workspace.database_file))
        else:
            log.warning("no workspace database (set WORKSPACE in .env); data queries will fail")

    async def get_context(
        self, request: Request | WebSocket, response: Response | WebSocket
    ) -> Context:
        resources = self.workspace.resources_dir if self.workspace is not None else None
        return Context(self.sessions, resources, publish=self.publish)
