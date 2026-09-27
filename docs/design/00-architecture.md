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
  outputs/
    <version_name>/   # a rendered #site: active, 3, 4_<tag> (never edited directly)
```

Python package `intelliw`:

| Module                  | Responsibility                                             |
| ----------------------- | ---------------------------------------------------------- |
| `intelliw.workspace`    | Resolve paths within a workspace                           |
| `intelliw.businessdata` | Schema (pydantic) + load/save of #businessdata             |
| `intelliw.render`       | Designs, the design check, rendering into `outputs/`       |
| `intelliw.graphql`      | Strawberry GraphQL API over #businessdata                  |
| `intelliw.mcp`          | MCP server; `query(gql)` forwards to the GraphQL API       |
| `intelliw.config`       | Settings from environment / `.env`                         |
| `intelliw.cli`          | `svr` (servers), `client-cli` (testing), `business`, `render` |

Request flow: #owner agent → MCP (`query` tool) → GraphQL → #businessdata.

## Acceptance criteria

- [x] `uv run render examples/whitby_eye_care --design clinic` produces
      `examples/whitby_eye_care/outputs/active/index.html`.
- [x] `uv run pytest` passes.
