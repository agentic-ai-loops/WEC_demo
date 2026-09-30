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
  render/             #design: design bundles, the design check, rendering the #site
  graphql/            Strawberry GraphQL schema + server (workhorse for #businessdata)
  mcp/                MCP server exposing the above to owner agents
  cli/                typer CLIs: `svr` (servers), `client-cli` (test client),
                      `business` (inspect #businessdata), `render` (render the #site)
  config.py           Settings from environment / .env
  checks.py           Health checks used by `svr`
  workspace.py        Filesystem layout of one owner's workspace
workspaces/
  whitby_eye_care/    Sample workspace: businessdata (run `make reimport`) + designs
                      `clinic` (minimal site), `clinic-pro` (Bootstrap site) and
                      `inspector` (all data)
  demo/               A minimal business.json document
  import_whitby_digest.py   builds whitby_eye_care's businessdata from digest.xml
tests/                pytest suite
```

## Development

```bash
uv sync                                   # install deps
uv run pytest                             # run tests
uv run ruff check . && uv run ruff format .
uv run render --design clinic            # WORKSPACE from .env -> run/sites/<workspace>/clinic/ (--dryrun: write nothing)
```

## Claude Code plugin

`plugins/intelliw/` is a Claude Code plugin, listed by the repo-local marketplace in
`.claude-plugin/marketplace.json`. Its skill `/intelliw:design <workspace> <design-doc>…`
builds a #design bundle from design documents, then checks and renders it. Enable it once
with `/plugin marketplace add ./` and `/plugin install intelliw@intelliw-local`, or start
a session with `claude --plugin-dir plugins/intelliw`.

## Make targets

```bash
make mcp-start      # the server (/mcp, /graphql, /health) in the background (log in run/svr.log)
make mcp-stop       # stop it (also: make mcp-restart, make mcp-status)
make reimport       # rebuild workspaces/whitby_eye_care from digest.xml (restarts a running server)
make claude         # start Claude Code connected to the MCP server
```

Settings come from `.env`; override per call, e.g. `make mcp-start SVR_PORT=8100`.
`make claude` starts an owner agent isolated from this repository, so it can only learn
about the business through MCP:

- all built-in tools are disabled (`--tools ""`): no file access, no shell;
- it runs in an empty directory outside the home directory (`CLAUDE_DIR`, default
  `/tmp/intelliw-owner-agent`): no CLAUDE.md from this repo, its own memory and session
  history (so `--continue` there resumes only owner-agent sessions);
- only the intelliw MCP server is connected (`--strict-mcp-config`).

The read-only tools (`graphql_query`, `graphql_schema`) are pre-approved; `graphql_mutate`
asks before each change. It uses Sonnet (`make claude CLAUDE_MODEL=opus` to change).
Extra flags: `make claude CLAUDE_ARGS="..."`. Your user-level Claude Code settings and
`~/.claude/CLAUDE.md` still apply.

## Server

One server serves everything on one port:

| Route | What |
| --- | --- |
| `/mcp` | the MCP server for owner agents (streamable HTTP) |
| `/graphql` | the #businessdata GraphQL API |
| `/health` | a JSON health report (status, database, workspace, version) |

The MCP tools execute GraphQL in-process, against the same database as `/graphql`.

```bash
uv run svr start [--host 0.0.0.0] [--port 8000] [--run-dir ./run]   # foreground; writes run/svr.pid
uv run svr stop [--run-dir ./run]                                     # shut it down
uv run svr status [--run-dir ./run]                                   # process, /health, /graphql, /mcp
```

Defaults come from `.env` (copy `.env.example`): `SVR_HOST`, `SVR_PORT`, `INTELLIW_RUN_DIR`, and
`WORKSPACE`, the workspace it serves. Starting a second server, or on a port another program
uses, is refused. `start` also records its host and port in `run/svr.json`, so `status` finds
the server without options.

## Automatic re-rendering

Every committed GraphQL mutation (from `/graphql` or MCP) queues a job on Redis (rq); the
worker re-renders **every** design from the active version into
`run/sites/<workspace>/<design>/`.

```bash
make redis-start                 # Redis in docker (container intelliw-redis, port 6379)
make worker-start                # the worker in the background (log: run/worker.log)
uv run jobs status               # worker, queue, and the last job's result per design
uv run jobs enqueue              # queue a re-render by hand
make dashboard-start             # rq-dashboard: queues, jobs, workers (http://127.0.0.1:9181)
make worker-stop  /  make dashboard-stop  /  make redis-stop
```

rq-dashboard gets its own port: `DASHBOARD_HOST` / `DASHBOARD_PORT` (default
`127.0.0.1:9181`), never `SVR_PORT`. It can delete and requeue jobs and has no login
unless `DASHBOARD_USERNAME` and `DASHBOARD_PASSWORD` are set, so set them before binding
it to a reachable address.

`REDIS_URL` (default `redis://localhost:6379/0`) sets the queue. Without Redis, mutations
still work; only the automatic re-render is skipped (and logged). `/health` shows the
queue as `jobs`.

## Test client

```bash
uv run client-cli graphql --gql '{ hello { message } }'
uv run client-cli graphql --gql '{ business { name } staff { name languages } }'
echo '{ hello { message } }' | uv run client-cli graphql --stdin
uv run client-cli schema                  # print SDL via introspection
uv run client-cli mcp --list              # tools / resources / prompts
uv run client-cli mcp --tool graphql_query --document '{ staff { name } }'
uv run client-cli mcp --tool graphql_mutate --document 'mutation { takeSnapshot { number } }'
uv run client-cli mcp --resource graphql://schema
uv run client-cli mcp --prompt business_overview
uv run client-cli mcp --prompt update_business --request "Add French to Dr. Chan's languages"
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
