# intelliw

An MCP-driven service that lets small/mid-size business owners manage their
web presence through their own AI agent (Claude, Codex, ...).

See [AGENT.md](AGENT.md) for the premise and [docs/](docs/) for design documents.

## Layout

```
AGENT.md              Project premise (top-level context for agents)
docs/design/          Design documents, one per component; used as prompts
src/intelliw/
  businessdata/       #businessdata: fixed schema + storage of owner content/resources
  design/             #design: jinja2 templates + assets
  site/               #site: rendering businessdata x design -> static site
  graphql/            Strawberry GraphQL schema + server (workhorse for #businessdata)
  mcp/                MCP server exposing the above to owner agents
  cli/                typer CLIs: `intelliw` (site), `svr` (servers), `client-cli` (test client),
                      `business` (inspect #businessdata)
  config.py           Settings from environment / .env
  checks.py           Health checks used by `svr`
  workspace.py        Filesystem layout of one owner's workspace
examples/demo/        A sample workspace (businessdata + design)
tests/                pytest suite
```

## Development

```bash
uv sync                                   # install deps
uv run pytest                             # run tests
uv run ruff check . && uv run ruff format .
uv run intelliw build examples/demo            # render examples/demo/_site
```

## Servers

Configuration comes from `.env` (copy `.env.example`): `GRAPHQL_HOST/PORT`, `MCP_HOST/PORT`,
and `INTELLIW_RUN_DIR` (default `.run/`), where `svr` records the PID of each running server.
Starting a server that is already running is refused.

```bash
uv run svr graphql                        # GraphQL at http://GRAPHQL_HOST:GRAPHQL_PORT/graphql
uv run svr graphql --check
uv run svr graphql --stop
uv run svr mcp [--start-graphql]          # MCP (streamable HTTP) at http://MCP_HOST:MCP_PORT/mcp
uv run svr mcp --check
uv run svr mcp --stop                     # also stops a GraphQL started with --start-graphql
uv run svr status                         # table of both servers, with PIDs
```

## Test client

```bash
uv run client-cli graphql --gql '{ hello { message } }'
uv run client-cli graphql --gql '{ business { name } staff { name languages } }'
echo '{ hello { message } }' | uv run client-cli graphql --stdin
uv run client-cli schema                  # print SDL via introspection
uv run client-cli mcp --list              # tools / resources / prompts
uv run client-cli mcp --tool query --gql '{ hello { message } }'
uv run client-cli mcp --resource <uri>
uv run client-cli mcp --prompt <name> --<arg> <value>
```

## Business data inspection

The workspace comes from `WORKSPACE` in `.env` (relative to the `.env` file), or `--workspace`.

```bash
uv run business doctor                    # statistics, integrity and resource checks (active)
uv run business doctor --tag reviewed     # ... of a snapshot (or --version <n>)
uv run business dump                      # active version as indented JSON
uv run business dump --version 1          # a snapshot, by number (or --tag <tag>)
uv run business versions                  # snapshots, tags, what the active version is based on
uv run business snapshot --tag reviewed   # freeze the active version into a new snapshot
uv run business activate --tag reviewed   # roll back to a snapshot (or --version <n>)
```

`doctor` exits with status 1 if it finds an integrity problem or a missing resource file.
`activate` first saves unsnapshotted changes as a new untagged snapshot, so a rollback can
always be undone.
