"""Health checks for the running services."""

import httpx
from mcp import Client

from intelliw.config import Settings


def _root_cause(e: BaseException) -> BaseException:
    while isinstance(e, BaseExceptionGroup) and e.exceptions:
        e = e.exceptions[0]
    return e


def check_graphql(settings: Settings) -> tuple[bool, str]:
    try:
        resp = httpx.post(settings.graphql_url, json={"query": "{ __typename }"}, timeout=3)
        resp.raise_for_status()
        return True, f"{settings.graphql_url} -> {resp.json()}"
    except Exception as e:
        return False, f"{settings.graphql_url} -> {type(e).__name__}: {e}"


async def check_mcp(settings: Settings) -> tuple[bool, str]:
    try:
        async with Client(settings.mcp_url, read_timeout_seconds=3) as client:
            tools = await client.list_tools()
        return True, f"{settings.mcp_url} -> tools: {[t.name for t in tools.tools]}"
    except Exception as e:
        e = _root_cause(e)
        return False, f"{settings.mcp_url} -> {type(e).__name__}: {e}"
