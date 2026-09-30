# Architecture overview

**Status:** draft
**Depends on:** AGENT.md

## Goal

Establish the module boundaries so later increments can be built independently.

## Specification

A *workspace* is a directory owned by one #owner:

```
<workspace>/
  businessdata/
    business.db       # #businessdata (SQLite; 03-storage), validated against the fixed schema
    resources/        # raw files (documents, images, ...)
  design/
    <name>/           # a design bundle: templates, queries, static files (04-design)
```

Rendered sites go to the server's run directory, one folder per design:
`{run_dir}/sites/{workspace}/{design}/` (run_dir: `INTELLIW_RUN_DIR`, default `./run`).

Python package `intelliw`:

| Module                  | Responsibility                                             |
| ----------------------- | ---------------------------------------------------------- |
| `intelliw.workspace`    | Resolve paths within a workspace                           |
| `intelliw.businessdata` | Schema (pydantic) + load/save of #businessdata             |
| `intelliw.render`       | Designs, the design check, rendering into `{run_dir}/sites/` |
| `intelliw.jobs`         | The re-render queue (rq + Redis): mutation events, the worker's job |
| `intelliw.graphql`      | Strawberry GraphQL API over #businessdata                  |
| `intelliw.mcp`          | MCP server; its tools execute GraphQL in-process           |
| `intelliw.server`       | The single server: `/mcp`, `/graphql`, `/health` on one port |
| `intelliw.config`       | Settings from environment / `.env`                         |
| `intelliw.cli`          | `svr` (the server), `jobs` (the re-render worker), `client-cli` (testing), `business`, `render` |

Request flow: #owner agent → MCP (`query` tool) → GraphQL → #businessdata.

## Acceptance criteria

- [x] `uv run render workspaces/whitby_eye_care --design clinic` produces
      `run/sites/whitby_eye_care/clinic/index.html`.
- [x] `uv run pytest` passes.
