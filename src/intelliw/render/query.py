"""Run design queries in-process, in the render's view (version, hidden entities)."""

from pathlib import Path
from typing import Any

from graphql import OperationDefinitionNode, parse
from sqlalchemy.orm import Session, sessionmaker

from intelliw.graphql.context import Context, View
from intelliw.graphql.schema import schema
from intelliw.render.errors import RenderError


class QueryRunner:
    """Executes design queries against the GraphQL schema, one request each."""

    def __init__(self, sessions: sessionmaker[Session], resources_dir: Path, view: View):
        self._sessions = sessions
        self._resources_dir = resources_dir
        self._view = view

    async def run(self, file: str, document: str, params: dict[str, str]) -> dict[str, Any]:
        """The query's `data`; GraphQL errors raise `RenderError`.

        Only the variables the operation declares are passed.
        """
        op = next(d for d in parse(document).definitions if isinstance(d, OperationDefinitionNode))
        declared = {v.variable.name.value for v in op.variable_definitions or ()}
        variables = {k: v for k, v in params.items() if k in declared}
        ctx = Context(self._sessions, self._resources_dir, default_view=self._view)
        result = await schema.execute(document, variable_values=variables, context_value=ctx)
        if result.errors:
            messages = "; ".join(e.message for e in result.errors)
            line = result.errors[0].locations[0].line if result.errors[0].locations else None
            raise RenderError(file, f"query failed: {messages}", line)
        return result.data or {}
