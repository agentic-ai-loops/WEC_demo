"""`client-cli`: exercise the GraphQL and MCP servers."""

import asyncio
import json
import sys
from typing import Annotated, Any

import httpx
import typer
from graphql import build_client_schema, get_introspection_query, print_schema
from mcp import Client
from rich.console import Console
from rich.json import JSON
from rich.syntax import Syntax
from rich.table import Table

from intelliw.config import Settings

app = typer.Typer(no_args_is_help=True, help="Test client for the intelliw servers.")
console = Console()
err = Console(stderr=True)


def _post_graphql(gql: str, url: str) -> dict[str, Any]:
    resp = httpx.post(url, json={"query": gql}, timeout=10)
    resp.raise_for_status()
    return resp.json()


@app.command()
def graphql(
    gql: Annotated[str | None, typer.Option("--gql", help="GraphQL document.")] = None,
    stdin: Annotated[
        bool, typer.Option("--stdin", help="Read the GraphQL document from stdin.")
    ] = False,
    url: Annotated[str | None, typer.Option(help="GraphQL endpoint (default from .env).")] = None,
) -> None:
    """Send a GraphQL query/mutation and print the JSON result."""
    if stdin:
        gql = sys.stdin.read()
    if not gql:
        err.print("[red]Provide --gql '...' or --stdin[/]")
        raise typer.Exit(2)
    result = _post_graphql(gql, url or Settings.from_env().graphql_url)
    console.print(JSON.from_data(result))
    if result.get("errors"):
        raise typer.Exit(1)


@app.command()
def schema(
    url: Annotated[str | None, typer.Option(help="GraphQL endpoint (default from .env).")] = None,
) -> None:
    """Print the server's GraphQL schema (SDL) via introspection."""
    result = _post_graphql(get_introspection_query(), url or Settings.from_env().graphql_url)
    sdl = print_schema(build_client_schema(result["data"]))
    console.print(Syntax(sdl, "graphql", theme="ansi_dark"))


def _parse_params(extra: list[str]) -> dict[str, str]:
    """Turn ['--a', '1', '--b', 'x'] into {'a': '1', 'b': 'x'}."""
    params: dict[str, str] = {}
    it = iter(extra)
    for key in it:
        if not key.startswith("--"):
            raise typer.BadParameter(f"expected --<param>, got {key!r}")
        try:
            params[key[2:].replace("-", "_")] = next(it)
        except StopIteration:
            raise typer.BadParameter(f"missing value for {key}") from None
    return params


def _coerce(params: dict[str, str], input_schema: dict[str, Any]) -> dict[str, Any]:
    """Coerce string CLI values using the tool's JSON input schema."""
    props = input_schema.get("properties", {})
    return {
        k: v if props.get(k, {}).get("type", "string") == "string" else json.loads(v)
        for k, v in params.items()
    }


def _print_content(content: list[Any]) -> None:
    for item in content:
        text = getattr(item, "text", None)
        if text is None:
            console.print(item)
            continue
        try:
            console.print(JSON(text))
        except (json.JSONDecodeError, ValueError):
            console.print(text)


async def _mcp(
    url: str,
    tool: str | None,
    resource: str | None,
    prompt: str | None,
    list_all: bool,
    params: dict[str, str],
) -> bool:
    async with Client(url) as client:
        if list_all:
            for title, items in [
                ("tools", (await client.list_tools()).tools),
                ("resources", (await client.list_resources()).resources),
                ("prompts", (await client.list_prompts()).prompts),
            ]:
                table = Table(title=title)
                table.add_column("name", style="bold cyan")
                table.add_column("description")
                for i in items:
                    table.add_row(str(getattr(i, "uri", None) or i.name), i.description or "")
                console.print(table)
        if tool:
            tools = {t.name: t for t in (await client.list_tools()).tools}
            if tool not in tools:
                err.print(f"[red]Unknown tool {tool!r}; available: {sorted(tools)}[/]")
                return False
            result = await client.call_tool(tool, _coerce(params, tools[tool].input_schema))
            _print_content(result.content)
            if result.is_error:
                return False
        if resource:
            result = await client.read_resource(resource)
            _print_content(result.contents)
        if prompt:
            result = await client.get_prompt(prompt, params)
            for m in result.messages:
                console.print(f"[bold]{m.role}:[/]", getattr(m.content, "text", m.content))
    return True


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def mcp(
    ctx: typer.Context,
    tool: Annotated[
        str | None, typer.Option("--tool", help="Tool to call; pass args as --<param> <value>.")
    ] = None,
    resource: Annotated[
        str | None, typer.Option("--resource", help="Resource URI to read.")
    ] = None,
    prompt: Annotated[
        str | None, typer.Option("--prompt", help="Prompt to get; pass args as --<param> <value>.")
    ] = None,
    list_all: Annotated[
        bool, typer.Option("--list", help="List tools, resources and prompts.")
    ] = False,
    url: Annotated[str | None, typer.Option(help="MCP endpoint (default from .env).")] = None,
) -> None:
    """Call an MCP tool, read a resource, get a prompt, or list what the server offers."""
    if not (tool or resource or prompt or list_all):
        err.print("[red]Provide one of --tool, --resource, --prompt, --list[/]")
        raise typer.Exit(2)
    params = _parse_params(ctx.args)
    ok = asyncio.run(
        _mcp(url or Settings.from_env().mcp_url, tool, resource, prompt, list_all, params)
    )
    raise typer.Exit(0 if ok else 1)
